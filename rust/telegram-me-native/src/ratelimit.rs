//! Per-client rate limiting with the generic cell rate algorithm (GCRA).
//!
//! A client may make `burst` requests at once and then one every
//! `60 / per_minute` seconds. The state per client is a single instant, the
//! theoretical arrival time (TAT) of its next request, so a check is one map
//! lookup and memory stays flat no matter how fast a client sends.
//!
//! The limiter is local to one process. To bound a client across processes
//! and servers, the caller periodically takes the counts of accepted requests
//! ([`RateLimiter::take_counts`]), adds them up in a shared store and blocks
//! clients that went over the limit there ([`RateLimiter::block`]).

use std::sync::Mutex;
use std::time::{Duration, Instant};

use moka::sync::Cache;
use rustc_hash::FxHashMap;

/// Most clients tracked at once; the least recently seen are forgotten first.
const MAX_CLIENTS: u64 = 200_000;
/// Most clients whose counts are kept between two [`RateLimiter::take_counts`].
const MAX_COUNTED: usize = 50_000;

/// The outcome of a check.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Decision {
    /// Whether the request may proceed.
    pub allowed: bool,
    /// Requests the client may still make right away.
    pub remaining: u32,
    /// Seconds until the next request would be allowed (0 when allowed).
    pub retry_after: f64,
}

#[derive(Debug, Clone, Copy)]
struct State {
    /// When the client's next request is due (the TAT).
    tat: Instant,
    /// Rejected until then, whatever the TAT says (see `block`).
    blocked_until: Option<Instant>,
}

/// A GCRA rate limiter keyed by client.
pub struct RateLimiter {
    interval: Duration,
    tolerance: Duration,
    burst: u32,
    clients: Cache<String, State>,
    counts: Mutex<FxHashMap<String, u32>>,
}

impl RateLimiter {
    /// `per_minute` requests per minute with bursts of up to `burst` requests.
    ///
    /// # Panics
    ///
    /// When `per_minute` or `burst` is zero.
    #[must_use]
    pub fn new(per_minute: u32, burst: u32) -> Self {
        assert!(
            per_minute > 0 && burst > 0,
            "rate and burst must be positive"
        );
        let interval = Duration::from_secs(60) / per_minute;
        let tolerance = interval * (burst - 1);
        Self {
            interval,
            tolerance,
            burst,
            // An idle client's TAT is in the past after `tolerance + interval`:
            // it would start from a full burst anyway, so it can be forgotten.
            clients: Cache::builder()
                .max_capacity(MAX_CLIENTS)
                .time_to_idle(tolerance + interval + Duration::from_secs(60))
                .build(),
            counts: Mutex::new(FxHashMap::default()),
        }
    }

    /// The burst size.
    #[must_use]
    pub fn burst(&self) -> u32 {
        self.burst
    }

    /// Check (and on success count) one request of `key` costing `cost` cells.
    pub fn check(&self, key: &str, cost: u32) -> Decision {
        self.check_at(key, cost, Instant::now())
    }

    fn check_at(&self, key: &str, cost: u32, now: Instant) -> Decision {
        let cost = cost.max(1);
        let mut decision = None;
        self.clients.entry_by_ref(key).and_compute_with(|entry| {
            let state = entry.map(moka::Entry::into_value);
            if let Some(until) = state.and_then(|s| s.blocked_until).filter(|u| *u > now) {
                decision = Some(Decision {
                    allowed: false,
                    remaining: 0,
                    retry_after: (until - now).as_secs_f64(),
                });
                return moka::ops::compute::Op::Nop;
            }
            let tat = state.map_or(now, |s| s.tat.max(now));
            let new_tat = tat + self.interval * cost;
            let horizon = now + self.tolerance + self.interval;
            if new_tat > horizon {
                decision = Some(Decision {
                    allowed: false,
                    remaining: 0,
                    retry_after: (new_tat - horizon).as_secs_f64(),
                });
                return moka::ops::compute::Op::Nop;
            }
            let headroom = horizon - new_tat;
            let remaining =
                u32::try_from(headroom.as_nanos() / self.interval.as_nanos()).unwrap_or(u32::MAX);
            decision = Some(Decision {
                allowed: true,
                remaining,
                retry_after: 0.0,
            });
            moka::ops::compute::Op::Put(State {
                tat: new_tat,
                blocked_until: None,
            })
        });
        let decision = decision.expect("the compute closure always decides");
        if decision.allowed {
            let mut counts = self
                .counts
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            if let Some(count) = counts.get_mut(key) {
                *count = count.saturating_add(cost);
            } else if counts.len() < MAX_COUNTED {
                counts.insert(key.to_owned(), cost);
            }
        }
        decision
    }

    /// Accepted cells per client since the previous call, emptying the counts.
    pub fn take_counts(&self) -> Vec<(String, u32)> {
        let mut counts = self
            .counts
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        std::mem::take(&mut *counts).into_iter().collect()
    }

    /// Reject every request of `key` for the next `seconds`.
    pub fn block(&self, key: &str, seconds: f64) {
        // Any f64 comes in from Python, and `Instant + Duration` panics on
        // overflow (which aborts the worker).
        let Some(until) = Duration::try_from_secs_f64(seconds)
            .ok()
            .and_then(|duration| Instant::now().checked_add(duration))
        else {
            return;
        };
        self.clients.entry_by_ref(key).and_compute_with(|entry| {
            let state = entry.map(moka::Entry::into_value);
            moka::ops::compute::Op::Put(State {
                tat: state.map_or_else(Instant::now, |s| s.tat),
                blocked_until: Some(
                    state
                        .and_then(|s| s.blocked_until)
                        .map_or(until, |u| u.max(until)),
                ),
            })
        });
    }

    /// Clients currently tracked.
    #[must_use]
    pub fn len(&self) -> u64 {
        self.clients.run_pending_tasks();
        self.clients.entry_count()
    }

    /// Whether no client is tracked.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn burst_then_steady_rate() {
        let limiter = RateLimiter::new(60, 3);
        let start = Instant::now();
        let remaining: Vec<u32> = (0..3)
            .map(|_| {
                let decision = limiter.check_at("a", 1, start);
                assert!(decision.allowed);
                decision.remaining
            })
            .collect();
        assert_eq!(remaining, [2, 1, 0]);

        let rejected = limiter.check_at("a", 1, start);
        assert!(!rejected.allowed);
        assert!((rejected.retry_after - 1.0).abs() < 1e-6);

        // One cell is replenished every second.
        assert!(
            limiter
                .check_at("a", 1, start + Duration::from_secs(1))
                .allowed
        );
        assert!(
            !limiter
                .check_at("a", 1, start + Duration::from_secs(1))
                .allowed
        );
        // An idle client is back to a full burst.
        assert_eq!(
            limiter
                .check_at("a", 1, start + Duration::from_secs(60))
                .remaining,
            2
        );
    }

    #[test]
    fn clients_are_independent() {
        let limiter = RateLimiter::new(1, 1);
        let now = Instant::now();
        assert!(limiter.check_at("a", 1, now).allowed);
        assert!(!limiter.check_at("a", 1, now).allowed);
        assert!(limiter.check_at("b", 1, now).allowed);
    }

    #[test]
    fn cost_takes_several_cells_and_never_exceeds_the_burst() {
        let limiter = RateLimiter::new(60, 5);
        let now = Instant::now();
        assert_eq!(limiter.check_at("a", 3, now).remaining, 2);
        assert!(!limiter.check_at("a", 3, now).allowed);
        assert!(!limiter.check_at("b", 6, now).allowed);
    }

    #[test]
    fn rejected_requests_are_not_counted() {
        let limiter = RateLimiter::new(1, 1);
        let now = Instant::now();
        for _ in 0..5 {
            limiter.check_at("a", 1, now);
        }
        assert_eq!(limiter.take_counts(), vec![("a".to_owned(), 1)]);
        assert_eq!(limiter.take_counts(), Vec::new());
    }

    #[test]
    fn block_rejects_until_it_expires() {
        let limiter = RateLimiter::new(600, 10);
        limiter.block("a", 0.05);
        let decision = limiter.check("a", 1);
        assert!(!decision.allowed);
        assert!(decision.retry_after > 0.0 && decision.retry_after <= 0.05);
        assert!(limiter.check("b", 1).allowed);
        std::thread::sleep(Duration::from_millis(60));
        assert!(limiter.check("a", 1).allowed);
    }

    #[test]
    fn block_ignores_invalid_durations() {
        let limiter = RateLimiter::new(60, 1);
        limiter.block("a", f64::NAN);
        limiter.block("a", -1.0);
        // A valid Duration, but past the end of `Instant`.
        limiter.block("a", 1.5e19);
        assert!(limiter.check("a", 1).allowed);
    }
}
