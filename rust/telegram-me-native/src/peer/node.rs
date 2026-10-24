//! The network side of the peer layer: one background thread per process.
//!
//! The thread runs a single-threaded tokio runtime with:
//!
//! - the gossip socket (UDP) and the membership timers;
//! - the request listener (TCP): another node asks for cached entries, which
//!   are answered from this process's in-process cache right here, without
//!   the interpreter, or asks this node to load a key it owns, which is
//!   handed to Python as an [`Event::Serve`];
//! - outgoing requests, over one multiplexed connection per peer.
//!
//! It never takes the GIL. Results and requests for Python go into a queue,
//! and one byte on a socket pair wakes the event loop, which reads the
//! queue with [`Node::poll`] (see `services/peers.py`). Python calls into it
//! with quick, non-blocking methods that queue commands.
//!
//! Nothing here may fail a request: every outgoing request ends, within its
//! deadline, with a result, a miss or [`Outcome::Unavailable`], and a peer
//! that did not answer in time is avoided for a few seconds.

use std::io::{Read as _, Write as _};
use std::net::{IpAddr, Ipv4Addr, SocketAddr, UdpSocket as StdUdpSocket};
use std::os::unix::net::UnixStream;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock, PoisonError, RwLock};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use rustc_hash::FxHashMap;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::tcp::{OwnedReadHalf, OwnedWriteHalf};
use tokio::net::{TcpListener, TcpStream, UdpSocket};
use tokio::sync::mpsc;

use super::allow::AllowList;
use super::counts::WindowCounts;
use super::hash;
use super::members::{MAX_GOSSIP, Membership, PeerId};
use super::msg::{MAX_COUNTS, MAX_ITEMS, Message, WireEntry};
use super::wire::{HEADER_LEN, MAX_BODY, Reject, Sealer, unix_ms};
use crate::cache::{Entry, LocalCache};
use crate::ratelimit::RateLimiter;

/// How long a peer that failed to answer is left out of ownership.
const AVOID_FOR: Duration = Duration::from_secs(3);
/// Seeds are announced to this often while we know no other node...
const SEED_EARLY: Duration = Duration::from_secs(2);
/// ...and this often afterwards (a node that restarted is found again).
const SEED_LATER: Duration = Duration::from_secs(30);
/// Most connections other nodes may hold open to us.
const MAX_INBOUND: usize = 256;
/// Most loads other nodes may have waiting on this one.
const MAX_SERVING: usize = 4096;
/// Most events waiting for Python; beyond it casts are dropped.
const MAX_EVENTS: usize = 65_536;

/// What a [`Node`] is started with.
pub struct Config {
    /// Ports to try, in order; the first free one (for both UDP and TCP) is
    /// used. `[0]` lets the OS pick (tests).
    pub ports: Vec<u16>,
    /// Address to listen on.
    pub bind: IpAddr,
    /// Address other nodes reach us at; by default the address of the
    /// default route.
    pub advertise: Option<IpAddr>,
    /// Where to look for other nodes: `host`, `host:port`, IPs or DNS names.
    pub seeds: Vec<String>,
    /// Ports tried on seeds given without one.
    pub seed_ports: Vec<u16>,
    /// Who may talk to us.
    pub allow: AllowList,
    /// The shared secret.
    pub secret: Vec<u8>,
    /// Encrypt every frame as well.
    pub encrypt: bool,
    /// Whether this process may run the feed refresher.
    pub refresher: bool,
    /// Deadline to connect, to answer a get, and to accept a load.
    pub ask_timeout: Duration,
    /// This process's in-process cache.
    pub cache: LocalCache,
}

/// What a load asks of the owner of a key.
#[derive(Debug, Clone, PartialEq)]
pub struct LoadRequest {
    /// The key.
    pub key: String,
    /// Values stored at or before this Unix time are too old.
    pub newer_than: f64,
    /// Background work (a refresh): the owner gives user requests precedence.
    pub background: bool,
}

/// How a load asked of another node ended.
#[derive(Debug, PartialEq)]
pub enum Outcome {
    /// The value and when it was stored.
    Value(Vec<u8>, f64),
    /// The owner failed with this error class and message.
    Failed(u8, String),
    /// No usable answer: do the work yourself.
    Unavailable,
}

/// Something for Python.
#[derive(Debug, PartialEq)]
pub enum Event {
    /// The answer to [`Node::get`]: the value and when it was stored, or a miss.
    Got {
        /// The request.
        token: u64,
        /// What was found.
        found: Option<(Vec<u8>, f64)>,
    },
    /// The answer to [`Node::load`].
    Loaded {
        /// The request.
        token: u64,
        /// How it ended.
        outcome: Outcome,
    },
    /// Another node asks us to load `key`; answer with [`Node::served`] or
    /// [`Node::serve_failed`].
    Serve {
        /// The request.
        token: u64,
        /// What is asked.
        request: LoadRequest,
    },
    /// A message another node sent to all ([`Node::cast`]).
    Cast {
        /// What it is about.
        topic: String,
        /// Its content.
        data: Vec<u8>,
    },
    /// The live members changed.
    Members,
}

/// Counters, for logs and tests.
#[derive(Default)]
pub struct Stats {
    /// Connections and datagrams from outside the allowed networks.
    pub refused: AtomicU64,
    /// Frames that failed authentication (or replays).
    pub rejected: AtomicU64,
    /// Requests sent to other nodes.
    pub asked: AtomicU64,
    /// Of those, the ones that got no usable answer.
    pub unanswered: AtomicU64,
    /// Gets answered for other nodes, from the cache.
    pub served_gets: AtomicU64,
    /// Loads other nodes asked for.
    pub served_loads: AtomicU64,
}

enum Command {
    Get {
        token: u64,
        to: SocketAddr,
        key: String,
    },
    Load {
        token: u64,
        to: SocketAddr,
        request: LoadRequest,
        wait: Duration,
    },
    Send(Message),
    Seeds(Vec<String>),
    Stop,
}

/// A load another node is waiting for.
struct Serving {
    reply: mpsc::UnboundedSender<Vec<u8>>,
    id: u64,
}

struct Shared {
    me: PeerId,
    sealer: Sealer,
    allow: AllowList,
    cache: LocalCache,
    ask_timeout: Duration,
    /// The live members other than us, sorted by address.
    view: RwLock<Vec<PeerId>>,
    avoid: Mutex<FxHashMap<SocketAddr, Instant>>,
    events: Mutex<Vec<Event>>,
    wake: UnixStream,
    woken: AtomicBool,
    next_token: AtomicU64,
    serving: Mutex<FxHashMap<u64, Serving>>,
    counts: Mutex<WindowCounts>,
    limiter: OnceLock<(Arc<RateLimiter>, u32)>,
    pool: Mutex<FxHashMap<SocketAddr, Arc<Slot>>>,
    inbound: AtomicU64,
    stats: Stats,
}

fn lock<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}

impl Shared {
    fn push(&self, event: Event) {
        {
            let mut events = lock(&self.events);
            if events.len() >= MAX_EVENTS && matches!(event, Event::Cast { .. }) {
                return;
            }
            events.push(event);
        }
        if !self.woken.swap(true, Ordering::AcqRel) {
            // Non-blocking; a full socket buffer already holds a wake-up.
            drop((&self.wake).write(&[1]));
        }
    }

    fn token(&self) -> u64 {
        self.next_token.fetch_add(1, Ordering::Relaxed)
    }

    fn avoid(&self, addr: SocketAddr) {
        lock(&self.avoid).insert(addr, Instant::now() + AVOID_FOR);
    }

    fn reachable(&self) -> Vec<PeerId> {
        let now = Instant::now();
        let avoided: Vec<SocketAddr> = {
            let mut avoid = lock(&self.avoid);
            avoid.retain(|_, until| *until > now);
            avoid.keys().copied().collect()
        };
        self.view
            .read()
            .unwrap_or_else(PoisonError::into_inner)
            .iter()
            .filter(|id| !avoided.contains(&id.addr))
            .cloned()
            .collect()
    }

    fn entries(&self, keys: &[String]) -> Vec<WireEntry> {
        let mut size = 0;
        let mut entries = Vec::new();
        for key in keys {
            let Some(entry) = self.cache.get(key) else {
                continue;
            };
            let ttl = entry.remaining();
            size += entry.stored().len() + key.len() + 64;
            if ttl.is_zero() || size > MAX_BODY / 2 {
                continue;
            }
            entries.push(WireEntry {
                key: key.clone(),
                stored_at: entry.stored_at(),
                ttl_ms: u64::try_from(ttl.as_millis()).unwrap_or(u64::MAX),
                len: u32::try_from(entry.len()).unwrap_or(u32::MAX),
                compressed: entry.is_compressed(),
                blob: entry.stored().to_vec(),
            });
        }
        entries
    }

    /// Keep entries another node sent; returns the value stored under `key`.
    fn keep(&self, entries: Vec<WireEntry>, key: &str) -> Option<(Vec<u8>, f64)> {
        let mut found = None;
        for wire in entries {
            let Some((entry, value)) = Entry::from_stored(
                wire.blob,
                wire.len as usize,
                wire.compressed,
                Duration::from_millis(wire.ttl_ms),
                wire.stored_at,
            ) else {
                continue;
            };
            // A newer copy written meanwhile stays.
            let _stored = self.cache.fill_stored(&wire.key, entry);
            if wire.key == key {
                found = Some((value, wire.stored_at));
            }
        }
        found
    }

    fn add_counts(&self, window: u64, items: &[(String, u32)]) {
        let Some((limiter, limit)) = self.limiter.get() else {
            return;
        };
        let over = lock(&self.counts).add(
            window,
            items
                .iter()
                .map(|(client, count)| (client.as_str(), *count)),
            *limit,
        );
        if !over.is_empty() {
            let now = unix_ms();
            let window_end = (window + 1) * 60_000;
            #[expect(clippy::cast_precision_loss, reason = "under a minute in ms")]
            let seconds = window_end.saturating_sub(now) as f64 / 1000.0;
            for client in over {
                limiter.block(&client, seconds);
            }
        }
    }
}

/// The peer layer of this process; dropping it leaves the cluster.
pub struct Node {
    shared: Arc<Shared>,
    commands: mpsc::UnboundedSender<Command>,
    thread: Mutex<Option<JoinHandle<()>>>,
    waker: UnixStream,
}

impl Node {
    /// Bind the sockets and start the background thread.
    ///
    /// # Errors
    ///
    /// When no port of `config.ports` is free, or the OS refuses sockets.
    pub fn start(config: Config) -> std::io::Result<Self> {
        let (listener, udp, port) = bind(config.bind, &config.ports)?;
        let ip = config.advertise.unwrap_or_else(default_route_ip);
        let me = PeerId {
            addr: SocketAddr::new(ip, port),
            generation: unix_ms(),
            refresher: config.refresher,
        };
        let (waker, wake) = UnixStream::pair()?;
        waker.set_nonblocking(true)?;
        wake.set_nonblocking(true)?;
        let shared = Arc::new(Shared {
            me,
            sealer: Sealer::new(&config.secret, config.encrypt),
            allow: config.allow,
            cache: config.cache,
            ask_timeout: config.ask_timeout,
            view: RwLock::new(Vec::new()),
            avoid: Mutex::new(FxHashMap::default()),
            events: Mutex::new(Vec::new()),
            wake,
            woken: AtomicBool::new(false),
            next_token: AtomicU64::new(1),
            serving: Mutex::new(FxHashMap::default()),
            counts: Mutex::new(WindowCounts::default()),
            limiter: OnceLock::new(),
            pool: Mutex::new(FxHashMap::default()),
            inbound: AtomicU64::new(0),
            stats: Stats::default(),
        });
        let (commands, receiver) = mpsc::unbounded_channel();
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_io()
            .enable_time()
            .build()?;
        let seeds = Seeds {
            entries: config.seeds,
            ports: config.seed_ports,
        };
        let state = Arc::clone(&shared);
        let thread = std::thread::Builder::new()
            .name("telegram-me-peer".into())
            .spawn(move || {
                runtime.block_on(run(state, receiver, listener, udp, seeds));
            })?;
        Ok(Self {
            shared,
            commands,
            thread: Mutex::new(Some(thread)),
            waker,
        })
    }

    /// Our identity: `ip:port`.
    #[must_use]
    pub fn addr(&self) -> SocketAddr {
        self.shared.me.addr
    }

    /// The live members other than us.
    #[must_use]
    pub fn members(&self) -> Vec<SocketAddr> {
        self.shared
            .view
            .read()
            .unwrap_or_else(PoisonError::into_inner)
            .iter()
            .map(|id| id.addr)
            .collect()
    }

    /// The node that owns `route`: `None` when it is us (or we are alone).
    #[must_use]
    pub fn owner(&self, route: &str) -> Option<SocketAddr> {
        let reachable = self.shared.reachable();
        if reachable.is_empty() {
            return None;
        }
        let names: Vec<String> = reachable.iter().map(|id| id.addr.to_string()).collect();
        let me = self.shared.me.addr.to_string();
        let owner = hash::owner(names.iter().map(String::as_str).chain([me.as_str()]), route)?;
        names
            .iter()
            .position(|name| name == owner)
            .map(|index| reachable[index].addr)
    }

    /// Whether this process should run the feed refresher: the live node
    /// that may refresh with the lowest address.
    #[must_use]
    pub fn is_leader(&self) -> bool {
        let me = &self.shared.me;
        me.refresher
            && self
                .shared
                .reachable()
                .iter()
                .filter(|id| id.refresher)
                .all(|id| id.addr > me.addr)
    }

    /// Ask `to` for the cached entry of `key`; answered with [`Event::Got`].
    #[must_use]
    pub fn get(&self, to: SocketAddr, key: String) -> u64 {
        let token = self.shared.token();
        self.command(Command::Get { token, to, key }, || Event::Got {
            token,
            found: None,
        });
        token
    }

    /// Ask `to` to load a key, waiting up to `wait`; answered with
    /// [`Event::Loaded`].
    #[must_use]
    pub fn load(&self, to: SocketAddr, request: LoadRequest, wait: Duration) -> u64 {
        let token = self.shared.token();
        let command = Command::Load {
            token,
            to,
            request,
            wait,
        };
        self.command(command, || Event::Loaded {
            token,
            outcome: Outcome::Unavailable,
        });
        token
    }

    fn command(&self, command: Command, stopped: impl FnOnce() -> Event) {
        if self.commands.send(command).is_err() {
            self.shared.push(stopped());
        }
    }

    /// Answer a [`Event::Serve`]: send the cached entries of `keys` (the
    /// requested key first) to the node that asked.
    pub fn served(&self, token: u64, keys: &[String]) {
        let Some(serving) = lock(&self.shared.serving).remove(&token) else {
            return;
        };
        let entries = self.shared.entries(keys);
        let message = if entries
            .first()
            .is_some_and(|entry| Some(&entry.key) == keys.first())
        {
            Message::Entries {
                id: serving.id,
                entries: entries.into_iter().take(MAX_ITEMS).collect(),
            }
        } else {
            // Evicted already: the asker does the work itself.
            Message::Failed {
                id: serving.id,
                code: 0,
                message: String::new(),
            }
        };
        drop(
            serving
                .reply
                .send(self.shared.sealer.seal(&message.encode())),
        );
    }

    /// Answer a [`Event::Serve`] with an error of class `code`.
    pub fn serve_failed(&self, token: u64, code: u8, message: String) {
        let Some(serving) = lock(&self.shared.serving).remove(&token) else {
            return;
        };
        let failed = Message::Failed {
            id: serving.id,
            code,
            message,
        };
        drop(
            serving
                .reply
                .send(self.shared.sealer.seal(&failed.encode())),
        );
    }

    /// Count requests per client across nodes: blocks clients over `limit`
    /// (here and, as the counts arrive, on every other node).
    pub fn attach_rate_limiter(&self, limiter: Arc<RateLimiter>, limit: u32) {
        // Attached once per process; a second limiter would be a bug upstream.
        drop(self.shared.limiter.set((limiter, limit)));
    }

    /// Add our accepted requests per client and send them to every node.
    pub fn share_counts(&self, items: &[(String, u32)]) {
        if items.is_empty() {
            return;
        }
        let window = unix_ms() / 60_000;
        self.shared.add_counts(window, items);
        for chunk in items.chunks(MAX_COUNTS) {
            let message = Message::Counts {
                window,
                items: chunk.to_vec(),
            };
            drop(self.commands.send(Command::Send(message)));
        }
    }

    /// Send a message to every other node, best effort.
    pub fn cast(&self, topic: String, data: Vec<u8>) {
        drop(
            self.commands
                .send(Command::Send(Message::Cast { topic, data })),
        );
    }

    /// More places to look for other nodes (for example from Redis).
    pub fn add_seeds(&self, seeds: Vec<String>) {
        drop(self.commands.send(Command::Seeds(seeds)));
    }

    /// The events since the last call (drains the wake-up socket as well).
    pub fn poll(&self) -> Vec<Event> {
        let mut buffer = [0; 64];
        while matches!((&self.waker).read(&mut buffer), Ok(n) if n > 0) {}
        self.shared.woken.store(false, Ordering::Release);
        std::mem::take(&mut *lock(&self.shared.events))
    }

    /// The socket that becomes readable when [`Self::poll`] has events.
    #[must_use]
    pub fn waker(&self) -> &UnixStream {
        &self.waker
    }

    /// Counters.
    #[must_use]
    pub fn stats(&self) -> &Stats {
        &self.shared.stats
    }

    /// Leave the cluster and stop the thread.
    pub fn close(&self) {
        // The thread may have stopped already: then there is no one to tell.
        drop(self.commands.send(Command::Stop));
        let thread = lock(&self.thread).take();
        if let Some(thread) = thread {
            // A panic aborts the process (panic = "abort"): nothing to report.
            drop(thread.join());
        }
    }
}

impl Drop for Node {
    fn drop(&mut self) {
        self.close();
    }
}

/// The address of the default route: what other hosts most likely reach us
/// at. Connecting a UDP socket sends nothing; it only picks the route.
fn default_route_ip() -> IpAddr {
    StdUdpSocket::bind((Ipv4Addr::UNSPECIFIED, 0))
        .and_then(|socket| {
            socket.connect((Ipv4Addr::new(192, 0, 2, 1), 9))?;
            socket.local_addr()
        })
        .map(|addr| addr.ip())
        .ok()
        .filter(|ip| !ip.is_unspecified())
        .unwrap_or(IpAddr::V4(Ipv4Addr::LOCALHOST))
}

fn bind(ip: IpAddr, ports: &[u16]) -> std::io::Result<(std::net::TcpListener, StdUdpSocket, u16)> {
    let mut last = std::io::Error::new(std::io::ErrorKind::AddrInUse, "no peer port to try");
    for &port in ports {
        // With port 0 the UDP socket takes the port the OS gave TCP; retry
        // the rare case where it is taken for UDP.
        for _ in 0..if port == 0 { 8 } else { 1 } {
            let attempt = std::net::TcpListener::bind((ip, port)).and_then(|listener| {
                let bound = listener.local_addr()?.port();
                let udp = StdUdpSocket::bind((ip, bound))?;
                Ok((listener, udp, bound))
            });
            match attempt {
                Ok((listener, udp, bound)) => {
                    listener.set_nonblocking(true)?;
                    udp.set_nonblocking(true)?;
                    return Ok((listener, udp, bound));
                }
                Err(error) => last = error,
            }
        }
    }
    Err(last)
}

struct Seeds {
    entries: Vec<String>,
    ports: Vec<u16>,
}

impl Seeds {
    /// Resolve every entry (DNS names again each time).
    async fn resolve(entries: Vec<String>, ports: Vec<u16>) -> Vec<SocketAddr> {
        let mut found = Vec::new();
        for entry in entries {
            let entry = entry.trim().to_owned();
            if entry.is_empty() {
                continue;
            }
            if let Ok(addr) = entry.parse::<SocketAddr>() {
                found.push(addr);
                continue;
            }
            let (host, explicit) = match entry.rsplit_once(':') {
                Some((host, port)) if !host.contains(':') => match port.parse::<u16>() {
                    Ok(port) => (host.trim_matches(['[', ']']).to_owned(), Some(port)),
                    Err(_) => continue,
                },
                _ => (entry.trim_matches(['[', ']']).to_owned(), None),
            };
            let ports: Vec<u16> = explicit.map_or_else(|| ports.clone(), |port| vec![port]);
            let ips: Vec<IpAddr> = if let Ok(ip) = host.parse::<IpAddr>() {
                vec![ip]
            } else {
                match tokio::net::lookup_host((host.as_str(), 0)).await {
                    Ok(addrs) => addrs.map(|addr| addr.ip()).collect(),
                    Err(_) => continue,
                }
            };
            for ip in ips {
                found.extend(ports.iter().map(|port| SocketAddr::new(ip, *port)));
            }
        }
        found.sort();
        found.dedup();
        found
    }
}

#[expect(
    clippy::too_many_lines,
    reason = "one select loop owning the membership"
)]
async fn run(
    shared: Arc<Shared>,
    mut commands: mpsc::UnboundedReceiver<Command>,
    listener: std::net::TcpListener,
    udp: StdUdpSocket,
    mut seeds: Seeds,
) {
    let (Ok(listener), Ok(udp)) = (TcpListener::from_std(listener), UdpSocket::from_std(udp))
    else {
        return;
    };
    let mut membership = Membership::new(shared.me.clone(), shared.me.generation);
    let (timers, mut fired) = mpsc::unbounded_channel();
    let (resolved_tx, mut resolved) = mpsc::unbounded_channel::<Vec<SocketAddr>>();
    let mut buffer = vec![0; MAX_GOSSIP + 512];
    let mut next_seeding = tokio::time::Instant::now();

    let flush = |membership: &mut Membership| {
        let outbox = membership.drain();
        for (to, data) in outbox.sends {
            let frame = shared.sealer.seal(&Message::Gossip(data).encode());
            // Gossip tolerates loss; never wait for a full socket buffer.
            drop(udp.try_send_to(&frame, to));
        }
        for (timer, after) in outbox.timers {
            let timers = timers.clone();
            tokio::spawn(async move {
                tokio::time::sleep(after).await;
                // False once the loop has stopped: the timer has nobody to wake.
                timers.send(timer).is_ok()
            });
        }
        if outbox.changed {
            let members = membership.members();
            let changed = {
                let mut view = shared.view.write().unwrap_or_else(PoisonError::into_inner);
                let changed = *view != members;
                *view = members;
                changed
            };
            if changed {
                shared.push(Event::Members);
            }
        }
    };

    loop {
        tokio::select! {
            command = commands.recv() => match command {
                None | Some(Command::Stop) => {
                    membership.leave();
                    flush(&mut membership);
                    return;
                }
                Some(Command::Get { token, to, key }) => {
                    tokio::spawn(ask_get(Arc::clone(&shared), token, to, key));
                }
                Some(Command::Load { token, to, request, wait }) => {
                    tokio::spawn(ask_load(Arc::clone(&shared), token, to, request, wait));
                }
                Some(Command::Send(message)) => {
                    let frame: Arc<[u8]> = Arc::from(shared.sealer.seal(&message.encode()));
                    for id in shared.reachable() {
                        tokio::spawn(send_one_way(Arc::clone(&shared), id.addr, Arc::clone(&frame)));
                    }
                }
                Some(Command::Seeds(more)) => {
                    for entry in more {
                        if !seeds.entries.contains(&entry) {
                            seeds.entries.push(entry);
                        }
                    }
                    next_seeding = tokio::time::Instant::now();
                }
            },
            received = udp.recv_from(&mut buffer) => {
                let Ok((len, from)) = received else { continue };
                if !shared.allow.allows(from.ip()) {
                    shared.stats.refused.fetch_add(1, Ordering::Relaxed);
                    continue;
                }
                match shared.sealer.open(&buffer[..len]).map(|payload| Message::decode(&payload)) {
                    Ok(Ok(Message::Gossip(data))) => {
                        // Refused gossip (stale, from ourselves) changes nothing.
                        drop(membership.handle_data(&data));
                        flush(&mut membership);
                    }
                    Ok(_) => {}
                    Err(_) => {
                        shared.stats.rejected.fetch_add(1, Ordering::Relaxed);
                    }
                }
            },
            Some(timer) = fired.recv() => {
                membership.handle_timer(timer);
                flush(&mut membership);
            },
            () = tokio::time::sleep_until(next_seeding) => {
                let alone = membership.members().is_empty();
                next_seeding = tokio::time::Instant::now() + if alone { SEED_EARLY } else { SEED_LATER };
                let (entries, ports, resolved_tx) =
                    (seeds.entries.clone(), seeds.ports.clone(), resolved_tx.clone());
                tokio::spawn(async move {
                    drop(resolved_tx.send(Seeds::resolve(entries, ports).await));
                });
            },
            Some(addrs) = resolved.recv() => {
                for addr in addrs {
                    if addr != shared.me.addr && !membership.knows(addr) {
                        membership.announce(addr);
                    }
                }
                flush(&mut membership);
            },
            accepted = listener.accept() => {
                let Ok((stream, from)) = accepted else { continue };
                if !shared.allow.allows(from.ip()) {
                    shared.stats.refused.fetch_add(1, Ordering::Relaxed);
                    continue;
                }
                if shared.inbound.load(Ordering::Relaxed) >= MAX_INBOUND as u64 {
                    continue;
                }
                tokio::spawn(serve(Arc::clone(&shared), stream));
            },
        }
    }
}

#[derive(Debug)]
enum ConnError {
    Io,
    Refused,
}

impl From<std::io::Error> for ConnError {
    fn from(_: std::io::Error) -> Self {
        Self::Io
    }
}

impl From<Reject> for ConnError {
    fn from(_: Reject) -> Self {
        Self::Refused
    }
}

async fn read_frame(reader: &mut OwnedReadHalf, sealer: &Sealer) -> Result<Vec<u8>, ConnError> {
    let mut header = [0; HEADER_LEN];
    reader.read_exact(&mut header).await?;
    let len = sealer.body_len(&header)?;
    let mut frame = vec![0; HEADER_LEN + len];
    frame[..HEADER_LEN].copy_from_slice(&header);
    reader.read_exact(&mut frame[HEADER_LEN..]).await?;
    Ok(sealer.open(&frame)?)
}

async fn write_frames(mut writer: OwnedWriteHalf, mut frames: mpsc::UnboundedReceiver<Vec<u8>>) {
    while let Some(frame) = frames.recv().await {
        if writer.write_all(&frame).await.is_err() {
            return;
        }
    }
}

/// One connection another node opened to us.
async fn serve(shared: Arc<Shared>, stream: TcpStream) {
    shared.inbound.fetch_add(1, Ordering::Relaxed);
    drop(stream.set_nodelay(true));
    let (mut reader, writer) = stream.into_split();
    let (reply, frames) = mpsc::unbounded_channel();
    let writing = tokio::spawn(write_frames(writer, frames));
    loop {
        let message = match read_frame(&mut reader, &shared.sealer).await {
            Ok(payload) => match Message::decode(&payload) {
                Ok(message) => message,
                Err(_) => break,
            },
            Err(error) => {
                if matches!(error, ConnError::Refused) {
                    shared.stats.rejected.fetch_add(1, Ordering::Relaxed);
                }
                break;
            }
        };
        match message {
            Message::Get { id, keys } => {
                shared.stats.served_gets.fetch_add(1, Ordering::Relaxed);
                let entries = shared.entries(&keys);
                let answer = Message::Entries { id, entries };
                drop(reply.send(shared.sealer.seal(&answer.encode())));
            }
            Message::Load {
                id,
                key,
                newer_than,
                wait_ms: _,
                background,
            } => {
                shared.stats.served_loads.fetch_add(1, Ordering::Relaxed);
                let token = shared.token();
                let admitted = {
                    let mut serving = lock(&shared.serving);
                    (serving.len() < MAX_SERVING)
                        .then(|| {
                            serving.insert(
                                token,
                                Serving {
                                    reply: reply.clone(),
                                    id,
                                },
                            )
                        })
                        .is_some()
                };
                let answer = if admitted {
                    Message::Accepted { id }
                } else {
                    Message::Failed {
                        id,
                        code: 0,
                        message: String::new(),
                    }
                };
                drop(reply.send(shared.sealer.seal(&answer.encode())));
                if admitted {
                    let request = LoadRequest {
                        key,
                        newer_than,
                        background,
                    };
                    shared.push(Event::Serve { token, request });
                }
            }
            Message::Counts { window, items } => shared.add_counts(window, &items),
            Message::Cast { topic, data } => shared.push(Event::Cast { topic, data }),
            // Answers and gossip have no business on this connection.
            _ => break,
        }
    }
    drop(reply);
    // Answers to loads in flight still go out; the writer ends once they did.
    drop(writing.await);
    shared.inbound.fetch_sub(1, Ordering::Relaxed);
}

/// The connection to one peer, opened by the first request that needs it.
type Slot = tokio::sync::Mutex<Option<Arc<Conn>>>;

/// Our connection to another node: requests out, answers matched by id.
struct Conn {
    frames: mpsc::UnboundedSender<Vec<u8>>,
    waiting: Mutex<FxHashMap<u64, mpsc::Sender<Message>>>,
    next_id: AtomicU64,
    closed: AtomicBool,
}

impl Conn {
    fn request(&self, sealer: &Sealer, build: impl FnOnce(u64) -> Message) -> Pending<'_> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let (sender, answers) = mpsc::channel(2);
        lock(&self.waiting).insert(id, sender);
        // A closed connection ends the request's answers: the caller sees that.
        drop(self.frames.send(sealer.seal(&build(id).encode())));
        Pending {
            conn: self,
            id,
            answers,
        }
    }
}

/// Answers to one request; forgets the request when dropped.
struct Pending<'a> {
    conn: &'a Conn,
    id: u64,
    answers: mpsc::Receiver<Message>,
}

impl Drop for Pending<'_> {
    fn drop(&mut self) {
        lock(&self.conn.waiting).remove(&self.id);
    }
}

async fn read_answers(shared: Arc<Shared>, conn: Arc<Conn>, mut reader: OwnedReadHalf) {
    loop {
        let Ok(Ok(message)) = read_frame(&mut reader, &shared.sealer)
            .await
            .map(|payload| Message::decode(&payload))
        else {
            break;
        };
        let id = match &message {
            Message::Entries { id, .. } | Message::Accepted { id } | Message::Failed { id, .. } => {
                *id
            }
            _ => break,
        };
        let sender = lock(&conn.waiting).get(&id).cloned();
        if let Some(sender) = sender {
            // At most two answers per request; a full channel is a duplicate.
            drop(sender.try_send(message));
        }
    }
    conn.closed.store(true, Ordering::Release);
    // Waiters see their channel close: no answer is coming.
    lock(&conn.waiting).clear();
}

async fn connection(shared: &Arc<Shared>, to: SocketAddr) -> Option<Arc<Conn>> {
    let slot = Arc::clone(lock(&shared.pool).entry(to).or_default());
    let mut slot = slot.lock().await;
    if let Some(conn) = slot
        .as_ref()
        .filter(|conn| !conn.closed.load(Ordering::Acquire))
    {
        return Some(Arc::clone(conn));
    }
    let stream = tokio::time::timeout(shared.ask_timeout, TcpStream::connect(to))
        .await
        .ok()?
        .ok()?;
    drop(stream.set_nodelay(true));
    let (reader, writer) = stream.into_split();
    let (frames, outgoing) = mpsc::unbounded_channel();
    let conn = Arc::new(Conn {
        frames,
        waiting: Mutex::new(FxHashMap::default()),
        next_id: AtomicU64::new(1),
        closed: AtomicBool::new(false),
    });
    tokio::spawn(write_frames(writer, outgoing));
    tokio::spawn(read_answers(Arc::clone(shared), Arc::clone(&conn), reader));
    *slot = Some(Arc::clone(&conn));
    // Held while connecting: concurrent requests to `to` share this connection.
    drop(slot);
    Some(conn)
}

async fn ask_get(shared: Arc<Shared>, token: u64, to: SocketAddr, key: String) {
    shared.stats.asked.fetch_add(1, Ordering::Relaxed);
    let answer = tokio::time::timeout(shared.ask_timeout, async {
        let conn = connection(&shared, to).await?;
        let mut pending = conn.request(&shared.sealer, |id| Message::Get {
            id,
            keys: vec![key.clone()],
        });
        pending.answers.recv().await
    })
    .await;
    let found = if let Ok(Some(Message::Entries { entries, .. })) = answer {
        shared.keep(entries, &key)
    } else {
        shared.stats.unanswered.fetch_add(1, Ordering::Relaxed);
        shared.avoid(to);
        None
    };
    shared.push(Event::Got { token, found });
}

async fn ask_load(
    shared: Arc<Shared>,
    token: u64,
    to: SocketAddr,
    request: LoadRequest,
    wait: Duration,
) {
    shared.stats.asked.fetch_add(1, Ordering::Relaxed);
    let outcome = load(&shared, to, request, wait).await;
    if outcome == Outcome::Unavailable {
        shared.stats.unanswered.fetch_add(1, Ordering::Relaxed);
    }
    shared.push(Event::Loaded { token, outcome });
}

async fn load(
    shared: &Arc<Shared>,
    to: SocketAddr,
    request: LoadRequest,
    wait: Duration,
) -> Outcome {
    let LoadRequest {
        key,
        newer_than,
        background,
    } = request;
    let started = tokio::time::Instant::now();
    let Ok(Some(conn)) = tokio::time::timeout(shared.ask_timeout, connection(shared, to)).await
    else {
        shared.avoid(to);
        return Outcome::Unavailable;
    };
    let mut pending = conn.request(&shared.sealer, |id| Message::Load {
        id,
        key: key.clone(),
        newer_than,
        wait_ms: u32::try_from(wait.as_millis()).unwrap_or(u32::MAX),
        background,
    });
    // The owner accepts at once; one that does not is down or stuck.
    let first = tokio::time::timeout(shared.ask_timeout, pending.answers.recv()).await;
    let answer = match first {
        Ok(Some(Message::Accepted { .. })) => {
            let left = wait.saturating_sub(started.elapsed());
            match tokio::time::timeout(left, pending.answers.recv()).await {
                Ok(Some(answer)) => answer,
                _ => return Outcome::Unavailable,
            }
        }
        Ok(Some(answer)) => answer,
        _ => {
            shared.avoid(to);
            return Outcome::Unavailable;
        }
    };
    match answer {
        Message::Entries { entries, .. } => match shared.keep(entries, &key) {
            Some((value, stored_at)) => Outcome::Value(value, stored_at),
            None => Outcome::Unavailable,
        },
        Message::Failed { code: 0, .. } => Outcome::Unavailable,
        Message::Failed { code, message, .. } => Outcome::Failed(code, message),
        _ => Outcome::Unavailable,
    }
}

async fn send_one_way(shared: Arc<Shared>, to: SocketAddr, frame: Arc<[u8]>) {
    if let Ok(Some(conn)) = tokio::time::timeout(shared.ask_timeout, connection(&shared, to)).await
    {
        drop(conn.frames.send(frame.to_vec()));
    }
}
