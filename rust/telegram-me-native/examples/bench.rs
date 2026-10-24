//! `cargo run --release --example bench`: ranking time by batch size.
#![expect(clippy::cast_possible_wrap, reason = "synthetic test data")]
#![expect(clippy::print_stdout, reason = "a command-line benchmark")]

use std::time::Instant;

use telegram_me_native::rank::{Candidate, Options, rank};

const STORY: &str = "Министерство обороны сообщило о новых поставках техники в регион, \
    поставки начнутся уже на следующей неделе и продлятся до конца месяца по плану";
const WORDS: &[&str] = &[
    "ракета",
    "футбол",
    "погода",
    "выборы",
    "бюджет",
    "концерт",
    "вакцина",
    "биржа",
    "дорога",
    "школа",
    "пожар",
    "спутник",
    "налоги",
    "театр",
    "армия",
    "урожай",
    "банк",
    "музей",
];

fn varied_text(i: usize) -> String {
    (0..30)
        .map(|k| WORDS[(i * 7 + k * k + k * i) % WORDS.len()])
        .collect::<Vec<_>>()
        .join(" ")
}

fn main() {
    let now = 1_800_000_000;
    for (label, similar) in [("varied texts", false), ("near-identical texts", true)] {
        for size in [120_usize, 500, 2000] {
            let candidates: Vec<Candidate> = (0..size)
                .map(|i| Candidate {
                    channel: format!("c{}", i % 100),
                    post_id: i as u64,
                    published_at: now - (i as i64 % 48) * 3600,
                    views: 1000 + i as u64,
                    subscribers: 10_000 + i as u64,
                    text: if similar {
                        format!("{STORY} номер {}", i % 37)
                    } else {
                        varied_text(i)
                    },
                    ..Candidate::default()
                })
                .collect();
            let options = Options {
                now,
                ..Options::default()
            };
            let started = Instant::now();
            let runs = 5;
            for _ in 0..runs {
                std::hint::black_box(rank(&candidates, &options));
            }
            println!(
                "{label:22} {size:5}: {:7.2} ms",
                started.elapsed().as_secs_f64() * 1000.0 / f64::from(runs)
            );
        }
    }
}
