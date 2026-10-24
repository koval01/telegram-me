//! Which node owns a key: rendezvous (highest random weight) hashing.
//!
//! Every node scores every live node for a key and picks the highest score,
//! so all nodes that see the same members agree on the owner without
//! talking. Compared with a hash ring it needs no virtual nodes to balance a
//! handful of nodes, and a node that leaves or joins moves only the keys it
//! owns (about `1/n` of them). A lookup is `O(n)`, a few nanoseconds per node
//! for the clusters this serves.
//!
//! The hash is fixed (FNV-1a with a `splitmix64` finaliser), not `std`'s
//! randomly keyed `SipHash`: nodes of different processes, builds and
//! versions must compute the same owner.

const FNV_OFFSET: u64 = 0xcbf2_9ce4_8422_2325;
const FNV_PRIME: u64 = 0x0000_0100_0000_01b3;

/// A stable 64-bit hash of `bytes`.
#[must_use]
pub fn hash64(bytes: &[u8]) -> u64 {
    let mut hash = FNV_OFFSET;
    for byte in bytes {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(FNV_PRIME);
    }
    mix(hash)
}

/// `splitmix64`'s finaliser: spreads FNV's weak low bits over the word.
fn mix(mut value: u64) -> u64 {
    value = (value ^ (value >> 30)).wrapping_mul(0xbf58_476d_1ce4_e5b9);
    value = (value ^ (value >> 27)).wrapping_mul(0x94d0_49bb_1331_11eb);
    value ^ (value >> 31)
}

/// The weight of `node` for a key hashed to `key_hash`.
#[must_use]
pub fn score(node_hash: u64, key_hash: u64) -> u64 {
    mix(node_hash ^ key_hash.rotate_left(32))
}

/// The node of `nodes` that owns `key`; `None` when there are no nodes.
///
/// Ties (practically impossible) go to the smaller node name, so the result
/// never depends on the order of `nodes`.
pub fn owner<'a, I>(nodes: I, key: &str) -> Option<&'a str>
where
    I: IntoIterator<Item = &'a str>,
{
    let key_hash = hash64(key.as_bytes());
    nodes
        .into_iter()
        .map(|node| (score(hash64(node.as_bytes()), key_hash), node))
        .max_by(|(a, a_node), (b, b_node)| a.cmp(b).then_with(|| b_node.cmp(a_node)))
        .map(|(_, node)| node)
}

#[cfg(test)]
mod tests {
    use super::*;

    const NODES: [&str; 3] = ["10.0.0.108:7946", "10.0.0.165:7946", "10.0.0.165:7947"];

    fn keys(count: usize) -> Vec<String> {
        (0..count).map(|i| format!("channel{i}")).collect()
    }

    #[test]
    fn hash_is_stable_across_builds() {
        // Pinned: changing them reassigns every key of a running cluster
        // whose nodes run different versions.
        assert_eq!(hash64(b""), 0xf52a_15e9_a9b5_e89b);
        assert_eq!(hash64(b"durov"), 0xe5c9_4513_7f7e_5878);
        assert_eq!(owner(NODES, "durov"), Some("10.0.0.108:7946"));
        assert_eq!(owner(NODES, "telegram"), Some("10.0.0.108:7946"));
        assert_eq!(owner(NODES, "nebudetgg"), Some("10.0.0.165:7946"));
        assert_eq!(owner(NODES, "tginfo"), Some("10.0.0.165:7947"));
    }

    #[test]
    fn owner_does_not_depend_on_member_order() {
        let mut reversed = NODES;
        reversed.reverse();
        for key in keys(500) {
            assert_eq!(owner(NODES, &key), owner(reversed, &key));
        }
    }

    #[test]
    fn no_nodes_no_owner() {
        assert_eq!(owner([], "durov"), None);
        assert_eq!(owner(["a"], "durov"), Some("a"));
    }

    #[test]
    fn keys_spread_evenly() {
        let keys = keys(30_000);
        for node in NODES {
            let owned = keys
                .iter()
                .filter(|key| owner(NODES, key) == Some(node))
                .count();
            // 10_000 expected; a 5% band is > 8 standard deviations.
            assert!((9_500..=10_500).contains(&owned), "{node}: {owned}");
        }
    }

    #[test]
    fn a_leaving_node_moves_only_its_own_keys() {
        let rest = &NODES[..2];
        for key in keys(10_000) {
            let before = owner(NODES, &key).unwrap();
            let after = owner(rest.iter().copied(), &key).unwrap();
            if before != NODES[2] {
                assert_eq!(before, after, "{key} moved needlessly");
            }
        }
    }

    #[test]
    fn a_joining_node_takes_about_its_share() {
        let before = &NODES[..2];
        let keys = keys(10_000);
        let moved = keys
            .iter()
            .filter(|key| owner(before.iter().copied(), key) != owner(NODES, key))
            .count();
        // Every moved key goes to the new node, about a third of them.
        assert!((3_000..=3_700).contains(&moved), "{moved}");
        for key in &keys {
            let (old, new) = (owner(before.iter().copied(), key), owner(NODES, key));
            if old != new {
                assert_eq!(new, Some(NODES[2]));
            }
        }
    }
}
