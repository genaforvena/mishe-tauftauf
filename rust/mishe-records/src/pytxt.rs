//! The CPython `str` behaviours record verification depends on.
//!
//! `records.payload`/`records.envelope` select the record reference with
//! `body.splitlines()` and `line.lstrip().startswith("[record]")`, and match it with
//! `re.fullmatch`. Rust's `lines()` and `trim_start()` are not the same functions, so
//! the Python rules are written out here instead of assumed.

/// `str.splitlines()`: the Unicode line boundaries Python splits on are not just `\n`.
///
/// A trailing separator produces no final empty line, and `\r\n` counts once.
pub fn splitlines(s: &str) -> Vec<&str> {
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

/// `str.lstrip()`: Python's whitespace, which is Unicode `White_Space` plus the
/// `\x1c`-`\x1f` separators that Rust's `char::is_whitespace` does not include.
pub fn lstrip(s: &str) -> &str {
    s.trim_start_matches(is_python_space)
}

pub fn is_python_space(c: char) -> bool {
    c.is_whitespace() || matches!(c, '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{1f}')
}

/// `REFERENCE_RE.fullmatch` against `refs[0]`, the line as written (not stripped):
/// `\[record\] records/([0-9a-f]{64})\.json sha256=([0-9a-f]{64})\Z`.
///
/// Returns `(digest, expected)`. The two groups are returned apart because the caller
/// treats their disagreement as a fall-back, exactly as `cached_payload` does.
pub fn parse_reference(line: &str) -> Option<(&str, &str)> {
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
