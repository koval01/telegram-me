//! Which addresses may talk to us: `PEER_ALLOW_CIDR`.

use std::net::IpAddr;

/// A network such as `10.0.0.0/16` or `fd00::/8`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Cidr {
    network: IpAddr,
    prefix: u8,
}

impl Cidr {
    /// Parse `a.b.c.d/n`, `x::y/n`, or a single address.
    ///
    /// # Errors
    ///
    /// When `text` is neither.
    pub fn parse(text: &str) -> Result<Self, String> {
        let text = text.trim();
        let (address, prefix) = text.split_once('/').unwrap_or((text, ""));
        let network: IpAddr = address
            .parse()
            .map_err(|_| format!("not an IP network: {text}"))?;
        let max = if network.is_ipv4() { 32 } else { 128 };
        let prefix = if prefix.is_empty() {
            max
        } else {
            prefix
                .parse::<u8>()
                .ok()
                .filter(|prefix| *prefix <= max)
                .ok_or_else(|| format!("bad prefix length: {text}"))?
        };
        Ok(Self { network, prefix })
    }

    /// Whether `ip` is in this network (IPv4-mapped IPv6 counts as IPv4).
    #[must_use]
    pub fn contains(&self, ip: IpAddr) -> bool {
        let ip = match ip {
            IpAddr::V6(v6) => v6.to_ipv4_mapped().map_or(ip, IpAddr::V4),
            IpAddr::V4(_) => ip,
        };
        match (self.network, ip) {
            (IpAddr::V4(network), IpAddr::V4(ip)) => {
                prefix_matches(&network.octets(), &ip.octets(), self.prefix)
            }
            (IpAddr::V6(network), IpAddr::V6(ip)) => {
                prefix_matches(&network.octets(), &ip.octets(), self.prefix)
            }
            _ => false,
        }
    }
}

fn prefix_matches(network: &[u8], ip: &[u8], prefix: u8) -> bool {
    let full = usize::from(prefix / 8);
    let rest = prefix % 8;
    if network[..full] != ip[..full] {
        return false;
    }
    rest == 0 || (network[full] ^ ip[full]) >> (8 - rest) == 0
}

/// The networks allowed to connect.
#[derive(Debug, Clone, Default)]
pub struct AllowList(Vec<Cidr>);

impl AllowList {
    /// Parse every entry.
    ///
    /// # Errors
    ///
    /// On the first entry that is not a network.
    pub fn parse<'a>(entries: impl IntoIterator<Item = &'a str>) -> Result<Self, String> {
        entries
            .into_iter()
            .filter(|entry| !entry.trim().is_empty())
            .map(Cidr::parse)
            .collect::<Result<_, _>>()
            .map(Self)
    }

    /// Whether `ip` may talk to us.
    #[must_use]
    pub fn allows(&self, ip: IpAddr) -> bool {
        self.0.iter().any(|cidr| cidr.contains(ip))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ip(text: &str) -> IpAddr {
        text.parse().unwrap()
    }

    #[test]
    fn networks_contain_their_addresses() {
        let vcn = Cidr::parse("10.0.0.0/16").unwrap();
        assert!(vcn.contains(ip("10.0.0.108")));
        assert!(vcn.contains(ip("10.0.255.1")));
        assert!(!vcn.contains(ip("10.1.0.1")));
        assert!(vcn.contains(ip("::ffff:10.0.0.165")));
        assert!(!vcn.contains(ip("fd00::1")));

        let odd = Cidr::parse("172.16.0.0/12").unwrap();
        assert!(odd.contains(ip("172.31.255.255")));
        assert!(!odd.contains(ip("172.32.0.0")));

        let v6 = Cidr::parse("fd00::/8").unwrap();
        assert!(v6.contains(ip("fdab::1")));
        assert!(!v6.contains(ip("fe80::1")));

        assert!(
            Cidr::parse("0.0.0.0/0")
                .unwrap()
                .contains(ip("203.0.113.7"))
        );
        let single = Cidr::parse("127.0.0.1").unwrap();
        assert!(single.contains(ip("127.0.0.1")));
        assert!(!single.contains(ip("127.0.0.2")));
    }

    #[test]
    fn bad_networks_are_errors() {
        for bad in ["10.0.0.0/33", "fd00::/129", "nope", "10.0.0.0/x", ""] {
            assert!(Cidr::parse(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn a_list_allows_any_of_its_networks() {
        let list = AllowList::parse(["10.0.0.0/16", " 127.0.0.0/8 ", ""]).unwrap();
        assert!(list.allows(ip("127.0.0.1")));
        assert!(list.allows(ip("10.0.3.4")));
        assert!(!list.allows(ip("192.168.1.1")));
        assert!(!AllowList::default().allows(ip("127.0.0.1")));
        assert!(AllowList::parse(["10.0.0.0/16", "x"]).is_err());
    }
}
