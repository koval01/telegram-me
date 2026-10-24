//! `PyO3` bindings: `telegram_me_native._native`.
//!
//! Every call does its work detached from the interpreter, so other Python
//! threads (and the event loop, when called through `asyncio.to_thread` or an
//! executor) keep running.

use pyo3::create_exception;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use pyo3::pybacked::{PyBackedBytes, PyBackedStr};
use pyo3::types::PyBytes;

use std::time::Duration;

use crate::cache;
use crate::feed;
use crate::parse;
use crate::pow;
use crate::rank as ranking;
use crate::ratelimit;
use crate::sched;

/// Ranking parameters.
#[pyclass(frozen, module = "telegram_me_native._native", name = "RankOptions")]
pub struct PyRankOptions {
    inner: ranking::Options,
}

#[pymethods]
impl PyRankOptions {
    #[new]
    #[pyo3(signature = (
        *, now, half_life_hours = 24.0, diversity = 0.5, collapse_duplicates = true,
        duplicate_distance = 20, duplicate_containment = 0.7, duplicate_min_tokens = 10,
        topic_threshold = 0.3, topic_keywords = 5
    ))]
    #[expect(
        clippy::too_many_arguments,
        reason = "keyword arguments of the Python constructor"
    )]
    fn new(
        now: i64,
        half_life_hours: f64,
        diversity: f64,
        collapse_duplicates: bool,
        duplicate_distance: u32,
        duplicate_containment: f64,
        duplicate_min_tokens: usize,
        topic_threshold: f32,
        topic_keywords: usize,
    ) -> Self {
        Self {
            inner: ranking::Options {
                now,
                half_life_hours,
                diversity,
                collapse_duplicates,
                duplicate_distance,
                duplicate_containment,
                duplicate_min_tokens,
                topic_threshold,
                topic_keywords,
            },
        }
    }
}

/// Rank the posts of cached channel pages and render the `/v1/feed` body.
///
/// `pages` are `(requested username, cached page JSON)`. Returns the response
/// JSON and the indices of pages that could not be read (they are left out).
/// The work runs detached from the interpreter, reading the page bytes in place.
#[pyfunction]
#[expect(
    clippy::needless_pass_by_value,
    reason = "PyO3 extracts arguments by value"
)]
fn build_feed<'py>(
    py: Python<'py>,
    pages: Vec<(PyBackedStr, PyBackedBytes)>,
    options: PyRef<'py, PyRankOptions>,
) -> (Bound<'py, PyBytes>, Vec<usize>) {
    let options = options.inner.clone();
    let built = py.detach(|| {
        let pages: Vec<feed::Page<'_>> = pages
            .iter()
            .map(|(channel, json)| feed::Page { channel, json })
            .collect();
        feed::build(&pages, &options)
    });
    (PyBytes::new(py, &built.json), built.unreadable)
}

/// Skipped posts as `(data-post, reason)`.
type Skipped = Vec<(String, String)>;

fn to_json(value: &serde_json::Value) -> String {
    serde_json::to_string(value).expect("JSON values always serialise")
}

/// Parse `t.me/s/<channel>`: `(page JSON, skipped posts)`, or `None` when the
/// page is not a public channel.
#[pyfunction]
fn parse_channel_page(py: Python<'_>, html: String, simplified: bool) -> Option<(String, Skipped)> {
    py.detach(move || {
        parse::parse_channel_page(&html, simplified).map(|p| (to_json(&p.value), p.skipped))
    })
}

/// Parse a "load more" fragment: `(page JSON, skipped posts)`.
#[pyfunction]
fn parse_more_page(py: Python<'_>, html: String, simplified: bool) -> (String, Skipped) {
    py.detach(move || {
        let parsed = parse::parse_more_page(&html, simplified);
        (to_json(&parsed.value), parsed.skipped)
    })
}

/// Parse a `t.me/<channel>` landing page; `None` when it is not a channel.
#[pyfunction]
fn parse_preview_page(py: Python<'_>, html: String) -> Option<String> {
    py.detach(move || parse::parse_preview_page(&html).map(|v| to_json(&v)))
}

/// In-process byte cache (see `crate::cache`).
///
/// Calls keep the GIL. They are made from the event loop and take tens of
/// microseconds (a 95 KB page: 38 us to read, 83 us to store); released, the
/// GIL goes to a busy parsing thread and the event loop waits for it to come
/// back: a read then took 6.3 ms (p50) next to two CPU-bound Python threads.
#[pyclass(frozen, module = "telegram_me_native._native", name = "LocalCache")]
pub struct PyLocalCache {
    inner: cache::LocalCache,
}

#[pymethods]
impl PyLocalCache {
    #[new]
    fn new(max_bytes: u64) -> Self {
        Self {
            inner: cache::LocalCache::new(max_bytes),
        }
    }

    /// `(value, stored_at)` for `key`, or `None`.
    fn get<'py>(&self, py: Python<'py>, key: &str) -> PyResult<Option<(Bound<'py, PyBytes>, f64)>> {
        let Some(entry) = self.inner.get(key) else {
            return Ok(None);
        };
        let value = PyBytes::new_with(py, entry.len(), |out| {
            entry.write_value(out);
            Ok(())
        })?;
        Ok(Some((value, entry.stored_at())))
    }

    /// Store `value` for `ttl` seconds.
    fn set(&self, key: &str, value: &[u8], ttl: f64, stored_at: f64) {
        self.inner.insert(key, value, ttl, stored_at);
    }

    /// Store `value` unless a newer entry is held for `key`; True when stored.
    fn fill(&self, key: &str, value: &[u8], ttl: f64, stored_at: f64) -> bool {
        self.inner.fill(key, value, ttl, stored_at)
    }

    /// Drop every entry.
    fn clear(&self) {
        self.inner.clear();
    }

    /// Bytes held by the entries.
    #[getter]
    fn size(&self) -> u64 {
        self.inner.weighted_size()
    }

    /// The memory limit in bytes.
    #[getter]
    fn max_bytes(&self) -> u64 {
        self.inner.max_bytes()
    }

    fn __len__(&self) -> usize {
        usize::try_from(self.inner.len()).unwrap_or(usize::MAX)
    }
}

/// Per-client GCRA rate limiter (see `crate::ratelimit`).
///
/// Calls keep the GIL: a check is one map lookup, made from the event loop.
#[pyclass(frozen, module = "telegram_me_native._native", name = "RateLimiter")]
pub struct PyRateLimiter {
    inner: ratelimit::RateLimiter,
}

#[pymethods]
impl PyRateLimiter {
    #[new]
    fn new(per_minute: u32, burst: u32) -> PyResult<Self> {
        if per_minute == 0 || burst == 0 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "per_minute and burst must be positive",
            ));
        }
        Ok(Self {
            inner: ratelimit::RateLimiter::new(per_minute, burst),
        })
    }

    /// `(allowed, remaining, retry_after_seconds)` for one request of `key`.
    #[pyo3(signature = (key, cost = 1))]
    fn check(&self, key: &str, cost: u32) -> (bool, u32, f64) {
        let decision = self.inner.check(key, cost);
        (decision.allowed, decision.remaining, decision.retry_after)
    }

    /// Accepted cells per client since the previous call.
    fn take_counts(&self) -> Vec<(String, u32)> {
        self.inner.take_counts()
    }

    /// Reject every request of `key` for the next `seconds`.
    fn block(&self, key: &str, seconds: f64) {
        self.inner.block(key, seconds);
    }

    /// The burst size.
    #[getter]
    fn burst(&self) -> u32 {
        self.inner.burst()
    }

    fn __len__(&self) -> usize {
        usize::try_from(self.inner.len()).unwrap_or(usize::MAX)
    }
}

/// Proof-of-work challenges and their verification (see `crate::pow`).
///
/// Calls keep the GIL: issuing or checking takes a few microseconds.
#[pyclass(frozen, module = "telegram_me_native._native", name = "ProofOfWork")]
pub struct PyProofOfWork {
    inner: pow::ProofOfWork,
}

#[pymethods]
impl PyProofOfWork {
    #[new]
    #[pyo3(signature = (secret, bits, ttl, bind_ip = true))]
    fn new(secret: &[u8], bits: u8, ttl: u64, bind_ip: bool) -> PyResult<Self> {
        if bits > 64 || ttl == 0 {
            return Err(pyo3::exceptions::PyValueError::new_err(
                "bits must be at most 64 and ttl positive",
            ));
        }
        Ok(Self {
            inner: pow::ProofOfWork::new(secret, bits, Duration::from_secs(ttl), bind_ip),
        })
    }

    /// `(challenge, bits, expires)` for a client at `ip`.
    fn issue(&self, ip: &str) -> (String, u8, u64) {
        let issued = self.inner.issue(ip);
        (issued.challenge, issued.bits, issued.expires)
    }

    /// `None` when the `X-PoW` header is accepted, else the rejection code.
    #[pyo3(signature = (header, method, target, ip))]
    fn verify(
        &self,
        header: Option<&str>,
        method: &str,
        target: &str,
        ip: &str,
    ) -> Option<&'static str> {
        self.inner
            .verify(header, method, target, ip)
            .err()
            .map(pow::Rejection::code)
    }

    /// Required leading zero bits.
    #[getter]
    fn bits(&self) -> u8 {
        self.inner.bits()
    }
}

/// Find a nonce for `challenge` (reference solver, for tests and tools).
#[pyfunction]
fn solve_pow(challenge: &str, method: &str, target: &str, bits: u8) -> String {
    pow::solve(challenge, method, target, bits)
}

create_exception!(
    _native,
    Overloaded,
    PyException,
    "A scheduling bound was reached; the argument names it: keys, waiters or queue."
);

fn overloaded(shed: sched::Shed) -> PyErr {
    Overloaded::new_err(match shed {
        sched::Shed::Keys => "keys",
        sched::Shed::Waiters => "waiters",
        sched::Shed::Queue => "queue",
    })
}

/// Work in flight per key (see `crate::sched::Flights`); values are the
/// shared Python tasks. Calls keep the GIL: they take well under a microsecond.
#[pyclass(module = "telegram_me_native._native", name = "Flights")]
pub struct PyFlights {
    inner: sched::Flights<Py<PyAny>>,
}

#[pymethods]
impl PyFlights {
    #[new]
    fn new(max_keys: usize, max_waiters: usize) -> Self {
        Self {
            inner: sched::Flights::new(max_keys, max_waiters),
        }
    }

    /// The task in flight for `key`, or `None` when the caller leads.
    fn join(&mut self, py: Python<'_>, key: &str, wait: bool) -> PyResult<Option<Py<PyAny>>> {
        match self.inner.join(key, wait) {
            Ok(sched::Join::Lead) => Ok(None),
            Ok(sched::Join::Follow(task)) => Ok(Some(task.clone_ref(py))),
            Err(shed) => Err(overloaded(shed)),
        }
    }

    /// Record `task` as in flight for `key`.
    fn lead(&mut self, key: &str, task: Py<PyAny>) {
        self.inner.lead(key, task);
    }

    /// A waiting caller is done.
    fn leave(&mut self) {
        self.inner.leave();
    }

    /// Forget `key` if `task` is still the work in flight for it.
    fn finish(&mut self, key: &str, task: &Bound<'_, PyAny>) {
        if self.inner.get(key).is_some_and(|current| current.is(task)) {
            self.inner.finish(key);
        }
    }

    /// Number of waiting callers.
    #[getter]
    fn waiters(&self) -> usize {
        self.inner.waiters()
    }

    fn __len__(&self) -> usize {
        self.inner.len()
    }
}

/// Fair admission to the upstream slots (see `crate::sched::Gate`).
#[pyclass(module = "telegram_me_native._native", name = "Gate")]
pub struct PyGate {
    inner: sched::Gate,
}

#[pymethods]
impl PyGate {
    #[new]
    fn new(capacity: usize, flow_cap: usize, background_cap: usize, max_queue: usize) -> Self {
        Self {
            inner: sched::Gate::new(capacity, flow_cap, background_cap, max_queue),
        }
    }

    /// `(ticket, granted)`; a ticket that is not granted yet is queued.
    fn acquire(&mut self, flow: &str, background: bool) -> PyResult<(u64, bool)> {
        let priority = if background {
            sched::Priority::Background
        } else {
            sched::Priority::Interactive
        };
        match self.inner.acquire(flow, priority) {
            Ok(sched::Admit::Granted(ticket)) => Ok((ticket, true)),
            Ok(sched::Admit::Queued(ticket)) => Ok((ticket, false)),
            Err(shed) => Err(overloaded(shed)),
        }
    }

    /// Give up a held or queued ticket; returns the tickets granted meanwhile.
    fn release(&mut self, ticket: u64) -> Vec<u64> {
        self.inner.release(ticket)
    }

    /// Raise a queued background ticket to user priority; returns the
    /// tickets granted meanwhile.
    fn promote(&mut self, ticket: u64) -> Vec<u64> {
        self.inner.promote(ticket)
    }

    /// Suggested client back-off in seconds.
    #[getter]
    fn retry_after(&self) -> f64 {
        self.inner.retry_after()
    }

    /// Slots held now.
    #[getter]
    fn active(&self) -> usize {
        self.inner.active()
    }

    /// Requests waiting for a slot.
    #[getter]
    fn queued(&self) -> usize {
        self.inner.queued()
    }

    /// Flows with held or queued requests.
    #[getter]
    fn flows(&self) -> usize {
        self.inner.flows()
    }
}

/// Early renewal and TTL jitter (see `crate::sched::EarlyRefresh`).
#[pyclass(frozen, module = "telegram_me_native._native", name = "EarlyRefresh")]
pub struct PyEarlyRefresh {
    inner: sched::EarlyRefresh,
}

#[pymethods]
impl PyEarlyRefresh {
    #[new]
    fn new(window: f64, seed: u64) -> Self {
        Self {
            inner: sched::EarlyRefresh::new(window, seed),
        }
    }

    /// True once per entry when it is past its renewal point and still fresh.
    fn claim(&self, key: &str, age: f64, ttl: f64, stored_at: f64) -> bool {
        self.inner.claim(key, age, ttl, stored_at)
    }

    /// `ttl` shortened by a random part of the renewal window.
    fn jitter(&self, ttl: f64) -> f64 {
        self.inner.jitter(ttl)
    }
}

/// The extension module.
#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    module.add_class::<PyRankOptions>()?;
    module.add_class::<PyLocalCache>()?;
    module.add_class::<PyRateLimiter>()?;
    module.add_class::<PyProofOfWork>()?;
    module.add_function(wrap_pyfunction!(solve_pow, module)?)?;
    module.add_function(wrap_pyfunction!(build_feed, module)?)?;
    module.add_class::<PyFlights>()?;
    module.add_class::<PyGate>()?;
    module.add_class::<PyEarlyRefresh>()?;
    module.add("Overloaded", module.py().get_type::<Overloaded>())?;
    module.add_function(wrap_pyfunction!(parse_channel_page, module)?)?;
    module.add_function(wrap_pyfunction!(parse_more_page, module)?)?;
    module.add_function(wrap_pyfunction!(parse_preview_page, module)?)?;
    Ok(())
}
