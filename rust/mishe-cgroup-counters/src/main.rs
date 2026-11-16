//! mishe-cgroup-counters: decode cgroup v2 counter files byte-exactly.
//!
//! Contract: see CONTRACT.md. This program reads one file body on stdin and owns
//! only decoding: Python keeps the file read and every verdict. `stat NAME...`
//! decodes a `cpu.stat` body; `max-period` decodes a `cpu.max` body.
//!
//! Exit 0 = whole input decoded, stdout is the result; 1 = usage or read error;
//! 2 = data fault (invalid UTF-8, over bound, a required counter absent or
//! non-decimal); 3 = write error; anything else is outside the contract.
//! A data fault is UNKNOWN to the caller, never a zero reading.

use std::collections::BTreeSet;
use std::io::{self, Read, Write};
use std::process::ExitCode;

/// A few kernel counter lines are far below 1 MiB; a larger body is a fault.
const MAX_INPUT: usize = 1 << 20;

/// Python `int()` on an ASCII decimal token: optional sign, digits, single
/// underscores between digits. Everything else is a data fault. Kernel files
/// never contain the exotic forms, but accepting them keeps the byte contract
/// identical to the Python decoder this binary replaces.
fn decimal(token: &str) -> Option<i64> {
    let bytes = token.as_bytes();
    if bytes.is_empty() {
        return None;
    }
    let mut at = 0usize;
    let negative = match bytes[0] {
        b'-' => {
            at = 1;
            true
        }
        b'+' => {
            at = 1;
            false
        }
        _ => false,
    };
    if at == bytes.len() {
        return None;
    }
    let mut digits = String::with_capacity(bytes.len() - at);
    let mut last_was_digit = false;
    for &byte in &bytes[at..] {
        if byte.is_ascii_digit() {
            digits.push(byte as char);
            last_was_digit = true;
        } else if byte == b'_' && last_was_digit {
            last_was_digit = false;
        } else {
            return None;
        }
    }
    if !last_was_digit {
        return None;
    }
    let value = digits.parse::<i64>().ok()?;
    Some(if negative { -value } else { value })
}

/// Body -> requested counters. `None` means a data fault: a named counter was
/// absent, or its value was not a decimal integer. Other lines are ignored,
/// exactly as in `cpu_stat_text.parse_cpu_stat`.
fn stat_fields(text: &str, names: &[&str]) -> Option<Vec<(String, i64)>> {
    let wanted: BTreeSet<&str> = names.iter().copied().collect();
    let mut found: Vec<(String, i64)> = Vec::new();
    for line in text.lines() {
        let parts: Vec<&str> = line.split_whitespace().collect();
        if parts.len() != 2 || !wanted.contains(parts[0]) {
            continue;
        }
        let value = decimal(parts[1])?;
        if let Some(existing) = found.iter_mut().find(|(name, _)| name == parts[0]) {
            existing.1 = value;
        } else {
            found.push((parts[0].to_owned(), value));
        }
    }
    if found.len() != wanted.len() {
        return None;
    }
    // Emit in the order the caller requested, so output is deterministic.
    let mut out = Vec::with_capacity(names.len());
    let mut seen: BTreeSet<&str> = BTreeSet::new();
    for name in names {
        if !seen.insert(name) {
            continue;
        }
        let (_, value) = found.iter().find(|(found_name, _)| found_name == name)?;
        out.push(((*name).to_owned(), *value));
    }
    Some(out)
}

/// `cpu.max` is `quota period` or `max period`; only a positive integer period
/// is decoded. The `max` form has no period for this reader and is a fault.
fn max_period(text: &str) -> Option<i64> {
    let parts: Vec<&str> = text.split_whitespace().collect();
    if parts.len() != 2 {
        return None;
    }
    let period = decimal(parts[1])?;
    (period > 0).then_some(period)
}

fn read_stdin() -> Result<String, ExitCode> {
    let mut bytes = Vec::new();
    io::stdin()
        .take((MAX_INPUT + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| ExitCode::from(1))?;
    if bytes.len() > MAX_INPUT {
        return Err(ExitCode::from(2));
    }
    String::from_utf8(bytes).map_err(|_| ExitCode::from(2))
}

fn emit(rows: &[(String, i64)]) -> Result<(), ExitCode> {
    let mut stdout = io::stdout().lock();
    for (name, value) in rows {
        writeln!(stdout, "{name}\t{value}").map_err(|_| ExitCode::from(3))?;
    }
    stdout.flush().map_err(|_| ExitCode::from(3))
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let rows: Vec<(String, i64)> = match args.first().map(String::as_str) {
        Some("stat") if args.len() >= 2 => {
            let text = match read_stdin() {
                Ok(text) => text,
                Err(code) => return code,
            };
            let names: Vec<&str> = args[1..].iter().map(String::as_str).collect();
            match stat_fields(&text, &names) {
                Some(rows) => rows,
                None => return ExitCode::from(2),
            }
        }
        Some("max-period") if args.len() == 1 => {
            let text = match read_stdin() {
                Ok(text) => text,
                Err(code) => return code,
            };
            match max_period(&text) {
                Some(period) => vec![("period".to_owned(), period)],
                None => return ExitCode::from(2),
            }
        }
        _ => return ExitCode::from(1),
    };
    match emit(&rows) {
        Ok(()) => ExitCode::SUCCESS,
        Err(code) => code,
    }
}

#[cfg(test)]
mod tests {
    use super::{decimal, max_period, stat_fields};

    #[test]
    fn decimal_matches_python_int_for_kernel_and_signed_forms() {
        assert_eq!(decimal("123"), Some(123));
        assert_eq!(decimal("+5"), Some(5));
        assert_eq!(decimal("-5"), Some(-5));
        assert_eq!(decimal("1_000"), Some(1000));
        for bad in ["", "+", "-", "_10", "10_", "1__0", "x", "1.5", " 1"] {
            assert_eq!(decimal(bad), None, "{bad}");
        }
    }

    #[test]
    fn stat_requires_every_named_counter_and_ignores_others() {
        let text = "usage_usec 1\nuser_usec bad\nnr_periods 10\nnr_throttled 4\nthrottled_usec 99\n";
        let names = ["nr_periods", "nr_throttled", "throttled_usec"];
        assert_eq!(
            stat_fields(text, &names),
            Some(vec![
                ("nr_periods".to_owned(), 10),
                ("nr_throttled".to_owned(), 4),
                ("throttled_usec".to_owned(), 99),
            ])
        );
        assert_eq!(stat_fields("nr_periods 10\n", &names), None);
        assert_eq!(stat_fields("", &names), None);
        assert_eq!(
            stat_fields("nr_periods x\nnr_throttled 1\nthrottled_usec 1\n", &names),
            None
        );
        // A different caller asks for a different counter set.
        assert_eq!(
            stat_fields(text, &["usage_usec"]),
            Some(vec![("usage_usec".to_owned(), 1)])
        );
    }

    #[test]
    fn max_period_rejects_the_unlimited_form_and_nonpositive_values() {
        assert_eq!(max_period("max 100000\n"), Some(100000));
        assert_eq!(max_period("200000 100000\n"), Some(100000));
        assert_eq!(max_period("max max\n"), None);
        assert_eq!(max_period("max 0\n"), None);
        assert_eq!(max_period("max -1\n"), None);
        assert_eq!(max_period(""), None);
    }
}
