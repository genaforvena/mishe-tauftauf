//! mishe-signal-freshness: the freshness verdict of one signal observation.
//!
//! Usage: `mishe-signal-freshness SEEN NOW MAX_AGE`, three ASCII float arguments in
//! Python `repr` form. The verdict is the exit status, so a caller composes it
//! without parsing any output:
//!
//!   0  FRESH    `now - seen > max_age` is false
//!   1  EXPIRED  `now - seen > max_age` is true
//!   2  outside the contract (wrong argument count or a malformed number)
//!
//! Nothing is written to stdout. See CONTRACT.md.

use std::process::ExitCode;

fn parse_float(token: &str) -> Option<f64> {
    // Only plain ASCII float forms: digits, sign, dot, exponent, inf, nan.
    if token.is_empty()
        || !token
            .bytes()
            .all(|b| b.is_ascii_digit() || matches!(b, b'+' | b'-' | b'.' | b'e' | b'E' | b'i' | b'n' | b'f' | b'a'))
    {
        return None;
    }
    token.parse::<f64>().ok()
}

fn expired(seen: f64, now: f64, max_age: f64) -> bool {
    now - seen > max_age
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let [seen, now, max_age] = args.as_slice() else {
        eprintln!("usage: mishe-signal-freshness SEEN NOW MAX_AGE  (exit 0 FRESH, 1 EXPIRED, 2 outside contract)");
        return ExitCode::from(2);
    };
    match (parse_float(seen), parse_float(now), parse_float(max_age)) {
        (Some(s), Some(n), Some(m)) => {
            if expired(s, n, m) {
                ExitCode::from(1)
            } else {
                ExitCode::SUCCESS
            }
        }
        _ => {
            eprintln!("outside contract: {seen:?} {now:?} {max_age:?}");
            ExitCode::from(2)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn verdict_matches_the_python_comparison() {
        assert!(!expired(10.0, 15.0, 5.0)); // equal age is FRESH
        assert!(expired(10.0, 15.5, 5.0));
        assert!(!expired(f64::NAN, 15.0, 5.0)); // NaN > x is false in both
        assert!(expired(0.0, f64::INFINITY, 5.0));
    }

    #[test]
    fn parser_accepts_repr_forms_and_rejects_others() {
        assert_eq!(parse_float("1.5"), Some(1.5));
        assert_eq!(parse_float("1e+16"), Some(1e16));
        assert_eq!(parse_float("inf"), Some(f64::INFINITY));
        assert!(parse_float("1_000").is_none());
        assert!(parse_float(" 1").is_none());
        assert!(parse_float("").is_none());
    }
}
