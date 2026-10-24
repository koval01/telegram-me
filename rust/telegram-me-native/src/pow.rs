//! Proof of work: every request carries a hash that took the client work to
//! find and costs the server microseconds to check.
//!
//! **Challenge.** Issued without any server-side state:
//! `1.<expires>.<bits>.<salt>.<tag>`, where `tag` is an HMAC-SHA256 (16 bytes)
//! over the other fields and, when binding is on, the client's IP address.
//! A challenge is valid until `expires` (Unix seconds) and may be reused for
//! many requests during that time.
//!
//! **Solution.** For each request the client finds a `nonce` such that
//! `SHA-256(challenge "\n" nonce "\n" METHOD " " target)` starts with `bits`
//! zero bits, where `target` is the request path and query exactly as sent,
//! and sends `challenge:nonce` in the `X-PoW` header. Binding the hash to
//! the request means a solution is worth one request target only.
//!
//! **Checks**, cheapest first: size and syntax, expiry, difficulty, the tag
//! (constant-time comparison), the hash, and finally a replay cache. An
//! accepted solution is remembered until its challenge expires, so each one
//! is accepted once per process.

use std::time::{Duration, SystemTime, UNIX_EPOCH};

use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD as B64;
use hmac::{Hmac, KeyInit, Mac};
use moka::sync::Cache;
use sha2::{Digest, Sha256};

type HmacSha256 = Hmac<Sha256>;

const VERSION: &str = "1";
const TAG_LEN: usize = 16;
const SALT_LEN: usize = 12;
/// `SALT_LEN` bytes in unpadded base64.
const SALT_B64_LEN: usize = 16;
/// Longest accepted `X-PoW` header; a valid one is about 70 bytes plus the nonce.
pub const MAX_HEADER_LEN: usize = 256;
/// Longest accepted nonce.
pub const MAX_NONCE_LEN: usize = 64;
/// Most solutions remembered for replay protection per process.
const MAX_REMEMBERED: u64 = 500_000;
/// Clock skew tolerated between servers when checking `expires`.
const SKEW: u64 = 30;

/// Why a solution was rejected.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Rejection {
    /// No `X-PoW` header.
    Missing,
    /// Not `challenge:nonce`, or a field out of range.
    Malformed,
    /// The challenge has expired.
    Expired,
    /// The challenge asks for less work than the server requires now.
    TooEasy,
    /// The challenge was not issued by this server (or for this client).
    Forged,
    /// The hash does not have enough leading zero bits.
    InsufficientWork,
    /// This solution was already used.
    Replayed,
}

impl Rejection {
    /// A stable, machine-readable name.
    #[must_use]
    pub fn code(self) -> &'static str {
        match self {
            Self::Missing => "missing",
            Self::Malformed => "malformed",
            Self::Expired => "expired",
            Self::TooEasy => "too_easy",
            Self::Forged => "forged",
            Self::InsufficientWork => "insufficient_work",
            Self::Replayed => "replayed",
        }
    }
}

/// Issues challenges and checks solutions.
pub struct ProofOfWork {
    key: HmacSha256,
    bits: u8,
    ttl: Duration,
    bind_ip: bool,
    seen: Cache<[u8; 32], ()>,
}

/// A freshly issued challenge.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Challenge {
    /// The challenge string to send back with every solution.
    pub challenge: String,
    /// Required leading zero bits.
    pub bits: u8,
    /// Unix time (seconds) after which it is no longer accepted.
    pub expires: u64,
}

impl ProofOfWork {
    /// `secret` signs challenges and must be the same on every server.
    ///
    /// # Panics
    ///
    /// When `bits` is above 64 or the TTL is zero.
    #[must_use]
    pub fn new(secret: &[u8], bits: u8, ttl: Duration, bind_ip: bool) -> Self {
        assert!(bits <= 64, "difficulty above 64 bits");
        assert!(!ttl.is_zero(), "challenge TTL must be positive");
        Self {
            key: HmacSha256::new_from_slice(secret).expect("HMAC takes keys of any length"),
            bits,
            ttl,
            bind_ip,
            seen: Cache::builder()
                .max_capacity(MAX_REMEMBERED)
                .time_to_live(ttl + Duration::from_secs(SKEW))
                .build(),
        }
    }

    /// Required leading zero bits.
    #[must_use]
    pub fn bits(&self) -> u8 {
        self.bits
    }

    /// Issue a challenge for a client at `ip`.
    #[must_use]
    pub fn issue(&self, ip: &str) -> Challenge {
        self.issue_at(ip, unix_now())
    }

    fn issue_at(&self, ip: &str, now: u64) -> Challenge {
        let mut salt = [0u8; SALT_LEN];
        getrandom::fill(&mut salt).expect("the OS random number generator works");
        let expires = now + self.ttl.as_secs();
        let body = format!("{VERSION}.{expires}.{}.{}", self.bits, B64.encode(salt));
        let tag = B64.encode(&self.tag(&body, ip)[..TAG_LEN]);
        Challenge {
            challenge: format!("{body}.{tag}"),
            bits: self.bits,
            expires,
        }
    }

    fn tag(&self, body: &str, ip: &str) -> [u8; 32] {
        let mut mac = self.key.clone();
        mac.update(body.as_bytes());
        mac.update(b"|");
        if self.bind_ip {
            mac.update(ip.as_bytes());
        }
        mac.finalize().into_bytes().into()
    }

    /// Check the `X-PoW` header of a `method` request to `target` from `ip`.
    ///
    /// # Errors
    ///
    /// The reason the solution is not accepted.
    pub fn verify(
        &self,
        header: Option<&str>,
        method: &str,
        target: &str,
        ip: &str,
    ) -> Result<(), Rejection> {
        self.verify_at(header, method, target, ip, unix_now())
    }

    fn verify_at(
        &self,
        header: Option<&str>,
        method: &str,
        target: &str,
        ip: &str,
        now: u64,
    ) -> Result<(), Rejection> {
        let header = header.ok_or(Rejection::Missing)?.trim();
        if header.is_empty() {
            return Err(Rejection::Missing);
        }
        if header.len() > MAX_HEADER_LEN {
            return Err(Rejection::Malformed);
        }
        let (challenge, nonce) = header.rsplit_once(':').ok_or(Rejection::Malformed)?;
        if nonce.is_empty()
            || nonce.len() > MAX_NONCE_LEN
            || !nonce
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
        {
            return Err(Rejection::Malformed);
        }
        let (body, tag) = challenge.rsplit_once('.').ok_or(Rejection::Malformed)?;
        let mut fields = body.split('.');
        let (Some(version), Some(expires), Some(bits), Some(salt), None) = (
            fields.next(),
            fields.next(),
            fields.next(),
            fields.next(),
            fields.next(),
        ) else {
            return Err(Rejection::Malformed);
        };
        if version != VERSION || salt.len() != SALT_B64_LEN {
            return Err(Rejection::Malformed);
        }
        let expires: u64 = expires.parse().map_err(|_| Rejection::Malformed)?;
        let bits: u8 = bits.parse().map_err(|_| Rejection::Malformed)?;
        if expires.saturating_add(SKEW) < now {
            return Err(Rejection::Expired);
        }
        if expires > now + self.ttl.as_secs() + SKEW {
            // Issued with a longer TTL than ours: never by this configuration.
            return Err(Rejection::Forged);
        }
        if bits < self.bits {
            return Err(Rejection::TooEasy);
        }
        let mut given = [0u8; TAG_LEN];
        match B64.decode_slice(tag, &mut given) {
            Ok(TAG_LEN) => {}
            _ => return Err(Rejection::Malformed),
        }
        let mut mac = self.key.clone();
        mac.update(body.as_bytes());
        mac.update(b"|");
        if self.bind_ip {
            mac.update(ip.as_bytes());
        }
        mac.verify_truncated_left(&given)
            .map_err(|_| Rejection::Forged)?;

        let digest: [u8; 32] = Sha256::new()
            .chain_update(challenge.as_bytes())
            .chain_update(b"\n")
            .chain_update(nonce.as_bytes())
            .chain_update(b"\n")
            .chain_update(method.as_bytes())
            .chain_update(b" ")
            .chain_update(target.as_bytes())
            .finalize()
            .into();
        if leading_zero_bits(&digest) < u32::from(bits) {
            return Err(Rejection::InsufficientWork);
        }
        let mut fresh = false;
        self.seen.entry(digest).or_insert_with(|| {
            fresh = true;
        });
        if fresh {
            Ok(())
        } else {
            Err(Rejection::Replayed)
        }
    }
}

/// Leading zero bits of a hash.
#[must_use]
pub fn leading_zero_bits(hash: &[u8]) -> u32 {
    let mut bits = 0;
    for byte in hash {
        if *byte == 0 {
            bits += 8;
        } else {
            return bits + byte.leading_zeros();
        }
    }
    bits
}

/// Solve a challenge for a request (used by tests and benchmarks; clients
/// implement the same loop in their own language).
#[must_use]
pub fn solve(challenge: &str, method: &str, target: &str, bits: u8) -> String {
    let prefix = Sha256::new()
        .chain_update(challenge.as_bytes())
        .chain_update(b"\n");
    let suffix = format!("\n{method} {target}");
    let mut n: u64 = 0;
    loop {
        let nonce = format!("{n:x}");
        let digest = prefix
            .clone()
            .chain_update(nonce.as_bytes())
            .chain_update(suffix.as_bytes())
            .finalize();
        if leading_zero_bits(&digest) >= u32::from(bits) {
            return nonce;
        }
        n = n.wrapping_add(1);
    }
}

fn unix_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| d.as_secs())
}

#[cfg(test)]
mod tests {
    use super::*;

    const NOW: u64 = 1_800_000_000;

    /// A random key: tests never embed one (and it differs on every run).
    fn random_secret() -> [u8; 32] {
        let mut key = [0u8; 32];
        getrandom::fill(&mut key).expect("the OS random number generator works");
        key
    }

    /// The key shared by every `pow()` of a test run.
    fn secret() -> &'static [u8; 32] {
        static SECRET: std::sync::OnceLock<[u8; 32]> = std::sync::OnceLock::new();
        SECRET.get_or_init(random_secret)
    }

    fn pow(bits: u8) -> ProofOfWork {
        ProofOfWork::new(secret(), bits, Duration::from_secs(300), true)
    }

    fn header(challenge: &str, method: &str, target: &str, bits: u8) -> String {
        format!("{challenge}:{}", solve(challenge, method, target, bits))
    }

    #[test]
    fn solved_challenge_is_accepted_once() {
        let pow = pow(10);
        let issued = pow.issue_at("203.0.113.7", NOW);
        assert_eq!((issued.bits, issued.expires), (10, NOW + 300));
        let h = header(&issued.challenge, "GET", "/v1/body/durov", 10);
        assert_eq!(
            pow.verify_at(Some(&h), "GET", "/v1/body/durov", "203.0.113.7", NOW),
            Ok(())
        );
        assert_eq!(
            pow.verify_at(Some(&h), "GET", "/v1/body/durov", "203.0.113.7", NOW),
            Err(Rejection::Replayed)
        );
    }

    #[test]
    fn challenge_serves_many_requests() {
        let pow = pow(8);
        let issued = pow.issue_at("ip", NOW);
        for target in ["/v1/body/a", "/v1/body/b", "/v1/body/a?position=2"] {
            let h = header(&issued.challenge, "GET", target, 8);
            assert_eq!(
                pow.verify_at(Some(&h), "GET", target, "ip", NOW + 10),
                Ok(())
            );
        }
    }

    #[test]
    fn solution_is_bound_to_the_request() {
        let pow = pow(12);
        let issued = pow.issue_at("ip", NOW);
        let h = header(&issued.challenge, "GET", "/v1/body/durov", 12);
        let other = pow.verify_at(Some(&h), "GET", "/v1/body/telegram", "ip", NOW);
        let method = pow.verify_at(Some(&h), "POST", "/v1/body/durov", "ip", NOW);
        // 12 bits: a wrong target passes only with probability 1/4096.
        assert_eq!(other, Err(Rejection::InsufficientWork));
        assert_eq!(method, Err(Rejection::InsufficientWork));
    }

    #[test]
    fn challenge_is_bound_to_the_client_ip() {
        let pow = pow(4);
        let issued = pow.issue_at("203.0.113.7", NOW);
        let h = header(&issued.challenge, "GET", "/x", 4);
        assert_eq!(
            pow.verify_at(Some(&h), "GET", "/x", "198.51.100.1", NOW),
            Err(Rejection::Forged)
        );

        let unbound = ProofOfWork::new(secret(), 4, Duration::from_secs(300), false);
        let issued = unbound.issue_at("203.0.113.7", NOW);
        let h = header(&issued.challenge, "GET", "/x", 4);
        assert_eq!(
            unbound.verify_at(Some(&h), "GET", "/x", "198.51.100.1", NOW),
            Ok(())
        );
    }

    #[test]
    fn expired_and_future_challenges_are_rejected() {
        let pow = pow(4);
        let issued = pow.issue_at("ip", NOW);
        let h = header(&issued.challenge, "GET", "/x", 4);
        assert_eq!(
            pow.verify_at(Some(&h), "GET", "/x", "ip", NOW + 300 + SKEW + 1),
            Err(Rejection::Expired)
        );
        // A challenge from a server whose clock is far ahead.
        let ahead = pow.issue_at("ip", NOW + 3600);
        let h = header(&ahead.challenge, "GET", "/x", 4);
        assert_eq!(
            pow.verify_at(Some(&h), "GET", "/x", "ip", NOW),
            Err(Rejection::Forged)
        );
    }

    #[test]
    fn other_secret_or_tampered_fields_are_forged() {
        let pow = pow(4);
        let other = ProofOfWork::new(&random_secret(), 4, Duration::from_secs(300), true);
        let issued = other.issue_at("ip", NOW);
        let h = header(&issued.challenge, "GET", "/x", 4);
        assert_eq!(
            pow.verify_at(Some(&h), "GET", "/x", "ip", NOW),
            Err(Rejection::Forged)
        );

        let issued = pow.issue_at("ip", NOW);
        let tampered = issued.challenge.replacen(".4.", ".5.", 1);
        let h = header(&tampered, "GET", "/x", 5);
        assert_eq!(
            pow.verify_at(Some(&h), "GET", "/x", "ip", NOW),
            Err(Rejection::Forged)
        );
    }

    #[test]
    fn easier_challenges_are_rejected_after_a_difficulty_raise() {
        let easy = pow(4);
        let issued = easy.issue_at("ip", NOW);
        let h = header(&issued.challenge, "GET", "/x", 4);
        let hard = pow(8);
        assert_eq!(
            hard.verify_at(Some(&h), "GET", "/x", "ip", NOW),
            Err(Rejection::TooEasy)
        );
    }

    #[test]
    fn missing_and_malformed_headers() {
        let pow = pow(4);
        let issued = pow.issue_at("ip", NOW);
        let c = issued.challenge.as_str();
        for (header, expected) in [
            (None, Rejection::Missing),
            (Some("  "), Rejection::Missing),
            (Some("nonce-only"), Rejection::Malformed),
            (Some(&*format!("{c}:")), Rejection::Malformed),
            (Some(&*format!("{c}:bad nonce")), Rejection::Malformed),
            (
                Some(&*format!("{c}:{}", "a".repeat(MAX_NONCE_LEN + 1))),
                Rejection::Malformed,
            ),
            (Some(&*"x".repeat(MAX_HEADER_LEN + 1)), Rejection::Malformed),
            (
                Some("2.1.1.AAAAAAAAAAAAAAAA.AAAAAAAAAAAAAAAAAAAAAA:0"),
                Rejection::Malformed,
            ),
            (
                Some("1.x.4.AAAAAAAAAAAAAAAA.AAAAAAAAAAAAAAAAAAAAAA:0"),
                Rejection::Malformed,
            ),
            (
                Some(&*format!("{}:0", c.replace('.', "_"))),
                Rejection::Malformed,
            ),
        ] {
            assert_eq!(
                pow.verify_at(header, "GET", "/x", "ip", NOW),
                Err(expected),
                "{header:?}"
            );
        }
    }

    #[test]
    fn insufficient_work_is_rejected() {
        let pow = pow(20);
        let issued = pow.issue_at("ip", NOW);
        // Nonce "0" almost surely lacks 20 zero bits (probability 2^-20).
        let h = format!("{}:0", issued.challenge);
        assert_eq!(
            pow.verify_at(Some(&h), "GET", "/x", "ip", NOW),
            Err(Rejection::InsufficientWork)
        );
    }

    #[test]
    fn leading_zeros() {
        assert_eq!(leading_zero_bits(&[0, 0, 0x0f, 0xff]), 20);
        assert_eq!(leading_zero_bits(&[0x80]), 0);
        assert_eq!(leading_zero_bits(&[0, 0]), 16);
    }
}
