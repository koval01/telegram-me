//! Feed ranking.
//!
//! 1. **Quality** of each post, relative to its own channel: views are
//!    projected to their eventual value from the post's age and compared with
//!    the channel's other posts (robust z-score: median / MAD), so a post that
//!    does unusually well for its channel stands out regardless of channel size.
//!    Reactions per view, negative-reaction share, content richness and a small
//!    audience-size prior complete the picture.
//! 2. **Freshness**: exponential decay with a configurable half-life.
//! 3. **Duplicates**: the same story posted by several channels is collapsed
//!    into its best-scoring post (`SimHash`, union-find).
//! 4. **Diversity**: greedy re-ranking that penalises posts similar to the ones
//!    just placed (TF-IDF cosine) and runs of posts from the same channel.
//! 5. **Topics**: posts are grouped by subject with their keywords.

use rayon::prelude::*;
use rustc_hash::FxHashMap as HashMap;

use crate::simhash;
use crate::text::{Token, tokens};
use crate::topics::{self, Corpus, Topic};

/// One post to rank. Counters are already parsed (`1.2K` → 1200).
#[derive(Debug, Clone, Default)]
pub struct Candidate {
    /// Channel username.
    pub channel: String,
    /// Post id within the channel.
    pub post_id: u64,
    /// Publication time, Unix seconds.
    pub published_at: i64,
    /// View counter.
    pub views: u64,
    /// Channel audience.
    pub subscribers: u64,
    /// Sum of positive reaction counters.
    pub positive_reactions: u64,
    /// Sum of negative reaction counters.
    pub negative_reactions: u64,
    /// Attached photos.
    pub photos: u32,
    /// Attached videos and round videos.
    pub videos: u32,
    /// GIFs and stickers.
    pub gifs: u32,
    /// Polls (0 or 1).
    pub polls: u32,
    /// Plain text of the post.
    pub text: String,
    /// The post was edited.
    pub edited: bool,
}

/// Ranking parameters.
#[derive(Debug, Clone)]
pub struct Options {
    /// "Now", Unix seconds (passed in for reproducibility).
    pub now: i64,
    /// Hours after which freshness halves.
    pub half_life_hours: f64,
    /// 0 = pure score order, 1 = strongest diversification.
    pub diversity: f64,
    /// Collapse near-duplicate posts into one.
    pub collapse_duplicates: bool,
    /// Maximum `SimHash` distance (bits) for two posts to be compared.
    pub duplicate_distance: u32,
    /// Share of the shorter text's shingles the other text must contain.
    pub duplicate_containment: f64,
    /// Minimum tokens for a text to take part in duplicate detection.
    pub duplicate_min_tokens: usize,
    /// Cosine similarity needed to join a topic.
    pub topic_threshold: f32,
    /// Keywords reported per topic.
    pub topic_keywords: usize,
}

impl Default for Options {
    fn default() -> Self {
        Self {
            now: 0,
            half_life_hours: 24.0,
            diversity: 0.5,
            collapse_duplicates: true,
            duplicate_distance: 20,
            duplicate_containment: 0.7,
            duplicate_min_tokens: 10,
            topic_threshold: 0.3,
            topic_keywords: 5,
        }
    }
}

/// A ranked post.
#[derive(Debug, Clone, PartialEq)]
pub struct Ranked {
    /// Index into the candidate list.
    pub index: usize,
    /// Final score in `[0, 10]`.
    pub score: f64,
    /// Indices of near-duplicates collapsed into this post.
    pub duplicates: Vec<usize>,
    /// Index into [`Ranking::topics`].
    pub topic: Option<usize>,
}

/// Ranking result.
#[derive(Debug, Clone, PartialEq)]
pub struct Ranking {
    /// Posts in feed order.
    pub items: Vec<Ranked>,
    /// Topics referenced by `items`.
    pub topics: Vec<Topic>,
}

/// Fraction of its eventual views a post has after `age_hours`.
fn view_maturity(age_hours: f64) -> f64 {
    (1.0 - (-age_hours / 24.0).exp()).max(0.05)
}

fn median(values: &mut [f64]) -> f64 {
    values.sort_by(f64::total_cmp);
    let mid = values.len() / 2;
    if values.len() % 2 == 0 {
        f64::midpoint(values[mid - 1], values[mid])
    } else {
        values[mid]
    }
}

/// Posts a channel needs in the batch to be compared with itself.
const MIN_CHANNEL_POSTS: usize = 3;

/// Median and scaled median absolute deviation (never zero).
fn robust_center(values: &mut [f64]) -> (f64, f64) {
    if values.is_empty() {
        return (0.0, 1.0);
    }
    let center = median(values);
    let mut deviations: Vec<f64> = values.iter().map(|v| (v - center).abs()).collect();
    (center, 1.4826 * median(&mut deviations) + 0.05)
}

fn sigmoid(x: f64) -> f64 {
    1.0 / (1.0 + (-x).exp())
}

#[allow(clippy::cast_precision_loss)]
fn base_scores(candidates: &[Candidate], tokens: &[Vec<Token>], options: &Options) -> Vec<f64> {
    let age_hours: Vec<f64> = candidates
        .iter()
        .map(|c| ((options.now - c.published_at) as f64 / 3600.0).max(0.1))
        .collect();
    let projected: Vec<f64> = candidates
        .iter()
        .zip(&age_hours)
        .map(|(c, age)| (1.0 + c.views as f64 / view_maturity(*age)).ln())
        .collect();

    // Robust per-channel statistics of projected log-views.
    let mut by_channel: HashMap<&str, Vec<f64>> = HashMap::default();
    for (c, value) in candidates.iter().zip(&projected) {
        by_channel
            .entry(c.channel.as_str())
            .or_default()
            .push(*value);
    }
    let stats: HashMap<&str, (f64, f64)> = by_channel
        .into_iter()
        .filter(|(_, values)| values.len() >= MIN_CHANNEL_POSTS)
        .map(|(channel, mut values)| (channel, robust_center(&mut values)))
        .collect();

    // Channels with too few posts in the batch are compared with everyone
    // else on views per subscriber instead.
    let reach_rate: Vec<f64> = candidates
        .iter()
        .zip(&projected)
        .map(|(c, value)| value - (2.0 + c.subscribers as f64).ln())
        .collect();
    let global = robust_center(&mut reach_rate.clone());

    let max_reach = candidates
        .iter()
        .map(|c| (1.0 + c.subscribers as f64).ln())
        .fold(1.0_f64, f64::max);

    candidates
        .iter()
        .enumerate()
        .map(|(i, c)| {
            let z = match stats.get(c.channel.as_str()) {
                Some((center, spread)) => (projected[i] - center) / spread,
                None => (reach_rate[i] - global.0) / global.1,
            };
            let engagement = sigmoid(z.clamp(-3.0, 3.0));
            let net_reactions = c.positive_reactions.saturating_sub(c.negative_reactions);
            let reactions = (net_reactions as f64 / (c.views as f64 + 1.0) * 50.0).min(1.0);
            let negativity = c.negative_reactions as f64
                / (c.positive_reactions + c.negative_reactions + 1) as f64;
            let media = f64::from(c.photos + 2 * c.videos + c.gifs + 2 * c.polls);
            let text = (tokens[i].len() as f64 / 60.0).min(1.0);
            let content = ((1.0 + media).ln() / 6.0_f64.ln()).min(1.0) * 0.7 + text * 0.3;
            let reach = (1.0 + c.subscribers as f64).ln() / max_reach;

            let quality = 0.45 * engagement
                + 0.2 * reactions
                + 0.15 * content
                + 0.1 * reach
                + 0.1 * (1.0 - negativity)
                + if c.edited { 0.02 } else { 0.0 };
            let freshness = 0.5_f64.powf(age_hours[i] / options.half_life_hours.max(0.01));
            (10.0 * quality * (0.1 + 0.9 * freshness)).clamp(0.0, 10.0)
        })
        .collect()
}

/// Deterministic order: score, then newer first, then channel and id.
fn by_score<'a>(
    candidates: &'a [Candidate],
    scores: &'a [f64],
) -> impl Fn(&usize, &usize) -> std::cmp::Ordering + 'a {
    move |a, b| {
        scores[*b]
            .total_cmp(&scores[*a])
            .then_with(|| {
                candidates[*b]
                    .published_at
                    .cmp(&candidates[*a].published_at)
            })
            .then_with(|| candidates[*a].channel.cmp(&candidates[*b].channel))
            .then_with(|| candidates[*a].post_id.cmp(&candidates[*b].post_id))
    }
}

/// How many recently placed posts the diversity penalty looks at.
const SIMILARITY_WINDOW: usize = 10;
const CHANNEL_WINDOW: usize = 5;

/// Greedy diversification (a variant of maximal marginal relevance).
///
/// The adjusted score of a candidate is its base score multiplied by
/// `(1 − d·max_similarity)` and by `(1 − d/2)` for every post of the same
/// channel among the last few placed. Penalties never raise a score, so
/// candidates are visited in base-score order and the scan stops as soon as
/// no remaining base score can beat the best adjusted score found.
fn diversify(
    pool: Vec<usize>,
    candidates: &[Candidate],
    scores: &[f64],
    corpus: &Corpus,
    diversity: f64,
) -> Vec<(usize, f64)> {
    let mut remaining = pool;
    let mut placed: Vec<usize> = Vec::with_capacity(remaining.len());
    let mut result = Vec::with_capacity(remaining.len());
    let d = diversity.clamp(0.0, 1.0);

    while !remaining.is_empty() {
        let mut best: Option<(usize, f64)> = None;
        for (position, &candidate) in remaining.iter().enumerate() {
            if best.is_some_and(|(_, score)| scores[candidate] <= score) {
                break;
            }
            let max_similarity = placed
                .iter()
                .rev()
                .take(SIMILARITY_WINDOW)
                .map(|&p| topics::cosine(&corpus.vectors[candidate], &corpus.vectors[p]))
                .fold(0.0_f32, f32::max);
            let same_channel = placed
                .iter()
                .rev()
                .take(CHANNEL_WINDOW)
                .filter(|&&p| candidates[p].channel == candidates[candidate].channel)
                .count();
            #[allow(clippy::cast_possible_wrap, clippy::cast_possible_truncation)]
            let adjusted = scores[candidate]
                * (1.0 - d * f64::from(max_similarity))
                * (1.0 - d / 2.0).powi(same_channel as i32);
            if best.is_none_or(|(_, score)| adjusted > score) {
                best = Some((position, adjusted));
            }
        }
        let (position, _) = best.expect("remaining is not empty");
        let chosen = remaining.remove(position);
        placed.push(chosen);
        result.push((chosen, scores[chosen]));
    }
    result
}

/// Rank `candidates`.
#[must_use]
pub fn rank(candidates: &[Candidate], options: &Options) -> Ranking {
    let tokens: Vec<Vec<Token>> = candidates.par_iter().map(|c| tokens(&c.text)).collect();
    let scores = base_scores(candidates, &tokens, options);
    let corpus = Corpus::new(&tokens);

    let mut order: Vec<usize> = (0..candidates.len()).collect();
    order.sort_by(by_score(candidates, &scores));

    // Collapse near-duplicates into the best-scoring member of each cluster.
    let mut duplicates: HashMap<usize, Vec<usize>> = HashMap::default();
    let pool: Vec<usize> = if options.collapse_duplicates {
        let signatures: Vec<Option<simhash::Signature>> = tokens
            .par_iter()
            .map(|t| {
                let stems: Vec<String> = t.iter().map(|token| token.stem.clone()).collect();
                simhash::signature(&stems, options.duplicate_min_tokens)
            })
            .collect();
        let clusters = simhash::clusters(
            &signatures,
            options.duplicate_distance,
            options.duplicate_containment,
        );
        let mut leader: HashMap<usize, usize> = HashMap::default();
        let mut pool = Vec::with_capacity(order.len());
        for &i in &order {
            if let Some(&best) = leader.get(&clusters[i]) {
                duplicates.entry(best).or_default().push(i);
            } else {
                leader.insert(clusters[i], i);
                pool.push(i);
            }
        }
        pool
    } else {
        order
    };

    let ranked = diversify(pool, candidates, &scores, &corpus, options.diversity);
    let final_order: Vec<usize> = ranked.iter().map(|(i, _)| *i).collect();
    let (topics, assignment) = topics::cluster(
        &corpus,
        &final_order,
        options.topic_threshold,
        options.topic_keywords,
    );

    let items = ranked
        .into_iter()
        .map(|(index, score)| Ranked {
            index,
            score,
            duplicates: duplicates.remove(&index).unwrap_or_default(),
            topic: assignment[index],
        })
        .collect();
    Ranking { items, topics }
}

#[cfg(test)]
#[allow(clippy::cast_possible_wrap)] // small loop counters used as test data
mod tests {
    use super::*;

    const HOUR: i64 = 3600;
    const NOW: i64 = 1_800_000_000;

    fn post(channel: &str, id: u64, hours_ago: i64, views: u64, text: &str) -> Candidate {
        Candidate {
            channel: channel.into(),
            post_id: id,
            published_at: NOW - hours_ago * HOUR,
            views,
            subscribers: 10_000,
            text: text.into(),
            ..Candidate::default()
        }
    }

    fn options() -> Options {
        Options {
            now: NOW,
            ..Options::default()
        }
    }

    fn ids(ranking: &Ranking, candidates: &[Candidate]) -> Vec<(String, u64)> {
        ranking
            .items
            .iter()
            .map(|r| {
                (
                    candidates[r.index].channel.clone(),
                    candidates[r.index].post_id,
                )
            })
            .collect()
    }

    #[test]
    fn empty_input() {
        let ranking = rank(&[], &options());
        assert_eq!(ranking.items, Vec::new());
        assert_eq!(ranking.topics, Vec::new());
    }

    #[test]
    fn every_unique_post_is_returned_once() {
        let candidates: Vec<Candidate> = (0..30)
            .map(|i| {
                post(
                    ["a", "b", "c"][i % 3],
                    i as u64,
                    (i % 7) as i64,
                    100 * (i as u64 + 1),
                    "",
                )
            })
            .collect();
        let ranking = rank(&candidates, &options());
        let mut indices: Vec<usize> = ranking.items.iter().map(|r| r.index).collect();
        indices.sort_unstable();
        assert_eq!(indices, (0..30).collect::<Vec<_>>());
    }

    #[test]
    fn scores_are_bounded() {
        let candidates = vec![
            post("a", 1, 0, u64::MAX / 4, "x"),
            post("a", 2, 10_000, 0, ""),
            Candidate {
                published_at: NOW + 10 * HOUR,
                ..post("b", 3, 0, 5, "")
            },
        ];
        for item in rank(&candidates, &options()).items {
            assert!((0.0..=10.0).contains(&item.score), "{}", item.score);
        }
    }

    #[test]
    fn fresher_posts_win_when_everything_else_is_equal() {
        let candidates = vec![post("a", 1, 30, 1000, ""), post("b", 2, 1, 1000, "")];
        let mut o = options();
        o.diversity = 0.0;
        assert_eq!(ids(&rank(&candidates, &o), &candidates)[0], ("b".into(), 2));
    }

    #[test]
    fn engagement_is_relative_to_the_channel() {
        // Channel "small" usually gets 100 views; post 4 got 1000 (10x).
        // Channel "big" usually gets 100k views; post 9 got 100k (normal).
        let mut candidates = vec![];
        for i in 0..4 {
            candidates.push(post("small", i, 5, 100, ""));
            candidates.push(post("big", 10 + i, 5, 100_000, ""));
        }
        candidates.push(post("small", 4, 5, 1000, ""));
        candidates.push(post("big", 9, 5, 100_000, ""));
        let mut o = options();
        o.diversity = 0.0;
        let order = ids(&rank(&candidates, &o), &candidates);
        assert_eq!(order[0], ("small".into(), 4));
    }

    #[test]
    fn small_channels_are_compared_on_views_per_subscriber() {
        let candidates = vec![
            Candidate {
                subscribers: 1_000,
                ..post("tiny", 1, 3, 900, "")
            },
            Candidate {
                subscribers: 1_000_000,
                ..post("huge", 2, 3, 9_000, "")
            },
        ];
        let ranking = rank(
            &candidates,
            &Options {
                diversity: 0.0,
                ..options()
            },
        );
        assert_eq!(ranking.items[0].index, 0, "90% reach beats 0.9% reach");
    }

    #[test]
    fn robust_center_handles_degenerate_input() {
        assert_eq!(robust_center(&mut []), (0.0, 1.0));
        let (center, spread) = robust_center(&mut [5.0, 5.0, 5.0]);
        assert!((center - 5.0).abs() < 1e-9 && spread > 0.0);
    }

    #[test]
    fn negative_reactions_lower_the_score() {
        let good = Candidate {
            positive_reactions: 50,
            ..post("a", 1, 2, 1000, "")
        };
        let bad = Candidate {
            negative_reactions: 50,
            ..post("b", 2, 2, 1000, "")
        };
        let ranking = rank(
            &[good, bad],
            &Options {
                diversity: 0.0,
                ..options()
            },
        );
        assert_eq!(ranking.items[0].index, 0);
        assert!(ranking.items[0].score > ranking.items[1].score);
    }

    const STORY: &str = "Министерство обороны сообщило о новых поставках техники в регион, \
        поставки начнутся уже на следующей неделе и продлятся до конца месяца по плану";

    #[test]
    fn duplicates_collapse_into_the_best_post() {
        let candidates = vec![
            post("a", 1, 2, 500, STORY),
            post("b", 2, 2, 5000, &format!("Срочно! {STORY}")),
            post(
                "c",
                3,
                2,
                800,
                "Совсем другая новость про погоду и праздники в столице сегодня вечером ожидается салют",
            ),
        ];
        let ranking = rank(
            &candidates,
            &Options {
                diversity: 0.0,
                ..options()
            },
        );
        assert_eq!(ranking.items.len(), 2);
        let leader = ranking
            .items
            .iter()
            .find(|r| !r.duplicates.is_empty())
            .unwrap();
        assert_eq!(leader.duplicates.len(), 1);
        let collapsed = leader.duplicates[0];
        assert_ne!(leader.index, collapsed);
        assert!(ranking.items.iter().all(|r| r.index != collapsed));
    }

    #[test]
    fn duplicates_are_kept_when_collapsing_is_off() {
        let candidates = vec![post("a", 1, 2, 500, STORY), post("b", 2, 2, 500, STORY)];
        let ranking = rank(
            &candidates,
            &Options {
                collapse_duplicates: false,
                ..options()
            },
        );
        assert_eq!(ranking.items.len(), 2);
        assert!(ranking.items.iter().all(|r| r.duplicates.is_empty()));
    }

    #[test]
    fn diversity_breaks_up_runs_of_one_channel() {
        let mut candidates = vec![];
        for i in 0..6 {
            candidates.push(post("loud", i, 1, 10_000, ""));
        }
        for i in 0..3 {
            candidates.push(post("quiet", 100 + i, 1, 9_000, ""));
        }
        let plain = rank(
            &candidates,
            &Options {
                diversity: 0.0,
                ..options()
            },
        );
        let mixed = rank(
            &candidates,
            &Options {
                diversity: 0.8,
                ..options()
            },
        );
        let longest_run = |r: &Ranking| {
            let mut best = 0;
            let mut run = 0;
            let mut last = "";
            for item in &r.items {
                let channel = candidates[item.index].channel.as_str();
                run = if channel == last { run + 1 } else { 1 };
                last = channel;
                best = best.max(run);
            }
            best
        };
        assert!(longest_run(&mixed) < longest_run(&plain));
    }

    #[test]
    fn zero_diversity_is_plain_score_order() {
        let candidates: Vec<Candidate> = (0..10)
            .map(|i| post("a", i, i as i64, 100 * (10 - i), ""))
            .collect();
        let ranking = rank(
            &candidates,
            &Options {
                diversity: 0.0,
                ..options()
            },
        );
        let scores: Vec<f64> = ranking.items.iter().map(|r| r.score).collect();
        assert!(scores.windows(2).all(|w| w[0] >= w[1]));
    }

    #[test]
    fn ranking_is_deterministic() {
        let candidates: Vec<Candidate> = (0..20)
            .map(|i| {
                post(
                    ["a", "b"][i % 2],
                    i as u64,
                    3,
                    1000,
                    "одинаковый текст новости",
                )
            })
            .collect();
        assert_eq!(rank(&candidates, &options()), rank(&candidates, &options()));
    }

    #[test]
    fn topics_group_related_posts() {
        let candidates = vec![
            post(
                "a",
                1,
                1,
                100,
                "Запуск ракеты с космодрома перенесли, старт ракеты состоится завтра утром",
            ),
            post(
                "b",
                2,
                1,
                100,
                "Футбольный матч сборной завершился победой, гол забит на последней минуте",
            ),
            post(
                "c",
                3,
                1,
                100,
                "Старт ракеты с космодрома: запуск ракеты назначен на утро",
            ),
        ];
        let ranking = rank(
            &candidates,
            &Options {
                collapse_duplicates: false,
                ..options()
            },
        );
        let topic_of = |i: usize| ranking.items.iter().find(|r| r.index == i).unwrap().topic;
        assert_eq!(topic_of(0), topic_of(2));
        assert_ne!(topic_of(0), topic_of(1));
        let rocket = &ranking.topics[topic_of(0).unwrap()];
        assert!(rocket.keywords.iter().any(|k| k.starts_with("ракет")));
    }
}
