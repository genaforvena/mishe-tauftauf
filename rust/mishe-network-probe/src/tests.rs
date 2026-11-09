//! Unit tests for the decoders that turn helper output into the row.
//!
//! These are the places where a wrong token becomes a wrong measurement: the
//! gateway address, ping's loss and RTT line, curl's timing line and the
//! civil-calendar timestamp.

use super::*;

#[test]
fn a_helper_returns_its_code_and_stdout() {
    let out = run("echo", &["hi"], Duration::from_secs(5)).expect("echo runs");
    assert_eq!(out, Some((0, "hi\n".to_string())));
}

#[test]
fn a_timed_out_helper_is_killed_and_reported_unknown() {
    // The helper writes its own pid, then execs into sleep so that pid is the
    // long-running process. After run() returns, that pid must be gone.
    let pid_file = std::env::temp_dir().join(format!("mishe-probe-pid-{}", std::process::id()));
    let script = "echo $$ > \"$0\"; exec sleep 30";
    let started = Instant::now();
    let out = run(
        "sh",
        &["-c", script, pid_file.to_str().expect("utf-8 temp path")],
        Duration::from_secs(1),
    );
    assert!(out.is_err(), "a helper past its timeout is UNKNOWN, not a result");
    assert!(
        started.elapsed() < Duration::from_secs(10),
        "run must return at the timeout, not wait for the helper"
    );
    let pid = std::fs::read_to_string(&pid_file).expect("helper wrote its pid");
    let _ = std::fs::remove_file(&pid_file);
    let pid = pid.trim();
    assert!(
        !std::path::Path::new(&format!("/proc/{pid}")).exists(),
        "helper pid {pid} is still running after a timeout"
    );
}

#[test]
fn gateway_address_is_the_token_after_via() {
    let route = "default via 192.168.8.1 dev enp3s0 proto dhcp metric 600\n";
    assert_eq!(gateway_address(route).as_deref(), Some("192.168.8.1"));
}

#[test]
fn gateway_address_is_none_without_a_default_route() {
    assert_eq!(gateway_address("").as_deref(), None);
    assert_eq!(gateway_address("10.0.0.0/24 dev enp3s0 proto kernel scope link src 10.0.0.5\n").as_deref(), None);
}

#[test]
fn loss_is_the_number_before_the_marker() {
    assert_eq!(number_before("5 packets transmitted, 5 received, 0% packet loss, time 4002ms", "% packet loss"), Some(0.0));
    assert_eq!(number_before("5 packets transmitted, 2 received, 60% packet loss, time 4002ms", "% packet loss"), Some(60.0));
    assert_eq!(number_before("no loss line here", "% packet loss"), None);
}

#[test]
fn rtt_reads_the_min_avg_max_jitter_line() {
    let out = "5 packets transmitted, 5 received, 0% packet loss, time 4002ms\nrtt min/avg/max/mdev = 0.393/0.462/0.496/0.036 ms\n";
    let (min, avg, max, jitter) = rtt(out).expect("an rtt line");
    assert!((min - 0.393).abs() < 1e-9);
    assert!((avg - 0.462).abs() < 1e-9);
    assert!((max - 0.496).abs() < 1e-9);
    assert!((jitter - 0.036).abs() < 1e-9);
}

#[test]
fn rtt_is_none_without_the_line() {
    assert!(rtt("100% packet loss\n").is_none());
}

#[test]
fn a_site_is_ok_only_on_the_expected_status() {
    // The expected status came back with curl's exit 0.
    assert!(site_ok("204 0.002 0.798 0.863", Some(0), 204));
    // A wrong status is an intercepting or captive page: not a pass.
    assert!(!site_ok("302 0.01 0.02 0.03", Some(0), 204));
    // A timeout is not a pass either.
    assert!(!site_ok("000 0.000 0.000 8.001", Some(28), 204));
    // A curl that failed has no timing line at all.
    assert!(!site_ok("", Some(7), 204));
}

#[test]
fn timings_are_milliseconds_with_one_decimal() {
    let (connect, tls, total) = timings("204 0.002 0.798 0.863").expect("timings");
    assert_eq!(connect, 2.0);
    assert_eq!(tls, 798.0);
    assert_eq!(total, 863.0);
}

#[test]
fn timings_reject_a_line_that_does_not_parse() {
    assert!(timings("204 abc 0.798 0.863").is_none());
    assert!(timings("204 0.002 0.798").is_none());
}

#[test]
fn the_timestamp_is_utc_in_the_row_format() {
    // 2026-11-09 is 20766 days after 1970-01-01 (Python's date arithmetic).
    assert_eq!(civil_from_days(20766), (2026, 11, 9));
    assert_eq!(civil_from_days(0), (1970, 1, 1));
    assert_eq!(civil_from_days(1), (1970, 1, 2));
    // A leap day.
    assert_eq!(civil_from_days(19782), (2024, 2, 29));
}

#[test]
fn one_decimal_rounding_matches_the_python_shape() {
    assert_eq!(round1(2.0), 2.0);
    assert_eq!(round1(2.04), 2.0);
    // Python's round(2.05, 1) is 2.0: the double is below the tie, and both
    // languages round the exact binary value to nearest, ties to even.
    assert_eq!(round1(2.05), 2.0);
    assert_eq!(round1(0.393), 0.4);
    assert_eq!(round1(798.0), 798.0);
}

#[test]
fn a_json_string_escapes_what_a_row_must_not_carry() {
    assert_eq!(json_str("plain"), "\"plain\"");
    assert_eq!(json_str("with\"quote"), "\"with\\\"quote\"");
    assert_eq!(json_str("line\nbreak"), "\"line\\nbreak\"");
}

#[test]
fn a_row_keeps_the_python_field_set() {
    let row = Row {
        at: "2026-11-09T22:18:03Z".to_string(),
        gateway_ip: Some("192.168.8.1".to_string()),
        gateway: Some(Gateway { loss_pct: 0.0, min_ms: Some(0.393), avg_ms: Some(0.462), max_ms: Some(0.496), jitter_ms: Some(0.036) }),
        internet: vec![
            Site { name: "gstatic".to_string(), ok: true, curl_exit: None, connect_ms: Some(4.4), tls_ms: Some(220.4), total_ms: Some(300.4) },
            Site { name: "cloudflare".to_string(), ok: false, curl_exit: Some(28), connect_ms: None, tls_ms: None, total_ms: None },
        ],
        dns_ms: Some(0.7),
    };
    let text = json_row(&row);
    assert!(text.starts_with("{\"at\":\"2026-11-09T22:18:03Z\",\"gateway_ip\":\"192.168.8.1\","));
    assert!(text.contains("\"gateway\":{\"loss_pct\":0.0,\"min_ms\":0.393"));
    assert!(text.contains("\"gstatic\":{\"ok\":true,\"connect_ms\":4.4,\"tls_ms\":220.4,\"total_ms\":300.4}"));
    assert!(text.contains("\"cloudflare\":{\"ok\":false,\"curl_exit\":28}"));
    assert!(text.ends_with("\"dns_ms\":0.7}"));
    assert!(!text.contains(", "), "the row keeps Python's compact separators");
}
