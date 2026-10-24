//! `cargo run --release --example parse_bench -- page.html`: where parsing time goes.
#![expect(
    clippy::print_stdout,
    clippy::unwrap_used,
    reason = "a command-line benchmark"
)]

use std::time::Instant;

use telegram_me_native::parse::{dom::Document, parse_channel_page};

fn time<T>(label: &str, runs: u32, mut f: impl FnMut() -> T) {
    f();
    let started = Instant::now();
    for _ in 0..runs {
        std::hint::black_box(f());
    }
    println!(
        "{label:28} {:7.3} ms",
        started.elapsed().as_secs_f64() * 1000.0 / f64::from(runs)
    );
}

fn main() {
    let path = std::env::args().nth(1).expect("path to an HTML page");
    let html = std::fs::read_to_string(path).expect("readable file");
    time("html5ever tree", 30, || Document::parse(&html));
    time("full page parse", 30, || parse_channel_page(&html, false));
    time("full page parse (simple)", 30, || {
        parse_channel_page(&html, true)
    });
    let doc = Document::parse(&html);
    time("serialize <body>", 30, || {
        doc.outer_html(doc.section("body").unwrap())
    });
}
