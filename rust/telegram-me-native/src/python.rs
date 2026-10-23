//! `PyO3` bindings: `telegram_me_native._native`.
//!
//! Inputs are copied into Rust structures while holding the GIL; the ranking
//! itself runs detached from the interpreter, so other Python threads (and the
//! event loop, when called through `asyncio.to_thread`) keep running.

use pyo3::prelude::*;

use crate::parse;
use crate::rank as ranking;

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

/// The extension module.
#[pymodule]
fn _native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    module.add_class::<PyCandidate>()?;
    module.add_class::<PyRankOptions>()?;
    module.add_class::<PyRankedItem>()?;
    module.add_class::<PyTopic>()?;
    module.add_function(wrap_pyfunction!(rank, module)?)?;
    module.add_function(wrap_pyfunction!(parse_channel_page, module)?)?;
    module.add_function(wrap_pyfunction!(parse_more_page, module)?)?;
    module.add_function(wrap_pyfunction!(parse_preview_page, module)?)?;
    Ok(())
}
