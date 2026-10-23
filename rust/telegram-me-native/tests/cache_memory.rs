//! What `LocalCache` accounts for covers what its entries really hold.
//!
//! A separate test binary with a single test: the counting allocator sees
//! every allocation of the process, so nothing else may run alongside.

use std::alloc::System;

use cap::Cap;
use telegram_me_native::cache::{ENTRY_OVERHEAD, LocalCache};

#[global_allocator]
static ALLOCATOR: Cap<System> = Cap::new(System, usize::MAX);

/// A JSON-like value of `len` bytes that compresses about as well as pages.
fn page(seed: u64, len: usize) -> Vec<u8> {
    let mut state = seed | 1;
    let mut out = Vec::with_capacity(len + 64);
    while out.len() < len {
        state ^= state << 13;
        state ^= state >> 7;
        state ^= state << 17;
        let post = format!(
            r#"{{"id":{},"views":"{}K","text":"пост"}},"#,
            state % 100_000,
            state % 977
        );
        out.extend_from_slice(post.as_bytes());
    }
    out.truncate(len);
    out
}

#[test]
fn accounted_size_covers_real_memory() {
    let values: Vec<Vec<u8>> = (0..64_u64)
        .map(|i| page(i, 500 + 997 * usize::try_from(i % 40).expect("small")))
        .collect();
    // The thread's compression context is allocated on its first write and
    // reused afterwards: it belongs to no entry.
    let warmup = LocalCache::new(1 << 20);
    warmup.insert("warm-up", &values[0], 60.0, 0.0);
    drop(warmup);

    // Without and with evictions.
    for (count, limit) in [(2_000_usize, 256_u64 << 20), (20_000, 8 << 20)] {
        let before = ALLOCATOR.allocated();
        let cache = LocalCache::new(limit);
        for i in 0..count {
            let key = format!("json:body:page:full:channel_m{i}:0");
            cache.insert(&key, &values[i % values.len()], 60.0, 1.0);
        }
        let accounted = cache.weighted_size();
        let held = (ALLOCATOR.allocated() - before) as u64;
        let entries = cache.len();
        assert!(accounted <= limit);
        // Bytes requested from the allocator; ENTRY_OVERHEAD also allows for
        // malloc's headers and rounding, which no allocator wrapper sees.
        assert!(
            held <= accounted,
            "{held} bytes held, {accounted} accounted"
        );
        assert!(
            held + entries * (ENTRY_OVERHEAD / 2) > accounted,
            "{held} bytes held, {accounted} accounted: ENTRY_OVERHEAD is too large"
        );
        drop(cache);
    }
    // Nothing outlives the cache.
    let before = ALLOCATOR.allocated();
    let cache = LocalCache::new(1 << 20);
    cache.insert("k", &values[1], 60.0, 0.0);
    drop(cache);
    assert_eq!(ALLOCATOR.allocated(), before);
}
