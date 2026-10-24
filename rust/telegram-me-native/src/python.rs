//! `PyO3` bindings: `telegram_me_native._native`.
//!
//! Inputs are copied into Rust structures while holding the GIL; the ranking
//! itself runs detached from the interpreter, so other Python threads (and the
//! event loop, when called through `asyncio.to_thread`) keep running.

use pyo3::prelude::*;
use pyo3::types::PyBytes;

use std::time::Duration;

use crate::cache;
use crate::parse;
use crate::pow;
use crate::rank as ranking;
use crate::ratelimit;

/// One post to rank.
#[pyclass(frozen, module = "telegram_me_native._native", name = "Candidate")]
pub struct PyCandidate {
    inner: ranking::Candidate,
}

#[pymethods]
impl PyCandidate {
    #[new]
    #[pyo3(signature = (
        *, channel, post_id, published_at, views = 0, subscribers = 0,
        positive_reactions = 0, negative_reactions = 0, photos = 0, videos = 0,
        gifs = 0, polls = 0, text = String::new(), edited = false
    ))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        channel: String,
        post_id: u64,
        published_at: i64,
        views: u64,
        subscribers: u64,
        positive_reactions: u64,
        negative_reactions: u64,
        photos: u32,
        videos: u32,
        gifs: u32,
        polls: u32,
        text: String,
        edited: bool,
    ) -> Self {
        Self {
            inner: ranking::Candidate {
                channel,
                post_id,
                published_at,
                views,
                subscribers,
                positive_reactions,
                negative_reactions,
                photos,
                videos,
                gifs,
                polls,
                text,
                edited,
            },
        }
    }

    fn __repr__(&self) -> String {
        format!(
            "Candidate(channel={:?}, post_id={})",
            self.inner.channel, self.inner.post_id
        )
    }
}

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
    #[allow(clippy::too_many_arguments)]
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

/// A ranked post: `index` into the candidate list.
#[pyclass(
    frozen,
    get_all,
    module = "telegram_me_native._native",
    name = "RankedItem"
)]
pub struct PyRankedItem {
    index: usize,
    score: f64,
    duplicates: Vec<usize>,
    topic: Option<usize>,
}

/// A group of posts about the same subject.
#[pyclass(frozen, get_all, module = "telegram_me_native._native", name = "Topic")]
pub struct PyTopic {
    keywords: Vec<String>,
    members: Vec<usize>,
}

/// Rank candidates; returns `(items in feed order, topics)`.
#[pyfunction]
#[allow(clippy::needless_pass_by_value)] // PyO3 extracts arguments by value
fn rank(
    py: Python<'_>,
    candidates: Vec<PyRef<'_, PyCandidate>>,
    options: PyRef<'_, PyRankOptions>,
) -> (Vec<PyRankedItem>, Vec<PyTopic>) {
    let inputs: Vec<ranking::Candidate> = candidates.iter().map(|c| c.inner.clone()).collect();
    let options = options.inner.clone();
    let ranking = py.detach(move || ranking::rank(&inputs, &options));
    let items = ranking
        .items
        .into_iter()
        .map(|item| PyRankedItem {
            index: item.index,
            score: item.score,
            duplicates: item.duplicates,
            topic: item.topic,
        })
        .collect();
    let topics = ranking
        .topics
        .into_iter()
        .map(|topic| PyTopic {
            keywords: topic.keywords,
            members: topic.members,
        })
        .collect();
    (items, topics)
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

/// The extension module.
#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    module.add_class::<PyCandidate>()?;
    module.add_class::<PyRankOptions>()?;
    module.add_class::<PyRankedItem>()?;
    module.add_class::<PyTopic>()?;
    module.add_class::<PyLocalCache>()?;
    module.add_class::<PyRateLimiter>()?;
    module.add_class::<PyProofOfWork>()?;
    module.add_function(wrap_pyfunction!(solve_pow, module)?)?;
    module.add_function(wrap_pyfunction!(rank, module)?)?;
    module.add_function(wrap_pyfunction!(parse_channel_page, module)?)?;
    module.add_function(wrap_pyfunction!(parse_more_page, module)?)?;
    module.add_function(wrap_pyfunction!(parse_preview_page, module)?)?;
    Ok(())
}
