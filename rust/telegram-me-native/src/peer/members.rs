//! Cluster membership: who is alive, by SWIM gossip, without any I/O.
//!
//! [`foca`] runs the protocol (probes, indirect probes, suspicion,
//! dissemination piggybacked on probes). It is "sans-IO": it is handed
//! received datagrams and fired timers, and answers with datagrams to send
//! and timers to set, collected here in an [`Outbox`] for the network
//! runtime (`node`) to carry out. So every datagram is authenticated before
//! foca sees it, and the tests below run whole clusters in memory on a
//! simulated clock.
//!
//! A node's identity is its advertised address plus a generation: a
//! restarted process (or one wrongly declared down, which renews itself)
//! comes back with a higher generation and replaces its old identity at the
//! same address everywhere.

use std::net::SocketAddr;
use std::num::NonZeroU32;
use std::num::NonZeroUsize;
use std::time::Duration;

use foca::{
    Config, Foca, Identity, NoCustomBroadcast, Notification, PostcardCodec, Runtime, Timer,
};
use rand::SeedableRng;
use rand::rngs::SmallRng;
use serde::{Deserialize, Serialize};

/// Largest membership datagram payload. With the frame around it this stays
/// under the 1500-byte Ethernet MTU, so datagrams are never fragmented.
pub const MAX_GOSSIP: usize = 1300;

/// A node as the cluster knows it.
#[derive(Clone, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct PeerId {
    /// Where other nodes reach it (UDP for gossip, TCP for requests).
    pub addr: SocketAddr,
    /// Distinguishes successive processes (and renewals) at one address.
    pub generation: u64,
    /// Whether it may run the feed refresher.
    pub refresher: bool,
}

impl Identity for PeerId {
    type Addr = SocketAddr;

    fn renew(&self) -> Option<Self> {
        Some(Self {
            generation: self.generation + 1,
            ..self.clone()
        })
    }

    fn addr(&self) -> SocketAddr {
        self.addr
    }

    fn win_addr_conflict(&self, adversary: &Self) -> bool {
        self.generation > adversary.generation
    }
}

/// What foca asked for while handling an input.
#[derive(Default)]
pub struct Outbox {
    /// Datagrams to send.
    pub sends: Vec<(SocketAddr, Vec<u8>)>,
    /// Timers to fire later.
    pub timers: Vec<(Timer<PeerId>, Duration)>,
    /// Whether the set of live members (or our own state) changed.
    pub changed: bool,
}

impl Runtime<PeerId> for Outbox {
    fn notify(&mut self, notification: Notification<'_, PeerId>) {
        if matches!(
            notification,
            Notification::MemberUp(_)
                | Notification::MemberDown(_)
                | Notification::Rename(..)
                | Notification::Active
                | Notification::Idle
                | Notification::Defunct
        ) {
            self.changed = true;
        }
    }

    fn send_to(&mut self, to: PeerId, data: &[u8]) {
        self.sends.push((to.addr, data.to_vec()));
    }

    fn submit_after(&mut self, event: Timer<PeerId>, after: Duration) {
        self.timers.push((event, after));
    }
}

/// One node's view of the cluster.
pub struct Membership {
    foca: Foca<PeerId, PostcardCodec, SmallRng, NoCustomBroadcast>,
    outbox: Outbox,
}

impl Membership {
    /// Membership for the node `id`; `seed` seeds its random choices.
    ///
    /// # Panics
    ///
    /// Never: the constants are non-zero.
    #[must_use]
    pub fn new(id: PeerId, seed: u64) -> Self {
        // Timings of a LAN (memberlist's defaults): probes every second, a
        // dead node is declared down within a few seconds.
        let mut config = Config::new_lan(NonZeroU32::new(8).expect("non-zero"));
        config.max_packet_size = NonZeroUsize::new(MAX_GOSSIP).expect("non-zero");
        Self {
            foca: Foca::new(id, config, SmallRng::seed_from_u64(seed), PostcardCodec),
            outbox: Outbox::default(),
        }
    }

    /// This node's current identity.
    #[must_use]
    pub fn identity(&self) -> &PeerId {
        self.foca.identity()
    }

    /// Ask whoever listens at `addr` to let us in (a seed).
    pub fn announce(&mut self, addr: SocketAddr) {
        // Only the address of the destination of an announcement matters.
        let target = PeerId {
            addr,
            generation: 0,
            refresher: false,
        };
        if addr != self.identity().addr {
            let _ = self.foca.announce(target, &mut self.outbox);
        }
    }

    /// Handle a received (already authenticated) datagram payload.
    ///
    /// # Errors
    ///
    /// When foca refuses it (undecodable, from ourselves, ...).
    pub fn handle_data(&mut self, data: &[u8]) -> Result<(), foca::Error> {
        self.foca.handle_data(data, &mut self.outbox)
    }

    /// Handle a timer foca asked for earlier.
    pub fn handle_timer(&mut self, timer: Timer<PeerId>) {
        let _ = self.foca.handle_timer(timer, &mut self.outbox);
    }

    /// Tell the others we are leaving, so they need not detect it.
    pub fn leave(&mut self) {
        let _ = self.foca.leave_cluster(&mut self.outbox);
    }

    /// The live members other than us.
    #[must_use]
    pub fn members(&self) -> Vec<PeerId> {
        let mut members: Vec<PeerId> = self
            .foca
            .iter_members()
            .map(|member| member.id().clone())
            .collect();
        members.sort_by_key(|id| id.addr);
        members.dedup_by_key(|id| id.addr);
        members
    }

    /// Whether `addr` is a live member.
    #[must_use]
    pub fn knows(&self, addr: SocketAddr) -> bool {
        self.foca
            .iter_members()
            .any(|member| member.id().addr == addr)
    }

    /// What foca asked for since the last call.
    pub fn drain(&mut self) -> Outbox {
        std::mem::take(&mut self.outbox)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::{BTreeMap, HashSet};

    /// Nodes exchanging datagrams in memory, on a simulated clock.
    struct Cluster {
        nodes: BTreeMap<SocketAddr, Membership>,
        timers: Vec<(Duration, SocketAddr, Timer<PeerId>)>,
        now: Duration,
        /// Nodes whose datagrams are lost (crashed or cut off).
        dead: HashSet<SocketAddr>,
    }

    fn addr(port: u16) -> SocketAddr {
        SocketAddr::from(([10, 0, 0, 1], port))
    }

    fn id(port: u16, generation: u64) -> PeerId {
        PeerId {
            addr: addr(port),
            generation,
            refresher: true,
        }
    }

    impl Cluster {
        fn new() -> Self {
            Self {
                nodes: BTreeMap::new(),
                timers: Vec::new(),
                now: Duration::ZERO,
                dead: HashSet::new(),
            }
        }

        fn start(&mut self, id: PeerId, seed: Option<u16>) {
            let at = id.addr;
            let mut node = Membership::new(id, u64::from(at.port()));
            if let Some(seed) = seed {
                node.announce(addr(seed));
            }
            self.nodes.insert(at, node);
            self.dead.remove(&at);
            self.flush(at);
        }

        /// Deliver what a node asked for: datagrams at once, timers later.
        fn flush(&mut self, from: SocketAddr) {
            let mut queue = vec![from];
            while let Some(at) = queue.pop() {
                let Some(node) = self.nodes.get_mut(&at) else {
                    continue;
                };
                let outbox = node.drain();
                for (timer, after) in outbox.timers {
                    self.timers.push((self.now + after, at, timer));
                }
                if self.dead.contains(&at) {
                    continue;
                }
                for (to, data) in outbox.sends {
                    if self.dead.contains(&to) {
                        continue;
                    }
                    if let Some(peer) = self.nodes.get_mut(&to) {
                        let _ = peer.handle_data(&data);
                        queue.push(to);
                    }
                }
            }
        }

        fn run_for(&mut self, duration: Duration) {
            let until = self.now + duration;
            loop {
                self.timers.sort_by_key(|(at, ..)| std::cmp::Reverse(*at));
                match self.timers.last() {
                    Some((at, ..)) if *at <= until => {}
                    _ => break,
                }
                let (at, node, timer) = self.timers.pop().expect("checked");
                self.now = at;
                if let Some(membership) = self.nodes.get_mut(&node) {
                    membership.handle_timer(timer);
                    self.flush(node);
                }
            }
            self.now = until;
        }

        fn view(&self, port: u16) -> Vec<u16> {
            self.nodes[&addr(port)]
                .members()
                .iter()
                .map(|id| id.addr.port())
                .collect()
        }
    }

    #[test]
    fn nodes_find_each_other_through_one_seed() {
        let mut cluster = Cluster::new();
        cluster.start(id(1, 1), None);
        cluster.start(id(2, 1), Some(1));
        cluster.start(id(3, 1), Some(1));
        cluster.run_for(Duration::from_secs(5));
        assert_eq!(cluster.view(1), [2, 3]);
        assert_eq!(cluster.view(2), [1, 3]);
        assert_eq!(cluster.view(3), [1, 2]);
    }

    #[test]
    fn a_crashed_node_is_declared_down_and_a_restart_replaces_it() {
        let mut cluster = Cluster::new();
        for port in 1..=3 {
            cluster.start(id(port, 1), (port > 1).then_some(1));
        }
        cluster.run_for(Duration::from_secs(5));

        cluster.dead.insert(addr(3));
        cluster.run_for(Duration::from_secs(15));
        assert_eq!(cluster.view(1), [2]);
        assert_eq!(cluster.view(2), [1]);

        // The process comes back at the same address with a new generation.
        cluster.start(id(3, 2), Some(2));
        cluster.run_for(Duration::from_secs(5));
        assert_eq!(cluster.view(1), [2, 3]);
        let known = cluster.nodes[&addr(1)].members();
        assert_eq!(known.last().map(|id| id.generation), Some(2));
    }

    #[test]
    fn a_leaving_node_is_gone_at_once() {
        let mut cluster = Cluster::new();
        cluster.start(id(1, 1), None);
        cluster.start(id(2, 1), Some(1));
        cluster.run_for(Duration::from_secs(3));
        assert_eq!(cluster.view(1), [2]);

        cluster.nodes.get_mut(&addr(2)).unwrap().leave();
        cluster.flush(addr(2));
        cluster.nodes.remove(&addr(2));
        assert_eq!(cluster.view(1), Vec::<u16>::new());
    }

    #[test]
    fn a_partition_heals() {
        let mut cluster = Cluster::new();
        cluster.start(id(1, 1), None);
        cluster.start(id(2, 1), Some(1));
        cluster.run_for(Duration::from_secs(3));

        // Cut off long enough to be declared down on both sides...
        cluster.dead.insert(addr(2));
        cluster.run_for(Duration::from_secs(20));
        assert_eq!(cluster.view(1), Vec::<u16>::new());
        // ...then reachable again: node 2 learns it was declared down,
        // renews its identity and rejoins (periodic announcements).
        cluster.dead.remove(&addr(2));
        cluster.nodes.get_mut(&addr(2)).unwrap().announce(addr(1));
        cluster.flush(addr(2));
        cluster.run_for(Duration::from_secs(10));
        assert_eq!(cluster.view(1), [2]);
        assert_eq!(cluster.view(2), [1]);
    }

    #[test]
    fn announcing_to_ourselves_or_nobody_is_harmless() {
        let mut cluster = Cluster::new();
        cluster.start(id(1, 1), Some(1));
        cluster.nodes.get_mut(&addr(1)).unwrap().announce(addr(9));
        cluster.flush(addr(1));
        cluster.run_for(Duration::from_secs(3));
        assert_eq!(cluster.view(1), Vec::<u16>::new());
    }

    #[test]
    fn garbage_is_refused() {
        let mut node = Membership::new(id(1, 1), 1);
        assert!(node.handle_data(b"not gossip").is_err());
        assert!(node.handle_data(&[0; MAX_GOSSIP + 1]).is_err());
        assert_eq!(node.members(), Vec::<PeerId>::new());
    }
}
