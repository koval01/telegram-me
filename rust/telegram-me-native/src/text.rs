//! Text normalisation shared by duplicate detection and topic modelling.
//!
//! Posts are mostly Russian, Ukrainian and English. Tokens are runs of
//! alphanumeric characters, lower-cased; URLs, mentions and very short tokens
//! are dropped, stop words removed, and words are "stemmed": a common
//! Cyrillic inflection ending is removed and the result truncated. Crude, but
//! dependency-free and good enough to match inflected forms of the same word
//! ("войны", "война" → "войн"; "ракеты", "ракетой" → "ракет").

use std::sync::LazyLock;

use rustc_hash::FxHashSet;

/// Maximum characters kept from a word when stemming.
const STEM_CHARS: usize = 5;
/// Stems never get shorter than this when an ending is removed.
const MIN_STEM_CHARS: usize = 3;

/// Russian and Ukrainian inflection endings, longest first.
const ENDINGS: &[&str] = &[
    "ями", "ами", "ого", "его", "ому", "ему", "ыми", "ими", "ові", "еві", "ях", "ах", "ых", "их",
    "ая", "яя", "ое", "ее", "ые", "ие", "ій", "ий", "ый", "ой", "ей", "ом", "ем", "ам", "ям", "ов",
    "ев", "ою", "ею", "у", "ю", "ы", "и", "і", "а", "я", "о", "е", "ь", "й",
];

fn stem(word: &str) -> String {
    let mut base = word;
    if word.chars().any(|c| ('\u{0400}'..='\u{04FF}').contains(&c)) {
        for ending in ENDINGS {
            if let Some(stripped) = word.strip_suffix(ending)
                && stripped.chars().count() >= MIN_STEM_CHARS
            {
                base = stripped;
                break;
            }
        }
    }
    base.chars().take(STEM_CHARS).collect()
}
/// Shorter tokens are ignored.
const MIN_TOKEN_CHARS: usize = 3;

const STOP_WORDS: &[&str] = &[
    // English
    "the",
    "and",
    "for",
    "are",
    "but",
    "not",
    "you",
    "all",
    "any",
    "can",
    "had",
    "her",
    "was",
    "one",
    "our",
    "out",
    "has",
    "have",
    "this",
    "that",
    "with",
    "from",
    "they",
    "will",
    "would",
    "there",
    "their",
    "what",
    "about",
    "which",
    "when",
    "your",
    "into",
    "than",
    "then",
    "them",
    "these",
    "those",
    "been",
    "were",
    "also",
    "just",
    "more",
    "some",
    "only",
    "over",
    "such",
    "very",
    "here",
    "http",
    "https",
    "www",
    // Russian
    "что",
    "это",
    "как",
    "так",
    "его",
    "она",
    "они",
    "мне",
    "вас",
    "нас",
    "все",
    "был",
    "была",
    "были",
    "быть",
    "уже",
    "еще",
    "ещё",
    "для",
    "или",
    "при",
    "под",
    "над",
    "без",
    "чем",
    "кто",
    "где",
    "там",
    "тут",
    "вот",
    "если",
    "когда",
    "потом",
    "очень",
    "этот",
    "эта",
    "эти",
    "того",
    "чтобы",
    "только",
    "также",
    "тоже",
    "будет",
    "есть",
    "нет",
    "даже",
    "после",
    "через",
    "между",
    "который",
    "которые",
    "которая",
    "которых",
    "свой",
    "своих",
    "себя",
    // Ukrainian
    "що",
    "це",
    "як",
    "так",
    "його",
    "вона",
    "вони",
    "мені",
    "вас",
    "нас",
    "усі",
    "всі",
    "був",
    "була",
    "були",
    "бути",
    "вже",
    "ще",
    "для",
    "або",
    "при",
    "під",
    "над",
    "без",
    "ніж",
    "хто",
    "де",
    "там",
    "тут",
    "ось",
    "якщо",
    "коли",
    "потім",
    "дуже",
    "цей",
    "ця",
    "ці",
    "щоб",
    "тільки",
    "також",
    "теж",
    "буде",
    "немає",
    "навіть",
    "після",
    "через",
    "між",
    "який",
    "які",
];

static STOP_SET: LazyLock<FxHashSet<&'static str>> =
    LazyLock::new(|| STOP_WORDS.iter().copied().collect());

fn is_stop_word(token: &str) -> bool {
    STOP_SET.contains(token)
}

/// A normalised word: `stem` for matching, `word` (lower-cased) for display.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Token {
    /// Normalised stem used for matching.
    pub stem: String,
    /// The lower-cased word as written.
    pub word: String,
}

/// Lower-case `text` and split it into normalised tokens, in document order.
#[must_use]
pub fn tokens(text: &str) -> Vec<Token> {
    let mut out = Vec::new();
    let mut word = String::with_capacity(32);
    for raw in text.split_whitespace() {
        if raw.starts_with("http://") || raw.starts_with("https://") || raw.starts_with('@') {
            continue;
        }
        word.clear();
        for ch in raw.chars() {
            if ch.is_alphanumeric() {
                word.extend(ch.to_lowercase());
            } else {
                push_token(&mut out, &word);
                word.clear();
            }
        }
        push_token(&mut out, &word);
    }
    out
}

/// Only the stems of [`tokens`].
#[must_use]
pub fn stems(text: &str) -> Vec<String> {
    tokens(text).into_iter().map(|token| token.stem).collect()
}

fn push_token(out: &mut Vec<Token>, word: &str) {
    let len = word.chars().count();
    if len < MIN_TOKEN_CHARS || is_stop_word(word) || word.chars().all(char::is_numeric) {
        return;
    }
    out.push(Token {
        stem: stem(word),
        word: word.to_owned(),
    });
}

/// 64-bit FNV-1a: tiny, fast and stable across runs and platforms.
#[must_use]
pub fn fnv1a(bytes: &[u8]) -> u64 {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in bytes {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x0100_0000_01b3);
    }
    hash
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stems_are_lowercase_and_filtered() {
        let text = "The quick brown foxes https://example.com @someone 2026";
        assert_eq!(stems(text), vec!["quick", "brown", "foxes"]);
    }

    #[test]
    fn inflected_forms_share_a_stem() {
        let pairs = [
            ("войны", "война"),
            ("ракеты", "ракетой"),
            ("старт", "стартовала"),
            ("заявление", "заявления"),
            ("космодром", "космодрома"),
            ("україни", "україна"),
            ("rocket", "rockets"),
        ];
        for (a, b) in pairs {
            assert_eq!(stems(a), stems(b), "{a} / {b}");
        }
    }

    #[test]
    fn different_words_keep_different_stems() {
        assert_ne!(stems("ракета"), stems("радио"));
        assert_ne!(stems("война"), stems("вода"));
    }

    #[test]
    fn punctuation_splits_words() {
        assert_eq!(stems("hello,world-wide"), vec!["hello", "world", "wide"]);
    }

    #[test]
    fn stop_words_and_short_tokens_are_dropped() {
        assert_eq!(stems("и в на the and что это"), Vec::<String>::new());
    }

    #[test]
    fn fnv_is_stable() {
        assert_eq!(fnv1a(b""), 0xcbf2_9ce4_8422_2325);
        assert_eq!(fnv1a(b"a"), 0xaf63_dc4c_8601_ec8c);
    }
}
