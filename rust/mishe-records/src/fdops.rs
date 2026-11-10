//! Directory-relative file operations, in the form record verification needs.
//!
//! `records.py` reads a record through the *records directory's own descriptor*
//! (`os.open(..., dir_fd=directory)` plus `os.stat(..., dir_fd=directory,
//! follow_symlinks=False)`), so a swapped path cannot redirect a read and no path is
//! ever followed. Those are `openat`/`fstatat`/`fstat`, reached through `libc`.

use std::ffi::CString;
use std::os::unix::io::RawFd;

pub struct SysErr {
    pub errno: i32,
    pub name: String,
}

impl std::fmt::Display for SysErr {
    /// The text CPython's `OSError` would print for the same call: `os.open`'s
    /// `OSError` reads `[Errno 2] No such file or directory: 'x.json'`.
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "[Errno {}] {}: '{}'", self.errno, strerror(self.errno), self.name)
    }
}

fn strerror(errno: i32) -> &'static str {
    match errno {
        libc::EPERM => "Operation not permitted",
        libc::ENOENT => "No such file or directory",
        libc::EIO => "Input/output error",
        libc::EBADF => "Bad file descriptor",
        libc::EACCES => "Permission denied",
        libc::ENOMEM => "Cannot allocate memory",
        libc::ENOTDIR => "Not a directory",
        libc::EISDIR => "Is a directory",
        libc::EINVAL => "Invalid argument",
        libc::EMFILE => "Too many open files",
        libc::ENAMETOOLONG => "File name too long",
        libc::ELOOP => "Too many levels of symbolic links",
        libc::ENOSPC => "No space left on device",
        _ => "Unknown error",
    }
}

#[derive(Clone, Copy)]
pub struct Stat {
    pub dev: u64,
    pub ino: u64,
    pub mode: u32,
    pub size: i64,
    pub ctime_ns: i64,
    pub mtime_ns: i64,
}

impl Stat {
    pub fn is_reg(&self) -> bool {
        self.mode & libc::S_IFMT == libc::S_IFREG
    }
    pub fn is_dir(&self) -> bool {
        self.mode & libc::S_IFMT == libc::S_IFDIR
    }
    /// The identity `records.VerifiedStore` keys on: `(st_ino, st_size,
    /// st_ctime_ns, st_mtime_ns)`.
    pub fn identity(&self) -> (i64, i64, i64, i64) {
        (self.ino as i64, self.size, self.ctime_ns, self.mtime_ns)
    }
}

struct Syscall;

impl Syscall {
    fn errno() -> i32 {
        std::io::Error::last_os_error().raw_os_error().unwrap_or(libc::EIO)
    }
    fn fail(name: &str) -> SysErr {
        SysErr { errno: Self::errno(), name: name.to_string() }
    }
    fn stat_of(raw: &libc::stat) -> Stat {
        Stat {
            dev: raw.st_dev as u64,
            ino: raw.st_ino as u64,
            mode: raw.st_mode as u32,
            size: raw.st_size as i64,
            ctime_ns: (raw.st_ctime as i64) * 1_000_000_000 + raw.st_ctime_nsec as i64,
            mtime_ns: (raw.st_mtime as i64) * 1_000_000_000 + raw.st_mtime_nsec as i64,
        }
    }
}

/// `os.open(path, O_RDONLY|O_DIRECTORY|O_NOFOLLOW)`.
pub fn open_dir(path: &str) -> Result<RawFd, SysErr> {
    let c = CString::new(path).map_err(|_| SysErr { errno: libc::EINVAL, name: path.to_string() })?;
    let fd = unsafe { libc::open(c.as_ptr(), libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW) };
    if fd < 0 {
        return Err(Syscall::fail(path));
    }
    Ok(fd)
}

/// `os.open(name, O_RDONLY|O_NOFOLLOW|O_NONBLOCK, dir_fd=directory)`.
pub fn openat_nofollow(dirfd: RawFd, name: &str) -> Result<RawFd, SysErr> {
    let c = CString::new(name).map_err(|_| SysErr { errno: libc::EINVAL, name: name.to_string() })?;
    let flags = libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_NONBLOCK;
    let fd = unsafe { libc::openat(dirfd, c.as_ptr(), flags) };
    if fd < 0 {
        return Err(Syscall::fail(name));
    }
    Ok(fd)
}

/// `os.stat(name, dir_fd=directory, follow_symlinks=False)` — `lstat` semantics.
pub fn fstatat_nofollow(dirfd: RawFd, name: &str) -> Result<Stat, SysErr> {
    let c = CString::new(name).map_err(|_| SysErr { errno: libc::EINVAL, name: name.to_string() })?;
    let mut raw: libc::stat = unsafe { std::mem::zeroed() };
    let rc = unsafe { libc::fstatat(dirfd, c.as_ptr(), &mut raw, libc::AT_SYMLINK_NOFOLLOW) };
    if rc != 0 {
        return Err(Syscall::fail(name));
    }
    Ok(Syscall::stat_of(&raw))
}

/// `os.lstat(path)`, for the records-directory swap check.
pub fn lstat_path(path: &str) -> Result<Stat, SysErr> {
    let c = CString::new(path).map_err(|_| SysErr { errno: libc::EINVAL, name: path.to_string() })?;
    let mut raw: libc::stat = unsafe { std::mem::zeroed() };
    if unsafe { libc::lstat(c.as_ptr(), &mut raw) } != 0 {
        return Err(Syscall::fail(path));
    }
    Ok(Syscall::stat_of(&raw))
}

/// `os.fstat(fd)`.
pub fn fstat(fd: RawFd) -> Result<Stat, SysErr> {
    let mut raw: libc::stat = unsafe { std::mem::zeroed() };
    if unsafe { libc::fstat(fd, &mut raw) } != 0 {
        return Err(SysErr { errno: Syscall::errno(), name: String::new() });
    }
    Ok(Syscall::stat_of(&raw))
}

/// `handle.read(limit + 1)`: read to end of file, or until `limit` bytes are certain.
pub fn read_up_to(fd: RawFd, limit: usize) -> Result<Vec<u8>, SysErr> {
    let mut out: Vec<u8> = Vec::new();
    let mut buf = [0u8; 64 * 1024];
    loop {
        let want = std::cmp::min(buf.len(), limit + 1 - out.len().min(limit + 1));
        if want == 0 {
            break;
        }
        let n = unsafe { libc::read(fd, buf.as_mut_ptr() as *mut libc::c_void, want) };
        if n < 0 {
            return Err(SysErr { errno: Syscall::errno(), name: String::new() });
        }
        if n == 0 {
            break;
        }
        out.extend_from_slice(&buf[..n as usize]);
    }
    Ok(out)
}

pub fn close(fd: RawFd) {
    unsafe { libc::close(fd) };
}
