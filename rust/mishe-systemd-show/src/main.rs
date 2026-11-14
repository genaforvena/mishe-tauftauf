//! mishe-systemd-show: decode `systemctl show` text into a dict-shaped line stream.
//!
//! Contract: see CONTRACT.md. stdin is the systemctl show output; stdout is one
//! `KEY=VALUE` line per distinct key, in first-insertion order, carrying the last
//! value seen for that key. Exit 0 ok, 1 usage/IO, 2 data fault (invalid UTF-8,
//! or input over the bound).

use std::collections::HashMap;
use std::io::{self, Read, Write};
use std::process::ExitCode;

/// Input bound: systemctl show for a few properties is well under 1 KiB.
const MAX_INPUT: u64 = 1 << 20;

/// Python `str.splitlines()` line boundaries.
fn is_line_break(c: char) -> bool {
    matches!(
        c,
        '\n' | '\r' | '\u{0b}' | '\u{0c}' | '\u{1c}' | '\u{1d}' | '\u{1e}' | '\u{85}' | '\u{2028}' | '\u{2029}'
    )
}

fn decode(text: &str) -> Vec<(&str, &str)> {
    let mut order: Vec<(&str, &str)> = Vec::new();
    let mut index: HashMap<&str, usize> = HashMap::new();
    for line in text.split(is_line_break) {
        let Some((key, value)) = line.split_once('=') else {
            continue;
        };
        if key.is_empty() {
            continue;
        }
        match index.get(key) {
            Some(&pos) => order[pos].1 = value,
            None => {
                index.insert(key, order.len());
                order.push((key, value));
            }
        }
    }
    order
}

fn main() -> ExitCode {
    if std::env::args_os().len() != 1 {
        eprintln!("usage: mishe-systemd-show  (stdin: systemctl show text)");
        return ExitCode::from(1);
    }
    let mut raw = Vec::new();
    if let Err(e) = io::stdin().lock().take(MAX_INPUT + 1).read_to_end(&mut raw) {
        eprintln!("mishe-systemd-show: read: {e}");
        return ExitCode::from(1);
    }
    if raw.len() as u64 > MAX_INPUT {
        eprintln!("mishe-systemd-show: input over {MAX_INPUT} bytes");
        return ExitCode::from(2);
    }
    let text = match std::str::from_utf8(&raw) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("mishe-systemd-show: invalid UTF-8 at byte {}", e.valid_up_to());
            return ExitCode::from(2);
        }
    };
    let mut out = Vec::with_capacity(raw.len());
    for (key, value) in decode(text) {
        out.extend_from_slice(key.as_bytes());
        out.push(b'=');
        out.extend_from_slice(value.as_bytes());
        out.push(b'\n');
    }
    let mut stdout = io::stdout().lock();
    if stdout.write_all(&out).and_then(|_| stdout.flush()).is_err() {
        return ExitCode::from(1);
    }
    ExitCode::SUCCESS
}

#[cfg(test)]
mod tests {
    use super::decode;

    #[test]
    fn last_value_wins_first_position_kept() {
        assert_eq!(decode("A=1\nB=2\nA=3\n"), vec![("A", "3"), ("B", "2")]);
    }

    #[test]
    fn skips_blank_keyless_and_separatorless() {
        assert_eq!(decode("\n=x\nnoequals\nK=v=w\n"), vec![("K", "v=w")]);
    }

    #[test]
    fn python_splitlines_boundaries() {
        assert_eq!(decode("A=1\u{2028}B=2\u{0b}C=3\rD=4"), vec![("A", "1"), ("B", "2"), ("C", "3"), ("D", "4")]);
    }
}
