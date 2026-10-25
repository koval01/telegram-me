//! Authenticated frames: every byte nodes send each other is in one.
//!
//! A frame is a fixed 40-byte header followed by its body:
//!
//! | bytes  | field                                              |
//! |--------|----------------------------------------------------|
//! | 0..2   | magic `TM`                                         |
//! | 2      | version (1)                                        |
//! | 3      | flags: bit 0 = body encrypted                      |
//! | 4..12  | sender's Unix time in milliseconds, big endian     |
//! | 12..36 | nonce: unique per frame and sender                 |
//! | 36..40 | body length, big endian                            |
//!
//! Without encryption the body is the payload and an HMAC-SHA256 tag over
//! header and payload. With it the body is the payload encrypted with
//! XChaCha20-Poly1305 (the header is the associated data) and its 16-byte
//! tag. Both keys are derived from `PEER_SECRET` (HKDF-SHA256 expand, one
//! label per key in its `info`).
//!
//! A frame is accepted only if its tag verifies, its time is within
//! [`WINDOW`] of ours and its nonce was not seen before (a replay). Nonces
//! are remembered for twice the window, so a replayed frame is either too
//! old or known. Lengths are checked against [`MAX_BODY`] before anything
//! is read or allocated, and nothing is parsed before the tag verifies.

use std::sync::Mutex;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

use chacha20poly1305::XChaCha20Poly1305;
use chacha20poly1305::aead::AeadInOut;
use hmac::{Hmac, KeyInit, Mac};
use rustc_hash::FxHashSet;
use sha2::Sha256;

type HmacSha256 = Hmac<Sha256>;

/// Header length in bytes.
pub const HEADER_LEN: usize = 40;
/// The largest body accepted: a few hundred cached pages.
pub const MAX_BODY: usize = 8 << 20;
/// Most clock difference (and network delay) tolerated between nodes, in ms.
pub const WINDOW: u64 = 30_000;
/// Most nonces remembered at once; beyond it frames are refused. Only
/// holders of the secret can add nonces, so this bounds a runaway peer.
const MAX_NONCES: usize = 1 << 20;

const MAGIC: [u8; 2] = *b"TM";
const VERSION: u8 = 1;
const ENCRYPTED: u8 = 1;
const MAC_LEN: usize = 32;
const AEAD_TAG_LEN: usize = 16;
const NONCE_LEN: usize = 24;
// HKDF `info` labels: domain separation between the protocol's keys.
const MAC_INFO: &[u8] = b"telegram-me peer v1 mac";
const ENCRYPTION_INFO: &[u8] = b"telegram-me peer v1 encryption";

type Nonce = [u8; NONCE_LEN];

/// Why a frame was refused.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Reject {
    /// Not a frame of this protocol (or of another version).
    Malformed,
    /// The body is longer than [`MAX_BODY`] (or than a datagram).
    TooLarge,
    /// Encrypted where plain text is expected, or the other way round.
    WrongMode,
    /// The tag does not verify: another secret, or tampered with.
    Forged,
    /// The sender's clock is more than [`WINDOW`] away from ours.
    Stale,
    /// This nonce was seen before.
    Replayed,
}

impl std::fmt::Display for Reject {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(match self {
            Self::Malformed => "malformed frame",
            Self::TooLarge => "frame too large",
            Self::WrongMode => "encryption mode differs",
            Self::Forged => "authentication failed",
            Self::Stale => "clock skew beyond the window",
            Self::Replayed => "replayed frame",
        })
    }
}

/// Seals frames to send and opens received ones.
pub struct Sealer {
    mac: HmacSha256,
    cipher: Option<XChaCha20Poly1305>,
    prefix: [u8; 16],
    counter: AtomicU64,
    guard: Mutex<ReplayGuard>,
}

impl Sealer {
    /// A sealer for `secret`; `encrypt` encrypts bodies as well.
    ///
    /// # Panics
    ///
    /// When the OS random number generator fails.
    #[must_use]
    pub fn new(secret: &[u8], encrypt: bool) -> Self {
        let mac_key = expand(secret, MAC_INFO);
        let cipher = encrypt.then(|| {
            let key = expand(secret, ENCRYPTION_INFO);
            XChaCha20Poly1305::new_from_slice(&key).expect("the key is 32 bytes")
        });
        // A random prefix and a counter: unique nonces without a system call
        // per frame, also across processes and restarts.
        let mut prefix = [0; 16];
        getrandom::fill(&mut prefix).expect("the OS random number generator works");
        Self {
            mac: HmacSha256::new_from_slice(&mac_key).expect("HMAC takes keys of any length"),
            cipher,
            prefix,
            counter: AtomicU64::new(0),
            guard: Mutex::new(ReplayGuard::new(2 * WINDOW)),
        }
    }

    /// Bytes a frame adds to its payload.
    #[must_use]
    pub fn overhead(&self) -> usize {
        HEADER_LEN
            + if self.cipher.is_some() {
                AEAD_TAG_LEN
            } else {
                MAC_LEN
            }
    }

    /// The frame carrying `payload`.
    ///
    /// # Panics
    ///
    /// When `payload` is longer than [`MAX_BODY`]; callers bound it first.
    #[must_use]
    pub fn seal(&self, payload: &[u8]) -> Vec<u8> {
        self.seal_at(payload, unix_ms())
    }

    fn seal_at(&self, payload: &[u8], now_ms: u64) -> Vec<u8> {
        let body_len = payload.len() + self.overhead() - HEADER_LEN;
        assert!(body_len <= MAX_BODY, "payload too large for a frame");
        let count = self.counter.fetch_add(1, Ordering::Relaxed).to_be_bytes();
        let nonce: Nonce = std::array::from_fn(|i| {
            if i < self.prefix.len() {
                self.prefix[i]
            } else {
                count[i - self.prefix.len()]
            }
        });

        let mut frame = Vec::with_capacity(HEADER_LEN + body_len);
        frame.extend_from_slice(&MAGIC);
        frame.push(VERSION);
        frame.push(if self.cipher.is_some() { ENCRYPTED } else { 0 });
        frame.extend_from_slice(&now_ms.to_be_bytes());
        frame.extend_from_slice(&nonce);
        frame.extend_from_slice(
            &u32::try_from(body_len)
                .expect("bounded by MAX_BODY")
                .to_be_bytes(),
        );
        frame.extend_from_slice(payload);
        if let Some(cipher) = &self.cipher {
            let (header, body) = frame.split_at_mut(HEADER_LEN);
            let tag = cipher
                .encrypt_inout_detached(&nonce.into(), header, body.into())
                .expect("the payload is far below the cipher's limit");
            frame.extend_from_slice(&tag);
        } else {
            let mut mac = self.mac.clone();
            mac.update(&frame);
            frame.extend_from_slice(&mac.finalize().into_bytes());
        }
        frame
    }

    /// The body length announced by a header, after checking the header.
    ///
    /// # Errors
    ///
    /// When the header is not one of this protocol, or announces too much.
    pub fn body_len(&self, header: &[u8]) -> Result<usize, Reject> {
        if header.len() < HEADER_LEN || header[..2] != MAGIC || header[2] != VERSION {
            return Err(Reject::Malformed);
        }
        if (header[3] & ENCRYPTED != 0) != self.cipher.is_some() || header[3] & !ENCRYPTED != 0 {
            return Err(Reject::WrongMode);
        }
        let len = u32::from_be_bytes(field(header, 36)?) as usize;
        if len > MAX_BODY {
            return Err(Reject::TooLarge);
        }
        if len < self.overhead() - HEADER_LEN {
            return Err(Reject::Malformed);
        }
        Ok(len)
    }

    /// The payload of a whole frame (header and body).
    ///
    /// # Errors
    ///
    /// See [`Reject`].
    pub fn open(&self, frame: &[u8]) -> Result<Vec<u8>, Reject> {
        self.open_at(frame, unix_ms())
    }

    fn open_at(&self, frame: &[u8], now_ms: u64) -> Result<Vec<u8>, Reject> {
        let len = self.body_len(frame)?;
        if frame.len() != HEADER_LEN + len {
            return Err(Reject::Malformed);
        }
        let (header, body) = frame.split_at(HEADER_LEN);
        let nonce: Nonce = field(header, 12)?;
        let payload = if let Some(cipher) = &self.cipher {
            let (ciphertext, tag) = body.split_at(body.len() - AEAD_TAG_LEN);
            let mut payload = ciphertext.to_vec();
            cipher
                .decrypt_inout_detached(
                    &nonce.into(),
                    header,
                    payload.as_mut_slice().into(),
                    tag.try_into().map_err(|_| Reject::Malformed)?,
                )
                .map_err(|_| Reject::Forged)?;
            payload
        } else {
            let (payload, tag) = body.split_at(body.len() - MAC_LEN);
            let mut mac = self.mac.clone();
            mac.update(header);
            mac.update(payload);
            mac.verify_slice(tag).map_err(|_| Reject::Forged)?;
            payload.to_vec()
        };
        // Only authentic frames reach the clock and the nonce memory.
        let sent_ms = u64::from_be_bytes(field(header, 4)?);
        if sent_ms.abs_diff(now_ms) > WINDOW {
            return Err(Reject::Stale);
        }
        let admitted = self
            .guard
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .admit(nonce, now_ms);
        if !admitted {
            return Err(Reject::Replayed);
        }
        Ok(payload)
    }
}

/// The `N` header bytes at `at`.
fn field<const N: usize>(header: &[u8], at: usize) -> Result<[u8; N], Reject> {
    header
        .get(at..at + N)
        .and_then(|bytes| bytes.try_into().ok())
        .ok_or(Reject::Malformed)
}

/// HKDF-SHA256 expand (RFC 5869) of one 32-byte key for `info`.
///
/// The secret is the pseudorandom key itself: the extract step, with no salt,
/// may be skipped for input that is already a strong key (RFC 5869 §3.3), and
/// `PEER_SECRET` must be at least 32 characters.
fn expand(secret: &[u8], info: &[u8]) -> [u8; 32] {
    let mut mac = HmacSha256::new_from_slice(secret).expect("HMAC takes keys of any length");
    mac.update(info);
    mac.update(&[1]);
    mac.finalize().into_bytes().into()
}

/// Milliseconds since the Unix epoch.
#[must_use]
pub fn unix_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |since| {
            u64::try_from(since.as_millis()).unwrap_or(u64::MAX)
        })
}

/// Nonces seen recently, in two generations of `span` milliseconds each.
///
/// A nonce is remembered for at least `span` after it was admitted, which is
/// as long as a frame carrying it can pass the clock check.
struct ReplayGuard {
    span: u64,
    current: FxHashSet<Nonce>,
    previous: FxHashSet<Nonce>,
    rotated_at: u64,
}

impl ReplayGuard {
    fn new(span: u64) -> Self {
        Self {
            span,
            current: FxHashSet::default(),
            previous: FxHashSet::default(),
            rotated_at: 0,
        }
    }

    fn admit(&mut self, nonce: Nonce, now_ms: u64) -> bool {
        if now_ms.saturating_sub(self.rotated_at) >= self.span {
            if now_ms.saturating_sub(self.rotated_at) >= 2 * self.span {
                self.current.clear();
            }
            self.previous = std::mem::take(&mut self.current);
            self.rotated_at = now_ms;
        }
        if self.current.contains(&nonce) || self.previous.contains(&nonce) {
            return false;
        }
        if self.current.len() + self.previous.len() >= MAX_NONCES {
            return false;
        }
        self.current.insert(nonce);
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const NOW: u64 = 1_790_000_000_000;

    fn secret() -> Vec<u8> {
        let mut secret = vec![0; 32];
        getrandom::fill(&mut secret).unwrap();
        secret
    }

    #[test]
    fn round_trip_in_both_modes() {
        let secret = secret();
        for encrypt in [false, true] {
            let (alice, bob) = (Sealer::new(&secret, encrypt), Sealer::new(&secret, encrypt));
            let frame = alice.seal_at(b"hello", NOW);
            assert_eq!(frame.len(), 5 + alice.overhead());
            assert_eq!(
                bob.body_len(&frame[..HEADER_LEN]),
                Ok(frame.len() - HEADER_LEN)
            );
            assert_eq!(bob.open_at(&frame, NOW + 5).unwrap(), b"hello");
            assert_eq!(bob.open_at(&alice.seal_at(b"", NOW), NOW).unwrap(), b"");
        }
    }

    #[test]
    fn each_key_has_its_own_label() {
        let secret = secret();
        assert_ne!(expand(&secret, MAC_INFO), expand(&secret, ENCRYPTION_INFO));
        assert_ne!(expand(&secret, MAC_INFO), expand(&self::secret(), MAC_INFO));
        assert_eq!(expand(&secret, MAC_INFO), expand(&secret, MAC_INFO));
    }

    #[test]
    fn encryption_hides_the_payload() {
        let sealer = Sealer::new(&secret(), true);
        let frame = sealer.seal_at(b"durov durov durov", NOW);
        assert!(!frame.windows(5).any(|window| window == b"durov"));
        let plain = Sealer::new(&secret(), false).seal_at(b"durov", NOW);
        assert!(plain.windows(5).any(|window| window == b"durov"));
    }

    #[test]
    fn another_secret_is_refused() {
        for encrypt in [false, true] {
            let frame = Sealer::new(&secret(), encrypt).seal_at(b"hello", NOW);
            let other = Sealer::new(&secret(), encrypt);
            assert_eq!(other.open_at(&frame, NOW), Err(Reject::Forged));
        }
    }

    #[test]
    fn any_flipped_bit_is_refused() {
        let secret = secret();
        for encrypt in [false, true] {
            let alice = Sealer::new(&secret, encrypt);
            let bob = Sealer::new(&secret, encrypt);
            let frame = alice.seal_at(b"some payload", NOW);
            for index in 0..frame.len() {
                let mut tampered = frame.clone();
                tampered[index] ^= 0x10;
                assert!(bob.open_at(&tampered, NOW).is_err(), "byte {index}");
            }
            // Untouched, it still opens: refusals left no trace.
            assert!(bob.open_at(&frame, NOW).is_ok());
        }
    }

    #[test]
    fn replays_are_refused() {
        let secret = secret();
        let (alice, bob) = (Sealer::new(&secret, false), Sealer::new(&secret, false));
        let frame = alice.seal_at(b"once", NOW);
        assert!(bob.open_at(&frame, NOW).is_ok());
        assert_eq!(bob.open_at(&frame, NOW + 1), Err(Reject::Replayed));
        // Still known after a rotation of the nonce memory...
        assert_eq!(
            bob.open_at(&frame, NOW + 2 * WINDOW - 1),
            Err(Reject::Stale)
        );
        let late = alice.seal_at(b"late", NOW + 2 * WINDOW);
        assert!(bob.open_at(&late, NOW + 2 * WINDOW).is_ok());
        assert_eq!(
            bob.open_at(&late, NOW + 2 * WINDOW + 10),
            Err(Reject::Replayed)
        );
        // ...and every frame gets its own nonce.
        let (a, b) = (alice.seal_at(b"x", NOW), alice.seal_at(b"x", NOW));
        assert_ne!(a[12..36], b[12..36]);
    }

    #[test]
    fn nonces_are_remembered_as_long_as_frames_pass_the_clock() {
        let mut guard = ReplayGuard::new(2 * WINDOW);
        let mut nonce = [0; NONCE_LEN];
        getrandom::fill(&mut nonce).unwrap();
        assert!(guard.admit(nonce, NOW));
        for later in [1, WINDOW, 2 * WINDOW, 3 * WINDOW, 4 * WINDOW - 1] {
            assert!(!guard.admit(nonce, NOW + later), "{later}");
        }
    }

    #[test]
    fn frames_out_of_the_clock_window_are_refused() {
        let secret = secret();
        let (alice, bob) = (Sealer::new(&secret, true), Sealer::new(&secret, true));
        let old = alice.seal_at(b"x", NOW - WINDOW - 1);
        let future = alice.seal_at(b"x", NOW + WINDOW + 1);
        assert_eq!(bob.open_at(&old, NOW), Err(Reject::Stale));
        assert_eq!(bob.open_at(&future, NOW), Err(Reject::Stale));
        assert!(bob.open_at(&alice.seal_at(b"x", NOW - WINDOW), NOW).is_ok());
    }

    #[test]
    fn headers_are_checked_before_anything_is_read() {
        let secret = secret();
        let sealer = Sealer::new(&secret, false);
        let frame = sealer.seal_at(b"payload", NOW);

        let mut huge = frame.clone();
        huge[36..40].copy_from_slice(&u32::MAX.to_be_bytes());
        assert_eq!(sealer.body_len(&huge), Err(Reject::TooLarge));
        let mut just_over = frame.clone();
        let over = u32::try_from(MAX_BODY + 1).unwrap();
        just_over[36..40].copy_from_slice(&over.to_be_bytes());
        assert_eq!(sealer.body_len(&just_over), Err(Reject::TooLarge));

        assert_eq!(
            sealer.body_len(b"GET / HTTP/1.1\r\n"),
            Err(Reject::Malformed)
        );
        assert_eq!(sealer.body_len(&[0; HEADER_LEN]), Err(Reject::Malformed));
        let mut short = frame.clone();
        short[36..40].copy_from_slice(&3u32.to_be_bytes());
        assert_eq!(sealer.body_len(&short), Err(Reject::Malformed));
        assert_eq!(
            sealer.open_at(&frame[..frame.len() - 1], NOW),
            Err(Reject::Malformed)
        );

        let encrypted = Sealer::new(&secret, true);
        assert_eq!(encrypted.body_len(&frame), Err(Reject::WrongMode));
        assert_eq!(
            sealer.body_len(&encrypted.seal_at(b"x", NOW)),
            Err(Reject::WrongMode)
        );
        let mut flags = frame;
        flags[3] = 0x80;
        assert_eq!(sealer.body_len(&flags), Err(Reject::WrongMode));
    }
}
