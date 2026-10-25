//! What frames carry: a small binary encoding with bounded lengths.
//!
//! Integers are big endian; strings and byte strings carry their length
//! first. Every length is checked against a limit and against the bytes
//! left before anything is allocated, and a message must use its payload
//! exactly. Payloads are only decoded after their frame was authenticated
//! (`wire`), so the limits guard against bugs rather than strangers.

/// Longest key, client address or topic.
pub const MAX_STR: usize = 1024;
/// Most keys asked for, or entries answered, in one message.
pub const MAX_ITEMS: usize = 256;
/// Most clients in one rate-limit count message.
pub const MAX_COUNTS: usize = 10_000;
/// Longest error message passed on.
const MAX_MESSAGE: usize = 1024;

/// A cached entry as it travels: the stored (possibly compressed) bytes.
#[derive(Debug, Clone, PartialEq)]
pub struct WireEntry {
    /// The cache key.
    pub key: String,
    /// Unix time the value was stored at.
    pub stored_at: f64,
    /// Milliseconds the entry has left to live.
    pub ttl_ms: u64,
    /// Length of the original value.
    pub len: u32,
    /// Whether `blob` is zstd-compressed.
    pub compressed: bool,
    /// The value as the cache stores it.
    pub blob: Vec<u8>,
}

/// A message between two nodes.
#[derive(Debug, Clone, PartialEq)]
pub enum Message {
    /// Membership protocol data (SWIM), over UDP only.
    Gossip(Vec<u8>),
    /// Ask for cached entries; answered with `Entries` (missing keys left out).
    Get {
        /// Request id, echoed in the answer.
        id: u64,
        /// The keys.
        keys: Vec<String>,
    },
    /// Cached entries.
    Entries {
        /// The request answered.
        id: u64,
        /// The entries found.
        entries: Vec<WireEntry>,
    },
    /// Ask the owner of `key` to load it unless it holds a value stored
    /// after `newer_than`; answered with `Accepted` at once, then `Entries`
    /// or `Failed`.
    Load {
        /// Request id.
        id: u64,
        /// The key.
        key: String,
        /// A value stored at or before this Unix time is too old.
        newer_than: f64,
        /// How long the asker waits, in milliseconds.
        wait_ms: u32,
        /// Background work (a refresh) for the asker: the owner gives user
        /// requests precedence.
        background: bool,
    },
    /// The owner took a `Load` on.
    Accepted {
        /// The request.
        id: u64,
    },
    /// A `Load` failed; `code` tells how (see the Python side).
    Failed {
        /// The request.
        id: u64,
        /// Error class.
        code: u8,
        /// Error message.
        message: String,
    },
    /// Rate-limit counts: accepted requests per client in a minute window.
    Counts {
        /// Minutes since the Unix epoch.
        window: u64,
        /// `(client, requests)`.
        items: Vec<(String, u32)>,
    },
    /// An application message for every node (for example popularity hits).
    Cast {
        /// What it is about.
        topic: String,
        /// Its content.
        data: Vec<u8>,
    },
}

/// A payload that is not a valid message.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DecodeError;

impl std::fmt::Display for DecodeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("undecodable peer message")
    }
}

const GOSSIP: u8 = 1;
const GET: u8 = 10;
const ENTRIES: u8 = 11;
const LOAD: u8 = 12;
const ACCEPTED: u8 = 13;
const FAILED: u8 = 14;
const COUNTS: u8 = 20;
const CAST: u8 = 21;

impl Message {
    /// The payload carrying this message.
    ///
    /// # Panics
    ///
    /// When a field exceeds its limit; callers bound what they send.
    #[must_use]
    pub fn encode(&self) -> Vec<u8> {
        let mut out = Writer(Vec::with_capacity(64));
        match self {
            Self::Gossip(data) => {
                out.u8(GOSSIP);
                out.0.extend_from_slice(data);
            }
            Self::Get { id, keys } => {
                out.u8(GET);
                out.u64(*id);
                out.count(keys.len(), MAX_ITEMS);
                for key in keys {
                    out.str(key, MAX_STR);
                }
            }
            Self::Entries { id, entries } => {
                out.u8(ENTRIES);
                out.u64(*id);
                out.count(entries.len(), MAX_ITEMS);
                for entry in entries {
                    out.str(&entry.key, MAX_STR);
                    out.u64(entry.stored_at.to_bits());
                    out.u64(entry.ttl_ms);
                    out.u32(entry.len);
                    out.u8(u8::from(entry.compressed));
                    out.bytes(&entry.blob);
                }
            }
            Self::Load {
                id,
                key,
                newer_than,
                wait_ms,
                background,
            } => {
                out.u8(LOAD);
                out.u64(*id);
                out.str(key, MAX_STR);
                out.u64(newer_than.to_bits());
                out.u32(*wait_ms);
                out.u8(u8::from(*background));
            }
            Self::Accepted { id } => {
                out.u8(ACCEPTED);
                out.u64(*id);
            }
            Self::Failed { id, code, message } => {
                out.u8(FAILED);
                out.u64(*id);
                out.u8(*code);
                out.str(truncated(message, MAX_MESSAGE), MAX_MESSAGE);
            }
            Self::Counts { window, items } => {
                out.u8(COUNTS);
                out.u64(*window);
                out.count(items.len(), MAX_COUNTS);
                for (client, count) in items {
                    out.str(client, MAX_STR);
                    out.u32(*count);
                }
            }
            Self::Cast { topic, data } => {
                out.u8(CAST);
                out.str(topic, MAX_STR);
                out.bytes(data);
            }
        }
        out.0
    }

    /// The message in `payload`.
    ///
    /// # Errors
    ///
    /// When `payload` is not exactly one valid message.
    pub fn decode(payload: &[u8]) -> Result<Self, DecodeError> {
        let mut input = Reader(payload);
        let message = match input.u8()? {
            GOSSIP => return Ok(Self::Gossip(input.0.to_vec())),
            GET => {
                let id = input.u64()?;
                let count = input.count(MAX_ITEMS)?;
                let keys = (0..count)
                    .map(|_| input.str(MAX_STR))
                    .collect::<Result<_, _>>()?;
                Self::Get { id, keys }
            }
            ENTRIES => {
                let id = input.u64()?;
                let count = input.count(MAX_ITEMS)?;
                let mut entries = Vec::with_capacity(count);
                for _ in 0..count {
                    entries.push(WireEntry {
                        key: input.str(MAX_STR)?,
                        stored_at: f64::from_bits(input.u64()?),
                        ttl_ms: input.u64()?,
                        len: input.u32()?,
                        compressed: input.bool()?,
                        blob: input.bytes()?,
                    });
                }
                Self::Entries { id, entries }
            }
            LOAD => Self::Load {
                id: input.u64()?,
                key: input.str(MAX_STR)?,
                newer_than: f64::from_bits(input.u64()?),
                wait_ms: input.u32()?,
                background: input.bool()?,
            },
            ACCEPTED => Self::Accepted { id: input.u64()? },
            FAILED => Self::Failed {
                id: input.u64()?,
                code: input.u8()?,
                message: input.str(MAX_MESSAGE)?,
            },
            COUNTS => {
                let window = input.u64()?;
                let count = input.count(MAX_COUNTS)?;
                let items = (0..count)
                    .map(|_| Ok((input.str(MAX_STR)?, input.u32()?)))
                    .collect::<Result<_, _>>()?;
                Self::Counts { window, items }
            }
            CAST => Self::Cast {
                topic: input.str(MAX_STR)?,
                data: input.bytes()?,
            },
            _ => return Err(DecodeError),
        };
        if input.0.is_empty() {
            Ok(message)
        } else {
            Err(DecodeError)
        }
    }
}

fn truncated(text: &str, max: usize) -> &str {
    if text.len() <= max {
        return text;
    }
    let mut end = max;
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    &text[..end]
}

struct Writer(Vec<u8>);

impl Writer {
    fn u8(&mut self, value: u8) {
        self.0.push(value);
    }

    fn u32(&mut self, value: u32) {
        self.0.extend_from_slice(&value.to_be_bytes());
    }

    fn u64(&mut self, value: u64) {
        self.0.extend_from_slice(&value.to_be_bytes());
    }

    fn count(&mut self, count: usize, max: usize) {
        assert!(count <= max, "too many items for one message");
        self.u32(u32::try_from(count).expect("bounded"));
    }

    fn str(&mut self, value: &str, max: usize) {
        assert!(value.len() <= max, "string too long for a message");
        self.u32(u32::try_from(value.len()).expect("bounded"));
        self.0.extend_from_slice(value.as_bytes());
    }

    fn bytes(&mut self, value: &[u8]) {
        self.u32(u32::try_from(value.len()).expect("frames are bounded far below 4 GiB"));
        self.0.extend_from_slice(value);
    }
}

struct Reader<'a>(&'a [u8]);

impl Reader<'_> {
    fn take(&mut self, len: usize) -> Result<&[u8], DecodeError> {
        if len > self.0.len() {
            return Err(DecodeError);
        }
        let (head, rest) = self.0.split_at(len);
        self.0 = rest;
        Ok(head)
    }

    fn u8(&mut self) -> Result<u8, DecodeError> {
        Ok(self.take(1)?[0])
    }

    fn bool(&mut self) -> Result<bool, DecodeError> {
        match self.u8()? {
            0 => Ok(false),
            1 => Ok(true),
            _ => Err(DecodeError),
        }
    }

    fn u32(&mut self) -> Result<u32, DecodeError> {
        Ok(u32::from_be_bytes(
            self.take(4)?.try_into().map_err(|_| DecodeError)?,
        ))
    }

    fn u64(&mut self) -> Result<u64, DecodeError> {
        Ok(u64::from_be_bytes(
            self.take(8)?.try_into().map_err(|_| DecodeError)?,
        ))
    }

    fn count(&mut self, max: usize) -> Result<usize, DecodeError> {
        let count = self.u32()? as usize;
        if count > max {
            return Err(DecodeError);
        }
        Ok(count)
    }

    fn str(&mut self, max: usize) -> Result<String, DecodeError> {
        let len = self.count(max)?;
        String::from_utf8(self.take(len)?.to_vec()).map_err(|_| DecodeError)
    }

    fn bytes(&mut self) -> Result<Vec<u8>, DecodeError> {
        let len = self.u32()? as usize;
        Ok(self.take(len)?.to_vec())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn samples() -> Vec<Message> {
        vec![
            Message::Gossip(vec![1, 2, 3]),
            Message::Get {
                id: 7,
                keys: vec![
                    "page:full:durov:0".into(),
                    "json:body:page:full:durov:0".into(),
                ],
            },
            Message::Entries {
                id: 7,
                entries: vec![WireEntry {
                    key: "page:full:durov:0".into(),
                    stored_at: 1_790_000_000.25,
                    ttl_ms: 9_500,
                    len: 5,
                    compressed: false,
                    blob: b"{\"a\"}".to_vec(),
                }],
            },
            Message::Entries {
                id: 8,
                entries: vec![],
            },
            Message::Load {
                id: 9,
                key: "preview:durov".into(),
                newer_than: f64::NEG_INFINITY,
                wait_ms: 23_000,
                background: true,
            },
            Message::Accepted { id: 9 },
            Message::Failed {
                id: 9,
                code: 1,
                message: "Requested Telegram resource was not found".into(),
            },
            Message::Counts {
                window: 29_833_333,
                items: vec![("203.0.113.7".into(), 12), ("2001:db8::1".into(), 1)],
            },
            Message::Cast {
                topic: "hits".into(),
                data: b"durov\ntelegram".to_vec(),
            },
        ]
    }

    #[test]
    fn every_message_round_trips() {
        for message in samples() {
            assert_eq!(Message::decode(&message.encode()), Ok(message));
        }
    }

    #[test]
    fn truncated_or_padded_payloads_are_refused() {
        for message in samples() {
            if matches!(message, Message::Gossip(_)) {
                continue; // gossip is opaque: any length is its own
            }
            let payload = message.encode();
            for len in 0..payload.len() {
                assert_eq!(
                    Message::decode(&payload[..len]),
                    Err(DecodeError),
                    "{message:?}"
                );
            }
            let mut padded = payload;
            padded.push(0);
            assert_eq!(Message::decode(&padded), Err(DecodeError));
        }
        assert_eq!(Message::decode(&[200]), Err(DecodeError));
    }

    #[test]
    fn lengths_beyond_the_limits_are_refused_before_allocating() {
        let mut keys = vec![GET];
        keys.extend_from_slice(&1u64.to_be_bytes());
        keys.extend_from_slice(&u32::MAX.to_be_bytes());
        assert_eq!(Message::decode(&keys), Err(DecodeError));

        let mut long_key = vec![LOAD];
        long_key.extend_from_slice(&1u64.to_be_bytes());
        let too_long = u32::try_from(MAX_STR + 1).unwrap();
        long_key.extend_from_slice(&too_long.to_be_bytes());
        long_key.extend(std::iter::repeat_n(b'a', MAX_STR + 1));
        assert_eq!(Message::decode(&long_key), Err(DecodeError));

        let mut blob = vec![CAST, 0, 0, 0, 1, b't'];
        blob.extend_from_slice(&u32::MAX.to_be_bytes());
        assert_eq!(Message::decode(&blob), Err(DecodeError));

        let mut invalid_utf8 = vec![GET];
        invalid_utf8.extend_from_slice(&1u64.to_be_bytes());
        invalid_utf8.extend_from_slice(&1u32.to_be_bytes()); // one key
        invalid_utf8.extend_from_slice(&1u32.to_be_bytes()); // of one byte
        invalid_utf8.push(0xff);
        assert_eq!(Message::decode(&invalid_utf8), Err(DecodeError));
    }

    #[test]
    fn long_error_messages_are_cut_at_a_character() {
        let message = Message::Failed {
            id: 1,
            code: 4,
            message: "é".repeat(MAX_MESSAGE),
        };
        let Ok(Message::Failed { message, .. }) = Message::decode(&message.encode()) else {
            panic!("does not round trip");
        };
        assert_eq!(message.len(), MAX_MESSAGE);
        assert!(message.chars().all(|c| c == 'é'));
    }
}
