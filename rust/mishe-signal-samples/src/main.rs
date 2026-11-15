//! mishe-signal-sample: append-only segmented store for signal samples.
//!
//! Contract: see CONTRACT.md. Two subcommands:
//!   append DIR MAX_SEGMENT_BYTES KEEP_SEGMENTS   (stdin: TSV batch, all-or-nothing)
//!   read   DIR [--name NAME] [--since MS] [--until MS]   (stdout: matching TSV lines)
//! Exit 0 ok, 1 usage/IO (UNKNOWN to the caller), 2 data fault (malformed or torn input).

use std::fs::{self, OpenOptions};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;

const MAX_BATCH: u64 = 1 << 20;
const MAX_LINE: usize = 512;
const SEG_DIGITS: usize = 15;
const TS_LIMIT: u64 = 1_000_000_000_000_000; // 10^15 ms, the fixed segment-name width
const MIN_SEG: u64 = 4096;
const MAX_SEG: u64 = 64 << 20;

#[derive(Debug)]
enum Fail {
    Usage(String),
    Io(String),
    Data(String),
}

impl Fail {
    fn code(&self) -> u8 {
        match self {
            Fail::Usage(_) | Fail::Io(_) => 1,
            Fail::Data(_) => 2,
        }
    }
    fn message(&self) -> &str {
        match self {
            Fail::Usage(m) | Fail::Io(m) | Fail::Data(m) => m,
        }
    }
}

#[derive(Debug, PartialEq)]
struct Sample<'a> {
    ts: u64,
    name: &'a str,
    state: &'a str,
    value: &'a str,
    source: &'a str,
}

fn valid_token(s: &str, max: usize, ok: impl Fn(u8) -> bool) -> bool {
    !s.is_empty() && s.len() <= max && s.bytes().all(ok)
}

/// Parse one TSV record: ts_ms, name, state, value, source. Fields are validated, never repaired.
fn parse_line(line: &str) -> Result<Sample<'_>, String> {
    if line.len() > MAX_LINE {
        return Err("line too long".into());
    }
    let f: Vec<&str> = line.split('\t').collect();
    if f.len() != 5 {
        return Err(format!("expected 5 fields, got {}", f.len()));
    }
    let (ts_s, name, state, value, source) = (f[0], f[1], f[2], f[3], f[4]);
    if ts_s.is_empty() || ts_s.len() > 20 || !ts_s.bytes().all(|b| b.is_ascii_digit()) {
        return Err("bad ts".into());
    }
    let ts: u64 = ts_s.parse().map_err(|_| "ts out of range".to_string())?;
    if ts == 0 || ts >= TS_LIMIT {
        return Err("ts out of range".into());
    }
    if !valid_token(name, 96, |b| b.is_ascii_alphanumeric() || b"._:-".contains(&b)) {
        return Err("bad name".into());
    }
    if !matches!(state, "ok" | "warn" | "red" | "unknown") {
        return Err("bad state".into());
    }
    if value != "-" {
        let shaped = valid_token(value, 32, |b| b.is_ascii_digit() || b"+-.eE".contains(&b));
        match value.parse::<f64>() {
            Ok(v) if shaped && v.is_finite() => {}
            _ => return Err("bad value".into()),
        }
    }
    if !valid_token(source, 128, |b| b.is_ascii_alphanumeric() || b"._:/-".contains(&b)) {
        return Err("bad source".into());
    }
    Ok(Sample { ts, name, state, value, source })
}

/// A segment file name is 15 digits plus ".tsv"; the digits are the first ts in ms.
fn segment_ts(file_name: &str) -> Option<u64> {
    let stem = file_name.strip_suffix(".tsv")?;
    if stem.len() != SEG_DIGITS || !stem.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    stem.parse().ok()
}

/// Sorted segments plus stray names (anything that is not a segment file).
fn list_segments(dir: &Path) -> Result<(Vec<PathBuf>, Vec<String>), Fail> {
    let entries = fs::read_dir(dir).map_err(|e| Fail::Io(format!("read_dir {}: {e}", dir.display())))?;
    let mut segs: Vec<(String, PathBuf)> = Vec::new();
    let mut stray: Vec<String> = Vec::new();
    for entry in entries {
        let entry = entry.map_err(|e| Fail::Io(format!("read_dir entry: {e}")))?;
        let name = entry.file_name().to_string_lossy().into_owned();
        if segment_ts(&name).is_some() {
            segs.push((name, entry.path()));
        } else {
            stray.push(name);
        }
    }
    segs.sort();
    Ok((segs.into_iter().map(|(_, p)| p).collect(), stray))
}

fn append(dir: &Path, max_seg: u64, keep: usize, batch: &[u8]) -> Result<usize, Fail> {
    if batch.is_empty() {
        return Ok(0);
    }
    let text = std::str::from_utf8(batch).map_err(|e| Fail::Data(format!("batch not UTF-8 at byte {}", e.valid_up_to())))?;
    if !text.ends_with('\n') {
        return Err(Fail::Data("torn batch: last line has no newline".into()));
    }
    let mut first_ts: Option<u64> = None;
    let mut count = 0usize;
    for (i, line) in text[..text.len() - 1].split('\n').enumerate() {
        let s = parse_line(line).map_err(|r| Fail::Data(format!("line {}: {r}", i + 1)))?;
        first_ts.get_or_insert(s.ts);
        count += 1;
    }
    let first_ts = first_ts.unwrap_or(0);
    fs::create_dir_all(dir).map_err(|e| Fail::Io(format!("create_dir {}: {e}", dir.display())))?;
    let (segs, stray) = list_segments(dir)?;
    if let Some(s) = stray.first() {
        return Err(Fail::Data(format!("stray file in sample dir: {s}")));
    }
    let need_new = match segs.last() {
        None => true,
        Some(last) => {
            let len = fs::metadata(last).map_err(|e| Fail::Io(format!("stat {}: {e}", last.display())))?.len();
            len + batch.len() as u64 > max_seg
        }
    };
    let target = if need_new {
        dir.join(format!("{:0w$}.tsv", first_ts, w=SEG_DIGITS))
    } else {
        segs.last().unwrap().clone()
    };
    // A new segment must not merge into an existing file of the same name.
    let mut opts = OpenOptions::new();
    opts.append(true);
    if need_new {
        opts.create_new(true);
    }
    let mut file = opts
        .open(&target)
        .map_err(|e| Fail::Io(format!("open {}: {e}", target.display())))?;
    file.write_all(batch).map_err(|e| Fail::Io(format!("write {}: {e}", target.display())))?;
    file.sync_data().map_err(|e| Fail::Io(format!("sync {}: {e}", target.display())))?;
    if need_new {
        let (all, _) = list_segments(dir)?;
        let drop_n = all.len().saturating_sub(keep);
        for old in all.iter().take(drop_n) {
            fs::remove_file(old).map_err(|e| Fail::Io(format!("prune {}: {e}", old.display())))?;
        }
    }
    Ok(count)
}

struct Filter<'a> {
    name: Option<&'a str>,
    since: u64,
    until: u64,
}

fn read(dir: &Path, filter: &Filter, out: &mut dyn Write) -> Result<(), Fail> {
    let (segs, stray) = list_segments(dir)?;
    let mut faults: Vec<String> = stray.iter().map(|s| format!("stray file: {s}")).collect();
    for seg in &segs {
        let label = seg.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
        let mut bytes = Vec::new();
        fs::File::open(seg)
            .and_then(|mut f| f.read_to_end(&mut bytes))
            .map_err(|e| Fail::Io(format!("read {}: {e}", seg.display())))?;
        let text = match std::str::from_utf8(&bytes) {
            Ok(t) => t,
            Err(e) => {
                faults.push(format!("{label}: not UTF-8 at byte {}", e.valid_up_to()));
                continue;
            }
        };
        let mut rest = text;
        let mut lineno = 0usize;
        while !rest.is_empty() {
            lineno += 1;
            let Some(pos) = rest.find('\n') else {
                faults.push(format!("{label}:{lineno}: torn tail (no newline)"));
                break;
            };
            let line = &rest[..pos];
            rest = &rest[pos + 1..];
            match parse_line(line) {
                Err(reason) => faults.push(format!("{label}:{lineno}: {reason}")),
                Ok(s) => {
                    if filter.name.is_some_and(|n| n != s.name) || s.ts < filter.since || s.ts > filter.until {
                        continue;
                    }
                    writeln!(out, "{line}").map_err(|e| Fail::Io(format!("stdout: {e}")))?;
                }
            }
        }
    }
    if faults.is_empty() {
        return Ok(());
    }
    for f in &faults {
        eprintln!("mishe-signal-sample: {f}");
    }
    Err(Fail::Data(format!("{} data fault(s); valid records were still emitted", faults.len())))
}

fn parse_u64(arg: Option<&String>, what: &str) -> Result<u64, Fail> {
    arg.ok_or_else(|| Fail::Usage(format!("missing {what}")))?
        .parse()
        .map_err(|_| Fail::Usage(format!("{what} is not an integer")))
}

fn run(args: &[String]) -> Result<(), Fail> {
    let usage = "usage: mishe-signal-sample append DIR MAX_SEGMENT_BYTES KEEP_SEGMENTS | read DIR [--name N] [--since MS] [--until MS]";
    match args.get(1).map(String::as_str) {
        Some("append") => {
            let dir = PathBuf::from(args.get(2).ok_or_else(|| Fail::Usage(usage.into()))?);
            let max_seg = parse_u64(args.get(3), "MAX_SEGMENT_BYTES")?;
            let keep = parse_u64(args.get(4), "KEEP_SEGMENTS")?;
            if !(MIN_SEG..=MAX_SEG).contains(&max_seg) || keep == 0 {
                return Err(Fail::Usage("MAX_SEGMENT_BYTES must be 4096..64 MiB, KEEP_SEGMENTS >= 1".into()));
            }
            let mut batch = Vec::new();
            io::stdin().take(MAX_BATCH + 1).read_to_end(&mut batch).map_err(|e| Fail::Io(format!("stdin: {e}")))?;
            if batch.len() as u64 > MAX_BATCH {
                return Err(Fail::Data("batch over 1 MiB".into()));
            }
            let n = append(&dir, max_seg, keep as usize, &batch)?;
            eprintln!("mishe-signal-sample: appended {n} record(s)");
            Ok(())
        }
        Some("read") => {
            let dir = PathBuf::from(args.get(2).ok_or_else(|| Fail::Usage(usage.into()))?);
            let mut filter = Filter { name: None, since: 0, until: u64::MAX };
            let mut i = 3;
            while i < args.len() {
                let val = args.get(i + 1);
                match args[i].as_str() {
                    "--name" => filter.name = Some(val.ok_or_else(|| Fail::Usage("--name needs a value".into()))?),
                    "--since" => filter.since = parse_u64(val, "--since")?,
                    "--until" => filter.until = parse_u64(val, "--until")?,
                    other => return Err(Fail::Usage(format!("unknown option {other}"))),
                }
                i += 2;
            }
            let stdout = io::stdout();
            let mut lock = stdout.lock();
            read(&dir, &filter, &mut lock)
        }
        _ => Err(Fail::Usage(usage.into())),
    }
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().collect();
    match run(&args) {
        Ok(()) => ExitCode::from(0),
        Err(f) => {
            eprintln!("mishe-signal-sample: {}", f.message());
            ExitCode::from(f.code())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(tag: &str) -> PathBuf {
        let nanos = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        let p = std::env::temp_dir().join(format!("mss-{tag}-{}-{nanos}", std::process::id()));
        fs::create_dir_all(&p).unwrap();
        p
    }

    fn all(dir: &Path) -> Result<String, Fail> {
        let mut out = Vec::new();
        read(dir, &Filter { name: None, since: 0, until: u64::MAX }, &mut out)?;
        Ok(String::from_utf8(out).unwrap())
    }

    #[test]
    fn parses_valid_and_rejects_bad_fields() {
        assert!(parse_line("1794700000000\tpsi.some\tok\t0.25\tsense").is_ok());
        assert!(parse_line("1794700000000\tpsi.some\tok\t-\tsense").is_ok());
        assert!(parse_line("1794700000000\tpsi.some\tfine\t0.25\tsense").is_err());
        assert!(parse_line("1794700000000\tpsi.some\tok\tNaN\tsense").is_err());
        assert!(parse_line("1794700000000\tpsi.some\tok\tinf\tsense").is_err());
        assert!(parse_line("0\tpsi.some\tok\t1\tsense").is_err());
        assert!(parse_line("1794700000000\tpsi.some\tok\t1\tsense\r").is_err());
        assert!(parse_line("1794700000000\tpsi.some\tok\t1").is_err());
    }

    #[test]
    fn roundtrip_and_gaps_stay_gaps() {
        let d = tmp("rt");
        let batch = b"1794700000000\ta.x\tok\t1.5\tsrc\n1794700060000\ta.x\tunknown\t-\tsrc\n";
        assert_eq!(append(&d, 1 << 20, 4, batch).unwrap(), 2);
        // no record at 1794700120000: a gap, not a zero
        let out = all(&d).unwrap();
        assert_eq!(out, String::from_utf8(batch.to_vec()).unwrap());
        fs::remove_dir_all(&d).unwrap();
    }

    #[test]
    fn rotates_and_prunes() {
        let d = tmp("rot");
        for i in 0..6u64 {
            let line = format!("{}\ta.x\tok\t{}\tsrc\n", 1_794_700_000_000 + i, i);
            append(&d, 4096, 2, line.as_bytes()).unwrap();
        }
        let (segs, _) = list_segments(&d).unwrap();
        assert!(segs.len() <= 2, "kept {} segments", segs.len());
        fs::remove_dir_all(&d).unwrap();
    }

    #[test]
    fn torn_or_bad_batch_writes_nothing() {
        let d = tmp("bad");
        assert!(matches!(append(&d, 4096, 2, b"1794700000000\ta\tok\t1\ts"), Err(Fail::Data(_))));
        assert!(matches!(append(&d, 4096, 2, b"1794700000000\ta\tok\t1\ts\nbogus\n"), Err(Fail::Data(_))));
        assert!(list_segments(&d).unwrap().0.is_empty());
        fs::remove_dir_all(&d).unwrap();
    }

    #[test]
    fn reader_reports_torn_tail_but_emits_valid_records() {
        let d = tmp("torn");
        append(&d, 1 << 20, 4, b"1794700000000\ta\tok\t1\ts\n").unwrap();
        let (segs, _) = list_segments(&d).unwrap();
        let mut f = OpenOptions::new().append(true).open(&segs[0]).unwrap();
        f.write_all(b"1794700060000\ta\tok").unwrap();
        let mut out = Vec::new();
        let r = read(&d, &Filter { name: None, since: 0, until: u64::MAX }, &mut out);
        assert!(matches!(r, Err(Fail::Data(_))));
        assert_eq!(String::from_utf8(out).unwrap(), "1794700000000\ta\tok\t1\ts\n");
        fs::remove_dir_all(&d).unwrap();
    }

    #[test]
    fn missing_dir_is_io_not_empty() {
        let d = std::env::temp_dir().join("mss-definitely-missing-dir-xyz");
        assert!(matches!(all(&d), Err(Fail::Io(_))));
    }
}
