//! Every page parser over arbitrary input: no panic, no hang, bounded memory.
#![no_main]

use libfuzzer_sys::fuzz_target;
use telegram_me_native::parse::{parse_channel_page, parse_more_page, parse_preview_page};

fuzz_target!(|data: &[u8]| {
    let html = String::from_utf8_lossy(data);
    for simplified in [false, true] {
        let _ = parse_channel_page(&html, simplified);
        let _ = parse_more_page(&html, simplified);
    }
    let _ = parse_preview_page(&html);
});
