//! HTTP client for t.me that looks like one browser on the wire.
//!
//! One emulation profile sets the TLS `ClientHello` (`BoringSSL`: cipher and
//! extension order, GREASE, ALPS, certificate compression), the HTTP/2
//! `SETTINGS`, `WINDOW_UPDATE`, priority and pseudo-header order, and the
//! default request headers, `User-Agent` and `sec-ch-ua*` included. None of
//! them is written per request, so headers can not claim a browser the
//! handshake contradicts. The profile is our own current Chrome
//! ([`chrome::NAME`], see [`chrome`]) or one of `wreq_util`'s Chromium ones.
//!
//! Two kinds of request are sent, the two a browser sends to t.me:
//!
//! - a navigation (`GET`): the profile's headers as they are, with our
//!   `accept-language`;
//! - the "load more" call (`POST` with `x-requested-with`): the page's
//!   jQuery XHR, with the fetch metadata, `accept`, `origin`, `referer` and
//!   header order Chromium uses for it (captured from Chrome 152).
//!
//! Like a browser, the client keeps the cookies t.me sets (`stel_ssid`) and
//! sends them back. A [`Session`] — cookie jar, connections, TLS session
//! tickets — is one browser identity; it is replaced by a fresh one after
//! [`Options::session_lifetime`] (with jitter), once its cookies expire, or
//! when t.me answers a 4xx other than 404. Cookies stay inside the session:
//! they are never part of a [`Response`], an error or `Debug` output.

mod chrome;

use std::fmt;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, PoisonError, RwLock};
use std::time::{Duration, Instant};

use wreq::cookie::Jar;
use wreq::header::{
    ACCEPT, ACCEPT_ENCODING, ACCEPT_LANGUAGE, CONTENT_LENGTH, COOKIE, HeaderMap, HeaderName,
    HeaderValue, ORIGIN, OrigHeaderMap, REFERER, USER_AGENT,
};
use wreq::{IntoEmulation, Method};
use wreq_util::{Emulation, Platform, Profile};

/// Profile families whose headers and XHR header order we derive (Chromium).
const CHROMIUM_FAMILIES: [&str; 3] = ["chrome_", "edge_", "opera_"];

/// Connections idle longer than this are closed (as pyreqwest did).
const POOL_IDLE: Duration = Duration::from_secs(90);

/// The longest a TCP + TLS handshake may take, at most the whole timeout.
const MAX_CONNECT: Duration = Duration::from_secs(5);

/// A session lives its lifetime times a random factor in this range, so the
/// workers of a fleet do not all start new sessions at once.
const LIFETIME_JITTER: (f64, f64) = (0.75, 1.25);

/// `accept` that jQuery sends for `dataType: "json"`, as t.me's page does.
const XHR_ACCEPT: &str = "application/json, text/javascript, */*; q=0.01";

/// Header order of a Chromium XHR `POST` (`content-length` comes from the body).
const XHR_ORDER: [&str; 16] = [
    "content-length",
    "sec-ch-ua-platform",
    "x-requested-with",
    "user-agent",
    "accept",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "origin",
    "sec-fetch-site",
    "sec-fetch-mode",
    "sec-fetch-dest",
    "referer",
    "accept-encoding",
    "accept-language",
    "cookie",
    "priority",
];

/// Headers of the navigation request carried over to the XHR as they are.
const XHR_INHERITED: [&str; 5] = [
    "sec-ch-ua-platform",
    "user-agent",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "accept-encoding",
];

/// Client settings.
#[derive(Debug, Clone)]
pub struct Options {
    /// Scheme and host requests go to, such as `https://t.me`.
    pub base_url: String,
    /// [`chrome::NAME`] or a Chromium profile name of `wreq-util`, such as
    /// `chrome_149`.
    pub profile: String,
    /// `windows`, `macos`, `linux`, `android` or `ios`.
    pub platform: String,
    /// The `accept-language` header.
    pub accept_language: String,
    /// The whole request, from connecting to the last body byte.
    pub timeout: Duration,
    /// How long one browser identity lives, before jitter; `None` keeps it
    /// until its cookies expire or t.me rejects it.
    pub session_lifetime: Option<Duration>,
}

/// Why a request failed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    /// The options can not build a client.
    Config(String),
    /// No answer in time.
    Timeout(String),
    /// The connection failed or broke before the answer was read; a request
    /// that changes nothing may be retried once on a new connection.
    Network(String),
    /// Anything else, such as a body that does not decompress.
    Other(String),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Config(msg) | Error::Timeout(msg) | Error::Network(msg) | Error::Other(msg) => {
                f.write_str(msg)
            }
        }
    }
}

impl std::error::Error for Error {}

/// Status and UTF-8 body of a response; error statuses are not errors.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Response {
    /// HTTP status code.
    pub status: u16,
    /// Decompressed body, invalid UTF-8 replaced.
    pub text: String,
}

/// Builds the sessions of one client: everything but the identity.
struct Factory {
    emulation: wreq::Emulation,
    timeout: Duration,
    lifetime: Option<Duration>,
}

impl Factory {
    fn session(&self, id: u64) -> Result<Session, Error> {
        let jar = Arc::new(Jar::default());
        let inner = wreq::Client::builder()
            .emulation(self.emulation.clone())
            .cookie_provider(Arc::clone(&jar))
            .gzip(true)
            .brotli(true)
            .zstd(true)
            .deflate(true)
            .redirect(wreq::redirect::Policy::none())
            .timeout(self.timeout)
            .connect_timeout(self.timeout.min(MAX_CONNECT))
            .pool_idle_timeout(POOL_IDLE)
            .build()
            .map_err(|err| Error::Config(chain(&err)))?;
        Ok(Session {
            id,
            inner,
            jar,
            ends: self
                .lifetime
                .map(|lifetime| Instant::now() + jittered(lifetime)),
            had_cookies: AtomicBool::new(false),
            retired: AtomicBool::new(false),
        })
    }
}

/// `lifetime` times a random factor in [`LIFETIME_JITTER`].
fn jittered(lifetime: Duration) -> Duration {
    let random = getrandom::u32().expect("the OS random number generator works");
    let (low, high) = LIFETIME_JITTER;
    lifetime.mul_f64(low + (high - low) * f64::from(random) / f64::from(u32::MAX))
}

/// One browser identity: a cookie jar and the connections (with their TLS
/// session tickets) that carried it. Requests in flight finish on the
/// session they started on; its connections close when the last one ends.
struct Session {
    id: u64,
    inner: wreq::Client,
    jar: Arc<Jar>,
    ends: Option<Instant>,
    /// t.me has set a cookie in this session.
    had_cookies: AtomicBool,
    /// t.me rejected a request of this session.
    retired: AtomicBool,
}

impl Session {
    fn is_over(&self, now: Instant) -> bool {
        self.retired.load(Ordering::Relaxed)
            || self.ends.is_some_and(|ends| now >= ends)
            // Its cookies expired (or t.me deleted them): a browser would
            // start over with a new one, so the session does.
            || (self.had_cookies.load(Ordering::Relaxed) && self.jar.is_empty())
    }

    /// Note what the answer says about the session.
    fn observe(&self, status: u16) {
        if (400..500).contains(&status) && status != 404 {
            // 403, 429 and the like: t.me may have flagged this identity.
            // 404 is a channel that does not exist.
            self.retired.store(true, Ordering::Relaxed);
        } else if !self.had_cookies.load(Ordering::Relaxed) && !self.jar.is_empty() {
            self.had_cookies.store(true, Ordering::Relaxed);
        }
    }
}

/// A pooled HTTP/2 client with one browser identity at a time.
#[derive(Clone)]
pub struct Client {
    factory: Arc<Factory>,
    session: Arc<RwLock<Arc<Session>>>,
    base: String,
    origin: HeaderValue,
    navigation: HeaderMap,
    xhr: HeaderMap,
    xhr_order: OrigHeaderMap,
}

impl Client {
    /// Build the client.
    ///
    /// # Errors
    ///
    /// [`Error::Config`] for an unknown or non-Chromium profile, an unknown
    /// platform (or iOS for [`chrome::NAME`]), a base URL that is not
    /// `http(s)://host[:port]`, or a header value that is not valid.
    pub fn new(options: &Options) -> Result<Self, Error> {
        let base = options.base_url.trim_end_matches('/').to_owned();
        let origin = origin_of(&base)?;
        let mut emulation = emulation(
            &options.profile,
            &options.platform,
            &options.accept_language,
        )?;
        let navigation = emulation.headers.clone();
        emulation.orig_headers = navigation_order(&navigation);
        let xhr = xhr_headers(&navigation);
        let factory = Factory {
            emulation,
            timeout: options.timeout,
            lifetime: options.session_lifetime,
        };
        let session = factory.session(1)?;
        let mut xhr_order = OrigHeaderMap::with_capacity(XHR_ORDER.len());
        for name in XHR_ORDER {
            xhr_order.insert(HeaderName::from_static(name));
        }
        Ok(Self {
            factory: Arc::new(factory),
            session: Arc::new(RwLock::new(Arc::new(session))),
            base,
            origin,
            navigation,
            xhr,
            xhr_order,
        })
    }

    /// The headers of a navigation request, in the order they are sent.
    #[must_use]
    pub fn navigation_headers(&self) -> &HeaderMap {
        &self.navigation
    }

    /// The `User-Agent` the profile claims.
    #[must_use]
    pub fn user_agent(&self) -> &str {
        self.navigation
            .get(USER_AGENT)
            .and_then(|value| value.to_str().ok())
            .unwrap_or_default()
    }

    /// Number of the current session: 1 for the first, one more for each
    /// that replaced it (counted when a request needs a new one).
    #[must_use]
    pub fn session_id(&self) -> u64 {
        self.session
            .read()
            .unwrap_or_else(PoisonError::into_inner)
            .id
    }

    /// The session for a request starting now, after replacing one that is
    /// over.
    fn current_session(&self) -> Result<Arc<Session>, Error> {
        let now = Instant::now();
        let current = Arc::clone(&self.session.read().unwrap_or_else(PoisonError::into_inner));
        if !current.is_over(now) {
            return Ok(current);
        }
        let mut slot = self.session.write().unwrap_or_else(PoisonError::into_inner);
        // Another request may have replaced it while this one waited.
        if slot.is_over(now) {
            let next = self.factory.session(slot.id + 1)?;
            *slot = Arc::new(next);
        }
        let session = Arc::clone(&slot);
        drop(slot);
        Ok(session)
    }

    /// Send one request to `path` on the base URL.
    ///
    /// A `POST` that carries `x-requested-with` is sent as the page's XHR;
    /// any other request as a navigation. `extra` headers are added last.
    ///
    /// # Errors
    ///
    /// [`Error::Timeout`], [`Error::Network`] or [`Error::Other`]; an error
    /// status is a [`Response`], not an error.
    pub async fn fetch(
        &self,
        method: &str,
        path: &str,
        query: &[(String, i64)],
        extra: &[(String, String)],
    ) -> Result<Response, Error> {
        let method = match method {
            "GET" => Method::GET,
            "POST" => Method::POST,
            other => return Err(Error::Other(format!("unsupported method {other}"))),
        };
        if !path.starts_with('/') {
            return Err(Error::Other("path must start with '/'".into()));
        }
        let url = format!("{}{}{}", self.base, path, query_string(query)?);
        let extra = header_map(extra)?;
        if extra.contains_key(COOKIE) {
            // The session's jar is the only source of cookies.
            return Err(Error::Other("cookies come from the session".into()));
        }
        let is_xhr = method == Method::POST && extra.contains_key("x-requested-with");
        let session = self
            .current_session()
            .map_err(|err| Error::Other(err.to_string()))?;

        let mut request = session.inner.request(method.clone(), url);
        if is_xhr {
            let mut headers = self.xhr.clone();
            headers.insert(ORIGIN, self.origin.clone());
            let referer = HeaderValue::from_str(&format!("{}{}", self.base, path))
                .map_err(|_| Error::Other("path is not a valid referer".into()))?;
            headers.insert(REFERER, referer);
            request = request
                .default_headers(false)
                .headers(headers)
                .orig_headers(self.xhr_order.clone());
        }
        request = request.headers(extra);
        if method == Method::POST {
            // t.me answers a POST without a length "411 Length Required";
            // HTTP/2 would add it for an empty body, HTTP/1.1 does not.
            request = request
                .header(CONTENT_LENGTH, HeaderValue::from_static("0"))
                .body(Vec::new());
        }

        let response = request.send().await.map_err(|err| classify(&err))?;
        let status = response.status().as_u16();
        session.observe(status);
        let text = response.text().await.map_err(|err| classify(&err))?;
        Ok(Response { status, text })
    }
}

impl fmt::Debug for Client {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        // Never the jar: cookies are the session's secret.
        f.debug_struct("Client")
            .field("base", &self.base)
            .field("user_agent", &self.user_agent())
            .field("session", &self.session_id())
            .finish_non_exhaustive()
    }
}

/// The headers a navigation request of `profile` on `platform` sends, in
/// order, for clients that can not emulate the handshake themselves.
///
/// # Errors
///
/// [`Error::Config`] as [`Client::new`].
pub fn navigation_headers(
    profile: &str,
    platform: &str,
    accept_language: &str,
) -> Result<HeaderMap, Error> {
    Ok(emulation(profile, platform, accept_language)?.headers)
}

fn emulation(
    profile: &str,
    platform: &str,
    accept_language: &str,
) -> Result<wreq::Emulation, Error> {
    let platform = parse_platform(platform)?;
    let mut emulation = if profile == chrome::NAME {
        chrome::emulation(platform).map_err(Error::Config)?
    } else {
        Emulation::builder()
            .profile(parse_profile(profile)?)
            .platform(platform)
            .build()
            .into_emulation()
    };
    let language = HeaderValue::from_str(accept_language)
        .map_err(|_| Error::Config("accept_language is not a valid header value".into()))?;
    // Replacing a value keeps its place in the profile's header order.
    emulation.headers.insert(ACCEPT_LANGUAGE, language);
    Ok(emulation)
}

/// The order of a navigation's headers: the profile's, with `cookie` where
/// Chromium puts it, between `accept-language` and `priority`.
fn navigation_order(headers: &HeaderMap) -> OrigHeaderMap {
    let mut order = OrigHeaderMap::with_capacity(headers.keys_len() + 1);
    for name in headers.keys() {
        if name == "priority" {
            order.insert(COOKIE);
        }
        order.insert(name.clone());
    }
    if !headers.contains_key("priority") {
        order.insert(COOKIE);
    }
    order
}

/// Names of the profiles [`Options::profile`] accepts, oldest first: those
/// of `wreq-util`, then ours ([`chrome::NAME`]).
#[must_use]
pub fn profiles() -> Vec<String> {
    Profile::VARIANTS
        .iter()
        .filter_map(|profile| Some(serde_json::to_value(profile).ok()?.as_str()?.to_owned()))
        .filter(|name| is_chromium(name))
        .chain([chrome::NAME.to_owned()])
        .collect()
}

fn is_chromium(name: &str) -> bool {
    CHROMIUM_FAMILIES
        .iter()
        .any(|family| name.starts_with(family))
}

fn parse_profile(name: &str) -> Result<Profile, Error> {
    let unknown = || {
        Error::Config(format!(
            "unknown browser profile {name:?}; Chromium profiles such as {:?} are supported",
            chrome::NAME
        ))
    };
    if !is_chromium(name) {
        return Err(unknown());
    }
    serde_json::from_value(serde_json::Value::String(name.to_owned())).map_err(|_| unknown())
}

fn parse_platform(name: &str) -> Result<Platform, Error> {
    serde_json::from_value(serde_json::Value::String(name.to_owned())).map_err(|_| {
        Error::Config(format!(
            "unknown platform {name:?}; expected windows, macos, linux, android or ios"
        ))
    })
}

/// `scheme://host[:port]` of a base URL that has no path.
fn origin_of(base: &str) -> Result<HeaderValue, Error> {
    let invalid = || Error::Config(format!("base URL {base:?} is not http(s)://host[:port]"));
    let uri: wreq::Uri = base.parse().map_err(|_| invalid())?;
    let scheme = uri.scheme_str().ok_or_else(invalid)?;
    let authority = uri.authority().ok_or_else(invalid)?;
    if !matches!(scheme, "http" | "https") || !matches!(uri.path(), "" | "/") {
        return Err(invalid());
    }
    HeaderValue::from_str(&format!("{scheme}://{authority}")).map_err(|_| invalid())
}

/// The XHR's headers: the profile's identity, fetch metadata of a same-origin
/// `cors` request, and the profile's encodings and language.
fn xhr_headers(navigation: &HeaderMap) -> HeaderMap {
    let mut headers = HeaderMap::with_capacity(XHR_ORDER.len());
    for name in XHR_INHERITED {
        if let Some(value) = navigation.get(name) {
            headers.insert(name, value.clone());
        }
    }
    headers.insert(
        "x-requested-with",
        HeaderValue::from_static("XMLHttpRequest"),
    );
    headers.insert(ACCEPT, HeaderValue::from_static(XHR_ACCEPT));
    headers.insert("sec-fetch-site", HeaderValue::from_static("same-origin"));
    headers.insert("sec-fetch-mode", HeaderValue::from_static("cors"));
    headers.insert("sec-fetch-dest", HeaderValue::from_static("empty"));
    if let Some(language) = navigation.get(ACCEPT_LANGUAGE) {
        headers.insert(ACCEPT_LANGUAGE, language.clone());
    }
    if !headers.contains_key(ACCEPT_ENCODING) {
        headers.insert(
            ACCEPT_ENCODING,
            HeaderValue::from_static("gzip, deflate, br, zstd"),
        );
    }
    if navigation.contains_key("priority") {
        headers.insert("priority", HeaderValue::from_static("u=1, i"));
    }
    headers
}

fn query_string(query: &[(String, i64)]) -> Result<String, Error> {
    let mut out = String::new();
    for (index, (key, value)) in query.iter().enumerate() {
        if key.is_empty() || !key.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_') {
            return Err(Error::Other(format!(
                "query key {key:?} is not a plain name"
            )));
        }
        out.push(if index == 0 { '?' } else { '&' });
        out.push_str(key);
        out.push('=');
        out.push_str(&value.to_string());
    }
    Ok(out)
}

fn header_map(pairs: &[(String, String)]) -> Result<HeaderMap, Error> {
    let mut headers = HeaderMap::with_capacity(pairs.len());
    for (name, value) in pairs {
        let name = HeaderName::from_bytes(name.as_bytes())
            .map_err(|_| Error::Other(format!("invalid header name {name:?}")))?;
        let value = HeaderValue::from_str(value)
            .map_err(|_| Error::Other(format!("invalid value for header {name}")))?;
        headers.insert(name, value);
    }
    Ok(headers)
}

/// Map a wreq error to the retry decision the caller needs.
fn classify(err: &wreq::Error) -> Error {
    let message = chain(err);
    if err.is_timeout() {
        Error::Timeout(message)
    } else if err.is_connect()
        || err.is_connection_reset()
        || err.is_tls()
        || err.is_request()
        || err.is_body()
        || broke_connection(err)
    {
        Error::Network(message)
    } else {
        Error::Other(message)
    }
}

/// Whether the connection failed under the error: wreq reports a body cut
/// short as a decode error, but its cause is a protocol error, unlike a body
/// that does not decompress.
fn broke_connection(err: &wreq::Error) -> bool {
    let mut source = std::error::Error::source(err);
    while let Some(cause) = source {
        if let Some(proto) = cause.downcast_ref::<wreq_proto::Error>() {
            return !proto.is_user() && !proto.is_parse();
        }
        source = cause.source();
    }
    false
}

/// The error and its sources, as "outer: inner: ...".
fn chain(err: &dyn std::error::Error) -> String {
    let mut message = err.to_string();
    let mut source = err.source();
    while let Some(cause) = source {
        let text = cause.to_string();
        if !message.contains(&text) {
            message.push_str(": ");
            message.push_str(&text);
        }
        source = cause.source();
    }
    message
}

#[cfg(test)]
mod tests {
    use super::*;

    fn options(profile: &str, platform: &str) -> Options {
        Options {
            base_url: "https://t.me".into(),
            profile: profile.into(),
            platform: platform.into(),
            accept_language: "ru".into(),
            timeout: Duration::from_secs(5),
            session_lifetime: None,
        }
    }

    fn names(headers: &HeaderMap) -> Vec<&str> {
        headers.keys().map(HeaderName::as_str).collect()
    }

    #[test]
    fn profiles_are_chromium_and_build() {
        let all = profiles();
        assert!(all.iter().any(|name| name == "chrome_149"));
        assert_eq!(all.last().map(String::as_str), Some(chrome::NAME));
        assert!(all.iter().all(|name| is_chromium(name)));
        for name in &all {
            assert!(emulation(name, "android", "ru").is_ok(), "{name}");
        }
    }

    #[test]
    fn our_profile_rejects_ios_and_the_rest_allow_it() {
        assert!(matches!(
            emulation(chrome::NAME, "ios", "ru"),
            Err(Error::Config(_))
        ));
        assert!(emulation("chrome_149", "ios", "ru").is_ok());
    }

    #[test]
    fn rejects_unknown_and_non_chromium_profiles() {
        for name in ["firefox_151", "safari_26", "chrome_999", ""] {
            assert!(
                matches!(parse_profile(name), Err(Error::Config(_))),
                "{name}"
            );
        }
        assert!(matches!(parse_platform("beos"), Err(Error::Config(_))));
        assert_eq!(parse_platform("android").ok(), Some(Platform::Android));
    }

    #[tokio::test]
    async fn navigation_headers_follow_the_profile() {
        let client = Client::new(&options(chrome::NAME, "android")).unwrap();
        let headers = client.navigation_headers();
        assert_eq!(
            names(headers),
            [
                "sec-ch-ua",
                "sec-ch-ua-mobile",
                "sec-ch-ua-platform",
                "upgrade-insecure-requests",
                "user-agent",
                "accept",
                "sec-fetch-site",
                "sec-fetch-mode",
                "sec-fetch-user",
                "sec-fetch-dest",
                "accept-encoding",
                "accept-language",
                "priority",
            ]
        );
        assert_eq!(headers["accept-language"], "ru");
        assert_eq!(headers["sec-ch-ua-mobile"], "?1");
        assert_eq!(headers["sec-ch-ua-platform"], "\"Android\"");
        assert!(headers["accept"].to_str().unwrap().contains("image/jxl"));
        let ua = client.user_agent();
        assert!(ua.contains("Chrome/156.") && ua.contains("Mobile"), "{ua}");
        assert!(headers["sec-ch-ua"].to_str().unwrap().contains("v=\"156\""));
    }

    #[test]
    fn cookie_goes_before_priority() {
        let names = |order: &OrigHeaderMap| -> Vec<String> {
            order
                .into_iter()
                .map(|(name, _)| name.as_str().to_owned())
                .collect()
        };
        let mut headers = HeaderMap::new();
        headers.insert(ACCEPT_LANGUAGE, HeaderValue::from_static("ru"));
        headers.insert("priority", HeaderValue::from_static("u=0, i"));
        assert_eq!(
            names(&navigation_order(&headers)),
            ["accept-language", "cookie", "priority"]
        );
        headers.remove("priority");
        assert_eq!(
            names(&navigation_order(&headers)),
            ["accept-language", "cookie"]
        );
    }

    #[test]
    fn lifetime_jitter_stays_in_range() {
        let lifetime = Duration::from_hours(4);
        for _ in 0..1000 {
            let jittered = jittered(lifetime);
            assert!(jittered >= lifetime.mul_f64(0.75) && jittered <= lifetime.mul_f64(1.25));
        }
    }

    #[tokio::test]
    async fn standalone_headers_match_the_client() {
        let client = Client::new(&options("chrome_149", "android")).unwrap();
        let headers = navigation_headers("chrome_149", "android", "ru").unwrap();
        assert_eq!(&headers, client.navigation_headers());
        assert!(navigation_headers("chrome_149", "android", "ru\r").is_err());
    }

    #[tokio::test]
    async fn desktop_platform_is_not_mobile() {
        let client = Client::new(&options("chrome_149", "windows")).unwrap();
        assert_eq!(client.navigation_headers()["sec-ch-ua-mobile"], "?0");
        assert!(!client.user_agent().contains("Mobile"));
    }

    #[tokio::test]
    async fn xhr_headers_keep_identity_and_change_fetch_metadata() {
        let client = Client::new(&options("chrome_149", "android")).unwrap();
        let xhr = &client.xhr;
        for name in XHR_INHERITED {
            assert_eq!(xhr[name], client.navigation[name], "{name}");
        }
        assert_eq!(xhr["sec-fetch-mode"], "cors");
        assert_eq!(xhr["sec-fetch-dest"], "empty");
        assert_eq!(xhr["sec-fetch-site"], "same-origin");
        assert_eq!(xhr["priority"], "u=1, i");
        assert_eq!(xhr["accept-language"], "ru");
        assert!(!xhr.contains_key("sec-fetch-user"));
        assert!(!xhr.contains_key("upgrade-insecure-requests"));
        // Every header sent has a place in the XHR order.
        for name in names(xhr) {
            assert!(XHR_ORDER.contains(&name), "{name}");
        }
    }

    #[tokio::test]
    async fn debug_names_base_and_user_agent() {
        let client = Client::new(&options("chrome_149", "android")).unwrap();
        let debug = format!("{client:?}");
        assert!(
            debug.contains("https://t.me") && debug.contains("Chrome/149."),
            "{debug}"
        );
        assert!(debug.contains("session: 1"), "{debug}");
    }

    #[test]
    fn xhr_headers_fill_in_what_a_profile_lacks() {
        let mut navigation = HeaderMap::new();
        navigation.insert(USER_AGENT, HeaderValue::from_static("agent"));
        let xhr = xhr_headers(&navigation);
        assert_eq!(xhr[ACCEPT_ENCODING], "gzip, deflate, br, zstd");
        assert_eq!(xhr[USER_AGENT], "agent");
        assert!(!xhr.contains_key("priority"));
        assert!(!xhr.contains_key(ACCEPT_LANGUAGE));
    }

    #[test]
    fn origin_requires_scheme_and_host_only() {
        assert_eq!(origin_of("https://t.me").unwrap(), "https://t.me");
        assert_eq!(
            origin_of("http://127.0.0.1:8080").unwrap(),
            "http://127.0.0.1:8080"
        );
        for base in ["t.me", "ftp://t.me", "https://t.me/s", ""] {
            assert!(origin_of(base).is_err(), "{base}");
        }
    }

    #[test]
    fn query_and_headers_are_checked() {
        assert_eq!(query_string(&[]).unwrap(), "");
        assert_eq!(
            query_string(&[("before".into(), 10), ("after".into(), -1)]).unwrap(),
            "?before=10&after=-1"
        );
        assert!(query_string(&[("a&b".into(), 1)]).is_err());
        assert!(header_map(&[("bad name".into(), "x".into())]).is_err());
        assert!(header_map(&[("x-ok".into(), "line\nbreak".into())]).is_err());
    }

    #[test]
    fn error_chain_joins_sources() {
        #[derive(Debug)]
        struct Outer(std::io::Error);
        impl fmt::Display for Outer {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str("request failed")
            }
        }
        impl std::error::Error for Outer {
            fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
                Some(&self.0)
            }
        }
        let err = Outer(std::io::Error::other("connection reset"));
        assert_eq!(chain(&err), "request failed: connection reset");
        assert_eq!(Error::Timeout("slow".into()).to_string(), "slow");
    }
}
