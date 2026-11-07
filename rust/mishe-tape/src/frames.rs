//! Tape framing: the Rust counterpart of `feed.iter_feed_entries` and of the
//! line splitting in `feed._iter_text_lines`.
//!
//! Scope: framing only. Record envelope verification (`records.py`), the
//! verified-payload store and the role fold stay in Python for now. This
//! module therefore owns exactly one responsibility: turning the tape bytes
//! into entries, or into the first framing fault.
//!
//! Mirrors `src/mishe_tauftauf/feed.py`:
//! - `HEADER_RE`: optional `v1 `, 20 digits, timestamp, source, ` ::\n`.
//! - `_iter_text_lines`: lines end where `str.splitlines` ends them (LF, CR,
//!   CRLF, VT, FF, FS, GS, RS, NEL, LS, PS), keeping the terminator.
//! - `iter_feed_entries`: body lines carry `    | `, the terminator line is
//!   `    .\n` or `    .-\n`, and every fault names the line index it failed on.
//! - Error text is kept identical to the Python `FeedError` messages.

use std::fmt;

/// One entry as framed. `body` has the same text the Python reader yields.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    pub sequence: u128,
    pub timestamp: String,
    pub source: String,
    pub body: String,
}

/// A framing fault. `message` is the Python `FeedError` text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FrameError {
    pub message: String,
}

impl fmt::Display for FrameError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

/// Split one text segment the way `str.splitlines(keepends=True)` does.
/// A segment is a run of bytes up to and including LF, so a CRLF pair is
/// always inside one segment.
pub fn split_lines(text: &str, out: &mut Vec<String>) {
    let mut start = 0usize;
    let mut chars = text.char_indices().peekable();
    while let Some((index, ch)) = chars.next() {
        let end = match ch {
            '\r' => {
                if let Some(&(next_index, '\n')) = chars.peek() {
                    chars.next();
                    next_index + 1
                } else {
                    index + ch.len_utf8()
                }
            }
            '\n' | '\u{0b}' | '\u{0c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}'
            | '\u{2029}' => index + ch.len_utf8(),
            _ => continue,
        };
        out.push(text[start..end].to_string());
        start = end;
    }
    if start < text.len() {
        out.push(text[start..].to_string());
    }
}

/// `HEADER_RE.fullmatch`: the shape of a header line, without calendar checks.
/// Returns `(sequence, timestamp, source)` or `None` when the line is malformed.
fn parse_header(line: &str) -> Option<(u128, String, String)> {
    let rest = line.strip_suffix(" ::\n")?;
    let rest = rest.strip_prefix("v1 ").unwrap_or(rest);
    let mut parts = rest.split(' ');
    let seq_text = parts.next()?;
    let timestamp = parts.next()?;
    let source = parts.next()?;
    if parts.next().is_some() {
        return None;
    }
    if seq_text.len() != 20 || !seq_text.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let sequence: u128 = seq_text.parse().ok()?;
    if source.is_empty()
        || !source
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'.' | b'_' | b'/' | b'-'))
    {
        return None;
    }
    if !timestamp_shape(timestamp) {
        return None;
    }
    Some((sequence, timestamp.to_string(), source.to_string()))
}

/// `\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z`, the regex shape only.
fn timestamp_shape(ts: &str) -> bool {
    let core = match ts.strip_suffix('Z') {
        Some(core) => core,
        None => return false,
    };
    let main = match core.split_once('.') {
        Some((main, fraction)) => {
            if fraction.is_empty()
                || fraction.len() > 6
                || !fraction.bytes().all(|b| b.is_ascii_digit())
            {
                return false;
            }
            main
        }
        None => core,
    };
    let b = main.as_bytes();
    b.len() == 19
        && b[4] == b'-'
        && b[7] == b'-'
        && b[10] == b'T'
        && b[13] == b':'
        && b[16] == b':'
        && [0, 1, 2, 3, 5, 6, 8, 9, 11, 12, 14, 15, 17, 18]
            .iter()
            .all(|&i| b[i].is_ascii_digit())
}

/// The calendar and clock ranges that `datetime.fromisoformat` enforces.
/// Only called on a timestamp whose shape already matched.
fn timestamp_in_range(ts: &str) -> bool {
    let core = ts.strip_suffix('Z').unwrap_or(ts);
    let main = core.split('.').next().unwrap_or(core);
    let field = |range: std::ops::Range<usize>| -> u32 { main[range].parse().unwrap_or(0) };
    let (year, month, day) = (field(0..4), field(5..7), field(8..10));
    let (hour, minute, second) = (field(11..13), field(14..16), field(17..19));
    (1..=12).contains(&month)
        && day >= 1
        && day <= days_in_month(year, month)
        && hour <= 23
        && minute <= 59
        && second <= 59
}

fn days_in_month(year: u32, month: u32) -> u32 {
    match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        _ => {
            let leap = (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
            if leap {
                29
            } else {
                28
            }
        }
    }
}

/// Stateful framer fed one line at a time, in tape order. It yields entries
/// and records the first fault, with the same line indexes as the Python reader.
pub struct Framer {
    expected: u128,
    index: u64,
    current: Option<Open>,
}

struct Open {
    sequence: u128,
    timestamp: String,
    source: String,
    body: String,
    final_newline: Option<bool>,
}

impl Framer {
    pub fn new(start_sequence: u128) -> Self {
        Framer {
            expected: start_sequence,
            index: 0,
            current: None,
        }
    }

    /// Feed one line. Returns an entry when a body closes, or a fault.
    pub fn push(&mut self, line: &str) -> Result<Option<Entry>, FrameError> {
        match self.current.take() {
            None => {
                self.index += 1;
                let (sequence, timestamp, source) = parse_header(line).ok_or_else(|| FrameError {
                    message: format!("malformed header at line {}", self.index),
                })?;
                if sequence != self.expected {
                    return Err(FrameError {
                        message: format!(
                            "non-contiguous sequence {}, expected {}",
                            sequence, self.expected
                        ),
                    });
                }
                if !timestamp_in_range(&timestamp) {
                    return Err(FrameError {
                        message: format!("invalid timestamp at sequence {}", sequence),
                    });
                }
                self.current = Some(Open {
                    sequence,
                    timestamp,
                    source,
                    body: String::new(),
                    final_newline: None,
                });
                Ok(None)
            }
            Some(mut open) => {
                self.index += 1;
                if line == "    .\n" {
                    open.final_newline = Some(true);
                    return self.close(open);
                }
                if line == "    .-\n" {
                    open.final_newline = Some(false);
                    return self.close(open);
                }
                if !line.starts_with("    | ") || !line.ends_with('\n') {
                    return Err(FrameError {
                        message: format!("malformed body framing at line {}", self.index),
                    });
                }
                open.body.push_str(&line[6..]);
                self.current = Some(open);
                Ok(None)
            }
        }
    }

    /// Called when the input ends. Fails if an entry was still open.
    pub fn finish(&self) -> Result<(), FrameError> {
        match &self.current {
            None => Ok(()),
            Some(open) => Err(FrameError {
                message: format!("unterminated entry {}", open.sequence),
            }),
        }
    }

    fn close(&mut self, open: Open) -> Result<Option<Entry>, FrameError> {
        let sequence = open.sequence;
        let mut body = open.body;
        if open.final_newline == Some(false) {
            if !body.ends_with('\n') {
                return Err(FrameError {
                    message: format!("malformed no-newline entry {}", sequence),
                });
            }
            body.pop();
        }
        if body.is_empty() {
            return Err(FrameError {
                message: format!("empty body at sequence {}", sequence),
            });
        }
        self.expected += 1;
        Ok(Some(Entry {
            sequence,
            timestamp: open.timestamp,
            source: open.source,
            body,
        }))
    }
}

/// Why framing stopped: a framing fault (`message` is the Python `FeedError`
/// text) or an input that is not UTF-8 (fail-closed; see the note in `read`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Stop {
    Fault(FrameError),
    Io(String),
}

/// Stream framed entries from a reader, one segment at a time, so memory is
/// bounded by one segment and one entry rather than by the tape. Every entry
/// before a fault is passed to `sink` before the fault is returned.
///
/// The Python reader decodes 1 MiB chunks, so for invalid UTF-8 it may withhold
/// the entries in the chunk that contains the bad byte, and its decoder text
/// names a chunk-relative position. This reader decodes per line, so it can
/// emit entries before the bad byte; the fail-closed stop is the same, and the
/// decoder's own text differs. That text difference is the one documented
/// exception to byte equality (see the plan, section 4).
pub fn read<R: std::io::BufRead>(
    mut reader: R,
    start_sequence: u128,
    mut sink: impl FnMut(&Entry) -> std::io::Result<()>,
) -> Result<(), Stop> {
    let mut framer = Framer::new(start_sequence);
    let mut segment = Vec::new();
    let mut lines = Vec::new();
    loop {
        segment.clear();
        let read = reader
            .read_until(b'\n', &mut segment)
            .map_err(|err| Stop::Io(err.to_string()))?;
        if read == 0 {
            break;
        }
        let text = match std::str::from_utf8(&segment) {
            Ok(text) => text,
            Err(exc) => {
                return Err(Stop::Fault(FrameError {
                    message: format!("feed is not UTF-8: {}", exc),
                }))
            }
        };
        lines.clear();
        split_lines(text, &mut lines);
        for line in &lines {
            match framer.push(line) {
                Ok(Some(entry)) => sink(&entry).map_err(|err| Stop::Io(err.to_string()))?,
                Ok(None) => {}
                Err(err) => return Err(Stop::Fault(err)),
            }
        }
    }
    framer.finish().map_err(Stop::Fault)
}

/// Frame a whole byte slice into a vector. Used by tests and small inputs.
pub fn frame_bytes(data: &[u8], start_sequence: u128) -> (Vec<Entry>, Option<FrameError>) {
    let mut entries = Vec::new();
    let result = read(data, start_sequence, |entry| {
        entries.push(entry.clone());
        Ok(())
    });
    match result {
        Ok(()) => (entries, None),
        Err(Stop::Fault(err)) => (entries, Some(err)),
        Err(Stop::Io(message)) => (
            entries,
            Some(FrameError {
                message: format!("io: {}", message),
            }),
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn header(seq: u128, ts: &str, src: &str) -> String {
        format!("{:020} {} {} ::\n", seq, ts, src)
    }

    #[test]
    fn frames_a_simple_entry() {
        let data = format!("{}    | hello\n    .\n", header(1, "2026-11-07T23:00:00.123456Z", "genome"));
        let (entries, err) = frame_bytes(data.as_bytes(), 1);
        assert!(err.is_none());
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].body, "hello\n");
        assert_eq!(entries[0].source, "genome");
    }

    #[test]
    fn no_newline_terminator_strips_one_newline() {
        let data = format!("{}    | hi\n    .-\n", header(1, "2026-01-01T00:00:00Z", "a"));
        let (entries, err) = frame_bytes(data.as_bytes(), 1);
        assert!(err.is_none());
        assert_eq!(entries[0].body, "hi");
    }

    #[test]
    fn splits_on_every_python_line_break() {
        let mut out = Vec::new();
        split_lines("a\rb\r\nc\u{0b}d\u{2028}e\n", &mut out);
        assert_eq!(out, vec!["a\r", "b\r\n", "c\u{0b}", "d\u{2028}", "e\n"]);
    }

    #[test]
    fn rejects_bad_calendar_and_clock() {
        let ok = |t: &str| timestamp_shape(t) && timestamp_in_range(t);
        assert!(ok("2026-02-28T23:59:59Z"));
        assert!(!ok("2026-02-29T00:00:00Z"));
        assert!(ok("2028-02-29T00:00:00Z"));
        assert!(!ok("2026-13-01T00:00:00Z"));
        assert!(!ok("2026-01-01T24:00:00Z"));
        assert!(!ok("2026-01-01T00:00:60Z"));
        assert!(!timestamp_shape("2026-01-01T00:00:00.1234567Z"));
    }

    #[test]
    fn non_contiguous_sequence_reports_both_numbers() {
        let data = format!("{}    | a\n    .\n", header(2, "2026-01-01T00:00:00Z", "a"));
        let (entries, err) = frame_bytes(data.as_bytes(), 1);
        assert!(entries.is_empty());
        assert_eq!(err.unwrap().message, "non-contiguous sequence 2, expected 1");
    }

    #[test]
    fn unterminated_entry_is_a_fault_after_prior_entries() {
        let good = format!("{}    | a\n    .\n", header(1, "2026-01-01T00:00:00Z", "a"));
        let bad = format!("{}    | b\n", header(2, "2026-01-01T00:00:00Z", "a"));
        let (entries, err) = frame_bytes(format!("{good}{bad}").as_bytes(), 1);
        assert_eq!(entries.len(), 1);
        assert_eq!(err.unwrap().message, "unterminated entry 2");
    }

    #[test]
    fn malformed_header_names_its_line() {
        let (entries, err) = frame_bytes(b"not a header\n", 1);
        assert!(entries.is_empty());
        assert_eq!(err.unwrap().message, "malformed header at line 1");
    }

    #[test]
    fn accepts_v1_prefix() {
        let data = format!("v1 {}    | x\n    .\n", header(1, "2026-01-01T00:00:00Z", "a"));
        let (entries, err) = frame_bytes(data.as_bytes(), 1);
        assert!(err.is_none());
        assert_eq!(entries.len(), 1);
    }
}
