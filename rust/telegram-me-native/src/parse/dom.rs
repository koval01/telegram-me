//! A thin DOM layer over `scraper` (html5ever) that reproduces the semantics
//! of selectolax/lexbor the original Python parser relied on:
//!
//! * `select`/`first` match the node itself *and* its descendants;
//! * `text` concatenates every descendant text node, `text_strip` strips each
//!   text node before concatenating;
//! * `outer_html` serialises like lexbor (attribute order kept, `& < > nbsp`
//!   escaped in text, `& < > " nbsp` in attributes, raw text in
//!   `script`/`style`);
//! * `unwrap` models selectolax's in-place `unwrap_tags`: every later
//!   serialisation of that subtree sees the listed tags replaced by their
//!   children.

use std::cell::RefCell;

use ego_tree::{NodeId, NodeRef};
use scraper::node::Node;
use scraper::{ElementRef, Html, Selector};

/// Compile a CSS selector once per call site.
macro_rules! css {
    ($selector:literal) => {{
        static SELECTOR: std::sync::LazyLock<scraper::Selector> = std::sync::LazyLock::new(|| {
            scraper::Selector::parse($selector).expect("valid static selector")
        });
        &*SELECTOR
    }};
}
pub(crate) use css;

const VOID_ELEMENTS: &[&str] = &[
    "area", "base", "basefont", "bgsound", "br", "col", "embed", "frame", "hr", "img", "input",
    "keygen", "link", "meta", "param", "source", "track", "wbr",
];
const RAW_TEXT_ELEMENTS: &[&str] = &[
    "script",
    "style",
    "xmp",
    "iframe",
    "noembed",
    "noframes",
    "plaintext",
];

/// A parsed document plus the tag unwrapping applied to some of its subtrees.
pub struct Document {
    html: Html,
    unwrapped: RefCell<Vec<(NodeId, &'static [&'static str])>>,
}

impl Document {
    /// Parse a full document (fragments get the implied `html/head/body`).
    #[must_use]
    pub fn parse(html: &str) -> Self {
        Self {
            html: Html::parse_document(html),
            unwrapped: RefCell::new(Vec::new()),
        }
    }

    /// The `<html>` element.
    #[must_use]
    pub fn root(&self) -> ElementRef<'_> {
        self.html.root_element()
    }

    /// A direct child of `<html>` (`head` or `body`).
    #[must_use]
    pub fn section(&self, name: &str) -> Option<ElementRef<'_>> {
        self.root()
            .child_elements()
            .find(|child| child.value().name() == name)
    }

    /// Replace `tags` inside `node` by their children for later serialisation.
    pub fn unwrap(&self, node: ElementRef<'_>, tags: &'static [&'static str]) {
        self.unwrapped.borrow_mut().push((node.id(), tags));
    }

    /// Serialise `node` (outer HTML) the way lexbor does.
    #[must_use]
    pub fn outer_html(&self, node: ElementRef<'_>) -> String {
        let unwrapped = self.unwrapped.borrow();
        let mut active: Vec<&'static [&'static str]> = Vec::new();
        let mut ancestor = Some(*node);
        while let Some(current) = ancestor {
            for (id, tags) in unwrapped.iter() {
                if *id == current.id() {
                    active.push(tags);
                }
            }
            ancestor = current.parent();
        }
        let mut out = String::with_capacity(256);
        serialize(*node, &unwrapped, &mut active, &mut out, false);
        out
    }
}

/// One step of the iterative serialisation: deeply nested markup must not
/// recurse (the release profile aborts on a stack overflow like on a panic).
enum Step<'a> {
    /// Serialise this node; `raw` when it sits in a raw-text element.
    Enter(NodeRef<'a, Node>, bool),
    /// After an element's children: its end tag, if any, and the unwrap
    /// rules it added.
    Leave(Option<&'a str>, usize),
}

fn serialize(
    node: NodeRef<'_, Node>,
    unwrapped: &[(NodeId, &'static [&'static str])],
    active: &mut Vec<&'static [&'static str]>,
    out: &mut String,
    raw_text: bool,
) {
    let mut steps = vec![Step::Enter(node, raw_text)];
    while let Some(step) = steps.pop() {
        let (node, raw_text) = match step {
            Step::Leave(end_tag, pushed) => {
                if let Some(name) = end_tag {
                    out.push_str("</");
                    out.push_str(name);
                    out.push('>');
                }
                active.truncate(active.len() - pushed);
                continue;
            }
            Step::Enter(node, raw_text) => (node, raw_text),
        };
        match node.value() {
            Node::Element(element) => {
                let pushed = unwrapped
                    .iter()
                    .filter(|(id, _)| *id == node.id())
                    .map(|(_, tags)| active.push(tags))
                    .count();
                let name = element.name();
                let skip_tag = active.iter().any(|tags| tags.contains(&name));
                if !skip_tag {
                    out.push('<');
                    out.push_str(name);
                    for (attribute, value) in &element.attrs {
                        out.push(' ');
                        if let Some(prefix) = &attribute.prefix {
                            out.push_str(prefix);
                            out.push(':');
                        }
                        out.push_str(&attribute.local);
                        out.push_str("=\"");
                        escape_attribute(value, out);
                        out.push('"');
                    }
                    out.push('>');
                }
                let void = VOID_ELEMENTS.contains(&name);
                steps.push(Step::Leave((!skip_tag && !void).then_some(name), pushed));
                if !void || skip_tag {
                    let raw = RAW_TEXT_ELEMENTS.contains(&name);
                    steps.extend(node.children().rev().map(|child| Step::Enter(child, raw)));
                }
            }
            Node::Text(text) => {
                if raw_text {
                    out.push_str(text);
                } else {
                    escape_text(text, out);
                }
            }
            Node::Comment(comment) => {
                out.push_str("<!--");
                out.push_str(comment);
                out.push_str("-->");
            }
            Node::Document | Node::Fragment => {
                steps.extend(node.children().rev().map(|child| Step::Enter(child, false)));
            }
            Node::Doctype(doctype) => {
                out.push_str("<!DOCTYPE ");
                out.push_str(doctype.name());
                out.push('>');
            }
            Node::ProcessingInstruction(_) => {}
        }
    }
}

fn escape_text(text: &str, out: &mut String) {
    for ch in text.chars() {
        match ch {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '\u{a0}' => out.push_str("&nbsp;"),
            _ => out.push(ch),
        }
    }
}

fn escape_attribute(text: &str, out: &mut String) {
    for ch in text.chars() {
        match ch {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            '"' => out.push_str("&quot;"),
            '\u{a0}' => out.push_str("&nbsp;"),
            _ => out.push(ch),
        }
    }
}

/// `node` itself (when it matches) followed by matching descendants.
#[must_use]
pub fn select<'a>(node: ElementRef<'a>, selector: &Selector) -> Vec<ElementRef<'a>> {
    let mut found = Vec::new();
    if selector.matches(&node) {
        found.push(node);
    }
    found.extend(node.select(selector));
    found
}

/// First match among `node` itself and its descendants.
#[must_use]
pub fn first<'a>(node: ElementRef<'a>, selector: &Selector) -> Option<ElementRef<'a>> {
    if selector.matches(&node) {
        Some(node)
    } else {
        node.select(selector).next()
    }
}

/// Concatenated text of every descendant text node.
#[must_use]
pub fn text(node: ElementRef<'_>) -> String {
    let mut out = String::new();
    for descendant in node.descendants() {
        if let Node::Text(text) = descendant.value() {
            out.push_str(text);
        }
    }
    out
}

/// Like [`text`], with every text node stripped of surrounding whitespace.
#[must_use]
pub fn text_strip(node: ElementRef<'_>) -> String {
    let mut out = String::new();
    for descendant in node.descendants() {
        if let Node::Text(text) = descendant.value() {
            out.push_str(text.trim());
        }
    }
    out
}

/// Attribute value.
#[must_use]
pub fn attr<'a>(node: ElementRef<'a>, name: &str) -> Option<&'a str> {
    node.value().attr(name)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn select_includes_the_node_itself() {
        let doc = Document::parse(r#"<div><i class="x"><i class="x"></i></i></div>"#);
        let outer = first(doc.root(), css!("i.x")).unwrap();
        assert_eq!(select(outer, css!("i.x")).len(), 2);
        assert_eq!(first(outer, css!("i.x")).unwrap().id(), outer.id());
    }

    #[test]
    fn text_and_text_strip() {
        let doc = Document::parse(r#"<span class="s">  a <b> b </b>  </span>"#);
        let span = first(doc.root(), css!("span.s")).unwrap();
        assert_eq!(text(span), "  a  b   ");
        assert_eq!(text_strip(span), "ab");
    }

    #[test]
    fn serialisation_matches_lexbor() {
        let doc = Document::parse(
            "<div class=\"w\"><a title=\"a<b>c&d\u{a0}e'f\" href=x>t&gt;u</a><!-- c --><br>\
             <script>if(a<b)x()</script><p>x&amp;y\u{a0}z &lt; \"q\"</p><img src=\"i\"></div>",
        );
        let div = first(doc.root(), css!("div.w")).unwrap();
        assert_eq!(
            doc.outer_html(div),
            "<div class=\"w\"><a title=\"a&lt;b&gt;c&amp;d&nbsp;e'f\" href=\"x\">t&gt;u</a>\
             <!-- c --><br><script>if(a<b)x()</script><p>x&amp;y&nbsp;z &lt; \"q\"</p>\
             <img src=\"i\"></div>"
        );
    }

    #[test]
    fn attribute_order_is_preserved() {
        let doc = Document::parse(r#"<a z="1" a="2" m="3">x</a>"#);
        let a = first(doc.root(), css!("a")).unwrap();
        assert_eq!(doc.outer_html(a), r#"<a z="1" a="2" m="3">x</a>"#);
    }

    #[test]
    fn unwrap_affects_later_serialisation_of_the_subtree() {
        let doc =
            Document::parse(r#"<div class="t">A <b>B <i>C</i></b> <a href="x">D</a><br/>E</div>"#);
        let div = first(doc.root(), css!("div.t")).unwrap();
        doc.unwrap(div, &["a", "b", "i"]);
        assert_eq!(doc.outer_html(div), r#"<div class="t">A B C D<br>E</div>"#);
        assert_eq!(text(div), "A B C DE");
    }
}
