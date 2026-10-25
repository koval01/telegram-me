//! The peer layer: nodes on a private network share their work directly.
//!
//! Optional (`ENABLE_PEERS`), groupcache-style. Every process is a node; the
//! nodes find each other ([`members`], SWIM gossip), and each cache key has
//! one owner among the live nodes ([`hash`], rendezvous hashing). On a miss
//! a node asks the owner, which answers from its in-process cache or loads
//! the key itself, so each key is fetched from t.me once for all nodes.
//! Every frame is authenticated with the shared secret, and optionally
//! encrypted ([`wire`]). The network runtime is [`node`]; the Python side
//! (`services/peers.py`) decides what to ask and serves loads.

pub mod allow;
pub mod counts;
pub mod hash;
pub mod members;
pub mod msg;
pub mod node;
pub mod wire;
