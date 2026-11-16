//! mishe-unit-paths: decode the absolute paths a unit's `systemctl show` text declares.
//!
//! Contract: see CONTRACT.md. stdin is the show text; stdout is one absolute path
//! per line in first-seen order, deduplicated, or a single `DEFER\n` line when the
//! input contains any non-ASCII byte. Exit 0 result/DEFER, 1 usage/read error,
//! 2 data fault, 3 write error.

use std::collections::HashSet;
use std::io::{self, Read, Write};
use std::process::ExitCode;

/// Input bound: systemctl show for a few properties is well under 1 MiB.
const MAX_INPUT: u64 = 1 << 20;

/// Python `\w` for an ASCII byte: letter, digit or underscore.
fn is_word(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_'
}

/// A slash preceded by one of these cannot start an absolute path: the Python
/// lookbehind `(?<![\w./-])` rejects a word byte, '.', '/' or '-'.
fn suppresses_start(b: u8) -> bool {
    is_word(b) || matches!(b, b'.' | b'/' | b'-')
}

/// A byte the path body may contain: not Python ASCII `\s`, and not systemd's
/// list punctuation `;:,"'()`. ('/' is deliberately allowed inside a path.)
fn is_path_byte(b: u8) -> bool {
    let ascii_space = matches!(
        b,
        b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c | 0x1c | 0x1d | 0x1e | 0x1f
    );
    !ascii_space && !matches!(b, b';' | b':' | b',' | b'"' | b'\'' | b'(' | b')')
}

/// Byte-exact counterpart of the Python `ABSOLUTE_PATH.finditer` + `rstrip(".,")`
/// + dedupe, for ASCII input only. Non-ASCII input is deferred before this runs.
fn decode_ascii(text: &str) -> Vec<&str> {
    let bytes = text.as_bytes();
    let mut out: Vec<&str> = Vec::new();
    let mut seen: HashSet<&str> = HashSet::new();
    let mut i = 0usize;
    while i < bytes.len() {
        if bytes[i] == b'/' && (i == 0 || !suppresses_start(bytes[i - 1])) {
            let start = i;
            let mut end = i + 1;
            while end < bytes.len() && is_path_byte(bytes[end]) {
                end += 1;
            }
            // The `+` in the Python pattern needs at least one body byte after
            // the leading '/'; a bare slash cannot start a match.
            if end == i + 1 {
                i += 1;
                continue;
            }
            // str.rstrip(".,"): drop trailing rendering punctuation. The leading
            // '/' is never stripped, so the path stays non-empty.
            let mut stop = end;
            while stop > start && matches!(bytes[stop - 1], b'.' | b',') {
                stop -= 1;
            }
            let path = &text[start..stop];
            if seen.insert(path) {
                out.push(path);
            }
            // finditer resumes after the whole match, not after the stripped part.
            i = end;
        } else {
            i += 1;
        }
    }
    out
}

fn main() -> ExitCode {
    if std::env::args_os().len() != 1 {
        eprintln!("usage: mishe-unit-paths  (stdin: systemctl show text)");
        return ExitCode::from(1);
    }
    let mut raw = Vec::new();
    if let Err(e) = io::stdin().lock().take(MAX_INPUT + 1).read_to_end(&mut raw) {
        eprintln!("mishe-unit-paths: read: {e}");
        return ExitCode::from(1);
    }
    if raw.len() as u64 > MAX_INPUT {
        eprintln!("mishe-unit-paths: input over {MAX_INPUT} bytes");
        return ExitCode::from(2);
    }
    let text = match std::str::from_utf8(&raw) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("mishe-unit-paths: invalid UTF-8 at byte {}", e.valid_up_to());
            return ExitCode::from(2);
        }
    };
    let mut out = Vec::with_capacity(raw.len());
    if !text.is_ascii() {
        out.extend_from_slice(b"DEFER\n");
    } else {
        for path in decode_ascii(text) {
            out.extend_from_slice(path.as_bytes());
            out.push(b'\n');
        }
    }
    let mut stdout = io::stdout().lock();
    if stdout.write_all(&out).and_then(|_| stdout.flush()).is_err() {
        return ExitCode::from(3);
    }
    ExitCode::SUCCESS
}

#[cfg(test)]
mod tests {
    use super::decode_ascii;

    #[test]
    fn three_systemd_renderings() {
        let text = concat!(
            "ExecStart={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 ",
            "/home/host/scripts/tool follow ; ignore_errors=no }\n",
            "WorkingDirectory=/home/host/work\n",
            "Environment=PATH=/usr/bin:/bin MESH_HOME=/home/host/state\n",
        );
        assert_eq!(
            decode_ascii(text),
            vec![
                "/usr/bin/python3",
                "/home/host/scripts/tool",
                "/home/host/work",
                "/usr/bin",
                "/bin",
                "/home/host/state",
            ]
        );
    }

    #[test]
    fn dedupe_relative_and_rendering_punctuation() {
        assert_eq!(
            decode_ascii("ExecStart={ path=/opt/a/tool ; argv[]=/opt/a/tool run }\nEnvironment=TOOL=/opt/a/tool"),
            vec!["/opt/a/tool"]
        );
        assert_eq!(
            decode_ascii("Environment=RELATIVE=./tool\nEnvironment=RELATIVE=some/where/tool\n"),
            Vec::<&str>::new()
        );
        assert_eq!(
            decode_ascii("ExecStart={ path=/opt/tool ; argv[]=/opt/tool \"quoted arg\" }\nEnvironment=PATH=/usr/bin:/usr/local/bin,\n"),
            vec!["/opt/tool", "/usr/bin", "/usr/local/bin"]
        );
    }

    #[test]
    fn lookbehind_suppresses_false_starts() {
        assert_eq!(
            decode_ascii("Environment=X=1/home/a\nEnvironment=X=_/home/a\nEnvironment=X=-/home/a\nEnvironment=X=./home/a\n"),
            Vec::<&str>::new()
        );
    }
}
