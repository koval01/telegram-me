//! Scheduling of upstream work: who fetches, when, and in which order.
//!
//! Three pieces, all called from the event loop of one worker process:
//!
//! - [`Flights`]: the table of work in flight (request coalescing). The first
//!   caller of a key leads, later callers follow and share its result. Both
//!   the number of distinct keys and the number of waiting callers are
//!   bounded, so a flood is shed instead of queued without limit.
//! - [`Gate`]: the per-host cap on concurrent upstream requests, shared fairly.
//!   Requests belong to a *flow* (a channel, or one feed request) and have a
//!   priority (user requests before background refreshes). Free slots go
//!   round-robin over flows, one flow holds at most `flow_cap` slots, and
//!   background work at most `background_cap`, so neither a hot channel, a
//!   feed of a hundred channels nor the refresher can starve the others.
//! - [`EarlyRefresh`]: jittered early renewal and TTL jitter, so a hot entry
//!   is renewed by one request shortly before it expires instead of by every
//!   request right after.

use std::collections::VecDeque;
use std::collections::hash_map::Entry;
use std::sync::Mutex;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Instant;

use rustc_hash::FxHashMap;

/// Which bound a caller was shed by.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Shed {
    /// Too many distinct keys in flight.
    Keys,
    /// Too many callers waiting for results.
    Waiters,
    /// The upstream queue is full.
    Queue,
}

/// What a caller of [`Flights::join`] has to do.
#[derive(Debug, PartialEq, Eq)]
pub enum Join<'a, T> {
    /// Nothing is in flight for the key: start the work and [`Flights::lead`] it.
    Lead,
    /// Work is in flight: wait for it.
    Follow(&'a T),
}

/// Work in flight, keyed by request key, with bounded keys and waiters.
pub struct Flights<T> {
    map: FxHashMap<String, T>,
    waiters: usize,
    max_keys: usize,
    max_waiters: usize,
}

impl<T> Flights<T> {
    /// At most `max_keys` keys in flight and `max_waiters` waiting callers.
    #[must_use]
    pub fn new(max_keys: usize, max_waiters: usize) -> Self {
        Self {
            map: FxHashMap::default(),
            waiters: 0,
            max_keys: max_keys.max(1),
            max_waiters: max_waiters.max(1),
        }
    }

    /// Join the work for `key`.
    ///
    /// With `wait`, the caller counts as a waiter until it calls
    /// [`Flights::leave`]; fire-and-forget callers pass `false`. Joining work
    /// already in flight never fails on the key bound: it adds no work.
    ///
    /// # Errors
    ///
    /// [`Shed::Waiters`] when `wait` is set and the waiter bound is reached,
    /// [`Shed::Keys`] when the key would start new work beyond the key bound.
    pub fn join(&mut self, key: &str, wait: bool) -> Result<Join<'_, T>, Shed> {
        if wait && self.waiters >= self.max_waiters {
            return Err(Shed::Waiters);
        }
        let join = match self.map.get(key) {
            Some(value) => Join::Follow(value),
            None if self.map.len() >= self.max_keys => return Err(Shed::Keys),
            None => Join::Lead,
        };
        if wait {
            self.waiters += 1;
        }
        Ok(join)
    }

    /// Record `value` (the shared work) as in flight for `key`.
    pub fn lead(&mut self, key: &str, value: T) {
        self.map.insert(key.to_owned(), value);
    }

    /// A waiting caller is done waiting.
    pub fn leave(&mut self) {
        self.waiters = self.waiters.saturating_sub(1);
    }

    /// The work for `key` is done; returns it.
    pub fn finish(&mut self, key: &str) -> Option<T> {
        self.map.remove(key)
    }

    /// The work in flight for `key`.
    #[must_use]
    pub fn get(&self, key: &str) -> Option<&T> {
        self.map.get(key)
    }

    /// Number of keys in flight.
    #[must_use]
    pub fn len(&self) -> usize {
        self.map.len()
    }

    /// True when nothing is in flight.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.map.is_empty()
    }

    /// Number of waiting callers.
    #[must_use]
    pub fn waiters(&self) -> usize {
        self.waiters
    }

    /// Iterate over the work in flight.
    pub fn values(&self) -> impl Iterator<Item = &T> {
        self.map.values()
    }
}

/// Priority of an upstream request.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Priority {
    /// A user is waiting for it.
    Interactive = 0,
    /// Background refresh: runs on spare capacity only.
    Background = 1,
}

/// Outcome of [`Gate::acquire`]; tickets are released with [`Gate::release`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Admit {
    /// The slot is held now.
    Granted(u64),
    /// Queued; the ticket is granted by a later `release` or `promote`.
    Queued(u64),
}

#[derive(Debug)]
enum State {
    Queued,
    Held(Instant),
}

#[derive(Debug)]
struct Ticket {
    flow: String,
    priority: Priority,
    state: State,
}

#[derive(Debug, Default)]
struct Flow {
    active: usize,
    queues: [VecDeque<u64>; 2],
}

impl Flow {
    fn is_idle(&self) -> bool {
        self.active == 0 && self.queues.iter().all(VecDeque::is_empty)
    }
}

/// Seconds an upstream request is assumed to hold its slot before any finished.
const INITIAL_HOLD: f64 = 0.5;
/// Weight of the newest sample in the moving average of hold times.
const HOLD_SMOOTHING: f64 = 0.1;

/// Fair, bounded admission to a fixed number of upstream slots.
#[derive(Debug)]
pub struct Gate {
    capacity: usize,
    flow_cap: usize,
    background_cap: usize,
    max_queue: usize,
    active: usize,
    active_background: usize,
    flows: FxHashMap<String, Flow>,
    // Flows with queued tickets, per priority, in round-robin order.
    ring: [VecDeque<String>; 2],
    tickets: FxHashMap<u64, Ticket>,
    queued: [usize; 2],
    next: u64,
    hold: f64,
}

impl Gate {
    /// `capacity` slots; one flow holds at most `flow_cap` of them, background
    /// work at most `background_cap`; at most `max_queue` requests wait (half
    /// of that for background ones).
    #[must_use]
    pub fn new(capacity: usize, flow_cap: usize, background_cap: usize, max_queue: usize) -> Self {
        let capacity = capacity.max(1);
        Self {
            capacity,
            flow_cap: flow_cap.clamp(1, capacity),
            background_cap: background_cap.clamp(1, capacity),
            max_queue,
            active: 0,
            active_background: 0,
            flows: FxHashMap::default(),
            ring: [VecDeque::new(), VecDeque::new()],
            tickets: FxHashMap::default(),
            queued: [0, 0],
            next: 0,
            hold: INITIAL_HOLD,
        }
    }

    /// Ask for a slot for `flow`.
    ///
    /// # Errors
    ///
    /// [`Shed::Queue`] when the request would have to wait and the queue is full.
    pub fn acquire(&mut self, flow: &str, priority: Priority) -> Result<Admit, Shed> {
        let ticket = self.next;
        if self.eligible(flow, priority) {
            self.next += 1;
            self.hold_slot(ticket, flow.to_owned(), priority);
            return Ok(Admit::Granted(ticket));
        }
        let limit = match priority {
            Priority::Interactive => self.max_queue,
            Priority::Background => self.max_queue / 2,
        };
        if self.queued() >= limit {
            return Err(Shed::Queue);
        }
        self.next += 1;
        self.enqueue(ticket, flow.to_owned(), priority);
        Ok(Admit::Queued(ticket))
    }

    /// Give up `ticket`, held or queued; returns the tickets granted meanwhile.
    pub fn release(&mut self, ticket: u64) -> Vec<u64> {
        let Some(Ticket {
            flow,
            priority,
            state,
        }) = self.tickets.remove(&ticket)
        else {
            return Vec::new();
        };
        match state {
            State::Held(since) => {
                self.active -= 1;
                if priority == Priority::Background {
                    self.active_background -= 1;
                }
                if let Some(entry) = self.flows.get_mut(&flow) {
                    entry.active -= 1;
                }
                let held = since.elapsed().as_secs_f64();
                self.hold += HOLD_SMOOTHING * (held - self.hold);
            }
            State::Queued => self.dequeue(ticket, &flow, priority),
        }
        self.forget_if_idle(&flow);
        self.dispatch()
    }

    /// Raise a queued background ticket to interactive priority (a user now
    /// waits for its result); returns the tickets granted meanwhile.
    pub fn promote(&mut self, ticket: u64) -> Vec<u64> {
        let flow = match self.tickets.get_mut(&ticket) {
            Some(entry)
                if matches!(entry.state, State::Queued)
                    && entry.priority == Priority::Background =>
            {
                entry.priority = Priority::Interactive;
                entry.flow.clone()
            }
            _ => return Vec::new(),
        };
        self.dequeue(ticket, &flow, Priority::Background);
        self.push(ticket, &flow, Priority::Interactive);
        self.dispatch()
    }

    /// Suggested client back-off in seconds while requests are being shed.
    #[must_use]
    pub fn retry_after(&self) -> f64 {
        #[allow(clippy::cast_precision_loss)] // queue lengths are far below 2^52
        let rounds = (self.queued() as f64 / self.capacity as f64).ceil() + 1.0;
        (rounds * self.hold).clamp(1.0, 60.0)
    }

    /// Slots held now.
    #[must_use]
    pub fn active(&self) -> usize {
        self.active
    }

    /// Requests waiting for a slot.
    #[must_use]
    pub fn queued(&self) -> usize {
        self.queued[0] + self.queued[1]
    }

    /// Flows with held or queued requests.
    #[must_use]
    pub fn flows(&self) -> usize {
        self.flows.len()
    }

    /// Moving average of how long a request holds its slot, in seconds.
    #[must_use]
    pub fn hold_seconds(&self) -> f64 {
        self.hold
    }

    fn eligible(&self, flow: &str, priority: Priority) -> bool {
        self.active < self.capacity
            && self.flows.get(flow).map_or(0, |f| f.active) < self.flow_cap
            && (priority == Priority::Interactive || self.active_background < self.background_cap)
    }

    fn hold_slot(&mut self, ticket: u64, flow: String, priority: Priority) {
        self.active += 1;
        if priority == Priority::Background {
            self.active_background += 1;
        }
        self.flows.entry(flow.clone()).or_default().active += 1;
        self.tickets.insert(
            ticket,
            Ticket {
                flow,
                priority,
                state: State::Held(Instant::now()),
            },
        );
    }

    fn enqueue(&mut self, ticket: u64, flow: String, priority: Priority) {
        self.push(ticket, &flow, priority);
        self.tickets.insert(
            ticket,
            Ticket {
                flow,
                priority,
                state: State::Queued,
            },
        );
    }

    fn push(&mut self, ticket: u64, flow: &str, priority: Priority) {
        let p = priority as usize;
        let entry = match self.flows.entry(flow.to_owned()) {
            Entry::Occupied(entry) => entry.into_mut(),
            Entry::Vacant(entry) => entry.insert(Flow::default()),
        };
        if entry.queues[p].is_empty() {
            self.ring[p].push_back(flow.to_owned());
        }
        entry.queues[p].push_back(ticket);
        self.queued[p] += 1;
    }

    fn dequeue(&mut self, ticket: u64, flow: &str, priority: Priority) {
        let p = priority as usize;
        let Some(entry) = self.flows.get_mut(flow) else {
            return;
        };
        let before = entry.queues[p].len();
        entry.queues[p].retain(|&t| t != ticket);
        self.queued[p] -= before - entry.queues[p].len();
        if entry.queues[p].is_empty() {
            self.ring[p].retain(|f| f != flow);
        }
    }

    fn forget_if_idle(&mut self, flow: &str) {
        if self.flows.get(flow).is_some_and(Flow::is_idle) {
            self.flows.remove(flow);
        }
    }

    /// Grant free slots to queued tickets: interactive before background,
    /// round-robin over flows within a priority.
    fn dispatch(&mut self) -> Vec<u64> {
        let mut granted = Vec::new();
        while self.active < self.capacity {
            let Some(ticket) = self
                .pick(Priority::Interactive)
                .or_else(|| self.pick(Priority::Background))
            else {
                break;
            };
            granted.push(ticket);
        }
        granted
    }

    fn pick(&mut self, priority: Priority) -> Option<u64> {
        if priority == Priority::Background && self.active_background >= self.background_cap {
            return None;
        }
        let p = priority as usize;
        for _ in 0..self.ring[p].len() {
            let flow = self.ring[p].pop_front()?;
            let entry = self.flows.get_mut(&flow)?;
            if entry.active >= self.flow_cap {
                self.ring[p].push_back(flow);
                continue;
            }
            let ticket = entry.queues[p].pop_front()?;
            self.queued[p] -= 1;
            entry.active += 1;
            if !entry.queues[p].is_empty() {
                self.ring[p].push_back(flow);
            }
            self.active += 1;
            if priority == Priority::Background {
                self.active_background += 1;
            }
            if let Some(t) = self.tickets.get_mut(&ticket) {
                t.state = State::Held(Instant::now());
            }
            return Some(ticket);
        }
        None
    }
}

/// The age (seconds) at which an entry stored at `stored_at` and fresh for
/// `ttl` seconds is renewed: a point within the last `window` fraction of its
/// TTL, derived from `stored_at`. Every reader in every process agrees on it,
/// and entries stored together (a refresher tick) renew spread out.
#[must_use]
pub fn renew_at(ttl: f64, window: f64, stored_at: f64) -> f64 {
    jitter(ttl, window, to_unit(splitmix(stored_at.to_bits())))
}

/// `ttl` shortened by up to `fraction` of it, so entries written together do
/// not expire together.
#[must_use]
pub fn jitter(ttl: f64, fraction: f64, unit: f64) -> f64 {
    ttl * (1.0 - fraction.clamp(0.0, 1.0) * unit.clamp(0.0, 1.0))
}

fn splitmix(mut x: u64) -> u64 {
    x = x.wrapping_add(0x9e37_79b9_7f4a_7c15);
    x = (x ^ (x >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    x = (x ^ (x >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    x ^ (x >> 31)
}

/// Uniform in [0, 1) from random bits.
fn to_unit(bits: u64) -> f64 {
    #[allow(clippy::cast_precision_loss)] // 53 bits fit an f64 mantissa exactly
    let unit = (bits >> 11) as f64 / (1u64 << 53) as f64;
    unit
}

/// Renewals already started, kept per key by the entry they renew.
const MAX_CLAIMS: usize = 16_384;

/// Early renewal of hot entries, and TTL jitter.
///
/// A fresh entry read past its [`renew_at`] point is renewed in the
/// background by the first such read ([`EarlyRefresh::claim`]); later reads
/// of the same entry, in this process, do not ask again. Renewals are also
/// coalesced like any fetch, across processes too, so a hot key is fetched
/// about once per TTL and never expires under load. A probabilistic scheme
/// such as `XFetch` would not do here: with hundreds of reads per second it
/// renews about `ln(reads per window)` windows before expiry, which doubles
/// the upstream traffic of exactly the hottest keys.
#[derive(Debug)]
pub struct EarlyRefresh {
    window: f64,
    state: AtomicU64,
    claims: Mutex<FxHashMap<String, u64>>,
}

impl EarlyRefresh {
    /// Renew within the last `window` fraction of a TTL (0 disables); any `seed`.
    #[must_use]
    pub fn new(window: f64, seed: u64) -> Self {
        Self {
            window: window.clamp(0.0, 1.0),
            state: AtomicU64::new(seed),
            claims: Mutex::new(FxHashMap::default()),
        }
    }

    /// True once per entry (`key`, `stored_at`) when it is `age` seconds old,
    /// past its renewal point and still fresh: the caller renews it.
    ///
    /// # Panics
    ///
    /// Never in practice: only if a previous caller panicked holding the lock.
    #[must_use]
    pub fn claim(&self, key: &str, age: f64, ttl: f64, stored_at: f64) -> bool {
        if !(self.window > 0.0 && age < ttl && age >= renew_at(ttl, self.window, stored_at)) {
            return false;
        }
        let bits = stored_at.to_bits();
        let mut claims = self.claims.lock().expect("claims lock");
        if claims.get(key) == Some(&bits) {
            return false;
        }
        if claims.len() >= MAX_CLAIMS {
            claims.clear();
        }
        claims.insert(key.to_owned(), bits);
        true
    }

    /// See [`jitter`]; shortens by up to the renewal window, at random.
    #[must_use]
    pub fn jitter(&self, ttl: f64) -> f64 {
        let bits = splitmix(self.state.fetch_add(1, Ordering::Relaxed));
        jitter(ttl, self.window, to_unit(bits))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn first_caller_leads_and_later_ones_follow() {
        let mut flights: Flights<u32> = Flights::new(10, 10);
        assert_eq!(flights.join("k", true), Ok(Join::Lead));
        flights.lead("k", 7);
        assert_eq!(flights.join("k", true), Ok(Join::Follow(&7)));
        assert_eq!(flights.join("k", false), Ok(Join::Follow(&7)));
        assert_eq!(flights.len(), 1);
        assert_eq!(flights.waiters(), 2);
        assert_eq!(flights.finish("k"), Some(7));
        flights.leave();
        flights.leave();
        flights.leave(); // never below zero
        assert_eq!(flights.waiters(), 0);
        assert!(flights.is_empty());
        assert_eq!(flights.join("k", true), Ok(Join::Lead));
    }

    #[test]
    fn distinct_keys_are_bounded_but_followers_are_not() {
        let mut flights: Flights<u32> = Flights::new(2, 100);
        for key in ["a", "b"] {
            assert_eq!(flights.join(key, true), Ok(Join::Lead));
            flights.lead(key, 1);
        }
        assert_eq!(flights.join("c", true), Err(Shed::Keys));
        assert_eq!(flights.join("a", true), Ok(Join::Follow(&1)));
        assert_eq!(flights.waiters(), 3); // the shed caller is not counted
    }

    #[test]
    fn waiters_are_bounded_unless_fire_and_forget() {
        let mut flights: Flights<u32> = Flights::new(10, 2);
        assert_eq!(flights.join("a", true), Ok(Join::Lead));
        flights.lead("a", 1);
        assert_eq!(flights.join("a", true), Ok(Join::Follow(&1)));
        assert_eq!(flights.join("a", true), Err(Shed::Waiters));
        assert_eq!(flights.join("b", true), Err(Shed::Waiters));
        assert_eq!(flights.join("b", false), Ok(Join::Lead));
        flights.leave();
        assert_eq!(flights.join("a", true), Ok(Join::Follow(&1)));
    }

    fn granted(admit: Result<Admit, Shed>) -> u64 {
        match admit {
            Ok(Admit::Granted(t)) => t,
            other => panic!("expected a grant, got {other:?}"),
        }
    }

    fn queued(admit: Result<Admit, Shed>) -> u64 {
        match admit {
            Ok(Admit::Queued(t)) => t,
            other => panic!("expected to queue, got {other:?}"),
        }
    }

    #[test]
    fn grants_up_to_capacity_then_queues_in_order() {
        let mut gate = Gate::new(2, 2, 2, 10);
        let a = granted(gate.acquire("x", Priority::Interactive));
        let _b = granted(gate.acquire("y", Priority::Interactive));
        let c = queued(gate.acquire("z", Priority::Interactive));
        let d = queued(gate.acquire("w", Priority::Interactive));
        assert_eq!((gate.active(), gate.queued()), (2, 2));
        assert_eq!(gate.release(a), vec![c]);
        assert_eq!(gate.active(), 2);
        assert_eq!(gate.queued(), 1);
        assert_eq!(gate.release(c), vec![d]);
    }

    #[test]
    fn free_slots_go_round_robin_over_flows() {
        // A feed queues 6 fetches, then a single-channel request arrives: it
        // gets the next free slot instead of waiting for the whole feed.
        let mut gate = Gate::new(2, 2, 2, 100);
        let first = granted(gate.acquire("feed", Priority::Interactive));
        granted(gate.acquire("feed", Priority::Interactive));
        let feed: Vec<u64> = (0..6)
            .map(|_| queued(gate.acquire("feed", Priority::Interactive)))
            .collect();
        let user = queued(gate.acquire("durov", Priority::Interactive));
        assert_eq!(gate.release(first), vec![feed[0]]);
        assert_eq!(gate.release(feed[0]), vec![user]);
        assert_eq!(gate.release(user), vec![feed[1]]);
    }

    #[test]
    fn one_flow_holds_at_most_flow_cap_slots() {
        let mut gate = Gate::new(4, 2, 4, 100);
        granted(gate.acquire("hot", Priority::Interactive));
        granted(gate.acquire("hot", Priority::Interactive));
        let third = queued(gate.acquire("hot", Priority::Interactive));
        // Others still get the remaining slots at once.
        let other = granted(gate.acquire("cold", Priority::Interactive));
        granted(gate.acquire("cold2", Priority::Interactive));
        assert_eq!(gate.active(), 4);
        // A slot freed by another flow does not go to the capped flow.
        assert_eq!(gate.release(other), Vec::<u64>::new());
        assert_eq!(gate.queued(), 1);
        assert_eq!(gate.active(), 3);
        let _ = third;
    }

    #[test]
    fn background_work_uses_spare_capacity_only() {
        let mut gate = Gate::new(4, 4, 2, 100);
        let b1 = granted(gate.acquire("a", Priority::Background));
        granted(gate.acquire("b", Priority::Background));
        let b3 = queued(gate.acquire("c", Priority::Background));
        // Two slots stay free for users.
        let u1 = granted(gate.acquire("d", Priority::Interactive));
        granted(gate.acquire("e", Priority::Interactive));
        let u3 = queued(gate.acquire("f", Priority::Interactive));
        // Users waiting go first.
        assert_eq!(gate.release(u1), vec![u3]);
        assert_eq!(gate.release(b1), vec![b3]);
    }

    #[test]
    fn promotion_moves_a_ticket_ahead_of_background_work() {
        let mut gate = Gate::new(3, 3, 1, 100);
        let held = granted(gate.acquire("a", Priority::Background));
        granted(gate.acquire("u", Priority::Interactive));
        let bg = queued(gate.acquire("b", Priority::Background));
        let bg2 = queued(gate.acquire("c", Priority::Background));
        // Background is at its cap: both wait although a slot is free...
        assert_eq!((gate.active(), gate.queued()), (2, 2));
        // ...until a user joins the flight of one of them.
        assert_eq!(gate.promote(bg2), vec![bg2]);
        assert_eq!(gate.promote(bg2), Vec::<u64>::new()); // held already
        assert_eq!(gate.release(held), vec![bg]);
    }

    #[test]
    fn full_queue_sheds_and_background_gets_half_of_it() {
        let mut gate = Gate::new(1, 1, 1, 4);
        granted(gate.acquire("a", Priority::Interactive));
        queued(gate.acquire("b", Priority::Background));
        queued(gate.acquire("c", Priority::Background));
        assert_eq!(gate.acquire("d", Priority::Background), Err(Shed::Queue));
        queued(gate.acquire("e", Priority::Interactive));
        queued(gate.acquire("f", Priority::Interactive));
        assert_eq!(gate.acquire("g", Priority::Interactive), Err(Shed::Queue));
        assert!(gate.retry_after() >= 1.0);
    }

    #[test]
    fn released_queued_tickets_leave_no_trace() {
        let mut gate = Gate::new(1, 1, 1, 10);
        let held = granted(gate.acquire("a", Priority::Interactive));
        let waiting = queued(gate.acquire("b", Priority::Interactive));
        assert_eq!(gate.release(waiting), Vec::<u64>::new());
        assert_eq!(gate.release(waiting), Vec::<u64>::new()); // unknown now
        assert_eq!(gate.flows(), 1);
        assert_eq!(gate.release(held), Vec::<u64>::new());
        assert_eq!((gate.active(), gate.queued(), gate.flows()), (0, 0, 0));
        assert!(gate.tickets.is_empty());
        assert!(gate.ring.iter().all(VecDeque::is_empty));
    }

    #[test]
    fn many_flows_stay_bounded_by_the_queue() {
        let mut gate = Gate::new(8, 2, 4, 64);
        let mut held = Vec::new();
        let mut shed = 0;
        for i in 0..10_000 {
            match gate.acquire(&format!("flow{i}"), Priority::Interactive) {
                Ok(Admit::Granted(t) | Admit::Queued(t)) => held.push(t),
                Err(_) => shed += 1,
            }
        }
        assert_eq!(held.len(), 8 + 64);
        assert_eq!(shed, 10_000 - 72);
        assert_eq!(gate.flows(), 72);
        for ticket in held {
            gate.release(ticket);
        }
        assert_eq!((gate.active(), gate.queued(), gate.flows()), (0, 0, 0));
    }

    #[test]
    fn hold_time_tracks_released_slots() {
        let mut gate = Gate::new(1, 1, 1, 10);
        let t = granted(gate.acquire("a", Priority::Interactive));
        gate.release(t);
        assert!(gate.hold_seconds() < INITIAL_HOLD);
    }

    #[test]
    fn renewal_points_spread_over_the_window_and_are_stable() {
        let points: Vec<f64> = (0..1000)
            .map(|i| renew_at(10.0, 0.1, 1_800_000_000.0 + f64::from(i) * 0.001))
            .collect();
        assert!(points.iter().all(|p| (9.0..=10.0).contains(p)));
        let (low, high) = points
            .iter()
            .fold((f64::MAX, f64::MIN), |(lo, hi), p| (lo.min(*p), hi.max(*p)));
        assert!(low < 9.05 && high > 9.95, "{low} {high}");
        assert!((renew_at(10.0, 0.1, 5.0) - renew_at(10.0, 0.1, 5.0)).abs() < f64::EPSILON);
        assert!((renew_at(10.0, 0.0, 5.0) - 10.0).abs() < f64::EPSILON);
    }

    #[test]
    fn an_entry_is_claimed_once_past_its_renewal_point() {
        let early = EarlyRefresh::new(0.1, 1);
        let stored_at = 1_800_000_000.25;
        let point = renew_at(10.0, 0.1, stored_at);
        assert!(!early.claim("k", point - 0.01, 10.0, stored_at));
        assert!(early.claim("k", point, 10.0, stored_at));
        assert!(!early.claim("k", point + 0.1, 10.0, stored_at));
        // Another key, or the renewed entry later on, can be claimed again.
        assert!(early.claim("other", point, 10.0, stored_at));
        let renewed = stored_at + 9.5;
        assert!(early.claim("k", renew_at(10.0, 0.1, renewed), 10.0, renewed));
        // Expired entries are fetched, not renewed; a zero window never renews.
        assert!(!early.claim("x", 10.0, 10.0, stored_at));
        assert!(!EarlyRefresh::new(0.0, 1).claim("k", 9.99, 10.0, stored_at));
    }

    #[test]
    fn claims_stay_bounded() {
        let early = EarlyRefresh::new(1.0, 1);
        for i in 0..(MAX_CLAIMS * 2) {
            assert!(early.claim(&format!("k{i}"), 9.999, 10.0, 0.0));
        }
        assert!(early.claims.lock().expect("lock").len() <= MAX_CLAIMS);
    }

    #[test]
    fn jitter_shortens_within_the_fraction() {
        assert!((jitter(10.0, 0.1, 0.0) - 10.0).abs() < 1e-9);
        assert!((jitter(10.0, 0.1, 1.0) - 9.0).abs() < 1e-9);
        let early = EarlyRefresh::new(0.1, 7);
        let values: Vec<f64> = (0..1000).map(|_| early.jitter(10.0)).collect();
        assert!(values.iter().all(|v| (9.0..=10.0).contains(v)));
        let spread = values.iter().copied().fold(f64::MAX, f64::min);
        assert!(spread < 9.1);
    }
}
