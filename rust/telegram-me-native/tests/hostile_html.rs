//! The page parsers on hostile and broken markup: no panic, no stack
//! overflow, no super-linear blow-up.
//!
//! Every t.me page is input the service does not control, and the release
//! profile aborts on panic: one panic in a parser takes the whole worker
//! process down. Three kinds of input:
//!
//! - the recorded pages (`tests/fixtures/telegram`), whole and mutated: ranges
//!   deleted, duplicated, swapped or truncated, t.me markup and absurd values
//!   spliced in. Deterministic (seeded); `HOSTILE_CASES` sets the mutations
//!   per page and `HOSTILE_SEED` the seed, e.g. for a longer local run;
//! - deeply nested markup, parsed on a thread with a small stack;
//! - growing inputs, whose parse time must grow about linearly.
//!
//! The coverage-guided fuzz targets in `fuzz/` (nightly workflow) search
//! further.

use std::io::Read;
use std::panic::{AssertUnwindSafe, catch_unwind};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use telegram_me_native::parse::{parse_channel_page, parse_more_page, parse_preview_page};

fn fixtures_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tests/fixtures/telegram")
}

/// `(file name, HTML)` of every recorded page; "load more" responses are JSON
/// strings around the HTML, as `TelegramClient.more_page` unwraps them.
fn fixtures() -> Vec<(String, String)> {
    let mut pages: Vec<(String, String)> = std::fs::read_dir(fixtures_dir())
        .expect("fixtures directory")
        .map(|entry| entry.expect("fixture entry").path())
        .filter(|path| path.extension().is_some_and(|ext| ext == "gz"))
        .map(|path| {
            let mut text = String::new();
            flate2::read::GzDecoder::new(std::fs::File::open(&path).expect("fixture"))
                .read_to_string(&mut text)
                .expect("gzip UTF-8 fixture");
            let name = path.file_name().unwrap().to_string_lossy().into_owned();
            if name.starts_with("more_") {
                text = serde_json::from_str::<String>(&text).expect("JSON-wrapped HTML");
            }
            (name, text)
        })
        .collect();
    pages.sort();
    assert!(
        pages.len() > 30,
        "fixtures not found in {}",
        fixtures_dir().display()
    );
    pages
}

/// All parsers over one input, in both parser modes.
fn parse_all(html: &str) {
    for simplified in [false, true] {
        if let Some(parsed) = parse_channel_page(html, simplified) {
            assert!(parsed.value.is_object());
        }
        assert!(parse_more_page(html, simplified).value.is_object());
    }
    if let Some(preview) = parse_preview_page(html) {
        assert!(preview.is_object());
    }
}

/// Runs `parse_all` and reports the input (saved under target/) on a panic.
fn check(label: &str, html: &str) {
    if catch_unwind(AssertUnwindSafe(|| parse_all(html))).is_err() {
        let dir = Path::new(env!("CARGO_TARGET_TMPDIR"));
        let file = dir.join(format!("hostile-{}.html", label.replace(['/', ' '], "_")));
        std::fs::write(&file, html).expect("write failing input");
        panic!(
            "parsers panicked on {label}; input saved to {}",
            file.display()
        );
    }
}

/// xorshift64*: deterministic, no dependency.
struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        self.0 ^= self.0 >> 12;
        self.0 ^= self.0 << 25;
        self.0 ^= self.0 >> 27;
        self.0.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }

    fn below(&mut self, n: usize) -> usize {
        usize::try_from(self.next() % (n.max(1) as u64)).unwrap()
    }

    fn pick<'a>(&mut self, items: &[&'a str]) -> &'a str {
        items[self.below(items.len())]
    }
}

/// Markup and values the parser looks for, broken in the ways it might meet.
const SNIPPETS: &[&str] = &[
    r#"<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message text_not_supported_wrap js-widget_message" data-post="durov/1">"#,
    r#"<div class="tgme_widget_message" data-post="">"#,
    r#"<div class="tgme_widget_message" data-post="x/18446744073709551616">"#,
    r#"<div class="tgme_widget_message" data-post="/-1" data-view="">"#,
    r#"<div class="tgme_widget_message_text js-message_text" dir="auto">"#,
    r#"<div class="tgme_widget_message_bubble">"#,
    r#"<a class="tgme_widget_message_date" href="https://t.me/x/99"><time datetime="9999-99-99T99:99:99+99:99" class="time">"#,
    r#"<time datetime="" class="time"></time>"#,
    r#"<span class="tgme_widget_message_views">1.2.3K</span>"#,
    r#"<span class="tgme_widget_message_views">99999999999999999999999M</span>"#,
    r#"<span class="counter_value">-0.5K</span><span class="counter_type">subscribers</span>"#,
    r#"<div class="tgme_channel_info_counter"><span class="counter_value">"#,
    r#"<div class="tgme_widget_message_poll_option_percent">200%</div>"#,
    r#"<div class="tgme_widget_message_poll_option"><div class="tgme_widget_message_poll_option_text">"#,
    r#"<i class="emoji" style="background-image:url('"#,
    r#"<i class="emoji" style="background-image:url('//x')"><b>"#,
    r#"<tg-emoji emoji-id=""><i class="emoji" style="background-image:url('')"><b></b></i></tg-emoji>"#,
    r#"<a class="tgme_widget_message_link_preview" href="#,
    r#"<a class="tgme_widget_message_photo_wrap" style="width:1px;background-image:url('"#,
    r#"<i class="tgme_widget_message_video_thumb" style="background-image:url(')"></i>"#,
    r#"<time class="message_video_duration">99:99:99:99</time>"#,
    r#"<time class="message_video_duration">:</time>"#,
    r#"<audio class="tgme_widget_message_voice" src="" data-waveform="AAAA////"></audio>"#,
    r#"<div class="tgme_widget_message_voice_duration">-1:-1</div>"#,
    r#"<span class="tgme_reaction"><i class="emoji"><b>❤</b></i>1.5M</span>"#,
    r#"<a class="tgme_widget_message_reply" href="https://t.me/x/"><div class="tgme_widget_message_author">"#,
    r#"<div class="tgme_widget_message_inline_keyboard"><div class="tgme_widget_message_inline_row"><a class="tgme_widget_message_inline_button" href="javascript:x">"#,
    r#"<div class="tgme_widget_message_forwarded_from"><a class="tgme_widget_message_forwarded_from_name">"#,
    r#"<a class="tme_messages_more" data-before="" data-after="-5" href="?before=x">"#,
    r#"<link rel="prev" href="/s/x?before=99999999999999999999999">"#,
    r#"<meta property="og:title" content=""#,
    r#"<meta property="og:description">"#,
    "<b><i><u><s><code><tg-spoiler>",
    "</b></i></u></s></code></tg-spoiler>",
    "<b></b><i></i><b>x</i></b>",
    r#"<a href="https://x#y" onclick="return confirm('x')">"#,
    r#"<a href=""" onclick=>"#,
    "<br><br/><br />",
    "</div></div></div></div>",
    "<div><span><div><span>",
    "<!--",
    "]]>",
    "<script>",
    "<template><div class=\"tgme_widget_message\">",
    "<svg><foreignObject><div class=\"tgme_widget_message_text\">",
    "<table><tr><div class=\"tgme_widget_message\">",
    "&#xD800;&#0;&#x110000;&amp;&lt;&gt;&quot;&#39;&nbsp;&bogus;",
    "\u{200d}\u{202e}\u{fe0f}\u{1f9ea}\u{1f468}\u{200d}\u{1f469}",
    "#hashtag#hashtag##",
    "\0",
    "\u{feff}",
];

const NUMBERS: &[&str] = &[
    "",
    "0",
    "-1",
    "1e309",
    "18446744073709551616",
    "9223372036854775808",
    "NaN",
    "1.",
    ".5",
    "٣",
];

fn boundary(text: &str, mut index: usize) -> usize {
    index = index.min(text.len());
    while !text.is_char_boundary(index) {
        index -= 1;
    }
    index
}

fn range(rng: &mut Rng, text: &str, max: usize) -> (usize, usize) {
    let start = boundary(text, rng.below(text.len() + 1));
    let end = boundary(text, start + rng.below(max + 1));
    (start, end.max(start))
}

fn mutate(rng: &mut Rng, mut text: String) -> String {
    for _ in 0..=rng.below(4) {
        match rng.below(7) {
            0 => {
                let (start, end) = range(rng, &text, 2_000);
                text.replace_range(start..end, "");
            }
            1 => {
                let (start, end) = range(rng, &text, 2_000);
                let copy = text[start..end].to_owned();
                text.insert_str(end, &copy);
            }
            2 | 3 => {
                let at = boundary(&text, rng.below(text.len() + 1));
                let snippet = rng.pick(SNIPPETS);
                text.insert_str(at, snippet);
            }
            4 => {
                // An absurd value where a number was.
                let at = boundary(&text, rng.below(text.len() + 1));
                if let Some(offset) = text[at..].find(|c: char| c.is_ascii_digit()) {
                    let start = at + offset;
                    let len = text[start..]
                        .find(|c: char| !c.is_ascii_digit())
                        .unwrap_or(text.len() - start);
                    let number = rng.pick(NUMBERS);
                    text.replace_range(start..start + len, number);
                }
            }
            5 => {
                let at = boundary(&text, rng.below(text.len() + 1));
                text.truncate(at);
            }
            _ => {
                let (a, b) = range(rng, &text, 1_000);
                let (c, d) = range(rng, &text, 1_000);
                if b <= c {
                    let (first, second) = (text[a..b].to_owned(), text[c..d].to_owned());
                    text.replace_range(c..d, &first);
                    text.replace_range(a..b, &second);
                }
            }
        }
    }
    text
}

fn env_number(name: &str, default: u64) -> u64 {
    std::env::var(name)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

#[test]
fn recorded_pages_parse() {
    for (name, html) in fixtures() {
        check(&name, &html);
        if name.starts_with("body_") && !name.contains("missing") {
            let parsed = parse_channel_page(&html, false).expect("channel page");
            let posts = parsed.value["content"]["posts"].as_array().expect("posts");
            assert!(!posts.is_empty(), "{name}: no posts");
            assert!(
                parsed.skipped.is_empty(),
                "{name}: skipped {:?}",
                parsed.skipped
            );
        }
        if name.starts_with("more_") {
            let posts = parse_more_page(&html, false).value["posts"].clone();
            assert!(
                posts.as_array().is_some_and(|p| !p.is_empty()),
                "{name}: no posts"
            );
        }
    }
}

#[test]
fn mutated_pages_never_panic() {
    let cases = env_number("HOSTILE_CASES", 12);
    let seed = env_number("HOSTILE_SEED", 0x5EED_CAFE_F00D);
    for (index, (name, html)) in fixtures().into_iter().enumerate() {
        let mut rng = Rng(seed ^ (index as u64 + 1).wrapping_mul(0x9E37_79B9_7F4A_7C15));
        for case in 0..cases {
            let mutated = mutate(&mut rng, html.clone());
            check(&format!("{name} case {case} seed {seed}"), &mutated);
        }
    }
}

#[test]
fn snippet_soup_never_panics() {
    let seed = env_number("HOSTILE_SEED", 0x5EED_CAFE_F00D);
    let mut rng = Rng(seed);
    for case in 0..env_number("HOSTILE_CASES", 12) * 20 {
        let soup: String = (0..rng.below(200))
            .map(|_| match rng.below(3) {
                0 => rng.pick(NUMBERS),
                _ => rng.pick(SNIPPETS),
            })
            .collect();
        check(&format!("snippet soup {case} seed {seed}"), &soup);
    }
}

/// A channel page with `posts` copies of `message` (which may use `{id}`).
fn page(posts: usize, message: &str) -> String {
    let mut html = String::from(
        r#"<html><head><meta property="og:title" content="X"></head><body>
<div class="tgme_channel_info_header_title"><span>X</span></div>
<div class="tgme_channel_info_header_username"><a href="https://t.me/x">@x</a></div>
<section class="tgme_channel_history js-message_history">"#,
    );
    for id in 1..=posts {
        html.push_str(&message.replace("{id}", &id.to_string()));
    }
    html.push_str("</section></body></html>");
    html
}

fn message(text: &str) -> String {
    format!(
        r#"<div class="tgme_widget_message_wrap js-widget_message_wrap"><div class="tgme_widget_message js-widget_message" data-post="x/{{id}}" data-view="v"><div class="tgme_widget_message_bubble"><div class="tgme_widget_message_text js-message_text" dir="auto">{text}</div><div class="tgme_widget_message_footer"><div class="tgme_widget_message_info"><span class="tgme_widget_message_views">1K</span><span class="tgme_widget_message_meta"><a class="tgme_widget_message_date" href="https://t.me/x/{{id}}"><time datetime="2026-01-01T00:00:00+00:00" class="time">00:00</time></a></span></div></div></div></div></div>"#
    )
}

/// Deeply nested markup on a thread with 1 MiB of stack, well below the
/// 8 MiB of the parse threads in production: no recursion may follow the
/// nesting. (Building the DOM itself is quadratic in the nesting depth in
/// html5ever, which keeps the depth here moderate; t.me does not nest deeply.)
#[test]
fn deep_nesting_does_not_overflow_the_stack() {
    let depth = 6_000;
    let cases = [
        ("div", "<div>".repeat(depth) + &"</div>".repeat(depth)),
        ("b", "<b>".repeat(depth) + "x" + &"</b>".repeat(depth)),
        ("mixed", "<i><u><a href=\"#\">".repeat(depth / 3)),
    ];
    for (name, inner) in cases {
        let html = page(1, &message(&inner));
        let result = std::thread::Builder::new()
            .stack_size(1 << 20)
            .spawn(move || {
                for simplified in [false, true] {
                    let parsed = parse_channel_page(&html, simplified).expect("channel page");
                    assert_eq!(
                        parsed.value["content"]["posts"].as_array().map(Vec::len),
                        Some(1)
                    );
                }
            })
            .expect("spawn")
            .join();
        assert!(result.is_ok(), "parsing failed on {depth} nested {name}");
    }
}

/// Median parse time of `html` over a few runs.
fn parse_time(html: &str) -> Duration {
    let mut times: Vec<Duration> = (0..3)
        .map(|_| {
            let started = Instant::now();
            parse_all(html);
            started.elapsed()
        })
        .collect();
    times.sort();
    times[1]
}

/// Parse time of 4× the input must stay well below 16× (quadratic).
fn assert_linear(name: &str, make: impl Fn(usize) -> String, base: usize) {
    let small = parse_time(&make(base));
    let large = parse_time(&make(4 * base));
    let ratio = large.as_secs_f64() / small.as_secs_f64().max(1e-6);
    assert!(
        ratio < 9.0,
        "{name}: 4x input took {ratio:.1}x as long ({small:?} -> {large:?}): super-linear"
    );
}

#[test]
fn parse_time_grows_linearly() {
    assert_linear(
        "posts",
        |n| page(n, &message("Hello <b>world</b> #tag")),
        100,
    );
    let entities = |n: usize| {
        let text = r#"<b>b</b> <i>i</i> <a href="https://x.y/z">u</a> <code>c</code> #h "#;
        page(1, &message(&text.repeat(n)))
    };
    assert_linear("entities in one post", entities, 200);
    let unmatched = |n: usize| page(1, &message(&"<b>never closed <i>".repeat(n)));
    assert_linear("unclosed tags in one post", unmatched, 200);
    let lookahead = |n: usize| page(1, &message(&"<b>x</b> </i>".repeat(n)));
    assert_linear("bold before </i>", lookahead, 200);
    let emoji = |n: usize| {
        let text = r#"<i class="emoji" style="background-image:url('//e')"><b>x</b></i>"#;
        page(1, &message(&text.repeat(n)))
    };
    assert_linear("emoji", emoji, 200);
    let hashes = |n: usize| page(1, &message(&r#"<a href="https://x/y">#</a>"#.repeat(n)));
    assert_linear("links before #", hashes, 200);
    let text = |n: usize| page(1, &message(&"word ".repeat(n * 50)));
    assert_linear("long text", text, 200);
}
