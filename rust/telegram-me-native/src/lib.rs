//! Native core of `TelegramMe`: everything CPU-heavy lives here.
//!
//! The algorithms live in plain Rust modules (tested with `cargo test`); the
//! optional `python` feature adds the `PyO3` bindings built by maturin.

pub mod parse;
pub mod rank;
pub mod simhash;
pub mod text;
pub mod topics;

#[cfg(feature = "python")]
mod python;
