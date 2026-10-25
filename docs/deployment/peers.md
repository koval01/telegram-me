# Peer layer

With several servers (or workers) on one private network, TelegramMe nodes can
share their work directly, without Redis or alongside it. Each cache key gets
one owner among the live nodes. Only the owner fetches the key from t.me and
renders it; the other nodes ask the owner and keep its answer. The layer is off
by default ([`ENABLE_PEERS`](../configuration.md#enable_peers)), and everything
works as before without it.

## How it works

- **Nodes.** Every worker process is a node. Nodes find each other through
  [`PEERS`](../configuration.md#peers) (addresses or DNS names) and, with
  Redis, through each other's heartbeat markers. They track who is alive with
  SWIM gossip over UDP.
- **Owners.** A key's owner is the live node that wins a rendezvous hash of
  the key's channel, so every key of one channel (pages, rendered bodies and
  posts, "more" fragments, the preview) has the same owner. Every node
  computes the same owner on its own; a node that leaves or joins moves only
  the channels it owns.
- **A miss.** On an in-process cache miss, a node asks the owner over TCP.
  The owner answers from its in-process cache directly in the native code,
  without Python. The asker keeps the copy for the entry's remaining TTL.
- **A load.** When the owner does not have the key either, the asker asks it
  to load the key. The owner joins any identical work in flight, fetches once,
  and sends the value (a page travels with its rendered body) or the error.
  With peers up, this replaces the Redis lease.
- **Coordination.** The feed refresher runs on the live node with the lowest
  address that may run it. Without Redis, nodes also send each other their
  rate-limit counts and feed popularity hits once a second.

## Turning it on

On every node:

```bash
ENABLE_PEERS=true
PEER_SECRET=<the same 64 hex characters everywhere>   # openssl rand -hex 32
PEERS=10.0.0.108,10.0.0.165
# In a container, the host's private address (the production deploy sets it):
PEER_ADVERTISE_ADDR=10.0.0.108
```

Each worker listens on the first free port from
[`PEER_PORT`](../configuration.md#peer_port) (7946) up, for UDP and TCP, so
open `PEER_PORT` to `PEER_PORT + WEB_CONCURRENCY` between the nodes.
The log shows `Peer layer on: this node is 10.0.0.108:7946` and then
`Peers: …` whenever the live members change.

## Discovery

The layer looks for other nodes in this order:

1. **`PEERS`**: addresses, `host:port` pairs or DNS names. Names are resolved
   again every 30 seconds (every 2 while no other node is known), so a name
   whose records list the servers keeps working as servers come and go.
2. **Redis**: with [`REDIS_URL`](../configuration.md#redis_url), each worker's
   heartbeat marker `tme:v2:node:<host>:<pid>` carries its peer address
   (`"peer": "10.0.0.108:7946"`), and every node reads the markers every
   10 seconds.

There is no multicast: cloud networks such as Oracle Cloud VCNs do not carry
it. One reachable node is enough to join; the rest is gossip.

## Security

- **Authentication.** Every frame (UDP datagram or TCP message) carries the
  sender's time, a unique nonce and an HMAC-SHA256 tag keyed from
  [`PEER_SECRET`](../configuration.md#peer_secret). A frame whose tag does not
  verify, whose time is more than 30 seconds off, or whose nonce was seen
  before is dropped, and nothing in it is parsed first.
- **Encryption.** With [`PEER_ENCRYPTION`](../configuration.md#peer_encryption)
  frames are encrypted with XChaCha20-Poly1305 instead. Without it, cached
  public channel data crosses the network in clear text, authenticated.
- **Networks.** Peers outside
  [`PEER_ALLOW_CIDR`](../configuration.md#peer_allow_cidr) are refused before
  a byte is read.
- **Bounds.** Frames are at most 8 MiB, keys 1 KiB, and the number of inbound
  connections and waiting loads is capped.

The secret is shared by the whole cluster: rotate it on every node at once.
Encryption uses a static key derived from it, so it offers no forward secrecy;
mutual TLS with certificates per node is not implemented.

## Firewall and Docker

`deploy/bootstrap-server.sh` opens `PEER_PORT` to `PEER_PORT + 15`, UDP and
TCP, in the host firewall for the private network only (`PEER_CIDR`, default
`10.0.0.0/16`). Oracle Cloud images reject every other port with "No route to
host"; the cloud security list must allow the ports between the servers too.

`deploy/compose.prod.yaml` publishes the peer ports of the API container on
`PEER_BIND`. With `ENABLE_PEERS=true`, `deploy/remote-deploy.sh` sets it, and
`PEER_ADVERTISE_ADDR`, to the server's private address (the source address of
its default route), and publishes one port per worker plus one.

The deploy workflow turns the layer on with the `ENABLE_PEERS` variable and
the `PEER_SECRET` secret; `PEERS`, `PEER_PORT`, `PEER_ALLOW_CIDR` and
`PEER_ENCRYPTION` are optional variables.

## When something fails

No peer failure fails a request; the asker does the work itself instead:

| Situation | What happens |
|---|---|
| The owner does not connect or answer a read within [`PEER_TIMEOUT`](../configuration.md#peer_timeout) | Redis is read, or the key loads locally; the owner is avoided for 3 s |
| The owner does not accept a load within `PEER_TIMEOUT` | The asker loads the key itself, with the Redis lease if there is Redis |
| The owner accepted a load and does not answer | The asker waits as long as for a Redis lease holder, then loads it itself |
| The owner crashed | Its port refuses at once; gossip declares it down within a few seconds |
| A node with another secret, or outside `PEER_ALLOW_CIDR` | Its frames are dropped and logged; it never joins |
| Two nodes disagree on the members for a moment | A key may be fetched twice, or two nodes refresh the feed; nothing else |

## Measurements

On one machine (Apple M-series, everything on localhost):

- **A miss answered by the owner** (a 56 KB cached page): 0.18 ms at the
  median, 0.35 ms at p99, encrypted or not. Through Redis 90 ms away it takes
  95 ms; against a local Redis, 0.6 ms.
- **Duplicate-heavy load** (`scripts/coalescing_load.py`, every endpoint of
  every recorded channel, t.me answering in 150 ms, 20 s):

| Nodes | Coordination | Fetches from t.me | p99 latency |
|---|---|---|---|
| 2 | none | 138 | 147 ms |
| 2 | Redis lease, Redis 90 ms away | 70 | 188 ms |
| 2 | peers, no Redis | 69 | 163 ms |
| 6 | none | 414 | 156 ms |
| 6 | Redis lease, Redis 90 ms away | 75 | 164 ms |
| 6 | peers, no Redis | 79 | 118 ms |

The 27 distinct upstream requests expire about every 10 s, so about 2.5
fetches each is one fetch per key per TTL for the whole cluster.
