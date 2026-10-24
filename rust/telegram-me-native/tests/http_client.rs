//! `http::Client` against a local HTTP/1.1 server (no internet needed).

use std::io::Write as _;
use std::time::Duration;

use flate2::Compression;
use flate2::write::GzEncoder;
use telegram_me_native::http::{Client, Error, Options};
use tokio::io::{AsyncReadExt as _, AsyncWriteExt as _};
use tokio::net::TcpListener;

/// Serve until the test ends: `/slow` never answers, `/missing` is a 404,
/// anything else echoes the request head back gzip-compressed.
async fn serve() -> String {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    tokio::spawn(async move {
        loop {
            let (mut socket, _) = listener.accept().await.unwrap();
            tokio::spawn(async move {
                let mut head = Vec::new();
                let mut buf = [0_u8; 4096];
                while !head.windows(4).any(|w| w == b"\r\n\r\n") {
                    let read = socket.read(&mut buf).await.unwrap();
                    if read == 0 {
                        return;
                    }
                    head.extend_from_slice(&buf[..read]);
                }
                let head = String::from_utf8(head).unwrap();
                let target = head.split(' ').nth(1).unwrap_or_default().to_owned();
                if target.starts_with("/slow") {
                    tokio::time::sleep(Duration::from_secs(30)).await;
                    return;
                }
                let status = if target.starts_with("/missing") {
                    "404 Not Found"
                } else {
                    "200 OK"
                };
                let mut gz = GzEncoder::new(Vec::new(), Compression::fast());
                gz.write_all(head.as_bytes()).unwrap();
                let body = gz.finish().unwrap();
                let reply = format!(
                    "HTTP/1.1 {status}\r\ncontent-type: text/plain\r\ncontent-encoding: gzip\r\n\
                     content-length: {}\r\nconnection: close\r\n\r\n",
                    body.len()
                );
                socket.write_all(reply.as_bytes()).await.unwrap();
                socket.write_all(&body).await.unwrap();
                socket.shutdown().await.ok();
            });
        }
    });
    format!("http://{address}")
}

fn client(base_url: String, timeout: Duration) -> Client {
    Client::new(&Options {
        base_url,
        profile: "chrome_149".into(),
        platform: "android".into(),
        accept_language: "ru".into(),
        timeout,
    })
    .unwrap()
}

/// Header names of an echoed request head, lower-cased, in order.
fn header_names(head: &str) -> Vec<String> {
    head.lines()
        .skip(1)
        .filter_map(|line| line.split_once(':'))
        .map(|(name, _)| name.to_ascii_lowercase())
        .collect()
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
        profile: "chrome_149".into(),
        platform: "android".into(),
        accept_language: "ru".into(),
        timeout: Duration::from_secs(1),
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
            accept_language: "ru\n".into(),
            ..base
        },
    ];
    for options in cases {
        assert!(matches!(Client::new(&options), Err(Error::Config(_))));
    }
}
