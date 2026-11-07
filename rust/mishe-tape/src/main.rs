//! `mishe-tape frames PATH [--size N] [--start N]`
//!
//! Frames the chat tape and writes each entry to stdout as:
//!
//! ```text
//! E <sequence> <timestamp> <source> <body-bytes>\n
//! <body bytes, exactly body-bytes long>\n
//! ```
//!
//! Entries are written in tape order, and every entry before a fault is written.
//! Exit status: 0 when the whole range frames, 2 on a framing fault (the Python
//! `FeedError` text is written to stderr after the entries), 1 on a usage or
//! I/O error. Any other status is unobserved to a caller.

mod frames;

use std::io::{self, BufWriter, Read, Write};
use std::process::ExitCode;

fn usage() -> ExitCode {
    eprintln!("usage: mishe-tape frames PATH [--size N] [--start N]");
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
            _ => return usage(),
        }
    }

    let file = match std::fs::File::open(path) {
        Ok(file) => file,
        Err(err) => {
            eprintln!("mishe-tape: cannot open {}: {}", path, err);
            return ExitCode::from(1);
        }
    };
    let stdout = io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let input: Box<dyn std::io::BufRead> = match size {
        Some(limit) => Box::new(io::BufReader::new(Read::take(file, limit))),
        None => Box::new(io::BufReader::new(file)),
    };
    let result = frames::read(input, start, |entry| {
        write!(
            out,
            "E {} {} {} {}\n",
            entry.sequence,
            entry.timestamp,
            entry.source,
            entry.body.len()
        )?;
        out.write_all(entry.body.as_bytes())?;
        out.write_all(b"\n")
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
