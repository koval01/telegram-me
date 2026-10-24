//! Peer nodes over real sockets on localhost.

use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::sync::Arc;
use std::time::{Duration, Instant};

use telegram_me_native::cache::LocalCache;
use telegram_me_native::peer::allow::AllowList;
use telegram_me_native::peer::node::{Config, Event, LoadRequest, Node, Outcome};
use telegram_me_native::ratelimit::RateLimiter;

const LOCALHOST: IpAddr = IpAddr::V4(Ipv4Addr::LOCALHOST);

fn secret() -> Vec<u8> {
    let mut secret = vec![0; 32];
    getrandom::fill(&mut secret).unwrap();
    secret
}

struct Options {
    secret: Vec<u8>,
    seeds: Vec<String>,
    allow: &'static str,
    encrypt: bool,
}

fn node(options: Options) -> (Node, LocalCache) {
    let cache = LocalCache::new(16 << 20);
    let node = Node::start(Config {
        ports: vec![0],
        bind: LOCALHOST,
        advertise: Some(LOCALHOST),
        seeds: options.seeds,
        seed_ports: vec![],
        allow: AllowList::parse([options.allow]).unwrap(),
        secret: options.secret,
        encrypt: options.encrypt,
        refresher: true,
        ask_timeout: Duration::from_millis(200),
        cache: cache.clone(),
    })
    .unwrap();
    (node, cache)
}

fn pair(encrypt: bool) -> ((Node, LocalCache), (Node, LocalCache)) {
    let secret = secret();
    let first = node(Options {
        secret: secret.clone(),
        seeds: vec![],
        allow: "127.0.0.0/8",
        encrypt,
    });
    let second = node(Options {
        secret,
        seeds: vec![first.0.addr().to_string()],
        allow: "127.0.0.0/8",
        encrypt,
    });
    wait_until(|| first.0.members() == [second.0.addr()] && second.0.members() == [first.0.addr()]);
    (first, second)
}

fn wait_until(mut done: impl FnMut() -> bool) {
    let deadline = Instant::now() + Duration::from_secs(10);
    while !done() {
        assert!(Instant::now() < deadline, "timed out");
        std::thread::sleep(Duration::from_millis(10));
    }
}

/// The next event matching `select`, polling `node`.
fn next<T>(node: &Node, mut select: impl FnMut(Event) -> Option<T>) -> T {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        for event in node.poll() {
            if let Some(found) = select(event) {
                return found;
            }
        }
        assert!(Instant::now() < deadline, "no such event");
        std::thread::sleep(Duration::from_millis(5));
    }
}

fn request(key: &str, newer_than: f64) -> LoadRequest {
    LoadRequest {
        key: key.into(),
        newer_than,
        background: false,
    }
}

fn got(node: &Node, token: u64) -> Option<(Vec<u8>, f64)> {
    next(node, |event| match event {
        Event::Got { token: t, found } if t == token => Some(found),
        _ => None,
    })
}

fn loaded(node: &Node, token: u64) -> Outcome {
    next(node, |event| match event {
        Event::Loaded { token: t, outcome } if t == token => Some(outcome),
        _ => None,
    })
}

#[test]
fn nodes_join_and_agree_on_owners_and_leader() {
    let ((a, _), (b, _)) = pair(false);
    for key in ["durov", "telegram", "tginfo", "nebudetgg", "stranaua"] {
        // Each node names the other or itself (None): never both themselves.
        let (from_a, from_b) = (a.owner(key), b.owner(key));
        assert!(
            (from_a == Some(b.addr()) && from_b.is_none())
                || (from_b == Some(a.addr()) && from_a.is_none()),
            "{key}: {from_a:?} {from_b:?}"
        );
    }
    let leaders = [a.is_leader(), b.is_leader()];
    assert_eq!(leaders, [a.addr() < b.addr(), b.addr() < a.addr()]);
}

#[test]
fn a_get_is_answered_from_the_owners_cache_and_fills_the_askers() {
    for encrypt in [false, true] {
        let ((a, a_cache), (b, b_cache)) = pair(encrypt);
        let value = br#"{"channel":"durov"}"#.repeat(50);
        a_cache.insert("page:full:durov:0", &value, 30.0, 1_790_000_000.5);

        let token = b.get(a.addr(), "page:full:durov:0".into());
        assert_eq!(got(&b, token), Some((value.clone(), 1_790_000_000.5)));
        let copy = b_cache.get("page:full:durov:0").unwrap();
        assert_eq!(copy.value(), value);
        assert!(copy.remaining() > Duration::from_secs(25));
        assert!(copy.remaining() <= Duration::from_secs(30));

        let token = b.get(a.addr(), "page:full:missing:0".into());
        assert_eq!(got(&b, token), None);
        assert_eq!(
            a.stats()
                .served_gets
                .load(std::sync::atomic::Ordering::Relaxed),
            2
        );
    }
}

#[test]
fn a_load_is_served_by_the_owner_with_its_companions() {
    let ((a, a_cache), (b, b_cache)) = pair(false);
    let background = LoadRequest {
        background: true,
        ..request("page:full:durov:0", 12.5)
    };
    let token = b.load(a.addr(), background, Duration::from_secs(5));
    let (serve, request) = next(&a, |event| match event {
        Event::Serve { token, request } => Some((token, request)),
        _ => None,
    });
    assert_eq!(
        request,
        LoadRequest {
            key: "page:full:durov:0".into(),
            newer_than: 12.5,
            background: true,
        }
    );
    // What Python does: load, write the cache, then answer with the keys.
    a_cache.insert("page:full:durov:0", b"page", 900.0, 100.0);
    a_cache.insert("json:body:page:full:durov:0", b"body", 10.0, 100.0);
    a.served(
        serve,
        &[
            "page:full:durov:0".into(),
            "json:body:page:full:durov:0".into(),
        ],
    );
    assert_eq!(loaded(&b, token), Outcome::Value(b"page".to_vec(), 100.0));
    assert_eq!(
        b_cache.get("json:body:page:full:durov:0").unwrap().value(),
        b"body"
    );
}

#[test]
fn failed_and_evicted_loads_tell_the_asker() {
    let ((a, _), (b, _)) = pair(false);
    let serve = |a: &Node| {
        next(a, |event| match event {
            Event::Serve { token, .. } => Some(token),
            _ => None,
        })
    };
    let token = b.load(
        a.addr(),
        request("preview:nobody", 0.0),
        Duration::from_secs(5),
    );
    a.serve_failed(serve(&a), 1, "not found".into());
    assert_eq!(loaded(&b, token), Outcome::Failed(1, "not found".into()));

    // The value is gone from the owner's cache by the time it answers.
    let token = b.load(
        a.addr(),
        request("preview:gone", 0.0),
        Duration::from_secs(5),
    );
    a.served(serve(&a), &["preview:gone".into()]);
    assert_eq!(loaded(&b, token), Outcome::Unavailable);
}

#[test]
fn a_slow_owner_runs_out_the_askers_wait() {
    let ((a, _), (b, _)) = pair(false);
    let started = Instant::now();
    let token = b.load(
        a.addr(),
        request("page:full:slow:0", 0.0),
        Duration::from_millis(300),
    );
    assert_eq!(loaded(&b, token), Outcome::Unavailable);
    assert!(started.elapsed() < Duration::from_secs(2));
    // Accepted in time: the owner is fine, only slow, so it is not avoided.
    assert_eq!(b.owner("x").is_some(), a.owner("x").is_none());
}

#[test]
fn an_unreachable_peer_answers_unavailable_fast_and_is_avoided() {
    let ((a, _), (b, _)) = pair(false);
    let key = [
        "durov",
        "telegram",
        "tginfo",
        "nebudetgg",
        "stranaua",
        "a",
        "b",
    ]
    .into_iter()
    .find(|key| b.owner(key) == Some(a.addr()))
    .unwrap();
    let a_addr = a.addr();
    drop(a);
    let started = Instant::now();
    let token = b.load(
        a_addr,
        request("page:full:x:0", 0.0),
        Duration::from_secs(5),
    );
    assert_eq!(loaded(&b, token), Outcome::Unavailable);
    let token = b.get(a_addr, "page:full:x:0".into());
    assert_eq!(got(&b, token), None);
    assert!(started.elapsed() < Duration::from_secs(1));
    // Left out of ownership (a leaving node is also gone from the members).
    assert_eq!(b.owner(key), None);
}

#[test]
fn another_secret_or_mode_is_refused() {
    let ((a, a_cache), (_b, _)) = pair(false);
    a_cache.insert("k", b"secret value", 30.0, 1.0);
    for encrypt in [false, true] {
        let (stranger, _) = node(Options {
            secret: secret(),
            seeds: vec![a.addr().to_string()],
            allow: "127.0.0.0/8",
            encrypt,
        });
        let token = stranger.get(a.addr(), "k".into());
        assert_eq!(got(&stranger, token), None);
        std::thread::sleep(Duration::from_millis(300));
        assert_eq!(stranger.members(), Vec::<SocketAddr>::new());
        assert_eq!(a.members().len(), 1);
    }
    assert!(
        a.stats()
            .rejected
            .load(std::sync::atomic::Ordering::Relaxed)
            > 0
    );
}

#[test]
fn addresses_outside_the_allowed_networks_are_refused() {
    let secret = secret();
    let (fenced, cache) = node(Options {
        secret: secret.clone(),
        seeds: vec![],
        allow: "10.0.0.0/16",
        encrypt: false,
    });
    cache.insert("k", b"v", 30.0, 1.0);
    let (outsider, _) = node(Options {
        secret,
        seeds: vec![fenced.addr().to_string()],
        allow: "127.0.0.0/8",
        encrypt: false,
    });
    let token = outsider.get(fenced.addr(), "k".into());
    assert_eq!(got(&outsider, token), None);
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(fenced.members(), Vec::<SocketAddr>::new());
    assert!(
        fenced
            .stats()
            .refused
            .load(std::sync::atomic::Ordering::Relaxed)
            > 0
    );
}

#[test]
fn rate_limit_counts_add_up_across_nodes() {
    let ((a, _), (b, _)) = pair(false);
    let limiters = [
        Arc::new(RateLimiter::new(60, 5)),
        Arc::new(RateLimiter::new(60, 5)),
    ];
    a.attach_rate_limiter(Arc::clone(&limiters[0]), 5);
    b.attach_rate_limiter(Arc::clone(&limiters[1]), 5);
    // Four requests on each node: within the limit of each, not of both.
    a.share_counts(&[("203.0.113.7".into(), 4)]);
    b.share_counts(&[("203.0.113.7".into(), 4)]);
    wait_until(|| {
        limiters
            .iter()
            .all(|limiter| !limiter.check("203.0.113.7", 1).allowed)
    });
    assert!(limiters[0].check("198.51.100.1", 1).allowed);
}

#[test]
fn casts_reach_every_other_node() {
    let ((a, _), (b, _)) = pair(false);
    a.cast("hits".into(), b"durov\t2".to_vec());
    let data = next(&b, |event| match event {
        Event::Cast { topic, data } if topic == "hits" => Some(data),
        _ => None,
    });
    assert_eq!(data, b"durov\t2");
}

#[test]
fn seeds_may_be_names_and_ports_lists() {
    let secret = secret();
    let (first, _) = node(Options {
        secret: secret.clone(),
        seeds: vec![],
        allow: "127.0.0.0/8",
        encrypt: false,
    });
    let port = first.addr().port();
    let cache = LocalCache::new(1 << 20);
    let second = Node::start(Config {
        ports: vec![0],
        bind: LOCALHOST,
        advertise: Some(LOCALHOST),
        seeds: vec!["localhost".into(), "  ".into(), "bad:port".into()],
        seed_ports: vec![port.wrapping_sub(1), port],
        allow: AllowList::parse(["127.0.0.0/8", "::1/128"]).unwrap(),
        secret,
        encrypt: false,
        refresher: false,
        ask_timeout: Duration::from_millis(200),
        cache,
    })
    .unwrap();
    wait_until(|| second.members() == [first.addr()]);
    // A node that may not refresh never leads.
    assert!(!second.is_leader());
    assert!(first.is_leader());
}

#[test]
fn busy_ports_are_skipped_and_exhausted_ranges_fail() {
    let taken = std::net::TcpListener::bind((LOCALHOST, 0)).unwrap();
    let busy = taken.local_addr().unwrap().port();
    let config = |ports: Vec<u16>| Config {
        ports,
        bind: LOCALHOST,
        advertise: None,
        seeds: vec![],
        seed_ports: vec![],
        allow: AllowList::default(),
        secret: secret(),
        encrypt: false,
        refresher: true,
        ask_timeout: Duration::from_millis(50),
        cache: LocalCache::new(1 << 20),
    };
    assert!(Node::start(config(vec![busy])).is_err());
    let node = Node::start(config(vec![busy, 0])).unwrap();
    assert_ne!(node.addr().port(), busy);
    // Without PEER_ADVERTISE_ADDR: the address of the default route.
    let _: SocketAddr = node.addr();
}
