//! `mishe-tape frames PATH | mishe-tape-facts` — byte-level facts per entry.
//!
//! Reads the framed output of `mishe-tape frames` from stdin and writes one
//! `DM`, `REF` or `OUT` line per fact, as described in CONTRACT.md. Exit 0 when
//! the whole input was read, 2 on malformed framing, 3 on read/write error, 1 on
//! a usage error.

use std::io::{self, BufRead, BufReader, BufWriter, Read, Write};
use std::process::ExitCode;

const DM_PREFIX: &[u8] = b"[dm] to=";
const RECORD_PREFIX: &[u8] = b"[record] records/";
const RECORD_MID: &[u8] = b".json sha256=";
const OUTCOME_PREFIX: &[u8] = b"Wall outcome ";
const BY: &[u8] = b" by ";

fn is_role(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_' || b == b'.' || b == b'-'
}

fn is_hex_lower(b: u8) -> bool {
    b.is_ascii_digit() || (b'a'..=b'f').contains(&b)
}

/// Python `str.isspace()`-compatible ASCII whitespace (what `\s` matches besides Unicode).
fn is_ascii_space(b: u8) -> bool {
    matches!(b, 0x09..=0x0D | 0x1C..=0x20)
}

/// Longest run of `pred` bytes starting at `start`; returns the end index.
fn run_end(body: &[u8], start: usize, pred: fn(u8) -> bool) -> usize {
    let mut end = start;
    while end < body.len() && pred(body[end]) {
        end += 1;
    }
    end
}

/// `^\[dm\] to=([A-Za-z0-9_.-]+)`
fn dm_target(body: &[u8]) -> Option<&[u8]> {
    if !body.starts_with(DM_PREFIX) {
        return None;
    }
    let start = DM_PREFIX.len();
    let end = run_end(body, start, is_role);
    if end > start { Some(&body[start..end]) } else { None }
}

/// First `\[record\] records/([0-9a-f]{64})\.json sha256=[0-9a-f]{64}` (group 1).
fn record_ref(body: &[u8]) -> Option<&[u8]> {
    let mut i = 0;
    while i + RECORD_PREFIX.len() <= body.len() {
        if body[i..].starts_with(RECORD_PREFIX) {
            let h = i + RECORD_PREFIX.len();
            let hash_end = h + 64;
            if hash_end <= body.len()
                && body[h..hash_end].iter().all(|&b| is_hex_lower(b))
                && body[hash_end..].starts_with(RECORD_MID)
            {
                let sha_start = hash_end + RECORD_MID.len();
                let sha_end = sha_start + 64;
                if sha_end <= body.len() && body[sha_start..sha_end].iter().all(|&b| is_hex_lower(b)) {
                    return Some(&body[h..hash_end]);
                }
            }
        }
        i += 1;
    }
    None
}

/// Result of the OUT rule: `Ok(Some(role))` a match, `Ok(None)` no match,
/// `Err(())` the body has non-ASCII bytes in the header token, where Python's
/// `\S` may differ: the caller must defer to the Python regex (`!` marker).
fn outcome_role(body: &[u8]) -> Result<Option<&[u8]>, ()> {
    if !body.starts_with(OUTCOME_PREFIX) {
        return Ok(None);
    }
    let token_start = OUTCOME_PREFIX.len();
    let mut i = token_start;
    while i < body.len() && !is_ascii_space(body[i]) {
        if body[i] >= 0x80 {
            return Err(());
        }
        i += 1;
    }
    if i == token_start || !body[i..].starts_with(BY) {
        return Ok(None);
    }
    let role_start = i + BY.len();
    let role_end = run_end(body, role_start, is_role);
    if role_end > role_start { Ok(Some(&body[role_start..role_end])) } else { Ok(None) }
}

/// Maximal ASCII digit runs as a comma list, `-` when none. `!` when a run touches
/// a non-ASCII byte (Python `\d` may differ there; caller defers to the regex).
fn digit_runs(body: &[u8]) -> Result<String, ()> {
    let mut out = String::new();
    let mut i = 0;
    let mut deferred = false;
    while i < body.len() {
        if body[i].is_ascii_digit() {
            let end = run_end(body, i, |b| b.is_ascii_digit());
            if i > 0 && body[i - 1] >= 0x80 || end < body.len() && body[end] >= 0x80 {
                deferred = true;
            }
            if !out.is_empty() {
                out.push(',');
            }
            out.push_str(std::str::from_utf8(&body[i..end]).unwrap_or("?"));
            i = end;
        } else {
            i += 1;
        }
    }
    if deferred {
        return Err(());
    }
    Ok(if out.is_empty() { "-".to_string() } else { out })
}

/// Parse `E <seq> <ts> <source> <len>` (no trailing newline).
fn parse_header(line: &[u8]) -> Option<(String, String, String, usize)> {
    let text = std::str::from_utf8(line).ok()?;
    let fields: Vec<&str> = text.split(' ').collect();
    if fields.len() != 5 || fields[0] != "E" {
        return None;
    }
    if fields[1].is_empty() || !fields[1].bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let len: usize = fields[4].parse().ok()?;
    Some((fields[1].to_string(), fields[2].to_string(), fields[3].to_string(), len))
}

fn fail(code: u8, message: &str) -> ExitCode {
    eprintln!("mishe-tape-facts: {}", message);
    ExitCode::from(code)
}

fn main() -> ExitCode {
    if std::env::args().len() != 1 {
        eprintln!("usage: mishe-tape frames PATH | mishe-tape-facts");
        return ExitCode::from(1);
    }
    let stdin = io::stdin();
    let mut input = BufReader::new(stdin.lock());
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let mut body: Vec<u8> = Vec::new();
    loop {
        let mut header = Vec::new();
        match input.read_until(b'\n', &mut header) {
            Ok(0) => break,
            Ok(_) => {}
            Err(err) => return fail(3, &format!("read failed: {}", err)),
        }
        if header.last() != Some(&b'\n') {
            return fail(2, "unterminated header line");
        }
        header.pop();
        let Some((seq, ts, source, len)) = parse_header(&header) else {
            return fail(2, "malformed E header");
        };
        body.clear();
        body.resize(len, 0);
        if let Err(err) = input.read_exact(&mut body) {
            return fail(2, &format!("short body: {}", err));
        }
        let mut terminator = [0u8; 1];
        if input.read_exact(&mut terminator).is_err() || terminator[0] != b'\n' {
            return fail(2, "missing body terminator");
        }
        // The framer guarantees UTF-8 bodies; a body that is not UTF-8 means the
        // input did not come from a valid `mishe-tape frames`. Fail closed before
        // emitting anything for this entry.
        if std::str::from_utf8(&body).is_err() {
            return fail(2, "body is not UTF-8");
        }
        let written = (|| -> io::Result<()> {
            if let Some(target) = dm_target(&body) {
                out.write_all(format!("DM {} {} {} ", seq, ts, source).as_bytes())?;
                out.write_all(target)?;
                out.write_all(b"\n")?;
            }
            if let Some(sha) = record_ref(&body) {
                out.write_all(format!("REF {} {} ", seq, ts).as_bytes())?;
                out.write_all(sha)?;
                out.write_all(b"\n")?;
            }
            match outcome_role(&body) {
                Ok(Some(role)) => {
                    let runs = digit_runs(&body).unwrap_or_else(|()| "!".to_string());
                    out.write_all(format!("OUT {} {} ", seq, ts).as_bytes())?;
                    out.write_all(role)?;
                    out.write_all(format!(" {}\n", runs).as_bytes())?;
                }
                Ok(None) => {}
                Err(()) => out.write_all(format!("DEFER {} {}\n", seq, ts).as_bytes())?,
            }
            Ok(())
        })();
        if let Err(err) = written {
            return fail(3, &format!("write failed: {}", err));
        }
    }
    if let Err(err) = out.flush() {
        return fail(3, &format!("write failed: {}", err));
    }
    ExitCode::from(0)
}
