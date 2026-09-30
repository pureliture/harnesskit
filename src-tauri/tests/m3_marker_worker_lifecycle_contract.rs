#![cfg(unix)]

use std::process::{Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};

use tempfile::tempdir;

#[test]
fn marker_worker_exits_when_its_direct_parent_ends() {
    assert_marker_worker_stops_after_parent_end(false);
}

#[test]
fn marker_worker_exits_when_its_direct_parent_crashes() {
    assert_marker_worker_stops_after_parent_end(true);
}

fn assert_marker_worker_stops_after_parent_end(parent_crashes: bool) {
    let fixture = tempdir().expect("temporary marker worker fixture");
    let home = fixture.path();
    let project = home.join("project");
    std::fs::create_dir(&project).expect("fixture project");
    for index in 0..20_000 {
        std::fs::write(project.join(format!("entry-{index:05}")), []).expect("fixture entry");
    }
    let worker_output = home.join("worker-output");
    let helper = r#"
exec 3<"$2"
"$1" --harnesskit-marker-walk-v1 70726f6a656374 2 50000 "$$" <&3 >"$3" 2>/dev/null &
worker_pid=$!
attempt=0
while [ ! -s "$3" ] && [ "$attempt" -lt 100 ]; do
  sleep 0.01
  attempt=$((attempt + 1))
done
if kill -0 "$worker_pid" 2>/dev/null; then
  worker_alive=1
else
  worker_alive=0
fi
printf '%s %s %s\n' "$worker_pid" "$attempt" "$worker_alive"
if [ "$4" = crash ]; then
  kill -KILL "$$"
fi
"#;
    let helper_output = Command::new("/bin/sh")
        .args([
            "-c",
            helper,
            "marker-worker-parent",
            env!("CARGO_BIN_EXE_harness-desktop"),
            home.to_str().expect("UTF-8 fixture path"),
            worker_output.to_str().expect("UTF-8 fixture path"),
            if parent_crashes { "crash" } else { "exit" },
        ])
        .output()
        .expect("direct-parent fixture process");
    assert_eq!(helper_output.status.success(), !parent_crashes);
    let handoff = String::from_utf8(helper_output.stdout).expect("fixture handoff");
    let mut fields = handoff.split_whitespace();
    let worker_pid = fields.next().expect("worker PID");
    let heartbeat_attempt = fields
        .next()
        .expect("heartbeat attempt")
        .parse::<usize>()
        .expect("numeric heartbeat attempt");
    let worker_alive_before_parent_end = fields.next().expect("worker liveness handoff");
    assert!(
        heartbeat_attempt < 100,
        "worker must start before parent exits"
    );
    assert_eq!(worker_alive_before_parent_end, "1");
    assert!(
        std::fs::read(&worker_output)
            .expect("worker heartbeat output")
            .starts_with(b"H"),
        "worker must publish its initial heartbeat before the parent ends"
    );

    let deadline = Instant::now() + Duration::from_secs(3);
    while marker_worker_is_alive(worker_pid) && Instant::now() < deadline {
        thread::sleep(Duration::from_millis(25));
    }
    if marker_worker_is_alive(worker_pid) {
        panic!("worker remained alive after its direct parent ended; no signal is sent to an identity-unverified PID");
    }
}

fn marker_worker_is_alive(worker_pid: &str) -> bool {
    Command::new("/bin/kill")
        .args(["-0", worker_pid])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|status| status.success())
}
