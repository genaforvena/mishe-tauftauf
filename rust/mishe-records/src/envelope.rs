//! `records.envelope`: verify the immutable record an entry references.
//!
//! The read goes through the records directory's own descriptor, exactly as
//! `records._read` does, and every failure keeps the Python `ValueError` text so a
//! caller cannot tell the two implementations apart.

use serde_json::Value;
use std::os::unix::io::RawFd;

use crate::{fdops, sha256, Fault};

/// `records.MAX_BYTES`.
const MAX_BYTES: usize = 1024 * 1024;

/// Verify `SITE/records/<digest>.json` and return the envelope's `payload` object.
///
/// `expected` is the digest the entry's reference names; the caller has already
/// established that it equals `digest`.
pub fn verify(dirfd: RawFd, digest: &str, expected: &str) -> Result<Value, Fault> {
    if digest != expected {
        return Err(Fault::Verify("record digest mismatch or missing site context".to_string()));
    }
    let name = format!("{digest}.json");
    let fd = fdops::openat_nofollow(dirfd, &name)
        .map_err(|error| Fault::Verify(format!("record unavailable: {error}")))?;
    let raw = read_record(fd);
    fdops::close(fd);
    let raw = raw?;
    if sha256::hex_digest(&raw) != expected {
        return Err(Fault::Verify("immutable record checksum mismatch".to_string()));
    }
    let record: Value = serde_json::from_slice(&raw)
        .map_err(|error| Fault::Verify(format!("record is not JSON: {error}")))?;
    let Value::Object(mut fields) = record else {
        return Err(Fault::Verify("invalid immutable record envelope".to_string()));
    };
    // `record.get("version") != 1`: Python's `1.0` and `True` both equal `1`.
    let version_ok = matches!(fields.get("version"), Some(Value::Number(n)) if n.as_f64() == Some(1.0))
        || matches!(fields.get("version"), Some(Value::Bool(true)));
    let kind_ok = matches!(fields.get("kind"), Some(Value::String(kind)) if !kind.is_empty());
    if !version_ok || !kind_ok {
        return Err(Fault::Verify("invalid immutable record envelope".to_string()));
    }
    match fields.remove("payload") {
        Some(payload @ Value::Object(_)) => Ok(payload),
        _ => Err(Fault::Verify("invalid immutable record envelope".to_string())),
    }
}

/// `records._read`: a regular file of 1 byte to 1 MiB, read in full.
fn read_record(fd: RawFd) -> Result<Vec<u8>, Fault> {
    let stat = fdops::fstat(fd).map_err(|error| Fault::Verify(format!("record unavailable: {error}")))?;
    if !stat.is_reg() || stat.size <= 0 || stat.size > MAX_BYTES as i64 {
        return Err(Fault::Verify("record must be a regular file of 1 byte to 1 MiB".to_string()));
    }
    let raw = fdops::read_up_to(fd, MAX_BYTES)
        .map_err(|error| Fault::Verify(format!("record unavailable: {error}")))?;
    if raw.len() > MAX_BYTES {
        return Err(Fault::Verify("record exceeds 1 MiB".to_string()));
    }
    Ok(raw)
}
