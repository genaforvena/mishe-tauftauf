//! `mishe-records` — record verification and the shared verified store.
//!
//! The Rust counterpart of `records.envelope`/`cached_payload`/`VerifiedStore` as they
//! are run by `feed._VerificationPass`. The contract this program is checked against is
//! `CONTRACT.md` next to this file; the caller composes it with `mishe-tape frames`
//! through a pipe.

mod envelope;
mod fdops;
mod pytxt;
mod sha256;
mod store;

use std::collections::HashMap;
use std::io::{BufRead, BufReader, BufWriter, Read, Write};
use std::os::unix::io::RawFd;

/// `records.VERIFIED_MAX_AGE`.
const VERIFIED_MAX_AGE: f64 = 6.0 * 3600.0;
/// `records.VERIFIED_FLUSH_ROWS`.
const VERIFIED_FLUSH_ROWS: usize = 500;
/// `feed.VERIFIED_BATCH_ENTRIES`.
const VERIFIED_BATCH_ENTRIES: usize = 500;
/// A tape entry body larger than this is not a record reference.
const MAX_BODY: usize = 64 * 1024 * 1024;

const USAGE: &str = "mishe-records verify --home SITE [--payloads] [--records-fd N]";

/// A fault the caller must see. `Verify` is a verification result (exit 2, the Python
/// `ValueError` text on stderr); `Unknown` is not a result (exit 1).
enum Fault {
    Verify(String),
    Unknown(String),
}

impl Fault {
    fn io(message: &str, error: std::io::Error) -> Fault {
        Fault::Unknown(format!("{message}: {error}"))
    }
}

struct Args {
    home: String,
    payloads: bool,
    records_fd: Option<RawFd>,
}

struct Entry {
    sequence: u64,
    body: String,
}

/// A payload verified in this pass, waiting to be stored and served to a repeat
/// reference: `records.cached_payload`'s `pending[digest]`, holding the canonical
/// payload bytes (the store's compressed blob is built at flush time).
struct Held {
    identity: (i64, i64, i64, i64),
    verified_at: f64,
    payload: Vec<u8>,
    kind: String,
}

struct Pass {
    records_path: String,
    store_path: String,
    payloads: bool,
    dirfd: Option<RawFd>,
    dir_error: Option<String>,
    store: Option<store::Store>,
    pending: HashMap<String, Held>,
    since_flush: usize,
}

fn main() {
    let args = match parse_args(std::env::args().skip(1).collect()) {
        Ok(args) => args,
        Err(message) => {
            eprintln!("{message}");
            eprintln!("{USAGE}");
            std::process::exit(1);
        }
    };
    let records_path = under_home(&args.home, "records");
    let store_path = under_home(&args.home, "records.verified.sqlite3");
    let mut pass = Pass {
        records_path,
        store_path,
        payloads: args.payloads,
        dirfd: args.records_fd,
        dir_error: None,
        store: None,
        pending: HashMap::new(),
        since_flush: 0,
    };

    let stdout = std::io::stdout();
    let mut out = BufWriter::new(stdout.lock());
    let stdin = std::io::stdin();
    let mut reader = BufReader::new(stdin.lock());
    loop {
        let entry = match read_entry(&mut reader) {
            Ok(Some(entry)) => entry,
            Ok(None) => break,
            Err(fault) => exit_with(&mut out, fault),
        };
        if let Err(fault) = pass.entry(&entry, &mut out) {
            exit_with(&mut out, fault);
        }
    }
    if let Err(fault) = pass.flush() {
        exit_with(&mut out, fault);
    }
    if let Err(error) = out.flush() {
        eprintln!("mishe-records: {error}");
        std::process::exit(1);
    }
}

/// Write what the pass decided so far, report the fault, and end with its status.
fn exit_with(out: &mut impl Write, fault: Fault) -> ! {
    out.flush().ok();
    match fault {
        Fault::Verify(message) => {
            eprintln!("{message}");
            std::process::exit(2);
        }
        Fault::Unknown(message) => {
            eprintln!("{message}");
            std::process::exit(1);
        }
    }
}

fn parse_args(argv: Vec<String>) -> Result<Args, String> {
    let mut argv = argv.into_iter();
    match argv.next().as_deref() {
        Some("verify") => {}
        Some(other) => return Err(format!("unknown subcommand: {other}")),
        None => return Err("no subcommand".to_string()),
    }
    let mut home: Option<String> = None;
    let mut payloads = false;
    let mut records_fd: Option<RawFd> = None;
    while let Some(flag) = argv.next() {
        match flag.as_str() {
            "--home" => {
                home = Some(argv.next().ok_or_else(|| "--home needs a value".to_string())?);
            }
            "--payloads" => payloads = true,
            "--records-fd" => {
                let value = argv.next().ok_or_else(|| "--records-fd needs a value".to_string())?;
                records_fd = Some(value.parse().map_err(|_| format!("--records-fd is not a number: {value}"))?);
            }
            other => return Err(format!("unknown argument: {other}")),
        }
    }
    Ok(Args {
        home: home.ok_or_else(|| "--home is required".to_string())?,
        payloads,
        records_fd,
    })
}

/// `Path(home) / name`, with the trailing-slash normalization `pathlib` applies.
fn under_home(home: &str, name: &str) -> String {
    let base = home.trim_end_matches('/');
    match base {
        "" => format!("/{name}"),
        "." => name.to_string(),
        base => format!("{base}/{name}"),
    }
}

/// Read one `mishe-tape frames` entry: `E <sequence> <timestamp> <source> <body-bytes>`,
/// the body bytes, and the newline the framer writes after them.
fn read_entry(reader: &mut impl BufRead) -> Result<Option<Entry>, Fault> {
    let mut header = Vec::new();
    let read = reader
        .read_until(b'\n', &mut header)
        .map_err(|error| Fault::io("mishe-records input error", error))?;
    if read == 0 {
        return Ok(None);
    }
    if header.last() != Some(&b'\n') {
        return Err(Fault::Unknown("mishe-records input truncated mid-header".to_string()));
    }
    header.pop();
    let fields: Vec<&[u8]> = header.splitn(5, |byte| *byte == b' ').collect();
    if fields.len() != 5 || fields[0] != b"E" {
        return Err(Fault::Unknown("mishe-records input is not a framed entry stream".to_string()));
    }
    let parse = |raw: &[u8], what: &str| -> Result<u64, Fault> {
        std::str::from_utf8(raw)
            .ok()
            .and_then(|text| text.parse().ok())
            .ok_or_else(|| Fault::Unknown(format!("mishe-records input {what} invalid")))
    };
    let sequence = parse(fields[1], "sequence")?;
    let length = parse(fields[4], "body length")? as usize;
    if length > MAX_BODY {
        return Err(Fault::Unknown("mishe-records input body exceeds 64 MiB".to_string()));
    }
    let mut body = vec![0u8; length];
    reader
        .read_exact(&mut body)
        .map_err(|error| Fault::io("mishe-records input error", error))?;
    let mut terminator = [0u8; 1];
    reader
        .read_exact(&mut terminator)
        .map_err(|error| Fault::io("mishe-records input error", error))?;
    if terminator[0] != b'\n' {
        return Err(Fault::Unknown("mishe-records input body length disagrees with header".to_string()));
    }
    let body = String::from_utf8(body)
        .map_err(|_| Fault::Unknown("mishe-records input body is not UTF-8".to_string()))?;
    Ok(Some(Entry { sequence, body }))
}

impl Pass {
    fn entry(&mut self, entry: &Entry, out: &mut impl Write) -> Result<(), Fault> {
        let lines = pytxt::splitlines(&entry.body);
        let refs: Vec<&str> = lines
            .iter()
            .copied()
            .filter(|line| pytxt::lstrip(line).starts_with("[record]"))
            .collect();
        if refs.is_empty() {
            // `feed._VerificationPass.verify` ignores an entry with no record reference;
            // the caller does the same with this decision.
            write_out(out, &format!("S {}\n", entry.sequence))?;
            return self.advance();
        }
        self.context()?;
        // Everything below is `records.payload`'s own reasoning on an entry the pass does
        // not ignore, and every branch of it raises the same `ValueError` the pass does.
        if lines.len() > 1 && pytxt::lstrip(lines[1]).starts_with('{') {
            return Err(Fault::Verify("mixed legacy JSON and immutable record reference".to_string()));
        }
        if refs.len() != 1 {
            return Err(Fault::Verify("missing or invalid immutable record reference".to_string()));
        }
        let Some((digest, expected)) = pytxt::parse_reference(refs[0]) else {
            return Err(Fault::Verify("missing or invalid immutable record reference".to_string()));
        };
        if digest != expected {
            return Err(Fault::Verify("record digest mismatch or missing site context".to_string()));
        }

        let now = now_secs();
        let name = format!("{digest}.json");
        let dirfd = self.dirfd.expect("context() proved a descriptor is held");
        let identity = match fdops::fstatat_nofollow(dirfd, &name) {
            Ok(stat) if stat.is_reg() => Some(stat.identity()),
            _ => None,
        };

        // The same record referenced again in this pass: its blob was verified already,
        // but the file must still carry the identity it had then.
        let want_payload = self.payloads;
        if let Some(held) = self.pending.get(digest) {
            if identity == Some(held.identity) {
                let payload = want_payload.then(|| held.payload.as_slice());
                self.serve(entry.sequence, &held.kind, payload, out)?;
                return self.advance();
            }
        }
        let hit = self.store.as_mut().and_then(|store| store.lookup(digest, want_payload));
        if let Some(hit) = hit {
            let age = now - hit.verified_at;
            if age >= 0.0 && age < VERIFIED_MAX_AGE && identity == Some(hit.identity) {
                if !want_payload {
                    return self.serve(entry.sequence, &hit.kind, None, out);
                }
                // A payload reader decodes the blob and reverifies on damage; a blob that
                // is not an object is a miss, exactly as `lookup` returns None.
                if let Some(blob) = &hit.payload {
                    if let Some(payload) = decode_payload(blob) {
                        self.serve(entry.sequence, &hit.kind, Some(&payload), out)?;
                        return self.advance();
                    }
                }
            }
        }

        // The miss path: capture the identity before the read, so a record rewritten
        // mid-read is never recorded with a newer identity.
        let (kind, payload) = envelope::verify(dirfd, digest, expected)?;
        let payload = serde_json::to_vec(&payload)
            .map_err(|error| Fault::Unknown(format!("mishe-records payload serialisation failed: {error}")))?;
        if self.payloads {
            write_payload(out, entry.sequence, 'N', &kind, &payload)?;
        } else {
            write_out(out, &format!("N {} {kind}\n", entry.sequence))?;
        }
        if let Some(before) = identity {
            self.pending.insert(
                digest.to_string(),
                Held { identity: before, verified_at: now, payload, kind },
            );
        }
        self.advance()
    }

    /// Report a verified entry: `V` (served without re-reading the record), with the
    /// payload body when the caller asked for one.
    fn serve(&self, sequence: u64, kind: &str, payload: Option<&[u8]>, out: &mut impl Write) -> Result<(), Fault> {
        if self.payloads {
            write_payload(out, sequence, 'V', kind, payload.unwrap_or_default())?;
        } else {
            write_out(out, &format!("V {sequence} {kind}\n"))?;
        }
        Ok(())
    }

    /// Create the pass's store and records descriptor the first time a record reference
    /// is seen, as `feed._VerificationPass.verify` does.
    fn context(&mut self) -> Result<(), Fault> {
        if self.store.is_none() {
            self.store = Some(store::Store::open(&self.store_path));
            if self.dirfd.is_none() && self.dir_error.is_none() {
                match fdops::open_dir(&self.records_path) {
                    Ok(fd) => self.dirfd = Some(fd),
                    Err(error) => self.dir_error = Some(error.to_string()),
                }
            }
        }
        match &self.dir_error {
            Some(text) => Err(Fault::Verify(format!("record unavailable: {text}"))),
            None => Ok(()),
        }
    }

    /// The pass's own bookkeeping: flush every `VERIFIED_FLUSH_ROWS` rows, and check the
    /// directory every `VERIFIED_BATCH_ENTRIES` entries.
    fn advance(&mut self) -> Result<(), Fault> {
        if self.pending.len() >= VERIFIED_FLUSH_ROWS {
            self.flush()?;
        }
        self.since_flush += 1;
        if self.since_flush >= VERIFIED_BATCH_ENTRIES {
            self.flush()?;
        }
        Ok(())
    }

    /// Store what the pass verified, after the live records path still names the
    /// directory the pass read. `records.check_pass_directory`.
    fn flush(&mut self) -> Result<(), Fault> {
        self.since_flush = 0;
        if self.store.is_none() {
            return Ok(());
        }
        if let Some(dirfd) = self.dirfd {
            check_pass_directory(dirfd, &self.records_path)?;
        }
        if self.pending.is_empty() {
            return Ok(());
        }
        let rows: Vec<store::InsertRow> = self
            .pending
            .drain()
            .map(|(digest, held)| store::InsertRow {
                digest,
                identity: held.identity,
                verified_at: held.verified_at,
                payload: zlib_compress(&held.payload),
                kind: held.kind,
            })
            .collect();
        if let Some(store) = self.store.as_mut() {
            store.insert(&rows);
        }
        Ok(())
    }
}

/// `records.check_pass_directory`: fail closed when the live path no longer names the
/// directory the pass read.
fn check_pass_directory(dirfd: RawFd, records_path: &str) -> Result<(), Fault> {
    let live = fdops::lstat_path(records_path)
        .map_err(|_| Fault::Verify("records directory unavailable during verification pass".to_string()))?;
    let held = fdops::fstat(dirfd)
        .map_err(|_| Fault::Verify("records directory replaced during verification pass".to_string()))?;
    if !live.is_dir() || (live.dev, live.ino) != (held.dev, held.ino) {
        return Err(Fault::Verify("records directory replaced during verification pass".to_string()));
    }
    Ok(())
}

fn write_out(out: &mut impl Write, text: &str) -> Result<(), Fault> {
    out.write_all(text.as_bytes()).map_err(|error| Fault::io("mishe-records output error", error))
}

/// `P <sequence> <payload-bytes>\n<payload bytes>\n`, after the `<decision> <sequence> <kind>\n`
/// line that introduces it.
fn write_payload(out: &mut impl Write, sequence: u64, decision: char, kind: &str, payload: &[u8]) -> Result<(), Fault> {
    let header = format!("{decision} {sequence} {kind}\nP {sequence} {}\n", payload.len());
    out.write_all(header.as_bytes())
        .and_then(|()| out.write_all(payload))
        .and_then(|()| out.write_all(b"\n"))
        .map_err(|error| Fault::io("mishe-records output error", error))
}

/// `time.time()`.
fn now_secs() -> f64 {
    match std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH) {
        Ok(elapsed) => elapsed.as_secs_f64(),
        Err(error) => -error.duration().as_secs_f64(),
    }
}

/// `zlib.compress(data, 6)`.
fn zlib_compress(data: &[u8]) -> Vec<u8> {
    let mut encoder = flate2::write::ZlibEncoder::new(Vec::new(), flate2::Compression::new(6));
    if encoder.write_all(data).is_err() {
        return Vec::new();
    }
    encoder.finish().unwrap_or_default()
}

/// Decode a stored blob into the canonical payload bytes, or `None` when the row is
/// unusable: the caller then runs the full verification, as Python does.
fn decode_payload(blob: &[u8]) -> Option<Vec<u8>> {
    let mut decoder = flate2::read::ZlibDecoder::new(blob);
    let mut raw = Vec::new();
    decoder.read_to_end(&mut raw).ok()?;
    let value: serde_json::Value = serde_json::from_slice(&raw).ok()?;
    if !value.is_object() {
        return None;
    }
    serde_json::to_vec(&value).ok()
}
