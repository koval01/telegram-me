//! In-process byte cache: per-entry expiry, bounded by the memory it holds.
//!
//! Built on `moka`'s concurrent cache with the LRU policy: every new entry is
//! admitted (a page fetched from t.me is about to be read again), and the
//! least recently used ones are evicted once the entries outweigh the limit.
//! An entry weighs what it really occupies: its key, its stored value and a
//! fixed allowance for the cache's own bookkeeping (measured by
//! `tests/cache_memory.rs`).
//!
//! Values are JSON documents and are kept zstd-compressed (level 1): a
//! rendered page shrinks about 4x, and decompressing it on a hit takes tens of
//! microseconds. Values that do not shrink are stored as they are.

use std::cell::RefCell;
use std::sync::Arc;
use std::time::{Duration, Instant};

use moka::Expiry;
use moka::ops::compute::Op;
use moka::policy::EvictionPolicy;
use moka::sync::Cache;

/// Bytes an entry costs besides its key and stored value: `moka`'s entry,
/// hash table, LRU and timer-wheel nodes, the shared key and the `Entry`
/// itself (~390 bytes requested, measured), plus malloc's headers and rounding.
pub const ENTRY_OVERHEAD: u64 = 512;

const LEVEL: i32 = 1;

thread_local! {
    static COMPRESSOR: RefCell<Option<zstd::bulk::Compressor<'static>>> =
        const { RefCell::new(None) };
}

/// A stored value and when it was stored.
#[derive(Debug)]
pub struct Entry {
    blob: Box<[u8]>,
    len: usize,
    compressed: bool,
    stored_at: f64,
    ttl: Duration,
}

impl Entry {
    fn new(value: &[u8], ttl: Duration, stored_at: f64) -> Self {
        let blob = compress(value)
            .filter(|blob| blob.len() < value.len())
            .map(Vec::into_boxed_slice);
        let compressed = blob.is_some();
        Self {
            blob: blob.unwrap_or_else(|| value.into()),
            len: value.len(),
            compressed,
            stored_at,
            ttl,
        }
    }

    /// Length of the original value in bytes.
    #[must_use]
    pub fn len(&self) -> usize {
        self.len
    }

    /// True for an empty value.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    /// Bytes the value occupies in the cache.
    #[must_use]
    pub fn stored_len(&self) -> usize {
        self.blob.len()
    }

    /// Wall-clock time (Unix seconds) the value was stored at.
    #[must_use]
    pub fn stored_at(&self) -> f64 {
        self.stored_at
    }

    /// Write the original value into `out`, which must be `len()` bytes long.
    ///
    /// # Panics
    ///
    /// When `out` has the wrong length, or the stored value is corrupt (it is
    /// only ever written by `Entry::new`).
    pub fn write_value(&self, out: &mut [u8]) {
        assert_eq!(out.len(), self.len, "output buffer has the wrong length");
        if self.compressed {
            let written = zstd::bulk::decompress_to_buffer(&self.blob, out)
                .expect("cached values decompress");
            assert_eq!(written, self.len, "cached value has the wrong length");
        } else {
            out.copy_from_slice(&self.blob);
        }
    }

    /// The original value.
    #[must_use]
    pub fn value(&self) -> Vec<u8> {
        let mut out = vec![0; self.len];
        self.write_value(&mut out);
        out
    }
}

fn compress(value: &[u8]) -> Option<Vec<u8>> {
    COMPRESSOR.with_borrow_mut(|compressor| {
        if compressor.is_none() {
            *compressor = zstd::bulk::Compressor::new(LEVEL).ok();
        }
        compressor.as_mut()?.compress(value).ok()
    })
}

struct PerEntryTtl;

impl Expiry<String, Arc<Entry>> for PerEntryTtl {
    fn expire_after_create(
        &self,
        _key: &String,
        value: &Arc<Entry>,
        _created_at: Instant,
    ) -> Option<Duration> {
        Some(value.ttl)
    }

    fn expire_after_update(
        &self,
        _key: &String,
        value: &Arc<Entry>,
        _updated_at: Instant,
        _duration_until_expiry: Option<Duration>,
    ) -> Option<Duration> {
        Some(value.ttl)
    }
}

fn weight(key: &str, entry: &Entry) -> u64 {
    key.len() as u64 + entry.blob.len() as u64 + ENTRY_OVERHEAD
}

/// Concurrent in-process cache of byte values.
pub struct LocalCache {
    inner: Cache<String, Arc<Entry>>,
    max_bytes: u64,
}

impl LocalCache {
    /// A cache holding at most `max_bytes` bytes of entries.
    #[must_use]
    pub fn new(max_bytes: u64) -> Self {
        let inner = Cache::builder()
            .max_capacity(max_bytes)
            .eviction_policy(EvictionPolicy::lru())
            .weigher(|key: &String, entry: &Arc<Entry>| {
                u32::try_from(weight(key, entry)).unwrap_or(u32::MAX)
            })
            .expire_after(PerEntryTtl)
            .build();
        Self { inner, max_bytes }
    }

    /// The live entry for `key`.
    #[must_use]
    pub fn get(&self, key: &str) -> Option<Arc<Entry>> {
        self.inner.get(key)
    }

    /// Store `value` for `ttl` seconds, replacing any entry for `key`.
    ///
    /// A non-positive TTL, or a value larger than the whole cache, stores
    /// nothing.
    pub fn insert(&self, key: &str, value: &[u8], ttl: f64, stored_at: f64) {
        if let Some(entry) = self.entry(key, value, ttl, stored_at) {
            self.inner.insert(key.to_owned(), entry);
            self.settle();
        }
    }

    /// Store `value` unless the entry already held for `key` is as new or
    /// newer (by `stored_at`); returns whether it was stored.
    ///
    /// For copying entries from a slower shared cache: a write that landed
    /// while the copy was in flight must not be replaced by the older copy.
    #[must_use]
    pub fn fill(&self, key: &str, value: &[u8], ttl: f64, stored_at: f64) -> bool {
        let Some(entry) = self.entry(key, value, ttl, stored_at) else {
            return false;
        };
        let result = self
            .inner
            .entry_by_ref(key)
            .and_compute_with(|current| match current {
                Some(current) if current.value().stored_at >= stored_at => Op::Nop,
                _ => Op::Put(entry),
            });
        let stored = matches!(
            result,
            moka::ops::compute::CompResult::Inserted(_)
                | moka::ops::compute::CompResult::ReplacedWith(_)
        );
        if stored {
            self.settle();
        }
        stored
    }

    fn entry(&self, key: &str, value: &[u8], ttl: f64, stored_at: f64) -> Option<Arc<Entry>> {
        let ttl = Duration::try_from_secs_f64(ttl)
            .ok()
            .filter(|ttl| !ttl.is_zero())?;
        let entry = Entry::new(value, ttl, stored_at);
        (weight(key, &entry) <= self.max_bytes).then(|| Arc::new(entry))
    }

    /// Apply pending evictions now, so the bound holds as soon as a write
    /// returns (`moka` otherwise batches them for up to 64 writes).
    fn settle(&self) {
        self.inner.run_pending_tasks();
    }

    /// Drop every entry.
    pub fn clear(&self) {
        self.inner.invalidate_all();
        self.settle();
    }

    /// Bytes held by the entries (keys, stored values and bookkeeping).
    #[must_use]
    pub fn weighted_size(&self) -> u64 {
        self.settle();
        self.inner.weighted_size()
    }

    /// Number of entries.
    #[must_use]
    pub fn len(&self) -> u64 {
        self.settle();
        self.inner.entry_count()
    }

    /// True when the cache holds no entry.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// The memory limit in bytes.
    #[must_use]
    pub fn max_bytes(&self) -> u64 {
        self.max_bytes
    }
}

#[cfg(test)]
mod tests {
    use std::thread::sleep;

    use super::*;

    fn page(repeat: usize) -> Vec<u8> {
        r#"{"text":"Привет 👋 world","views":"1.2K"},"#.repeat(repeat).into_bytes()
    }

    fn noise(len: usize) -> Vec<u8> {
        // xorshift: incompressible enough for zstd, deterministic for tests.
        let mut state = 0x2545_f491_4f6c_dd1d_u64;
        (0..len)
            .map(|_| {
                state ^= state << 13;
                state ^= state >> 7;
                state ^= state << 17;
                state.to_le_bytes()[0]
            })
            .collect()
    }

    #[test]
    fn roundtrip_keeps_value_and_stored_at() {
        let cache = LocalCache::new(1 << 20);
        cache.insert("k", b"value", 60.0, 1_000_000.5);
        let entry = cache.get("k").expect("entry");
        assert_eq!(entry.value(), b"value");
        assert!((entry.stored_at() - 1_000_000.5).abs() < f64::EPSILON);
        assert!(cache.get("other").is_none());
    }

    #[test]
    fn compresses_json_and_stores_noise_as_is() {
        let cache = LocalCache::new(1 << 20);
        let page = page(500);
        cache.insert("page", &page, 60.0, 0.0);
        let entry = cache.get("page").expect("page");
        assert_eq!(entry.value(), page);
        assert!(entry.stored_len() < page.len() / 4);

        let noise = noise(10_000);
        cache.insert("noise", &noise, 60.0, 0.0);
        let entry = cache.get("noise").expect("noise");
        assert_eq!(entry.value(), noise);
        assert_eq!(entry.stored_len(), noise.len());
        assert!(cache.weighted_size() > noise.len() as u64);
    }

    #[test]
    fn empty_value_roundtrips() {
        let cache = LocalCache::new(1 << 20);
        cache.insert("empty", b"", 60.0, 0.0);
        let entry = cache.get("empty").expect("entry");
        assert!(entry.is_empty());
        assert_eq!(entry.value(), Vec::<u8>::new());
    }

    #[test]
    fn entries_expire_after_their_own_ttl() {
        let cache = LocalCache::new(1 << 20);
        cache.insert("short", b"v", 0.05, 0.0);
        cache.insert("long", b"v", 60.0, 0.0);
        assert!(cache.get("short").is_some());
        sleep(Duration::from_millis(80));
        assert!(cache.get("short").is_none());
        assert!(cache.get("long").is_some());
    }

    #[test]
    fn expired_entries_free_their_memory_unread() {
        let cache = LocalCache::new(1 << 20);
        cache.insert("short", &page(100), 0.05, 0.0);
        cache.insert("long", b"v", 60.0, 0.0);
        // `moka`'s timer wheel removes expired entries in ~1 s steps.
        sleep(Duration::from_millis(1500));
        assert_eq!(cache.len(), 1);
        assert_eq!(cache.weighted_size(), ENTRY_OVERHEAD + 4 + 1);
    }

    #[test]
    fn overwrite_resets_the_ttl() {
        let cache = LocalCache::new(1 << 20);
        cache.insert("k", b"old", 60.0, 1.0);
        cache.insert("k", b"new", 0.05, 2.0);
        sleep(Duration::from_millis(80));
        assert!(cache.get("k").is_none());
    }

    #[test]
    fn ignores_non_positive_ttl_and_oversized_values() {
        let cache = LocalCache::new(ENTRY_OVERHEAD + 4);
        cache.insert("a", b"v", 0.0, 0.0);
        cache.insert("b", b"v", -1.0, 0.0);
        cache.insert("c", b"v", f64::NAN, 0.0);
        cache.insert("d", &noise(100), 60.0, 0.0);
        assert!(cache.is_empty());
        cache.insert("e", b"vvv", 60.0, 0.0);
        assert_eq!(cache.len(), 1);
    }

    #[test]
    fn evicts_least_recently_used_by_weight() {
        let entry = ENTRY_OVERHEAD + 1 + 4;
        let cache = LocalCache::new(2 * entry + entry / 2);
        cache.insert("a", b"aaaa", 60.0, 0.0);
        cache.insert("b", b"bbbb", 60.0, 0.0);
        assert!(cache.get("a").is_some()); // "a" becomes most recently used
        cache.insert("c", b"cccc", 60.0, 0.0);
        assert!(cache.get("b").is_none());
        assert!(cache.get("a").is_some());
        assert!(cache.get("c").is_some());
        assert_eq!(cache.len(), 2);
        assert_eq!(cache.weighted_size(), 2 * entry);
    }

    #[test]
    fn stays_within_its_limit_right_after_each_write() {
        let limit = 64 * 1024;
        let cache = LocalCache::new(limit);
        for i in 0..500 {
            cache.insert(&format!("k{i}"), &noise(1000 + i), 60.0, 0.0);
            assert!(cache.inner.weighted_size() <= limit);
        }
        assert!(cache.weighted_size() > limit * 9 / 10);
    }

    #[test]
    fn overwrite_keeps_size_accurate() {
        let cache = LocalCache::new(1 << 20);
        for _ in 0..10 {
            cache.insert("a", b"aaaa", 60.0, 0.0);
        }
        assert_eq!(cache.weighted_size(), ENTRY_OVERHEAD + 1 + 4);
    }

    #[test]
    fn fill_never_replaces_a_newer_entry() {
        let cache = LocalCache::new(1 << 20);
        assert!(cache.fill("k", b"first", 60.0, 10.0));
        assert!(!cache.fill("k", b"older", 60.0, 5.0));
        assert!(!cache.fill("k", b"same", 60.0, 10.0));
        assert_eq!(cache.get("k").expect("entry").value(), b"first");
        assert!(cache.fill("k", b"newer", 60.0, 11.0));
        assert_eq!(cache.get("k").expect("entry").value(), b"newer");
        assert!(!cache.fill("other", b"v", 0.0, 1.0));
    }

    #[test]
    fn fill_after_expiry_stores_again() {
        let cache = LocalCache::new(1 << 20);
        assert!(cache.fill("k", b"v", 0.05, 10.0));
        sleep(Duration::from_millis(80));
        assert!(cache.fill("k", b"older but live", 60.0, 5.0));
    }

    #[test]
    fn clear_drops_everything() {
        let cache = LocalCache::new(1 << 20);
        cache.insert("a", b"v", 60.0, 0.0);
        cache.insert("b", b"v", 60.0, 0.0);
        cache.clear();
        assert!(cache.get("a").is_none());
        assert_eq!(cache.weighted_size(), 0);
        assert_eq!(cache.max_bytes(), 1 << 20);
    }

    #[test]
    fn concurrent_writers_respect_the_limit() {
        let entry = ENTRY_OVERHEAD + 3 + 10;
        let cache = Arc::new(LocalCache::new(10 * entry));
        let handles: Vec<_> = (0..4)
            .map(|t| {
                let cache = Arc::clone(&cache);
                std::thread::spawn(move || {
                    for i in 0..50 {
                        cache.insert(&format!("{t}{i:02}"), &[b'x'; 10], 60.0, 0.0);
                    }
                })
            })
            .collect();
        for handle in handles {
            handle.join().expect("writer");
        }
        assert_eq!(cache.len(), 10);
        assert_eq!(cache.weighted_size(), 10 * entry);
    }
}
