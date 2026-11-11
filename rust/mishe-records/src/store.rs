//! The shared verified-payload store, `SITE/records.verified.sqlite3`.
//!
//! The table, its WAL mode and its row semantics are the ones `records.VerifiedStore`
//! keeps (`records.py:230-302`): a row is usable only while the four identity columns
//! are integers, `verified_at` is a number, the payload is a BLOB and the row is
//! younger than `VERIFIED_MAX_AGE`. A missing, unreadable or corrupt store disables the
//! cache; it never substitutes a stale payload and never fails the pass.

use rusqlite::types::ValueRef;
use rusqlite::Connection;
use std::time::Duration;

pub struct InsertRow {
    pub digest: String,
    pub identity: (i64, i64, i64, i64),
    pub verified_at: f64,
    pub payload: Vec<u8>,
    /// The envelope's `kind`, carried so a repeat reference never re-reads the file.
    pub kind: String,
}

pub struct Hit {
    /// `(st_ino, st_size, st_ctime_ns, st_mtime_ns)` from the stored row.
    pub identity: (i64, i64, i64, i64),
    pub verified_at: f64,
    /// The stored blob, read only in `--payloads` mode.
    pub payload: Option<Vec<u8>>,
    /// The envelope's `kind` as stored. A row written before the column existed has
    /// none, and is a miss: the caller then re-verifies and rewrites the row.
    pub kind: String,
}

pub struct Store {
    connection: Option<Connection>,
}

/// One row before the type rules are applied.
struct Raw {
    identity: (Option<i64>, Option<i64>, Option<i64>, Option<i64>),
    verified_at: Option<f64>,
    blob: Option<Vec<u8>>,
    kind: Option<String>,
}

impl Store {
    pub fn open(path: &str) -> Store {
        let connection = Connection::open(path).ok().and_then(|c| {
            c.busy_timeout(Duration::from_secs(10)).ok()?;
            c.execute_batch(
                "PRAGMA journal_mode=WAL;\n\
                 CREATE TABLE IF NOT EXISTS verified (digest TEXT PRIMARY KEY, ino INTEGER, \
                 size INTEGER, ctime_ns INTEGER, mtime_ns INTEGER, verified_at REAL, \
                 payload BLOB NOT NULL, kind TEXT);",
            )
            .ok()?;
            // A store written before the kind column existed keeps serving its rows; they
            // read as kind NULL, which `lookup` treats as a miss.
            if c.prepare("SELECT kind FROM verified LIMIT 0").is_err() {
                c.execute("ALTER TABLE verified ADD COLUMN kind TEXT", []).ok()?;
            }
            Some(c)
        });
        Store { connection }
    }

    pub fn lookup(&mut self, digest: &str, want_payload: bool) -> Option<Hit> {
        let connection = self.connection.as_mut()?;
        let sql = if want_payload {
            "SELECT ino, size, ctime_ns, mtime_ns, verified_at, payload, kind FROM verified WHERE digest=?1"
        } else {
            "SELECT ino, size, ctime_ns, mtime_ns, verified_at, typeof(payload)='blob', kind \
             FROM verified WHERE digest=?1"
        };
        let mut statement = connection.prepare_cached(sql).ok()?;
        // ValueRef borrows the row, so every column is classified inside the closure:
        // the four identity fields must be SQLite INTEGERs, `verified_at` a number, and
        // the payload a BLOB — `type(value) is int`, `type(row[4]) in (int, float)` and
        // `isinstance(row[5], bytes)`. `Some(_)` in `blob` means "the column is a BLOB".
        let row = statement.query_row([digest], |row| {
            Ok(Raw {
                identity: (
                    int_col(row.get_ref(0)?),
                    int_col(row.get_ref(1)?),
                    int_col(row.get_ref(2)?),
                    int_col(row.get_ref(3)?),
                ),
                verified_at: number_col(row.get_ref(4)?),
                blob: match row.get_ref(5)? {
                    ValueRef::Blob(bytes) if want_payload => Some(bytes.to_vec()),
                    ValueRef::Blob(_) => Some(Vec::new()),
                    _ => None,
                },
                kind: match row.get_ref(6)? {
                    ValueRef::Text(bytes) => std::str::from_utf8(bytes).ok().map(str::to_string),
                    _ => None,
                },
            })
        });
        let row = match row {
            Ok(row) => row,
            Err(rusqlite::Error::QueryReturnedNoRows) => return None,
            Err(_) => return None,
        };
        let identity = (row.identity.0?, row.identity.1?, row.identity.2?, row.identity.3?);
        let verified_at = row.verified_at?;
        let kind = row.kind?;
        Some(Hit { identity, verified_at, payload: row.blob, kind })
    }

    /// `records.VerifiedStore.store`: one transaction per batch, `INSERT OR REPLACE`.
    pub fn insert(&mut self, rows: &[InsertRow]) {
        if rows.is_empty() {
            return;
        }
        let Some(connection) = self.connection.as_mut() else {
            return;
        };
        let written = (|| -> rusqlite::Result<()> {
            let transaction = connection.transaction()?;
            {
                let mut statement = transaction.prepare_cached(
                    "INSERT OR REPLACE INTO verified \
                     (digest, ino, size, ctime_ns, mtime_ns, verified_at, payload, kind) \
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                )?;
                for row in rows {
                    statement.execute(rusqlite::params![
                        row.digest,
                        row.identity.0,
                        row.identity.1,
                        row.identity.2,
                        row.identity.3,
                        row.verified_at,
                        row.payload,
                        row.kind,
                    ])?;
                }
            }
            transaction.commit()
        })();
        if written.is_err() {
            self.connection = None;
        }
    }
}

/// A column that must be a SQLite INTEGER, as `type(value) is int` requires.
fn int_col(value: ValueRef<'_>) -> Option<i64> {
    match value {
        ValueRef::Integer(number) => Some(number),
        _ => None,
    }
}

/// A column that must be an SQLite number, as `type(row[4]) in (int, float)` requires.
fn number_col(value: ValueRef<'_>) -> Option<f64> {
    match value {
        ValueRef::Integer(number) => Some(number as f64),
        ValueRef::Real(number) => Some(number),
        _ => None,
    }
}
