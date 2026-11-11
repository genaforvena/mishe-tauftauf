//! Byte-level entry facts for `mishe-tape frames --facts`.
//!
//! Scope: the line-level predicates that decide whether an entry is a publication
//! *candidate* — the cheap discards in `outcome_events.classify_outcomes` and the
//! reference test in `feed._VerificationPass.verify` (`feed.py:674`). Framing
//! itself stays in `frames`; record verification, the kind of a cited record and
//! the classifer's own policy stay in Python. This module computes facts only.
//!
//! These are CPython `str` rules, so they are written out here rather than
//! approximated by Rust's `lines()`/`trim_start()`. The helpers mirror
//! `rust/mishe-records/src/pytxt.rs`, which is differential-tested against the
//! Python predicates; the framer has no dependencies, so they are copied, not
//! imported. The framer holds the body as valid UTF-8 and computes on the same
//! char sequence Python sees, so no DEFER arm is needed: the facts are exact.
//!
//! The predicates are `outcome_publication`'s, by name:
//! - `has_outcome_prefix(first_significant_line(lines))` — signature text
//!   `Wall outcome ` at the first non-blank line, after `str.lstrip`.
//! - `reference_lines(lines)` — lines whose `str.lstrip` opens with `[record]`.
//! - `sole_final_reference(lines)` — exactly one such line and it is the last.
//! - `reference_digest(refs[0])` — `REFERENCE_RE.fullmatch` on the line as
//!   written, so an indented reference yields no digest and must be decoded.

/// `outcome_publication.OUTCOME_PREFIX`.
pub const OUTCOME_PREFIX: &str = "Wall outcome ";
/// `outcome_publication.REFERENCE_LINE_PREFIX`.
pub const REFERENCE_LINE_PREFIX: &str = "[record]";

/// One entry's byte-level eligibility facts. All fields are exact: the consumer
/// may skip its own body scan when they are present.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Facts {
    /// `has_outcome_prefix(first_significant_line(lines))`.
    pub reserved: bool,
    /// `len(reference_lines(lines))`.
    pub references: usize,
    /// `sole_final_reference(lines) is not None`.
    pub sole_final: bool,
    /// `len(lines) == 1`.
    pub single_line: bool,
    /// `reference_digest(refs[0])` when there is a reference line, else `None`.
    pub digest: Option<String>,
}

/// Compute the facts of one entry body, exactly as `outcome_publication` would.
pub fn facts(body: &str) -> Facts {
    let lines = splitlines(body);
    let reserved = has_outcome_prefix(first_significant_line(&lines));
    let refs: Vec<&str> = reference_lines(&lines);
    let sole_final = !lines.is_empty() && refs.len() == 1 && refs[0] == lines[lines.len() - 1];
    let digest = refs.first().and_then(|line| parse_reference(line)).map(|(digest, _)| digest.to_string());
    Facts { reserved, references: refs.len(), sole_final, single_line: lines.len() == 1, digest }
}

/// `str.splitlines()`: Python splits on more than `\n`, a trailing separator
/// produces no final empty line, and `\r\n` counts once. Mirrors
/// `pytxt::splitlines`.
fn splitlines(s: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0usize;
    let mut it = s.char_indices().peekable();
    while let Some((i, c)) = it.next() {
        if is_line_boundary(c) {
            out.push(&s[start..i]);
            if c == '\r' {
                if let Some(&(_, '\n')) = it.peek() {
                    it.next();
                }
            }
            start = it.peek().map(|(j, _)| *j).unwrap_or(s.len());
        }
    }
    if start < s.len() {
        out.push(&s[start..]);
    }
    out
}

fn is_line_boundary(c: char) -> bool {
    matches!(
        c,
        '\n' | '\r' | '\u{0b}' | '\u{0c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}'
    )
}

/// `str.lstrip()`: Python's whitespace is Unicode `White_Space` plus the
/// `\x1c`-`\x1f` separators Rust's `char::is_whitespace` omits.
fn lstrip(s: &str) -> &str {
    s.trim_start_matches(is_python_space)
}

fn is_python_space(c: char) -> bool {
    c.is_whitespace() || matches!(c, '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{1f}')
}

/// `outcome_publication.has_outcome_prefix`.
fn has_outcome_prefix(line: &str) -> bool {
    lstrip(line).starts_with(OUTCOME_PREFIX)
}

/// `outcome_publication.first_significant_line`: the first line carrying
/// content, or empty text when none does. `str.strip()` empty means every
/// character is Python whitespace.
fn first_significant_line<'a>(lines: &'a [&'a str]) -> &'a str {
    lines.iter().copied().find(|line| !line.chars().all(is_python_space)).unwrap_or("")
}

/// `outcome_publication.reference_lines`.
fn reference_lines<'a>(lines: &'a [&'a str]) -> Vec<&'a str> {
    lines.iter().copied().filter(|line| lstrip(line).starts_with(REFERENCE_LINE_PREFIX)).collect()
}

/// `REFERENCE_RE.fullmatch` on the line as written: `[record] records/<64
/// lowercase hex>.json sha256=<64 lowercase hex>`. Mirrors `pytxt::parse_reference`.
fn parse_reference(line: &str) -> Option<(&str, &str)> {
    let rest = line.strip_prefix("[record] records/")?;
    let (digest, rest) = rest.split_at_checked(64)?;
    if !is_lower_hex(digest) {
        return None;
    }
    let rest = rest.strip_prefix(".json sha256=")?;
    if rest.len() != 64 || !is_lower_hex(rest) {
        return None;
    }
    Some((digest, rest))
}

fn is_lower_hex(s: &str) -> bool {
    s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plain_body_has_no_facts() {
        let f = facts("hello world\n");
        assert_eq!(f, Facts { reserved: false, references: 0, sole_final: false, single_line: true, digest: None });
    }

    #[test]
    fn reserved_after_blank_lines() {
        let f = facts("\n  \nWall outcome accepted by rust-plan\n");
        assert!(f.reserved);
        assert_eq!(f.references, 0);
        assert!(!f.single_line);
    }

    #[test]
    fn indented_reference_is_a_reference_without_a_digest() {
        let body = "  [record] records/0".to_string() + &"a".repeat(63) + ".json sha256=" + &"b".repeat(64) + "\n";
        let f = facts(&body);
        assert_eq!(f.references, 1);
        assert!(f.sole_final);
        assert_eq!(f.digest, None);
    }

    #[test]
    fn final_reference_carries_its_digest() {
        let digest = "c".repeat(64);
        let body = format!("Wall outcome accepted by genome\n[record] records/{digest}.json sha256={digest}\n");
        let f = facts(&body);
        assert!(f.reserved);
        assert_eq!(f.references, 1);
        assert!(f.sole_final);
        assert!(!f.single_line);
        assert_eq!(f.digest.as_deref(), Some(digest.as_str()));
    }

    #[test]
    fn two_references_are_not_sole_final() {
        let d = "d".repeat(64);
        let body = format!("[record] records/{d}.json sha256={d}\n[record] records/{d}.json sha256={d}\n");
        let f = facts(&body);
        assert_eq!(f.references, 2);
        assert!(!f.sole_final);
    }
}
