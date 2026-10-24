//! Text entities (bold, links, custom emoji, ...) of a message.
//!
//! Offsets and lengths are in Unicode code points of the tag-stripped text, as
//! Python counted them.
//!
//! The original parser ran one combined regular expression whose alternatives
//! use look-ahead, which forces a slow backtracking engine over the whole
//! text. Here every alternative is compiled on its own — eight of them for the
//! fast `regex` engine, the three with look-ahead for `fancy-regex` — and their
//! matches are merged with the combined pattern's semantics: the leftmost
//! match wins, ties go to the earlier alternative, scanning resumes after it.
//! The tests compare this with the combined expression.

use std::sync::LazyLock;

use serde_json::{Map, Value};

/// One alternative of the original combined pattern.
struct Alternative {
    pattern: &'static str,
    entity_type: &'static str,
    /// Capture group (within the alternative) holding the entity URL.
    url_group: Option<usize>,
    /// Tag depth of the visible text in the matched markup.
    depth: usize,
}

const ALTERNATIVES: &[Alternative] = &[
    Alternative {
        pattern: r"#\w+",
        entity_type: "hashtag",
        url_group: None,
        depth: 1,
    },
    Alternative {
        pattern: r"<b>(.+?)<\/b>(?![^<]*<\/i>)",
        entity_type: "bold",
        url_group: None,
        depth: 1,
    },
    Alternative {
        pattern: r"<i>(.+?)<\/i>",
        entity_type: "italic",
        url_group: None,
        depth: 1,
    },
    Alternative {
        pattern: r"<u>(.+?)<\/u>",
        entity_type: "underline",
        url_group: None,
        depth: 1,
    },
    Alternative {
        pattern: r"<code>(.+?)<\/code>",
        entity_type: "code",
        url_group: None,
        depth: 1,
    },
    Alternative {
        pattern: r"<s>(.+?)<\/s>",
        entity_type: "strikethrough",
        url_group: None,
        depth: 1,
    },
    Alternative {
        pattern: r"<tg-spoiler>(.+?)<\/tg-spoiler>",
        entity_type: "spoiler",
        url_group: None,
        depth: 1,
    },
    Alternative {
        pattern: r#"<i\s+class="emoji"\s+style=".*?:url\('(.*?)'\)"><b>(.*?)<\/b><\/i>(?![^<]*<\/tg-emoji>)"#,
        entity_type: "emoji",
        url_group: Some(1),
        depth: 2,
    },
    Alternative {
        pattern: r#"<a\s+(?:[^>]*?\s+)?href="([^"]*)"[^>]*\s+onclick="[^"]*"[^>]*>(.*?)<\/a>"#,
        entity_type: "text_link",
        url_group: Some(1),
        depth: 1,
    },
    // `url` entities never carried their URL in the original parser.
    Alternative {
        pattern: r#"<a\s+(?:[^>]*?\s+)?href="(?!(?:.*#))(.*?)"[^>]*>(.*?)<\/a>"#,
        entity_type: "url",
        url_group: None,
        depth: 1,
    },
    Alternative {
        pattern: r#"<tg-emoji.*?><i\s+class="emoji"\s+style="background-image:url\('(.*?)'\)"><b>(.*?)</b></i></tg-emoji>"#,
        entity_type: "animoji",
        url_group: Some(1),
        depth: 3,
    },
];

/// A match: span of the whole match and of the URL group, if any.
type Found = (usize, usize, Option<(usize, usize)>);

/// How an alternative's look-ahead is checked cheaply on a candidate.
#[derive(Clone, Copy)]
enum Lookahead {
    None,
    /// `(?![^<]*TAG)` right after the match.
    NotFollowedBy(&'static str),
    /// `(?!(?:.*#))` (dot-all) at the start of the URL group: no `#` after it.
    NoHashAfterUrl,
}

struct Compiled {
    /// The pattern without its look-ahead (fast engine).
    relaxed: regex::Regex,
    /// The exact pattern, used only when a candidate fails the look-ahead.
    exact: Option<fancy_regex::Regex>,
    lookahead: Lookahead,
}

fn compile(alternative: &Alternative) -> Compiled {
    let (relaxed, lookahead) = match alternative.entity_type {
        "bold" => (
            alternative.pattern.replace(r"(?![^<]*<\/i>)", ""),
            Lookahead::NotFollowedBy("</i>"),
        ),
        "emoji" => (
            alternative.pattern.replace(r"(?![^<]*<\/tg-emoji>)", ""),
            Lookahead::NotFollowedBy("</tg-emoji>"),
        ),
        "url" => (
            alternative.pattern.replace("(?!(?:.*#))", ""),
            Lookahead::NoHashAfterUrl,
        ),
        _ => (alternative.pattern.to_owned(), Lookahead::None),
    };
    let flags = |p: &str| format!("(?sm){p}");
    Compiled {
        relaxed: regex::Regex::new(&flags(&relaxed)).expect("relaxed pattern has no look-around"),
        exact: (!matches!(lookahead, Lookahead::None)).then(|| {
            fancy_regex::Regex::new(&flags(alternative.pattern)).expect("valid entity pattern")
        }),
        lookahead,
    }
}

impl Compiled {
    fn relaxed_at(&self, text: &str, start: usize, url_group: Option<usize>) -> Option<Found> {
        // Finding is a fast DFA search; capture groups (slower) are only
        // extracted, within the match, by the alternatives that need them.
        let whole = self.relaxed.find_at(text, start)?;
        // The URL span is resolved later, for reported matches only; the
        // `url` look-ahead needs its position right away.
        let _ = url_group;
        let url = if matches!(self.lookahead, Lookahead::NoHashAfterUrl) {
            self.group_span(text, (whole.start(), whole.end()), 1)
        } else {
            None
        };
        Some((whole.start(), whole.end(), url))
    }

    /// Span of capture `group` inside an already found match.
    fn group_span(
        &self,
        text: &str,
        (start, end): (usize, usize),
        group: usize,
    ) -> Option<(usize, usize)> {
        let caps = self.relaxed.captures(text.get(start..end)?)?;
        caps.get(group)
            .map(|m| (start + m.start(), start + m.end()))
    }

    /// Whether the exact pattern accepts the relaxed candidate as is.
    fn accepts(&self, text: &str, candidate: &Found, last_hash: Option<usize>) -> bool {
        match self.lookahead {
            Lookahead::None => true,
            Lookahead::NotFollowedBy(tag) => {
                let rest = text.get(candidate.1..).unwrap_or_default();
                !rest
                    .find('<')
                    .is_some_and(|i| rest.get(i..).is_some_and(|r| r.starts_with(tag)))
            }
            Lookahead::NoHashAfterUrl => {
                let position = candidate.2.map_or(candidate.0, |(s, _)| s);
                last_hash.is_none_or(|h| h < position)
            }
        }
    }

    fn exact_at(&self, text: &str, start: usize, url_group: Option<usize>) -> Option<Found> {
        let exact = self.exact.as_ref()?;
        let caps = exact.captures_from_pos(text, start).ok()??;
        let whole = caps.get(0)?;
        let url = url_group
            .and_then(|g| caps.get(g))
            .map(|m| (m.start(), m.end()));
        Some((whole.start(), whole.end(), url))
    }
}

static COMPILED: LazyLock<Vec<Compiled>> =
    LazyLock::new(|| ALTERNATIVES.iter().map(compile).collect());
static LINE_BREAK: LazyLock<regex::Regex> =
    LazyLock::new(|| regex::Regex::new(r"<br\s?/?>").expect("valid regex"));
static TAG: LazyLock<regex::Regex> =
    LazyLock::new(|| regex::Regex::new(r"<[^>]+>").expect("valid regex"));

/// `match.group().strip("<>").split(">")[-depth].split("<")[0]`.
fn visible_text(whole: &str, depth: usize) -> Option<&str> {
    let parts: Vec<&str> = whole.trim_matches(['<', '>']).split('>').collect();
    let part = parts.get(parts.len().checked_sub(depth)?)?;
    part.split('<').next()
}

/// Python's `str.find(sub, start)` over code points.
fn py_find(haystack: &[char], needle: &[char], start: i64) -> i64 {
    let len = i64::try_from(haystack.len()).unwrap_or(i64::MAX);
    let start = if start < 0 {
        (start + len).max(0)
    } else {
        start
    };
    if start > len {
        return -1;
    }
    let start = usize::try_from(start).unwrap_or(usize::MAX);
    if needle.is_empty() {
        return i64::try_from(start).unwrap_or(-1);
    }
    haystack
        .get(start..)
        .and_then(|rest| {
            rest.windows(needle.len())
                .position(|window| window == needle)
        })
        .map_or(-1, |position| i64::try_from(start + position).unwrap_or(-1))
}

/// Matches of the combined pattern as `(alternative, match)`.
///
/// Every alternative keeps its next candidate. Candidates come from the fast
/// relaxed pattern; one that fails its look-ahead is re-resolved with the
/// exact pattern only when it is the leftmost contender, which keeps the slow
/// engine off the hot path while giving exactly the combined pattern's result.
fn matches(text: &str) -> Vec<(usize, Found)> {
    struct Candidate {
        found: Found,
        exact: bool,
    }
    let last_hash = text.rfind('#');
    let search = |index: usize, from: usize| -> Option<Candidate> {
        let (compiled, alternative) = (COMPILED.get(index)?, ALTERNATIVES.get(index)?);
        let found = compiled.relaxed_at(text, from, alternative.url_group)?;
        let exact = compiled.accepts(text, &found, last_hash);
        Some(Candidate { found, exact })
    };
    let mut next: Vec<Option<Candidate>> = (0..ALTERNATIVES.len()).map(|i| search(i, 0)).collect();
    let mut cursor = 0;
    let mut out = Vec::new();
    loop {
        for (index, slot) in next.iter_mut().enumerate() {
            if slot.as_ref().is_some_and(|c| c.found.0 < cursor) {
                *slot = search(index, cursor);
            }
        }
        let Some((_, index, found, exact)) = next
            .iter()
            .enumerate()
            .filter_map(|(i, c)| c.as_ref().map(|c| (c.found.0, i, c.found, c.exact)))
            .min_by_key(|&(start, i, ..)| (start, i))
        else {
            break;
        };
        let (Some(compiled), Some(alternative)) = (COMPILED.get(index), ALTERNATIVES.get(index))
        else {
            break;
        };
        if !exact {
            // The exact match can only start at the candidate or later.
            let mut start = found.0;
            if let (Lookahead::NoHashAfterUrl, Some(hash)) = (compiled.lookahead, last_hash) {
                // Its URL must begin after the last `#`, so the match starts at
                // the earliest `<a` of the tag around that `#`, or after it.
                let before = text.get(..hash).unwrap_or_default();
                let tag_start = before.rfind('>').map_or(0, |i| i + 1);
                let earliest = before
                    .get(tag_start..)
                    .and_then(|tag| tag.find("<a"))
                    .map_or(hash, |i| tag_start + i);
                start = start.max(earliest);
            }
            if let Some(slot) = next.get_mut(index) {
                *slot = compiled
                    .exact_at(text, start, alternative.url_group)
                    .map(|found| Candidate { found, exact: true });
            }
            continue;
        }
        let url = alternative
            .url_group
            .and_then(|group| compiled.group_span(text, (found.0, found.1), group));
        let found = (found.0, found.1, url);
        out.push((index, found));
        // No alternative can match the empty string, so `end > start`.
        cursor = found.1.max(found.0 + 1);
    }
    out
}

/// Entities of a message's HTML, or an error where Python raised.
///
/// # Errors
/// When the matched markup has fewer `>`-separated parts than expected.
pub fn parse_entities(html: &str) -> Result<Vec<Value>, String> {
    let html_text = LINE_BREAK.replace_all(html, "\n");
    let html_text: &str = &html_text;
    let text_only: Vec<char> = TAG.replace_all(html_text, "").chars().collect();

    let mut entities = Vec::new();
    let mut offset: i64 = 0;
    for (index, (start, end, url)) in matches(html_text) {
        let Some(alternative) = ALTERNATIVES.get(index) else {
            continue;
        };
        let visible: Vec<char> = html_text
            .get(start..end)
            .and_then(|whole| visible_text(whole, alternative.depth))
            .ok_or_else(|| "IndexError: list index out of range".to_owned())?
            .chars()
            .collect();
        let position = py_find(&text_only, &visible, offset);
        let mut entity = Map::new();
        entity.insert("offset".into(), Value::from(position));
        entity.insert("length".into(), Value::from(visible.len()));
        entity.insert("type".into(), Value::from(alternative.entity_type));
        if let Some(url) = url
            .filter(|(s, e)| e > s)
            .and_then(|(s, e)| html_text.get(s..e))
        {
            let url = if matches!(alternative.entity_type, "emoji" | "animoji") {
                format!("https:{url}")
            } else {
                url.to_owned()
            };
            entity.insert("url".into(), Value::from(url));
        }
        entities.push(Value::Object(entity));
        offset = position + i64::try_from(visible.len()).unwrap_or(0);
    }
    Ok(entities)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    /// The original single combined expression, kept as an oracle:
    /// `(alternative, start, end)` of every match.
    fn reference_matches(text: &str) -> Vec<(usize, usize, usize)> {
        let joined: Vec<String> = ALTERNATIVES
            .iter()
            .map(|a| format!("({})", a.pattern))
            .collect();
        let combined = fancy_regex::Regex::new(&format!("(?sm){}", joined.join("|"))).unwrap();
        // First capture group of each alternative's outer parenthesis.
        let mut outer_groups = Vec::new();
        let mut group = 1;
        for alternative in ALTERNATIVES {
            outer_groups.push(group);
            group += fancy_regex::Regex::new(alternative.pattern)
                .unwrap()
                .captures_len();
        }
        combined
            .captures_iter(text)
            .map(|caps| {
                let caps = caps.unwrap();
                let whole = caps.get(0).unwrap();
                let index = outer_groups
                    .iter()
                    .position(|g| caps.get(*g).is_some())
                    .unwrap();
                (index, whole.start(), whole.end())
            })
            .collect()
    }

    const CASES: &[&str] = &[
        "#tag text #другой_тег",
        "a <i>b</i> <b>c</b> <u>d</u> <s>e</s> <code>f()</code> <tg-spoiler>g</tg-spoiler>",
        r#"<i class="emoji" style="background-image:url('//t.org/a.png')"><b>✨</b></i> <b>Title</b>"#,
        r#"<tg-emoji emoji-id="1"><i class="emoji" style="background-image:url('//t.org/b.png')"><b>🆕</b></i></tg-emoji> text"#,
        r#"<a href="https://x.y" onclick="return confirm('x')" target="_blank">site</a>"#,
        r#"<a href="https://x.y/#frag">frag</a> <a href="https://x.y/z">plain</a>"#,
        "<i><b>nested</b></i> <b>bold</b> <i>it</i>",
        "<b>multi\nline</b> #a#b <b>x</b></i>",
        "<b></b><i></i> #",
        "nothing here",
        r#"<a href="https://a.b/1">one</a> <a href="https://a.b/2">two</a> #late"#,
        r#"<a href="https://a.b/1">one</a> #mid <a href="https://a.b/2">two</a>"#,
        r#"<i class="emoji" style="background-image:url('//a/1.png')"><b>1</b></i></tg-emoji> <i class="emoji" style="x:url('//a/2.png')"><b>2</b></i>"#,
        "<b>a</b></i> <b>b</b> <b>c</b></i> tail",
        "<b>a</b>x</i> <b>b</b>",
    ];

    #[test]
    fn split_alternatives_match_the_combined_expression() {
        for case in CASES {
            let fast: Vec<(usize, usize, usize)> = matches(case)
                .into_iter()
                .map(|(i, (s, e, _))| (i, s, e))
                .collect();
            assert_eq!(fast, reference_matches(case), "{case}");
        }
    }

    #[test]
    fn only_lookahead_alternatives_have_an_exact_fallback() {
        let fallback: Vec<&str> = COMPILED
            .iter()
            .zip(ALTERNATIVES)
            .filter(|(compiled, _)| compiled.exact.is_some())
            .map(|(_, alternative)| alternative.entity_type)
            .collect();
        assert_eq!(fallback, ["bold", "emoji", "url"]);
        assert!(COMPILED.iter().all(|c| !c.relaxed.as_str().contains("(?!")));
    }

    #[test]
    fn simple_entities() {
        assert_eq!(
            parse_entities("#tag text").unwrap(),
            vec![json!({"offset": 0, "length": 4, "type": "hashtag"})]
        );
        assert_eq!(
            parse_entities("a <i>b</i>").unwrap(),
            vec![json!({"offset": 2, "length": 1, "type": "italic"})]
        );
        assert_eq!(
            parse_entities("<code>x()</code>").unwrap(),
            vec![json!({"offset": 0, "length": 3, "type": "code"})]
        );
        assert_eq!(parse_entities("plain").unwrap(), Vec::<Value>::new());
    }

    #[test]
    fn links_keep_their_url() {
        let html = r#"<a href="https://example.com" onclick="return false;">site</a>"#;
        assert_eq!(
            parse_entities(html).unwrap(),
            vec![
                json!({"offset": 0, "length": 4, "type": "text_link", "url": "https://example.com"})
            ]
        );
    }

    #[test]
    fn offsets_count_code_points() {
        let html = "привет <b>мир</b> 🙂 <i>ok</i>";
        assert_eq!(
            parse_entities(html).unwrap(),
            vec![
                json!({"offset": 7, "length": 3, "type": "bold"}),
                json!({"offset": 13, "length": 2, "type": "italic"}),
            ]
        );
    }

    #[test]
    fn py_find_matches_python() {
        let hay: Vec<char> = "abcabc".chars().collect();
        let needle: Vec<char> = "bc".chars().collect();
        assert_eq!(py_find(&hay, &needle, 0), 1);
        assert_eq!(py_find(&hay, &needle, 2), 4);
        assert_eq!(py_find(&hay, &needle, -2), 4);
        assert_eq!(py_find(&hay, &needle, 9), -1);
        assert_eq!(py_find(&hay, &[], 3), 3);
    }
}
