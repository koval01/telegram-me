//! Profiling helper: parse a page in a loop (`cargo run --release --example parse_loop -- page.html 2000`).
fn main() {
    let mut args = std::env::args().skip(1);
    let html = std::fs::read_to_string(args.next().expect("page")).expect("readable");
    let runs: usize = args.next().map_or(500, |n| n.parse().expect("number"));
    for _ in 0..runs {
        std::hint::black_box(telegram_me_native::parse::parse_channel_page(&html, false));
    }
}
