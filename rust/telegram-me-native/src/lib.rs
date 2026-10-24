//! Native core of `TelegramMe`: everything CPU-heavy lives here, the
//! in-process cache and the request protection (rate limit, proof of work).
//!
//! The algorithms live in plain Rust modules (tested with `cargo test`); the
//! optional `python` feature adds the `PyO3` bindings built by maturin.

pub mod cache;
pub mod feed;
pub mod parse;
pub mod pow;
pub mod rank;
pub mod ratelimit;
pub mod sched;
pub mod simhash;
pub mod text;
pub mod topics;

#[cfg(feature = "python")]
mod python;
