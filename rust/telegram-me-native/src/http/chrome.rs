//! Our own profile of the current stable Chrome, on wreq's emulation API.
//!
//! wreq-util's newest Chrome profile trails the stable channel by several
//! releases, and Chrome now ships every two weeks. Between releases only the
//! version in the `User-Agent` and `sec-ch-ua` changes, so [`MAJOR`] is the one
//! constant to bump; `sec-ch-ua` is derived from it with Chromium's own
//! algorithm.
//!
//! Compared with a capture of Chrome 155 (`tls.peet.ws`), three parts of the
//! `ClientHello` are missing because the `BoringSSL` that `btls-sys` 0.5.6
//! builds (API version 37) can not produce them:
//!
//! - the ML-DSA signature algorithms `0x0904`–`0x0906`: its table of
//!   signature algorithms lacks them, and `set_sigalg_prefs` rejects any
//!   codepoint not in that table, so they can not be configured at all (and
//!   so a server can never pick one we could not verify);
//! - a GREASE value in `signature_algorithms`;
//! - the `trust_anchors` extension (`0xca34`): the C library has it, but
//!   neither `btls` nor wreq exposes it.
//!
//! Everything else matches the capture: cipher suites, groups and key shares
//! (`X25519MLKEM768`, `X25519`), GREASE, extension permutation, ALPS with the
//! new codepoint, brotli certificate compression, ECH GREASE, the HTTP/2
//! preface and the header order. The HTTP/2 `HEADERS` priority is weight 256,
//! as Chrome sends it for a navigation and an XHR alike (wreq-util sends 220).

use wreq::Group;
use wreq::header::{
    ACCEPT, ACCEPT_ENCODING, ACCEPT_LANGUAGE, HeaderMap, HeaderName, HeaderValue, USER_AGENT,
};
use wreq::http2::{
    Http2Options, PseudoId, PseudoOrder, SettingId, SettingsOrder, StreamDependency, StreamId,
};
use wreq::tls::compress::CertificateCompressor;
use wreq::tls::{AlpsProtocol, TlsOptions, TlsVersion};
use wreq_util::Platform;
use wreq_util::emulate::compress::BrotliCompressor;

/// The profile's name in `TELEGRAM_BROWSER_PROFILE`.
pub const NAME: &str = "chrome_156";

/// Major version of the current stable Chrome (full rollout 2026-10-20).
const MAJOR: usize = 156;

const CIPHERS: &str = concat!(
    "TLS_AES_128_GCM_SHA256:TLS_AES_256_GCM_SHA384:TLS_CHACHA20_POLY1305_SHA256:",
    "TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256:TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256:",
    "TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384:TLS_ECDHE_RSA_WITH_AES_256_GCM_SHA384:",
    "TLS_ECDHE_ECDSA_WITH_CHACHA20_POLY1305_SHA256:TLS_ECDHE_RSA_WITH_CHACHA20_POLY1305_SHA256:",
    "TLS_ECDHE_RSA_WITH_AES_128_CBC_SHA:TLS_ECDHE_RSA_WITH_AES_256_CBC_SHA:",
    "TLS_RSA_WITH_AES_128_GCM_SHA256:TLS_RSA_WITH_AES_256_GCM_SHA384:",
    "TLS_RSA_WITH_AES_128_CBC_SHA:TLS_RSA_WITH_AES_256_CBC_SHA",
);

/// Chrome's groups; `BoringSSL` sends key shares for the first two.
const CURVES: &str = "X25519MLKEM768:X25519:P-256:P-384";

/// Chrome's signature algorithms after its ML-DSA ones (see the module docs).
const SIGALGS: &str = concat!(
    "ecdsa_secp256r1_sha256:rsa_pss_rsae_sha256:rsa_pkcs1_sha256:",
    "ecdsa_secp384r1_sha384:rsa_pss_rsae_sha384:rsa_pkcs1_sha384:",
    "rsa_pss_rsae_sha512:rsa_pkcs1_sha512",
);

const CERTIFICATE_COMPRESSORS: &[&'static dyn CertificateCompressor] = &[&BrotliCompressor];

/// `accept` of a navigation; `image/jxl` since Chrome 155 decodes JPEG XL.
const NAVIGATION_ACCEPT: &str = "text/html,application/xhtml+xml,application/xml;q=0.9,\
    image/jxl,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7";

/// The emulation of this profile on `platform`.
///
/// # Errors
///
/// A message for iOS: Chrome there is `WebKit` on Apple's TLS stack, so a
/// `BoringSSL` handshake would contradict the `User-Agent`.
pub fn emulation(platform: Platform) -> Result<wreq::Emulation, String> {
    let headers = headers(platform)?;
    Ok(wreq::Emulation::builder()
        .tls_options(tls_options())
        .http2_options(http2_options())
        .headers(headers)
        .build(Group::new(NAME)))
}

fn tls_options() -> TlsOptions {
    TlsOptions::builder()
        .grease_enabled(true)
        .enable_ocsp_stapling(true)
        .enable_signed_cert_timestamps(true)
        .curves_list(CURVES)
        .sigalgs_list(SIGALGS)
        .cipher_list(CIPHERS)
        .min_tls_version(TlsVersion::TLS_1_2)
        .max_tls_version(TlsVersion::TLS_1_3)
        .permute_extensions(true)
        .pre_shared_key(true)
        .enable_ech_grease(true)
        .alps_protocols([AlpsProtocol::HTTP2])
        .alps_use_new_codepoint(true)
        .aes_hw_override(true)
        .certificate_compressors(CERTIFICATE_COMPRESSORS)
        .build()
}

fn http2_options() -> Http2Options {
    Http2Options::builder()
        .initial_window_size(6_291_456)
        .initial_connection_window_size(15_728_640)
        .max_header_list_size(262_144)
        .header_table_size(65_536)
        .enable_push(false)
        // The wire carries weight - 1: 255 is Chrome's 256.
        .headers_stream_dependency(StreamDependency::new(StreamId::zero(), 255, true))
        .headers_pseudo_order(
            PseudoOrder::builder()
                .extend([
                    PseudoId::Method,
                    PseudoId::Authority,
                    PseudoId::Scheme,
                    PseudoId::Path,
                ])
                .build(),
        )
        .settings_order(
            SettingsOrder::builder()
                .extend([
                    SettingId::HeaderTableSize,
                    SettingId::EnablePush,
                    SettingId::MaxConcurrentStreams,
                    SettingId::InitialWindowSize,
                    SettingId::MaxFrameSize,
                    SettingId::MaxHeaderListSize,
                    SettingId::EnableConnectProtocol,
                    SettingId::NoRfc7540Priorities,
                ])
                .build(),
        )
        .build()
}

/// Navigation headers in Chrome's order (`accept-language` is replaced later).
fn headers(platform: Platform) -> Result<HeaderMap, String> {
    let (os, name, mobile) = match platform {
        Platform::Windows => ("Windows NT 10.0; Win64; x64", "\"Windows\"", false),
        Platform::MacOS => ("Macintosh; Intel Mac OS X 10_15_7", "\"macOS\"", false),
        Platform::Linux => ("X11; Linux x86_64", "\"Linux\"", false),
        // The reduced User-Agent: Android version and model are frozen.
        Platform::Android => ("Linux; Android 10; K", "\"Android\"", true),
        _ => {
            return Err(format!(
                "{NAME} has no iOS variant: Chrome on iOS is not BoringSSL"
            ));
        }
    };
    let user_agent = format!(
        "Mozilla/5.0 ({os}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{MAJOR}.0.0.0 {}Safari/537.36",
        if mobile { "Mobile " } else { "" }
    );
    let value = |text: String| HeaderValue::from_str(&text).expect("generated headers are ASCII");
    let mut headers = HeaderMap::with_capacity(13);
    headers.insert("sec-ch-ua", value(brands(MAJOR)));
    headers.insert(
        "sec-ch-ua-mobile",
        HeaderValue::from_static(if mobile { "?1" } else { "?0" }),
    );
    headers.insert("sec-ch-ua-platform", HeaderValue::from_static(name));
    headers.insert("upgrade-insecure-requests", HeaderValue::from_static("1"));
    headers.insert(USER_AGENT, value(user_agent));
    headers.insert(ACCEPT, HeaderValue::from_static(NAVIGATION_ACCEPT));
    for (name, text) in [
        ("sec-fetch-site", "none"),
        ("sec-fetch-mode", "navigate"),
        ("sec-fetch-user", "?1"),
        ("sec-fetch-dest", "document"),
    ] {
        headers.insert(
            HeaderName::from_static(name),
            HeaderValue::from_static(text),
        );
    }
    headers.insert(
        ACCEPT_ENCODING,
        HeaderValue::from_static("gzip, deflate, br, zstd"),
    );
    headers.insert(ACCEPT_LANGUAGE, HeaderValue::from_static("en-US,en;q=0.9"));
    headers.insert("priority", HeaderValue::from_static("u=0, i"));
    Ok(headers)
}

/// `sec-ch-ua` of Google Chrome `major`: a GREASE brand and its version, and
/// the order of the three brands, all chosen by the major version as
/// Chromium's `GenerateBrandVersionList` does.
fn brands(major: usize) -> String {
    const CHARS: [char; 11] = [' ', '(', ':', '-', '.', '/', ')', ';', '=', '?', '_'];
    const VERSIONS: [&str; 3] = ["8", "99", "24"];
    const ORDERS: [[usize; 3]; 6] = [
        [0, 1, 2],
        [0, 2, 1],
        [1, 0, 2],
        [1, 2, 0],
        [2, 0, 1],
        [2, 1, 0],
    ];
    let grease = format!(
        "\"Not{}A{}Brand\";v=\"{}\"",
        CHARS[major % CHARS.len()],
        CHARS[(major + 1) % CHARS.len()],
        VERSIONS[major % VERSIONS.len()]
    );
    let order = ORDERS[major % ORDERS.len()];
    let mut list = [String::new(), String::new(), String::new()];
    list[order[0]] = grease;
    list[order[1]] = format!("\"Chromium\";v=\"{major}\"");
    list[order[2]] = format!("\"Google Chrome\";v=\"{major}\"");
    list.join(", ")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn brands_follow_chromium() {
        // Released Chrome builds: wreq-util's captures and our own (155).
        let known = [
            (
                132,
                r#""Not A(Brand";v="8", "Chromium";v="132", "Google Chrome";v="132""#,
            ),
            (
                147,
                r#""Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147""#,
            ),
            (
                148,
                r#""Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99""#,
            ),
            (
                149,
                r#""Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24""#,
            ),
            (
                155,
                r#""Google Chrome";v="155", "Chromium";v="155", "Not(A:Brand";v="24""#,
            ),
        ];
        for (major, expected) in known {
            assert_eq!(brands(major), expected, "{major}");
        }
    }

    #[test]
    fn android_headers_claim_mobile_chrome() {
        let headers = headers(Platform::Android).unwrap();
        assert_eq!(
            headers[USER_AGENT],
            "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) \
             Chrome/156.0.0.0 Mobile Safari/537.36"
        );
        assert_eq!(
            headers["sec-ch-ua"],
            r#""Not:A-Brand";v="8", "Chromium";v="156", "Google Chrome";v="156""#
        );
        assert_eq!(headers["sec-ch-ua-mobile"], "?1");
        assert_eq!(headers["sec-ch-ua-platform"], "\"Android\"");
    }

    #[test]
    fn desktop_platforms_and_no_ios() {
        for (platform, os, name) in [
            (Platform::Windows, "Windows NT 10.0", "\"Windows\""),
            (Platform::MacOS, "Mac OS X 10_15_7", "\"macOS\""),
            (Platform::Linux, "X11; Linux x86_64", "\"Linux\""),
        ] {
            let headers = headers(platform).unwrap();
            let ua = headers[USER_AGENT].to_str().unwrap();
            assert!(ua.contains(os) && !ua.contains("Mobile"), "{ua}");
            assert_eq!(headers["sec-ch-ua-mobile"], "?0");
            assert_eq!(headers["sec-ch-ua-platform"], name);
        }
        assert!(emulation(Platform::IOS).unwrap_err().contains("iOS"));
    }
}
