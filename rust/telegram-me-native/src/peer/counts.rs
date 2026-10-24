//! Rate-limit counts added up across nodes, without Redis.
//!
//! Every node sends the requests it accepted per client (once a second) to
//! every other node, and each node adds up what it sent and received per
//! client and minute: the same fixed windows as the Redis counters
//! (`tme:v2:rl:<minute>:<client>`). A client whose total goes over the limit
//! is blocked locally until the window ends, on every node, as each one sees
//! the total pass the limit.

use rustc_hash::FxHashMap;

/// Most clients counted in one window; beyond it new clients are not counted
/// (their own node still limits them).
const MAX_CLIENTS: usize = 200_000;

/// Totals per client in the current minute window.
#[derive(Default)]
pub struct WindowCounts {
    window: u64,
    totals: FxHashMap<String, u32>,
}

impl WindowCounts {
    /// Add `items` counted in `window`; returns the clients that went over
    /// `limit` with them.
    ///
    /// Counts of an older window are dropped, a newer window replaces the
    /// current one.
    pub fn add<'a, I>(&mut self, window: u64, items: I, limit: u32) -> Vec<String>
    where
        I: IntoIterator<Item = (&'a str, u32)>,
    {
        if window < self.window {
            return Vec::new();
        }
        if window > self.window {
            self.window = window;
            self.totals.clear();
        }
        let mut over = Vec::new();
        for (client, count) in items {
            let total = if let Some(total) = self.totals.get_mut(client) {
                total
            } else if self.totals.len() < MAX_CLIENTS {
                self.totals.entry(client.to_owned()).or_insert(0)
            } else {
                continue;
            };
            let before = *total;
            *total = total.saturating_add(count);
            if before <= limit && *total > limit {
                over.push(client.to_owned());
            }
        }
        over
    }

    /// Clients counted in the current window.
    #[must_use]
    pub fn len(&self) -> usize {
        self.totals.len()
    }

    /// True when no client is counted.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.totals.is_empty()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn totals_add_up_across_nodes_and_report_crossing_once() {
        let mut counts = WindowCounts::default();
        assert_eq!(
            counts.add(5, [("a", 60), ("b", 1)], 100),
            Vec::<String>::new()
        );
        assert_eq!(counts.add(5, [("a", 41)], 100), ["a"]);
        // Already over: not reported again.
        assert_eq!(counts.add(5, [("a", 10)], 100), Vec::<String>::new());
        assert_eq!(counts.len(), 2);
    }

    #[test]
    fn windows_reset_and_late_counts_are_dropped() {
        let mut counts = WindowCounts::default();
        counts.add(5, [("a", 90)], 100);
        assert_eq!(counts.add(6, [("a", 90)], 100), Vec::<String>::new());
        assert_eq!(counts.add(5, [("a", 90)], 100), Vec::<String>::new());
        assert_eq!(counts.add(6, [("a", 11)], 100), ["a"]);
    }

    #[test]
    fn counts_saturate() {
        let mut counts = WindowCounts::default();
        assert_eq!(counts.add(1, [("a", u32::MAX), ("a", u32::MAX)], 5), ["a"]);
    }
}
