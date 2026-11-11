//! `mishe-tape frames PATH [--size N] [--start N] [--offset N] [--offsets] [--facts]`
//!
//! `--size N` is an absolute end offset in bytes; `--offset N` starts there.
//! Frames the chat tape and writes each entry to stdout as:
//!
//! ```text
//! E <sequence> <timestamp> <source> <body-bytes>\n
//! <body bytes, exactly body-bytes long>\n
//! ```
//!
//! `--facts` appends one eligibility-fact line after each entry (see `facts.rs`
//! and `CONTRACT.md`); without it the output is unchanged. `--offsets` prints
//! frame offsets instead, and cannot be combined with `--facts`.
//!
//! Entries are written in tape order, and every entry before a fault is written.
//! Exit status: 0 when the whole range frames, 2 on a framing fault (the Python
//! `FeedError` text is written to stderr after the entries), 1 on a usage or
//! I/O error. Any other status is unobserved to a caller.

mod facts;
mod frames;

use std::io::{self, BufWriter, Read, Write};
use std::process::ExitCode;

fn usage() -> ExitCode {
    eprintln!("usage: mishe-tape frames PATH [--size N] [--start N] [--offset N] [--offsets] [--facts]");
    ExitCode::from(1)
}

fn parse_u128(value: &str) -> Option<u128> {
    value.parse().ok()
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.first().map(String::as_str) != Some("frames") || args.len() < 2 {
        return usage();
    }
    let path = &args[1];
    let mut size: Option<u64> = None;
    let mut start: u128 = 1;
    let mut base_offset: u64 = 0;
    let mut offsets_only = false;
    let mut facts_only = false;
    let mut i = 2;
    while i < args.len() {
        match args[i].as_str() {
            "--size" => {
                let Some(value) = args.get(i + 1).and_then(|v| parse_u128(v)) else {
                    return usage();
                };
                size = Some(value as u64);
                i += 2;
            }
            "--start" => {
                let Some(value) = args.get(i + 1).and_then(|v| parse_u128(v)) else {
                    return usage();
                };
                start = value;
                i += 2;
            }
            "--offset" => {
                let Some(value) = args.get(i + 1).and_then(|v| parse_u128(v)) else {
                    return usage();
                };
                base_offset = value as u64;
                i += 2;
            }
            "--offsets" => {
                offsets_only = true;
                i += 1;
            }
            "--facts" => {
                facts_only = true;
                i += 1;
            }
            _ => return usage(),
        }
    }
    // Both select the output shape; combining them has no meaning.
    if offsets_only && facts_only {
        return usage();
    }

    // Reading through the path is for the CLI. A caller that holds an open
    // handle passes `/proc/self/fd/N` so the bytes come from the inode it captured.
    let file = match std::fs::File::open(path) {
        Ok(file) => file,
        Err(err) => {
            eprintln!("mishe-tape: cannot open {}: {}", path, err);
            return ExitCode::from(1);
        }
    };
    let mut file = file;
    if base_offset > 0 {
        if let Err(err) = std::io::Seek::seek(&mut file, std::io::SeekFrom::Start(base_offset)) {
            eprintln!("mishe-tape: cannot seek {}: {}", path, err);
            return ExitCode::from(1);
        }
    }
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    // --size is an absolute end offset, as in feed._frame_offsets(handle, offset, size).
    let input: Box<dyn std::io::BufRead> = match size {
        Some(end) => Box::new(io::BufReader::new(Read::take(file, end.saturating_sub(base_offset)))),
        None => Box::new(io::BufReader::new(file)),
    };
    let result = frames::read(input, start, base_offset, |entry| {
        if offsets_only {
            return writeln!(out, "O {} {}", entry.sequence, entry.offset);
        }
        write!(
            out,
            "E {} {} {} {}\n",
            entry.sequence,
            entry.timestamp,
            entry.source,
            entry.body.len()
        )?;
        out.write_all(entry.body.as_bytes())?;
        out.write_all(b"\n")?;
        if facts_only {
            let f = facts::facts(&entry.body);
            writeln!(
                out,
                "F {} {} {} {} {} {}",
                entry.sequence,
                u8::from(f.reserved),
                f.references,
                u8::from(f.sole_final),
                u8::from(f.single_line),
                f.digest.as_deref().unwrap_or("-")
            )?;
        }
        Ok(())
    });
    let flushed = out.flush();
    match result {
        Ok(()) => match flushed {
            Ok(()) => ExitCode::from(0),
            Err(err) => {
                eprintln!("mishe-tape: write failed: {}", err);
                ExitCode::from(1)
            }
        },
        Err(frames::Stop::Fault(err)) => {
            let _ = flushed;
            eprintln!("{}", err.message);
            ExitCode::from(2)
        }
        Err(frames::Stop::Io(message)) => {
            eprintln!("mishe-tape: {}", message);
            ExitCode::from(1)
        }
    }
}
