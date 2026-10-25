//! `http::Client` against a local HTTP/1.1 server (no internet needed).

use std::io::Write as _;
use std::sync::Arc;
use std::sync::atomic::{AtomicU32, Ordering};
use std::time::Duration;

use flate2::Compression;
use flate2::write::GzEncoder;
use telegram_me_native::http::{Client, Error, Options};
use tokio::io::{AsyncReadExt as _, AsyncWriteExt as _};
use tokio::net::TcpListener;

/// Serve until the test ends: `/slow` never answers, `/missing` is a 404,
/// `/forbidden` a 403; anything else echoes the request head back
/// gzip-compressed. `/cookie` also sets a new session cookie as t.me does
/// (`stel_ssid=1`, then 2, ...), `/short-cookie` one that lives a second.
async fn serve() -> String {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("bind a local port");
    let address = listener.local_addr().expect("bound address");
    let issued = Arc::new(AtomicU32::new(0));
    tokio::spawn(async move {
        loop {
            let (mut socket, _) = listener.accept().await.expect("accept a connection");
            let issued = Arc::clone(&issued);
            tokio::spawn(async move {
                let mut head = Vec::new();
                let mut buf = [0_u8; 4096];
                while !head.windows(4).any(|w| w == b"\r\n\r\n") {
                    let read = socket.read(&mut buf).await.expect("read the request");
                    if read == 0 {
                        return;
                    }
                    head.extend_from_slice(&buf[..read]);
                }
                let head = String::from_utf8(head).expect("ASCII request head");
                let target = head.split(' ').nth(1).unwrap_or_default().to_owned();
                if target.starts_with("/slow") {
                    tokio::time::sleep(Duration::from_secs(30)).await;
                    return;
                }
                if target.starts_with("/truncated") {
                    // Promises 100 bytes, sends 10, closes.
                    let reply = "HTTP/1.1 200 OK\r\ncontent-length: 100\r\n\r\n0123456789";
                    socket
                        .write_all(reply.as_bytes())
                        .await
                        .expect("send the reply");
                    return;
                }
                if target.starts_with("/corrupt") {
                    let reply = "HTTP/1.1 200 OK\r\ncontent-encoding: gzip\r\n\
                                 content-length: 10\r\nconnection: close\r\n\r\nnot gzip!!";
                    socket
                        .write_all(reply.as_bytes())
                        .await
                        .expect("send the reply");
                    // The client may have hung up already.
                    let _closed = socket.shutdown().await.is_ok();
                    return;
                }
                let status = if target.starts_with("/missing") {
                    "404 Not Found"
                } else if target.starts_with("/forbidden") {
                    "403 Forbidden"
                } else {
                    "200 OK"
                };
                let max_age = if target.starts_with("/short-cookie") {
                    "; max-age=1"
                } else {
                    ""
                };
                let set_cookie = if target.starts_with("/cookie") || !max_age.is_empty() {
                    let id = issued.fetch_add(1, Ordering::SeqCst) + 1;
                    format!(
                        "set-cookie: stel_ssid={id}; path=/; samesite=None; HttpOnly{max_age}\r\n"
                    )
                } else {
                    String::new()
                };
                let mut gz = GzEncoder::new(Vec::new(), Compression::fast());
                gz.write_all(head.as_bytes()).expect("compress in memory");
                let body = gz.finish().expect("compress in memory");
                let reply = format!(
                    "HTTP/1.1 {status}\r\ncontent-type: text/plain\r\ncontent-encoding: gzip\r\n\
                     {set_cookie}content-length: {}\r\nconnection: close\r\n\r\n",
                    body.len()
                );
                socket
                    .write_all(reply.as_bytes())
                    .await
                    .expect("send the reply");
                socket.write_all(&body).await.expect("send the body");
                // The client may have hung up already.
                let _closed = socket.shutdown().await.is_ok();
            });
        }
    });
    format!("http://{address}")
}

fn client(base_url: String, timeout: Duration) -> Client {
    client_with(base_url, timeout, None)
}

fn client_with(base_url: String, timeout: Duration, lifetime: Option<Duration>) -> Client {
    Client::new(&Options {
        base_url,
        profile: "chrome_156".into(),
        platform: "android".into(),
        accept_language: "ru".into(),
        timeout,
        session_lifetime: lifetime,
    })
    .expect("valid options")
}

const XHR: [(&str, &str); 1] = [("x-requested-with", "XMLHttpRequest")];

/// The echoed request head of `method path` (an XHR when `xhr`).
async fn head(client: &Client, method: &str, path: &str, xhr: bool) -> String {
    let extra: Vec<(String, String)> = if xhr {
        XHR.iter()
            .map(|(name, value)| ((*name).to_owned(), (*value).to_owned()))
            .collect()
    } else {
        Vec::new()
    };
    client
        .fetch(method, path, &[], &extra)
        .await
        .expect("the test server answers")
        .text
}

/// Header names of an echoed request head, lower-cased, in order.
fn header_names(head: &str) -> Vec<String> {
    head.lines()
        .skip(1)
        .filter_map(|line| line.split_once(':'))
        .map(|(name, _)| name.to_ascii_lowercase())
        .collect()
}

/// The last three header names before `host` (HTTP/1.1 appends it).
fn last_three(head: &str) -> Vec<String> {
    let mut names: Vec<String> = header_names(head)
        .into_iter()
        .filter(|name| name != "host")
        .collect();
    names.split_off(names.len().saturating_sub(3))
}

fn header<'a>(head: &'a str, name: &str) -> Option<&'a str> {
    head.lines().skip(1).find_map(|line| {
        let (key, value) = line.split_once(':')?;
        key.eq_ignore_ascii_case(name).then(|| value.trim())
    })
}

#[tokio::test]
async fn navigation_decodes_gzip_and_sends_profile_headers() {
    let client = client(serve().await, Duration::from_secs(5));
    let response = client
        .fetch("GET", "/s/durov", &[("before".into(), 10)], &[])
        .await
        .unwrap();
    assert_eq!(response.status, 200);
    let head = response.text;
    assert!(
        head.starts_with("GET /s/durov?before=10 HTTP/1.1\r\n"),
        "{head}"
    );
    assert_eq!(header(&head, "user-agent"), Some(client.user_agent()));
    assert_eq!(header(&head, "accept-language"), Some("ru"));
    assert_eq!(header(&head, "sec-fetch-mode"), Some("navigate"));
    assert_eq!(header(&head, "x-requested-with"), None);
}

#[tokio::test]
async fn xhr_post_has_empty_body_and_chromium_order() {
    let base = serve().await;
    let client = client(base.clone(), Duration::from_secs(5));
    let response = client
        .fetch(
            "POST",
            "/s/durov",
            &[("after".into(), 5)],
            &[("x-requested-with".into(), "XMLHttpRequest".into())],
        )
        .await
        .unwrap();
    let head = response.text;
    assert!(
        head.starts_with("POST /s/durov?after=5 HTTP/1.1\r\n"),
        "{head}"
    );
    assert_eq!(header(&head, "content-length"), Some("0"));
    assert_eq!(header(&head, "x-requested-with"), Some("XMLHttpRequest"));
    assert_eq!(header(&head, "sec-fetch-mode"), Some("cors"));
    assert_eq!(header(&head, "origin"), Some(base.as_str()));
    assert_eq!(
        header(&head, "referer"),
        Some(format!("{base}/s/durov").as_str())
    );
    assert_eq!(header(&head, "sec-fetch-user"), None);
    let order: Vec<String> = header_names(&head)
        .into_iter()
        .filter(|name| name != "host")
        .collect();
    assert_eq!(
        order,
        [
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
            "priority",
        ]
    );
}

#[tokio::test]
async fn cookies_are_kept_and_sent_back_where_chromium_puts_them() {
    let client = client(serve().await, Duration::from_secs(5));
    let first = head(&client, "GET", "/cookie", false).await;
    assert_eq!(header(&first, "cookie"), None);

    let navigation = head(&client, "GET", "/s/durov", false).await;
    assert_eq!(header(&navigation, "cookie"), Some("stel_ssid=1"));
    assert_eq!(
        last_three(&navigation),
        ["accept-language", "cookie", "priority"]
    );

    let xhr = head(&client, "POST", "/s/durov", true).await;
    assert_eq!(header(&xhr, "cookie"), Some("stel_ssid=1"));
    assert_eq!(last_three(&xhr), ["accept-language", "cookie", "priority"]);
    assert_eq!(client.session_id(), 1);
}

#[tokio::test]
async fn rejected_session_starts_over() {
    let client = client(serve().await, Duration::from_secs(5));
    head(&client, "GET", "/cookie", false).await;
    let response = client.fetch("GET", "/forbidden", &[], &[]).await.unwrap();
    assert_eq!(response.status, 403);
    let next = head(&client, "GET", "/s/durov", false).await;
    assert_eq!(header(&next, "cookie"), None);
    assert_eq!(client.session_id(), 2);
}

#[tokio::test]
async fn not_found_keeps_the_session() {
    let client = client(serve().await, Duration::from_secs(5));
    head(&client, "GET", "/cookie", false).await;
    let response = client.fetch("GET", "/missing", &[], &[]).await.unwrap();
    assert_eq!(response.status, 404);
    let next = head(&client, "GET", "/s/durov", false).await;
    assert_eq!(header(&next, "cookie"), Some("stel_ssid=1"));
    assert_eq!(client.session_id(), 1);
}

#[tokio::test]
async fn session_ends_after_its_lifetime() {
    // At most 1.25 x 200 ms with jitter.
    let client = client_with(
        serve().await,
        Duration::from_secs(5),
        Some(Duration::from_millis(200)),
    );
    head(&client, "GET", "/cookie", false).await;
    let soon = head(&client, "GET", "/s/durov", false).await;
    assert_eq!(header(&soon, "cookie"), Some("stel_ssid=1"));
    tokio::time::sleep(Duration::from_millis(300)).await;
    let later = head(&client, "GET", "/cookie", false).await;
    assert_eq!(header(&later, "cookie"), None);
    let next = head(&client, "GET", "/s/durov", false).await;
    assert_eq!(header(&next, "cookie"), Some("stel_ssid=2"));
    assert_eq!(client.session_id(), 2);
}

#[tokio::test]
async fn session_ends_when_its_cookies_expire() {
    let client = client(serve().await, Duration::from_secs(5));
    head(&client, "GET", "/short-cookie", false).await;
    let soon = head(&client, "GET", "/s/durov", false).await;
    assert_eq!(header(&soon, "cookie"), Some("stel_ssid=1"));
    assert_eq!(client.session_id(), 1);
    tokio::time::sleep(Duration::from_millis(1100)).await;
    let later = head(&client, "GET", "/s/durov", false).await;
    assert_eq!(header(&later, "cookie"), None);
    assert_eq!(client.session_id(), 2);
}

#[tokio::test]
async fn cookies_stay_in_the_session() {
    let client = client(serve().await, Duration::from_secs(5));
    let response = client.fetch("GET", "/cookie", &[], &[]).await.unwrap();
    // The response carries status and body, never the Set-Cookie header.
    assert!(!response.text.contains("stel_ssid"), "{}", response.text);
    assert!(!format!("{client:?}").contains("stel_ssid"));
    // Callers can not inject cookies either.
    let err = client
        .fetch(
            "GET",
            "/s/durov",
            &[],
            &[("cookie".into(), "stel_ssid=x".into())],
        )
        .await
        .unwrap_err();
    assert!(matches!(err, Error::Other(_)), "{err:?}");
}

#[tokio::test]
async fn error_status_is_a_response() {
    let client = client(serve().await, Duration::from_secs(5));
    let response = client.fetch("GET", "/missing", &[], &[]).await.unwrap();
    assert_eq!(response.status, 404);
}

#[tokio::test]
async fn slow_answer_is_a_timeout() {
    let client = client(serve().await, Duration::from_millis(300));
    let err = client.fetch("GET", "/slow", &[], &[]).await.unwrap_err();
    assert!(matches!(err, Error::Timeout(_)), "{err:?}");
}

#[tokio::test]
async fn body_cut_short_is_a_network_error() {
    let client = client(serve().await, Duration::from_secs(5));
    let err = client
        .fetch("GET", "/truncated", &[], &[])
        .await
        .unwrap_err();
    assert!(matches!(err, Error::Network(_)), "{err:?}");
}

#[tokio::test]
async fn corrupt_compression_is_not_retried() {
    let client = client(serve().await, Duration::from_secs(5));
    let err = client.fetch("GET", "/corrupt", &[], &[]).await.unwrap_err();
    assert!(matches!(err, Error::Other(_)), "{err:?}");
}

#[tokio::test]
async fn refused_connection_is_a_network_error() {
    // Bind and drop: the port is closed for the request.
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    drop(listener);
    let client = client(format!("http://{address}"), Duration::from_secs(2));
    let err = client.fetch("GET", "/s/durov", &[], &[]).await.unwrap_err();
    assert!(matches!(err, Error::Network(_)), "{err:?}");
}

#[tokio::test]
async fn bad_requests_are_rejected_before_sending() {
    let client = client("http://127.0.0.1:9".into(), Duration::from_secs(1));
    for (method, path) in [("PUT", "/s/durov"), ("GET", "s/durov")] {
        let err = client.fetch(method, path, &[], &[]).await.unwrap_err();
        assert!(matches!(err, Error::Other(_)), "{err:?}");
    }
}

#[test]
fn bad_options_are_config_errors() {
    let base = Options {
        base_url: "https://t.me".into(),
        profile: "chrome_156".into(),
        platform: "android".into(),
        accept_language: "ru".into(),
        timeout: Duration::from_secs(1),
        session_lifetime: None,
    };
    let cases = [
        Options {
            profile: "firefox_151".into(),
            ..base.clone()
        },
        Options {
            platform: "amiga".into(),
            ..base.clone()
        },
        Options {
            base_url: "https://t.me/s".into(),
            ..base.clone()
        },
        Options {
            platform: "ios".into(),
            ..base.clone()
        },
        Options {
            accept_language: "ru\n".into(),
            ..base
        },
    ];
    for options in cases {
        assert!(matches!(Client::new(&options), Err(Error::Config(_))));
    }
}
