//! `PyO3` bindings: `telegram_me_native._native`.
//!
//! Every call does its work detached from the interpreter, so other Python
//! threads (and the event loop, when called through `asyncio.to_thread` or an
//! executor) keep running. HTTP requests run on a shared tokio runtime and are
//! awaited from asyncio (see `PyBrowserClient`).

use pyo3::create_exception;
use pyo3::exceptions::PyException;
use pyo3::prelude::*;
use pyo3::pybacked::{PyBackedBytes, PyBackedStr};
use pyo3::types::PyBytes;

use std::net::{IpAddr, SocketAddr};
use std::os::fd::AsRawFd;
use std::sync::{Arc, Mutex, PoisonError};
use std::time::Duration;

use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::types::{PyBool, PyDict, PyTuple};

use crate::cache;
use crate::feed;
use crate::http;
use crate::parse;
use crate::peer;
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
    // Shared with the peer layer, which blocks clients over the shared limit.
    inner: Arc<ratelimit::RateLimiter>,
}

#[pymethods]
impl PyRateLimiter {
    #[new]
    fn new(per_minute: u32, burst: u32) -> PyResult<Self> {
        if per_minute == 0 || burst == 0 {
            return Err(PyValueError::new_err(
                "per_minute and burst must be positive",
            ));
        }
        Ok(Self {
            inner: Arc::new(ratelimit::RateLimiter::new(per_minute, burst)),
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
            return Err(PyValueError::new_err(
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

create_exception!(
    _native,
    FetchError,
    PyException,
    "A request to t.me failed."
);
create_exception!(
    _native,
    FetchTimeoutError,
    FetchError,
    "No answer from t.me in time."
);
create_exception!(
    _native,
    FetchNetworkError,
    FetchError,
    "The connection failed or broke before the answer was read."
);

fn fetch_error(err: http::Error) -> PyErr {
    match err {
        http::Error::Config(msg) => PyValueError::new_err(msg),
        http::Error::Timeout(msg) => FetchTimeoutError::new_err(msg),
        http::Error::Network(msg) => FetchNetworkError::new_err(msg),
        http::Error::Other(msg) => FetchError::new_err(msg),
    }
}

/// Worker threads of the shared HTTP runtime. They only move bytes: TLS,
/// HTTP/2 framing and decompression of pages of ~100 KB, so two keep up with
/// `TELEGRAM_MAX_CONCURRENCY` requests and leave the cores to the parser.
const HTTP_WORKERS: usize = 2;

/// Blocking threads: DNS lookups and handing results back to asyncio, which
/// takes the GIL (one at a time anyway).
const HTTP_BLOCKING: usize = 8;

fn http_runtime() -> tokio::runtime::Builder {
    let mut builder = tokio::runtime::Builder::new_multi_thread();
    builder
        .worker_threads(HTTP_WORKERS)
        .max_blocking_threads(HTTP_BLOCKING)
        .thread_name("telegram-me-http")
        .enable_all();
    builder
}

/// Browser-consistent HTTP client for t.me (see `crate::http`).
///
/// `fetch` returns an asyncio future: the request runs on the shared tokio
/// runtime without the GIL, and the result is set on the event loop with
/// `call_soon_threadsafe` (`pyo3-async-runtimes`). Cancelling the future
/// drops the request.
#[pyclass(frozen, module = "telegram_me_native._native", name = "BrowserClient")]
pub struct PyBrowserClient {
    inner: Mutex<Option<Arc<http::Client>>>,
    user_agent: String,
    headers: Vec<(String, String)>,
}

impl PyBrowserClient {
    fn client(&self) -> PyResult<Arc<http::Client>> {
        self.inner
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .clone()
            .ok_or_else(|| FetchError::new_err("the client is closed"))
    }
}

#[pymethods]
impl PyBrowserClient {
    #[new]
    #[pyo3(signature = (*, base_url, profile, platform, accept_language, timeout, session_lifetime))]
    fn new(
        py: Python<'_>,
        base_url: String,
        profile: String,
        platform: String,
        accept_language: String,
        timeout: f64,
        session_lifetime: f64,
    ) -> PyResult<Self> {
        let timeout = Duration::try_from_secs_f64(timeout)
            .ok()
            .filter(|t| !t.is_zero())
            .ok_or_else(|| PyValueError::new_err("timeout must be positive"))?;
        // 0 keeps a session until its cookies expire or t.me rejects it.
        let session_lifetime = Duration::try_from_secs_f64(session_lifetime)
            .map_err(|_| PyValueError::new_err("session_lifetime must be 0 or more"))?;
        let options = http::Options {
            base_url,
            profile,
            platform,
            accept_language,
            timeout,
            session_lifetime: (!session_lifetime.is_zero()).then_some(session_lifetime),
        };
        // Building the TLS context takes a few milliseconds; once per process.
        let client = py
            .detach(|| {
                let _runtime = pyo3_async_runtimes::tokio::get_runtime().enter();
                http::Client::new(&options)
            })
            .map_err(fetch_error)?;
        let headers = header_pairs(client.navigation_headers());
        Ok(Self {
            user_agent: client.user_agent().to_owned(),
            headers,
            inner: Mutex::new(Some(Arc::new(client))),
        })
    }

    /// Awaitable `(status, text)`; raises `FetchError` or a subclass.
    #[pyo3(signature = (method, path, query = Vec::new(), headers = Vec::new()))]
    fn fetch<'py>(
        &self,
        py: Python<'py>,
        method: String,
        path: String,
        query: Vec<(String, i64)>,
        headers: Vec<(String, String)>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let client = self.client()?;
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            client
                .fetch(&method, &path, &query, &headers)
                .await
                .map(|response| (response.status, response.text))
                .map_err(fetch_error)
        })
    }

    /// Stop taking requests; pooled connections close once the requests in
    /// flight finish.
    fn close(&self) {
        self.inner
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .take();
    }

    /// The `User-Agent` the profile claims.
    #[getter]
    fn user_agent(&self) -> &str {
        &self.user_agent
    }

    /// Navigation headers in the order they are sent.
    #[getter]
    fn headers(&self) -> Vec<(String, String)> {
        self.headers.clone()
    }

    /// Number of the current browser identity (1, then one more per new one).
    #[getter]
    fn session(&self) -> PyResult<u64> {
        Ok(self.client()?.session_id())
    }
}

fn header_pairs(headers: &wreq::header::HeaderMap) -> Vec<(String, String)> {
    headers
        .iter()
        .map(|(name, value)| {
            (
                name.as_str().to_owned(),
                String::from_utf8_lossy(value.as_bytes()).into_owned(),
            )
        })
        .collect()
}

/// Navigation headers of a profile, in order, without building a client.
#[pyfunction]
fn browser_headers(
    profile: &str,
    platform: &str,
    accept_language: &str,
) -> PyResult<Vec<(String, String)>> {
    http::navigation_headers(profile, platform, accept_language)
        .map(|headers| header_pairs(&headers))
        .map_err(fetch_error)
}

/// Profile names `BrowserClient` accepts.
#[pyfunction]
fn browser_profiles() -> Vec<String> {
    http::profiles()
}

/// This process's node of the peer layer (see `crate::peer`).
///
/// Calls keep the GIL: each one queues a command or reads shared state in
/// well under a microsecond. Answers arrive as events: the socket of
/// `fileno()` becomes readable and `poll()` returns them.
#[pyclass(frozen, module = "telegram_me_native._native", name = "PeerNode")]
pub struct PyPeerNode {
    inner: peer::node::Node,
}

fn socket_addr(text: &str) -> PyResult<SocketAddr> {
    text.parse()
        .map_err(|_| PyValueError::new_err(format!("not a peer address: {text}")))
}

#[pymethods]
impl PyPeerNode {
    #[new]
    #[pyo3(signature = (
        *, cache, secret, ports, bind = "0.0.0.0", advertise = None, seeds = Vec::new(),
        seed_ports = Vec::new(), allow = Vec::new(), encrypt = false, refresher = true,
        ask_timeout = 0.05
    ))]
    #[expect(
        clippy::too_many_arguments,
        clippy::needless_pass_by_value,
        reason = "PyO3 keyword arguments, extracted by value"
    )]
    fn new(
        cache: PyRef<'_, PyLocalCache>,
        secret: &[u8],
        ports: Vec<u16>,
        bind: &str,
        advertise: Option<&str>,
        seeds: Vec<String>,
        seed_ports: Vec<u16>,
        allow: Vec<String>,
        encrypt: bool,
        refresher: bool,
        ask_timeout: f64,
    ) -> PyResult<Self> {
        let ip = |text: &str| {
            text.parse::<IpAddr>()
                .map_err(|_| PyValueError::new_err(format!("not an IP address: {text}")))
        };
        let config = peer::node::Config {
            ports,
            bind: ip(bind)?,
            advertise: advertise.map(ip).transpose()?,
            seeds,
            seed_ports,
            allow: peer::allow::AllowList::parse(allow.iter().map(String::as_str))
                .map_err(PyValueError::new_err)?,
            secret: secret.to_vec(),
            encrypt,
            refresher,
            ask_timeout: Duration::try_from_secs_f64(ask_timeout)
                .map_err(|_| PyValueError::new_err("ask_timeout must be positive"))?,
            cache: cache.inner.clone(),
        };
        let inner = peer::node::Node::start(config)
            .map_err(|error| PyOSError::new_err(format!("peer layer: {error}")))?;
        Ok(Self { inner })
    }

    /// Our address, `ip:port`: the node id.
    #[getter]
    fn addr(&self) -> String {
        self.inner.addr().to_string()
    }

    /// The live members other than us.
    fn members(&self) -> Vec<String> {
        self.inner
            .members()
            .iter()
            .map(ToString::to_string)
            .collect()
    }

    /// The node owning `route`; `None` when it is this one (or we are alone).
    fn owner(&self, route: &str) -> Option<String> {
        self.inner.owner(route).map(|addr| addr.to_string())
    }

    /// Whether this process should run the feed refresher.
    fn is_leader(&self) -> bool {
        self.inner.is_leader()
    }

    /// Ask `to` for the cached entry of `key`; returns the event token.
    fn get(&self, to: &str, key: String) -> PyResult<u64> {
        Ok(self.inner.get(socket_addr(to)?, key))
    }

    /// Ask `to` to load `key` unless it holds a value stored after
    /// `newer_than`, waiting `wait` seconds at most; returns the event token.
    #[pyo3(signature = (to, key, newer_than, wait, background = false))]
    fn load(
        &self,
        to: &str,
        key: String,
        newer_than: f64,
        wait: f64,
        background: bool,
    ) -> PyResult<u64> {
        let wait = Duration::try_from_secs_f64(wait.max(0.0)).unwrap_or(Duration::MAX);
        let request = peer::node::LoadRequest {
            key,
            newer_than,
            background,
        };
        Ok(self.inner.load(socket_addr(to)?, request, wait))
    }

    /// Answer a served load with the cached entries of `keys`.
    #[expect(
        clippy::needless_pass_by_value,
        reason = "PyO3 extracts arguments by value"
    )]
    fn served(&self, token: u64, keys: Vec<String>) {
        self.inner.served(token, &keys);
    }

    /// Answer a served load with an error of class `code`.
    fn serve_failed(&self, token: u64, code: u8, message: String) {
        self.inner.serve_failed(token, code, message);
    }

    /// Block clients whose requests across all nodes go over `limit`.
    fn attach_rate_limiter(&self, limiter: &Bound<'_, PyRateLimiter>, limit: u32) {
        self.inner
            .attach_rate_limiter(Arc::clone(&limiter.get().inner), limit);
    }

    /// Add this process's counts per client and send them to every node.
    #[expect(
        clippy::needless_pass_by_value,
        reason = "PyO3 extracts arguments by value"
    )]
    fn share_counts(&self, items: Vec<(String, u32)>) {
        self.inner.share_counts(&items);
    }

    /// Send `data` about `topic` to every other node, best effort.
    fn cast(&self, topic: String, data: &[u8]) {
        self.inner.cast(topic, data.to_vec());
    }

    /// More places to look for other nodes.
    fn add_seeds(&self, seeds: Vec<String>) {
        self.inner.add_seeds(seeds);
    }

    /// The events since the last call, as tuples:
    ///
    /// - `("got", token, value | None, stored_at)`
    /// - `("value", token, value, stored_at)`: a load ended with a value
    /// - `("failed", token, code, message)` / `("unavailable", token, None, None)`
    /// - `("serve", token, key, (newer_than, background))`
    /// - `("cast", 0, topic, data)` / `("members", 0, None, None)`
    fn poll<'py>(&self, py: Python<'py>) -> PyResult<Vec<Bound<'py, PyTuple>>> {
        use peer::node::{Event, Outcome};
        let none = || py.None().into_bound(py);
        self.inner
            .poll()
            .into_iter()
            .map(|event| {
                let (kind, token, first, second) = match event {
                    Event::Got { token, found } => match found {
                        Some((value, stored_at)) => (
                            "got",
                            token,
                            PyBytes::new(py, &value).into_any(),
                            stored_at.into_pyobject(py)?.into_any(),
                        ),
                        None => ("got", token, none(), none()),
                    },
                    Event::Loaded { token, outcome } => match outcome {
                        Outcome::Value(value, stored_at) => (
                            "value",
                            token,
                            PyBytes::new(py, &value).into_any(),
                            stored_at.into_pyobject(py)?.into_any(),
                        ),
                        Outcome::Failed(code, message) => (
                            "failed",
                            token,
                            code.into_pyobject(py)?.into_any(),
                            message.into_pyobject(py)?.into_any(),
                        ),
                        Outcome::Unavailable => ("unavailable", token, none(), none()),
                    },
                    Event::Serve { token, request } => (
                        "serve",
                        token,
                        request.key.into_pyobject(py)?.into_any(),
                        PyTuple::new(
                            py,
                            [
                                request.newer_than.into_pyobject(py)?.into_any(),
                                PyBool::new(py, request.background).to_owned().into_any(),
                            ],
                        )?
                        .into_any(),
                    ),
                    Event::Cast { topic, data } => (
                        "cast",
                        0,
                        topic.into_pyobject(py)?.into_any(),
                        PyBytes::new(py, &data).into_any(),
                    ),
                    Event::Members => ("members", 0, none(), none()),
                };
                PyTuple::new(
                    py,
                    [
                        kind.into_pyobject(py)?.into_any(),
                        token.into_pyobject(py)?.into_any(),
                        first,
                        second,
                    ],
                )
            })
            .collect()
    }

    /// The socket that becomes readable when `poll()` has events.
    fn fileno(&self) -> i32 {
        self.inner.waker().as_raw_fd()
    }

    /// Counters: `refused`, `rejected`, `asked`, `unanswered`, `served_gets`, `served_loads`.
    fn stats<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        use std::sync::atomic::Ordering::Relaxed;
        let stats = self.inner.stats();
        let dict = PyDict::new(py);
        for (name, value) in [
            ("refused", &stats.refused),
            ("rejected", &stats.rejected),
            ("asked", &stats.asked),
            ("unanswered", &stats.unanswered),
            ("served_gets", &stats.served_gets),
            ("served_loads", &stats.served_loads),
        ] {
            dict.set_item(name, value.load(Relaxed))?;
        }
        Ok(dict)
    }

    /// Leave the cluster and stop the network thread.
    fn close(&self, py: Python<'_>) {
        py.detach(|| self.inner.close());
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
    module.add_class::<PyPeerNode>()?;
    module.add("Overloaded", module.py().get_type::<Overloaded>())?;
    module.add_function(wrap_pyfunction!(parse_channel_page, module)?)?;
    module.add_function(wrap_pyfunction!(parse_more_page, module)?)?;
    module.add_function(wrap_pyfunction!(parse_preview_page, module)?)?;
    // Stores the builder only: threads start with the first request.
    pyo3_async_runtimes::tokio::init(http_runtime());
    module.add_class::<PyBrowserClient>()?;
    module.add_function(wrap_pyfunction!(browser_profiles, module)?)?;
    module.add_function(wrap_pyfunction!(browser_headers, module)?)?;
    module.add("FetchError", module.py().get_type::<FetchError>())?;
    module.add(
        "FetchTimeoutError",
        module.py().get_type::<FetchTimeoutError>(),
    )?;
    module.add(
        "FetchNetworkError",
        module.py().get_type::<FetchNetworkError>(),
    )?;
    Ok(())
}
