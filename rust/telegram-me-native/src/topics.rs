//! TF-IDF vectors, cosine similarity and topic clustering.
//!
//! Every post becomes a sparse, L2-normalised TF-IDF vector over word stems
//! (IDF computed over the posts being ranked). Topics are formed greedily in
//! score order: a post joins the most similar existing topic when the cosine
//! similarity to its centroid passes a threshold, otherwise it starts a new
//! one. The same vectors drive the diversity penalty of the re-ranker.

use rustc_hash::FxHashMap as HashMap;

use crate::text::Token;

/// Sparse vector: (term id, weight), sorted by term id.
pub type SparseVector = Vec<(u32, f32)>;

/// Vocabulary and IDF weights for one batch of documents.
pub struct Corpus {
    /// Stem → term id.
    pub vocabulary: HashMap<String, u32>,
    /// Most frequent surface form for each term id.
    pub display: Vec<String>,
    /// Normalised TF-IDF vector per document.
    pub vectors: Vec<SparseVector>,
}

impl Corpus {
    /// Build vectors for `documents` (each a list of tokens).
    #[must_use]
    pub fn new(documents: &[Vec<Token>]) -> Self {
        let mut vocabulary: HashMap<String, u32> = HashMap::default();
        let mut forms: Vec<HashMap<String, u32>> = Vec::new();
        let mut doc_freq: Vec<u32> = Vec::new();
        let mut counts: Vec<HashMap<u32, u32>> = Vec::with_capacity(documents.len());

        for doc in documents {
            let mut tf: HashMap<u32, u32> = HashMap::default();
            for token in doc {
                let next = u32::try_from(vocabulary.len()).unwrap_or(u32::MAX);
                let id = *vocabulary.entry(token.stem.clone()).or_insert_with(|| {
                    forms.push(HashMap::default());
                    doc_freq.push(0);
                    next
                });
                *forms[id as usize].entry(token.word.clone()).or_insert(0) += 1;
                *tf.entry(id).or_insert(0) += 1;
            }
            for id in tf.keys() {
                doc_freq[*id as usize] += 1;
            }
            counts.push(tf);
        }

        #[expect(
            clippy::cast_precision_loss,
            reason = "document counts are far below 2^24"
        )]
        let n = documents.len().max(1) as f32;
        let vectors = counts
            .into_iter()
            .map(|tf| {
                let mut vector: SparseVector = tf
                    .into_iter()
                    .map(|(id, count)| {
                        #[expect(
                            clippy::cast_precision_loss,
                            reason = "document counts are far below 2^24"
                        )]
                        let df = doc_freq[id as usize] as f32;
                        #[expect(
                            clippy::cast_precision_loss,
                            reason = "term counts are far below 2^24"
                        )]
                        let weight = (1.0 + (count as f32).ln()) * ((1.0 + n) / (1.0 + df)).ln();
                        (id, weight + 1e-6)
                    })
                    .collect();
                vector.sort_unstable_by_key(|(id, _)| *id);
                normalise(&mut vector);
                vector
            })
            .collect();

        let display = forms
            .into_iter()
            .map(|forms| {
                forms
                    .into_iter()
                    .max_by(|(wa, ca), (wb, cb)| ca.cmp(cb).then_with(|| wb.cmp(wa)))
                    .map(|(word, _)| word)
                    .unwrap_or_default()
            })
            .collect();

        Self {
            vocabulary,
            display,
            vectors,
        }
    }
}

fn normalise(vector: &mut SparseVector) {
    let norm = vector.iter().map(|(_, w)| w * w).sum::<f32>().sqrt();
    if norm > 0.0 {
        for (_, w) in vector.iter_mut() {
            *w /= norm;
        }
    }
}

/// Cosine similarity of two normalised sparse vectors.
#[must_use]
pub fn cosine(a: &SparseVector, b: &SparseVector) -> f32 {
    let (mut i, mut j, mut dot) = (0, 0, 0.0);
    while i < a.len() && j < b.len() {
        match a[i].0.cmp(&b[j].0) {
            std::cmp::Ordering::Less => i += 1,
            std::cmp::Ordering::Greater => j += 1,
            std::cmp::Ordering::Equal => {
                dot += a[i].1 * b[j].1;
                i += 1;
                j += 1;
            }
        }
    }
    dot
}

/// A group of posts about the same subject.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Topic {
    /// Indices of member documents, in the order they were added.
    pub members: Vec<usize>,
    /// Most characteristic words, best first.
    pub keywords: Vec<String>,
}

/// Greedy leader clustering of `order` (document indices, best first).
///
/// Returns the topics and, per document, the index of its topic.
#[must_use]
pub fn cluster(
    corpus: &Corpus,
    order: &[usize],
    threshold: f32,
    keywords: usize,
) -> (Vec<Topic>, Vec<Option<usize>>) {
    let mut centroids: Vec<HashMap<u32, f32>> = Vec::new();
    let mut members: Vec<Vec<usize>> = Vec::new();
    let mut assignment = vec![None; corpus.vectors.len()];

    for &doc in order {
        let vector = &corpus.vectors[doc];
        if vector.is_empty() {
            continue;
        }
        let best = centroids
            .iter()
            .enumerate()
            .map(|(topic, centroid)| (topic, centroid_cosine(centroid, vector)))
            .filter(|(_, similarity)| *similarity >= threshold)
            .max_by(|a, b| a.1.total_cmp(&b.1));
        let topic = if let Some((topic, _)) = best {
            topic
        } else {
            centroids.push(HashMap::default());
            members.push(Vec::new());
            centroids.len() - 1
        };
        for (id, weight) in vector {
            *centroids[topic].entry(*id).or_insert(0.0) += weight;
        }
        members[topic].push(doc);
        assignment[doc] = Some(topic);
    }

    let topics = centroids
        .iter()
        .zip(members)
        .map(|(centroid, members)| {
            let mut terms: Vec<(&u32, &f32)> = centroid.iter().collect();
            terms.sort_by(|a, b| b.1.total_cmp(a.1).then_with(|| a.0.cmp(b.0)));
            let keywords = terms
                .into_iter()
                .take(keywords)
                .map(|(id, _)| corpus.display[*id as usize].clone())
                .collect();
            Topic { members, keywords }
        })
        .collect();
    (topics, assignment)
}

fn centroid_cosine(centroid: &HashMap<u32, f32>, vector: &SparseVector) -> f32 {
    let norm = centroid.values().map(|w| w * w).sum::<f32>().sqrt();
    if norm == 0.0 {
        return 0.0;
    }
    vector
        .iter()
        .map(|(id, w)| centroid.get(id).copied().unwrap_or(0.0) * w)
        .sum::<f32>()
        / norm
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::text::tokens;

    fn corpus(texts: &[&str]) -> Corpus {
        Corpus::new(&texts.iter().map(|t| tokens(t)).collect::<Vec<_>>())
    }

    #[test]
    fn vectors_are_normalised() {
        let c = corpus(&["ракета запуск космос", "футбол матч гол"]);
        for v in &c.vectors {
            let norm: f32 = v.iter().map(|(_, w)| w * w).sum();
            assert!((norm - 1.0).abs() < 1e-4);
        }
    }

    #[test]
    fn cosine_reflects_overlap() {
        let c = corpus(&[
            "запуск ракеты космодром старт",
            "ракета стартовала космодром запуск",
            "футбольный матч закончился вничью",
        ]);
        assert!(cosine(&c.vectors[0], &c.vectors[1]) > 0.5);
        assert!(cosine(&c.vectors[0], &c.vectors[2]) < 0.05);
        assert!((cosine(&c.vectors[0], &c.vectors[0]) - 1.0).abs() < 1e-4);
    }

    #[test]
    fn clustering_groups_similar_documents() {
        let c = corpus(&[
            "запуск ракеты космодром старт орбита",
            "футбольный матч сборная гол победа",
            "ракета космодром орбита спутник запуск",
            "сборная победа матч чемпионат гол",
        ]);
        let (topics, assignment) = cluster(&c, &[0, 1, 2, 3], 0.2, 3);
        assert_eq!(assignment[0], assignment[2]);
        assert_eq!(assignment[1], assignment[3]);
        assert_ne!(assignment[0], assignment[1]);
        assert_eq!(topics.len(), 2);
        assert_eq!(topics[0].keywords.len(), 3);
    }

    #[test]
    fn keywords_use_the_most_frequent_surface_form() {
        let c = corpus(&["заявление заявление заявления", "другой текст совсем"]);
        let (topics, _) = cluster(&c, &[0, 1], 0.9, 1);
        assert_eq!(topics[0].keywords, vec!["заявление"]);
    }

    #[test]
    fn empty_documents_get_no_topic() {
        let c = corpus(&["", "текст новости дня"]);
        let (_, assignment) = cluster(&c, &[0, 1], 0.2, 3);
        assert_eq!(assignment[0], None);
        assert!(assignment[1].is_some());
    }
}
