//! mishe-network-probe: one bounded network probe row.
//!
//! Contract: rust/mishe-network-probe/CONTRACT.md. One JSONL row appended to
//! HOME/network/probe.jsonl; exit 0 (network answered), 2 (network down),
//! 3 (UNKNOWN: the probe could not run). A probe that cannot run never
//! reports a quiet network.
//!
//! std only: no crates, no TLS stack. The secure page load stays with curl
//! (the system tool already doing certificate verification), so "the internet
//! answered" is still proven by a verified HTTPS status, not by a connect.

use std::fmt::Write as _;
use std::io::Write as _;
use std::net::ToSocketAddrs;
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

#[cfg(test)]
mod tests;

/// name -> (url, the HTTP status that proves the real site answered)
const INTERNET: [(&str, &str, u16); 2] = [
    ("gstatic", "https://www.gstatic.com/generate_204", 204),
    ("cloudflare", "https://www.cloudflare.com/cdn-cgi/trace", 200),
];
const DNS_NAME: &str = "www.wikipedia.org";
const CURL_FMT: &str = "%{http_code} %{time_connect} %{time_appconnect} %{time_total}";

/// Exit codes of the contract.
const EXIT_OK: i32 = 0;
const EXIT_DOWN: i32 = 2;
const EXIT_UNKNOWN: i32 = 3;

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let mut home: Option<String> = None;
    let mut i = 0;
    while i < args.len() {
        match args[i].as_str() {
            "--home" => {
                i += 1;
                if i >= args.len() {
                    usage("--home needs a path");
                }
                home = Some(args[i].clone());
            }
            "--help" | "-h" => {
                println!("usage: mishe-network-probe --home PATH");
                std::process::exit(EXIT_OK);
            }
            other => usage(&format!("unknown argument {other}")),
        }
        i += 1;
    }
    let home = match home {
        Some(h) => h,
        None => usage("--home is required"),
    };

    let row = match probe() {
        Ok(row) => row,
        Err(why) => {
            eprintln!("mishe-network-probe: {why}");
            std::process::exit(EXIT_UNKNOWN);
        }
    };

    if let Err(why) = append_row(&home, &row) {
        eprintln!("mishe-network-probe: {why}");
        std::process::exit(EXIT_UNKNOWN);
    }

    let gateway_down = match &row.gateway {
        None => true,
        Some(g) => g.loss_pct >= 50.0,
    };
    let internet_down = !row.internet.iter().any(|site| site.ok);
    if gateway_down || internet_down {
        print!(
            "network down: {}{}",
            if gateway_down { "router/LAN not answering" } else { "" },
            if internet_down { " internet unreachable (no secure site answered)" } else { "" }
        );
        std::process::exit(EXIT_DOWN);
    }
    std::process::exit(EXIT_OK);
}

fn usage(why: &str) -> ! {
    eprintln!("mishe-network-probe: {why}");
    eprintln!("usage: mishe-network-probe --home PATH");
    std::process::exit(1);
}

// ---------------------------------------------------------------- the probe

struct Gateway {
    loss_pct: f64,
    min_ms: Option<f64>,
    avg_ms: Option<f64>,
    max_ms: Option<f64>,
    jitter_ms: Option<f64>,
}

struct Site {
    name: String,
    ok: bool,
    curl_exit: Option<i32>,
    connect_ms: Option<f64>,
    tls_ms: Option<f64>,
    total_ms: Option<f64>,
}

struct Row {
    at: String,
    gateway_ip: Option<String>,
    gateway: Option<Gateway>,
    internet: Vec<Site>,
    dns_ms: Option<f64>,
}

fn probe() -> Result<Row, String> {
    let gateway_ip = gateway_ip()?;
    let gateway = match &gateway_ip {
        None => None,
        Some(ip) => ping(ip)?,
    };
    let mut internet = Vec::new();
    for (name, url, status) in INTERNET {
        internet.push(fetch(name, url, status)?);
    }
    let dns_ms = dns_ms();
    Ok(Row { at: utc_now(), gateway_ip, gateway, internet, dns_ms })
}

/// The default route's address. `Ok(None)` when there is no default route;
/// `Err` when `ip` could not be run at all (UNKNOWN, not a quiet network).
fn gateway_ip() -> Result<Option<String>, String> {
    let out = run("ip", &["route", "show", "default"], Duration::from_secs(5))?;
    let text = match out {
        None => String::new(),
        Some((_code, text)) => text,
    };
    Ok(gateway_address(&text))
}

/// The default route's address: the token after `default via`. `None` when
/// there is no default route.
fn gateway_address(route: &str) -> Option<String> {
    let words: Vec<&str> = route.split_whitespace().collect();
    words
        .windows(3)
        .find(|w| w[0] == "default" && w[1] == "via")
        .map(|w| w[2].to_string())
}

/// ICMP latency and loss to the router on the LAN.
fn ping(host: &str) -> Result<Option<Gateway>, String> {
    let out = run("ping", &["-n", "-c", "5", "-W", "2", "-i", "0.2", host], Duration::from_secs(30))?;
    let text = match out {
        None => String::new(),
        Some((_code, text)) => text,
    };
    let loss = match number_before(&text, "% packet loss") {
        Some(v) => v,
        None => return Ok(None),
    };
    let mut gateway = Gateway { loss_pct: loss, min_ms: None, avg_ms: None, max_ms: None, jitter_ms: None };
    if let Some((min, avg, max, jitter)) = rtt(&text) {
        gateway.min_ms = Some(min);
        gateway.avg_ms = Some(avg);
        gateway.max_ms = Some(max);
        gateway.jitter_ms = Some(jitter);
    }
    Ok(Some(gateway))
}

/// One secure page load. `ok` only when curl succeeded and the expected
/// status came back: a router that answers for every address answers the
/// connection, never the verified page.
fn fetch(name: &str, url: &str, expected: u16) -> Result<Site, String> {
    let out = run(
        "curl",
        &["-s", "-o", "/dev/null", "-m", "10", "-w", CURL_FMT, url],
        Duration::from_secs(15),
    )?;
    let (code, stdout) = match out {
        None => (None, String::new()),
        Some((code, stdout)) => (Some(code), stdout),
    };
    let curl_exit = code.unwrap_or(-1);
    if !site_ok(&stdout, code, expected) {
        return Ok(Site {
            name: name.to_string(),
            ok: false,
            curl_exit: Some(curl_exit),
            connect_ms: None,
            tls_ms: None,
            total_ms: None,
        });
    }
    let (connect, tls, total) = match timings(&stdout) {
        Some(values) => values,
        // A timing line that does not parse is not a successful measurement.
        None => {
            return Ok(Site {
                name: name.to_string(),
                ok: false,
                curl_exit: Some(curl_exit),
                connect_ms: None,
                tls_ms: None,
                total_ms: None,
            })
        }
    };
    Ok(Site {
        name: name.to_string(),
        ok: true,
        curl_exit: None,
        connect_ms: Some(connect),
        tls_ms: Some(tls),
        total_ms: Some(total),
    })
}

/// `ok` only when curl succeeded and the expected status came back: a router
/// that answers for every address answers the connection, never the page.
fn site_ok(stdout: &str, code: Option<i32>, expected: u16) -> bool {
    if code != Some(0) {
        return false;
    }
    let parts: Vec<&str> = stdout.split_whitespace().collect();
    parts.len() == 4 && parts[0] == expected.to_string()
}

/// The three timings of curl's `-w` line, in milliseconds with one decimal.
fn timings(stdout: &str) -> Option<(f64, f64, f64)> {
    let parts: Vec<&str> = stdout.split_whitespace().collect();
    if parts.len() != 4 {
        return None;
    }
    let connect = parts[1].parse::<f64>().ok().map(|v| round1(v * 1000.0))?;
    let tls = parts[2].parse::<f64>().ok().map(|v| round1(v * 1000.0))?;
    let total = parts[3].parse::<f64>().ok().map(|v| round1(v * 1000.0))?;
    Some((connect, tls, total))
}

/// DNS resolution time through the home resolver.
fn dns_ms() -> Option<f64> {
    let started = Instant::now();
    match (DNS_NAME, 443u16).to_socket_addrs() {
        Ok(_) => Some(round1(started.elapsed().as_secs_f64() * 1000.0)),
        Err(_) => None,
    }
}

// ------------------------------------------------------------------- output

fn append_row(home: &str, row: &Row) -> Result<(), String> {
    let dir = std::path::Path::new(home).join("network");
    std::fs::create_dir_all(&dir).map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
    let path = dir.join("probe.jsonl");
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|e| format!("cannot open {}: {e}", path.display()))?;
    let mut line = json_row(row);
    line.push('\n');
    file.write_all(line.as_bytes()).map_err(|e| format!("cannot write {}: {e}", path.display()))
}

fn json_row(row: &Row) -> String {
    let mut out = String::from("{");
    let _ = write!(out, "\"at\":{}", json_str(&row.at));
    let _ = write!(out, ",\"gateway_ip\":{}", match &row.gateway_ip {
        Some(ip) => json_str(ip),
        None => "null".to_string(),
    });
    let _ = write!(out, ",\"gateway\":{}", match &row.gateway {
        Some(g) => {
            let mut s = String::from("{\"loss_pct\":");
            s.push_str(&json_f64(g.loss_pct));
            if let Some(v) = g.min_ms {
                let _ = write!(s, ",\"min_ms\":{}", json_f64(v));
            }
            if let Some(v) = g.avg_ms {
                let _ = write!(s, ",\"avg_ms\":{}", json_f64(v));
            }
            if let Some(v) = g.max_ms {
                let _ = write!(s, ",\"max_ms\":{}", json_f64(v));
            }
            if let Some(v) = g.jitter_ms {
                let _ = write!(s, ",\"jitter_ms\":{}", json_f64(v));
            }
            s.push('}');
            s
        }
        None => "null".to_string(),
    });
    let _ = write!(out, ",\"internet\":{{");
    for (index, site) in row.internet.iter().enumerate() {
        if index > 0 {
            out.push(',');
        }
        let _ = write!(out, "{}:", json_str(&site.name));
        if site.ok {
            let _ = write!(out, "{{\"ok\":true");
            if let Some(v) = site.connect_ms {
                let _ = write!(out, ",\"connect_ms\":{}", json_f64(v));
            }
            if let Some(v) = site.tls_ms {
                let _ = write!(out, ",\"tls_ms\":{}", json_f64(v));
            }
            if let Some(v) = site.total_ms {
                let _ = write!(out, ",\"total_ms\":{}", json_f64(v));
            }
            out.push('}');
        } else {
            let _ = write!(out, "{{\"ok\":false,\"curl_exit\":{}", site.curl_exit.unwrap_or(-1));
            out.push('}');
        }
    }
    out.push('}');
    let _ = write!(out, ",\"dns_ms\":{}", match row.dns_ms {
        Some(v) => json_f64(v),
        None => "null".to_string(),
    });
    out.push('}');
    out
}

fn json_str(value: &str) -> String {
    let mut out = String::from("\"");
    for ch in value.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

/// One decimal place, rounded like Python's `round(value, 1)`: the nearest
/// double to the correctly rounded one-decimal value.
fn round1(value: f64) -> f64 {
    format!("{value:.1}").parse::<f64>().unwrap_or(value)
}

/// The shortest form that reads back as the same double, like Python's repr.
fn json_f64(value: f64) -> String {
    format!("{value:?}")
}

// -------------------------------------------------------------------- tools

/// Run a helper and return its exit code and stdout. `Err` when the helper
/// could not be run or did not finish in time (UNKNOWN). `Ok(None)` when it
/// ran and failed; the caller decides what that means.
fn run(program: &str, args: &[&str], timeout: Duration) -> Result<Option<(i32, String)>, String> {
    let child = Command::new(program)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("cannot run {program}: {e}"))?;
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        let _ = sender.send(child.wait_with_output());
    });
    match receiver.recv_timeout(timeout) {
        Ok(Ok(done)) => Ok(Some((
            done.status.code().unwrap_or(-1),
            String::from_utf8_lossy(&done.stdout).into_owned(),
        ))),
        Ok(Err(e)) => Err(format!("{program} failed: {e}")),
        Err(_) => Err(format!("{program} did not finish in time")),
    }
}

/// The number right before a marker, as `([0-9.]+)% packet loss` reads it.
fn number_before(text: &str, marker: &str) -> Option<f64> {
    let at = text.find(marker)?;
    let head = &text[..at];
    let start = head
        .rfind(|c: char| !(c.is_ascii_digit() || c == '.'))
        .map(|i| i + 1)
        .unwrap_or(0);
    head[start..].parse::<f64>().ok()
}

/// The four numbers of ping's `= min/avg/max/mdev` line. Like the Python
/// regex `= ([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+) ms`, the first `= ` whose
/// following numbers parse is the answer.
fn rtt(text: &str) -> Option<(f64, f64, f64, f64)> {
    let mut rest = text;
    while let Some(at) = rest.find("= ") {
        let after = &rest[at + 2..];
        if let Some(values) = four_numbers(after) {
            return Some(values);
        }
        rest = &rest[at + 2..];
    }
    None
}

/// Four `number/number/number/number` values followed by ` ms`.
fn four_numbers(text: &str) -> Option<(f64, f64, f64, f64)> {
    let end = text.find(" ms")?;
    let mut values = Vec::new();
    for token in text[..end].split('/') {
        match token.parse::<f64>() {
            Ok(v) => values.push(v),
            Err(_) => return None,
        }
    }
    if values.len() != 4 {
        return None;
    }
    Some((values[0], values[1], values[2], values[3]))
}

// --------------------------------------------------------------------- time

/// UTC as `YYYY-MM-DDTHH:MM:SSZ`, the format the probe rows carry.
fn utc_now() -> String {
    let seconds = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let days = (seconds / 86400) as i64;
    let rest = seconds % 86400;
    let (year, month, day) = civil_from_days(days);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        rest / 3600,
        (rest % 3600) / 60,
        rest % 60
    )
}

/// Days since 1970-01-01 to (year, month, day), the standard civil calendar.
fn civil_from_days(days: i64) -> (i64, u32, u32) {
    let shifted = days + 719468;
    let era = if shifted >= 0 { shifted } else { shifted - 146096 } / 146097;
    let day_of_era = (shifted - era * 146097) as u64;
    let year_of_era =
        (day_of_era - day_of_era / 1460 + day_of_era / 36524 - day_of_era / 146096) / 365;
    let year = year_of_era as i64 + era * 400;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let mp = (5 * day_of_year + 2) / 153;
    let day = (day_of_year - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let year = if month <= 2 { year + 1 } else { year };
    (year, month, day)
}
