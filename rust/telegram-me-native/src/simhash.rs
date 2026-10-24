//! Near-duplicate detection over word shingles.
//!
//! Each post gets a 64-bit `SimHash` and its set of hashed three-word shingles.
//! The `SimHash` distance is a cheap pre-filter; a pair is a duplicate when the
//! shingles shared by both cover most of the shorter text (containment), which
//! also catches a story re-posted with an added headline or signature. Posts
//! are grouped with union-find so that A~B and B~C put all three together.

use rayon::prelude::*;

use crate::text::fnv1a;

/// Words per shingle.
const SHINGLE: usize = 3;

/// What duplicate detection knows about one text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Signature {
    /// 64-bit `SimHash` of the shingles.
    pub simhash: u64,
    /// Sorted, de-duplicated shingle hashes.
    pub shingles: Vec<u64>,
}

/// Signature of a token sequence, or `None` when it is too short to compare.
#[must_use]
pub fn signature(tokens: &[String], min_tokens: usize) -> Option<Signature> {
    if tokens.len() < min_tokens.max(SHINGLE) {
        return None;
    }
    let mut shingles: Vec<u64> = tokens
        .windows(SHINGLE)
        .map(|window| fnv1a(window.join(" ").as_bytes()))
        .collect();
    let mut weights = [0_i32; 64];
    for &hash in &shingles {
        for (bit, weight) in weights.iter_mut().enumerate() {
            if hash >> bit & 1 == 1 {
                *weight += 1;
            } else {
                *weight -= 1;
            }
        }
    }
    let mut value = 0_u64;
    for (bit, weight) in weights.iter().enumerate() {
        if *weight > 0 {
            value |= 1 << bit;
        }
    }
    shingles.sort_unstable();
    shingles.dedup();
    Some(Signature {
        simhash: value,
        shingles,
    })
}

/// Fingerprint only (see [`signature`]).
#[must_use]
pub fn fingerprint(tokens: &[String], min_tokens: usize) -> Option<u64> {
    signature(tokens, min_tokens).map(|s| s.simhash)
}

/// Share of the smaller shingle set that also occurs in the other one.
#[must_use]
pub fn containment(a: &[u64], b: &[u64]) -> f64 {
    let (mut i, mut j, mut common) = (0, 0, 0_usize);
    while i < a.len() && j < b.len() {
        match a[i].cmp(&b[j]) {
            std::cmp::Ordering::Less => i += 1,
            std::cmp::Ordering::Greater => j += 1,
            std::cmp::Ordering::Equal => {
                common += 1;
                i += 1;
                j += 1;
            }
        }
    }
    let smaller = a.len().min(b.len());
    #[expect(
        clippy::cast_precision_loss,
        reason = "token counts are far below 2^52"
    )]
    if smaller == 0 {
        0.0
    } else {
        common as f64 / smaller as f64
    }
}

/// Number of differing bits.
#[must_use]
pub const fn distance(a: u64, b: u64) -> u32 {
    (a ^ b).count_ones()
}

/// Disjoint-set forest with path halving and union by size.
pub struct UnionFind {
    parent: Vec<usize>,
    size: Vec<usize>,
}

impl UnionFind {
    /// `n` singleton sets.
    #[must_use]
    pub fn new(n: usize) -> Self {
        Self {
            parent: (0..n).collect(),
            size: vec![1; n],
        }
    }

    /// Representative of `x`'s set.
    pub fn find(&mut self, mut x: usize) -> usize {
        while self.parent[x] != x {
            self.parent[x] = self.parent[self.parent[x]];
            x = self.parent[x];
        }
        x
    }

    /// Merge the sets of `a` and `b`.
    pub fn union(&mut self, a: usize, b: usize) {
        let (mut a, mut b) = (self.find(a), self.find(b));
        if a == b {
            return;
        }
        if self.size[a] < self.size[b] {
            std::mem::swap(&mut a, &mut b);
        }
        self.parent[b] = a;
        self.size[a] += self.size[b];
    }
}

/// Cluster id per item: two items are duplicates when their `SimHash` values
/// are within `max_distance` bits and their shingle containment is at least
/// `min_containment`. Items without a signature stay alone.
#[must_use]
pub fn clusters(
    signatures: &[Option<Signature>],
    max_distance: u32,
    min_containment: f64,
) -> Vec<usize> {
    // Comparing pairs is the expensive part and is spread over all cores;
    // merging the (few) matching pairs is sequential.
    let pairs: Vec<(usize, usize)> = (0..signatures.len())
        .into_par_iter()
        .flat_map_iter(|i| {
            let a = signatures[i].as_ref();
            signatures
                .iter()
                .enumerate()
                .skip(i + 1)
                .filter_map(move |(j, other)| {
                    let (a, b) = (a?, other.as_ref()?);
                    (distance(a.simhash, b.simhash) <= max_distance
                        && containment(&a.shingles, &b.shingles) >= min_containment)
                        .then_some((i, j))
                })
        })
        .collect();
    let mut sets = UnionFind::new(signatures.len());
    for (i, j) in pairs {
        sets.union(i, j);
    }
    (0..signatures.len()).map(|i| sets.find(i)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::text::stems;

    fn fp(text: &str) -> Option<u64> {
        fingerprint(&stems(text), 6)
    }

    const NEWS: &str = "Министерство обороны сообщило о новых поставках техники в регион, \
        поставки начнутся уже на следующей неделе и продлятся до конца месяца";

    #[test]
    fn identical_texts_have_identical_fingerprints() {
        assert_eq!(fp(NEWS), fp(NEWS));
    }

    #[test]
    fn small_edits_stay_close() {
        let edited = format!("⚡️ {NEWS} Подписывайтесь!");
        let (a, b) = (fp(NEWS).unwrap(), fp(&edited).unwrap());
        assert!(distance(a, b) <= 12, "distance {}", distance(a, b));
    }

    #[test]
    fn unrelated_texts_are_far_apart() {
        let other = "Сегодня в городе прошёл фестиваль уличной еды, гости попробовали \
            блюда из двадцати стран и послушали живую музыку до поздней ночи";
        let (a, b) = (fp(NEWS).unwrap(), fp(other).unwrap());
        assert!(distance(a, b) > 12, "distance {}", distance(a, b));
    }

    #[test]
    fn short_texts_are_not_fingerprinted() {
        assert_eq!(fp("Срочная новость дня"), None);
    }

    #[expect(
        clippy::unnecessary_wraps,
        reason = "matches the `clusters` input type"
    )]
    fn sig(simhash: u64, shingles: &[u64]) -> Option<Signature> {
        Some(Signature {
            simhash,
            shingles: shingles.to_vec(),
        })
    }

    #[test]
    fn prefixed_repost_is_contained() {
        let a = signature(&stems(NEWS), 6).unwrap();
        let b = signature(
            &stems(&format!("Срочно! {NEWS} Подписывайтесь на канал")),
            6,
        )
        .unwrap();
        assert!(containment(&a.shingles, &b.shingles) >= 0.7);
        assert!(distance(a.simhash, b.simhash) <= 20);
    }

    #[test]
    fn containment_bounds() {
        assert!((containment(&[1, 2, 3], &[1, 2, 3, 4, 5]) - 1.0).abs() < 1e-9);
        assert!(containment(&[1, 2], &[3, 4]).abs() < 1e-9);
        assert!(containment(&[], &[1]).abs() < 1e-9);
    }

    #[test]
    fn clusters_are_transitive() {
        let groups = clusters(
            &[
                sig(0b0000, &[1, 2, 3]),
                sig(0b0001, &[1, 2, 3]),
                sig(0b0011, &[1, 2, 3]),
                sig(u64::MAX, &[1, 2, 3]),
                None,
            ],
            1,
            0.5,
        );
        assert_eq!(groups[0], groups[1]);
        assert_eq!(groups[1], groups[2]);
        assert_ne!(groups[0], groups[3]);
        assert_ne!(groups[3], groups[4]);
    }

    #[test]
    fn close_fingerprints_with_different_text_are_not_duplicates() {
        let groups = clusters(&[sig(0, &[1, 2, 3, 4]), sig(0, &[5, 6, 7, 8])], 0, 0.7);
        assert_ne!(groups[0], groups[1]);
    }

    #[test]
    fn union_find_merges_by_size() {
        let mut sets = UnionFind::new(4);
        sets.union(0, 1);
        sets.union(2, 3);
        sets.union(1, 3);
        let root = sets.find(0);
        assert!((0..4).all(|i| sets.find(i) == root));
    }
}
