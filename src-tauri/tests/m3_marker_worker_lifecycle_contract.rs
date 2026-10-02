#![cfg(unix)]

use std::io::{Read, Write};
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
    // Real marker records make the terminal report larger than the stdout pipe.
    // Read only the heartbeat: backpressure keeps the worker alive even when
    // enumeration completes before the parent is scheduled again.
    for index in 0..1_000 {
        std::fs::create_dir_all(project.join(format!("entry-{index:05}/.claude")))
            .expect("fixture project marker");
    }
    let handoff_path = home.join("handoff");
    let helper = r#"
exec 3<"$2"
"$1" --harnesskit-marker-walk-v1 70726f6a656374 2 50000 "$$" <&3 2>/dev/null &
worker_pid=$!
# The test acknowledges an actual protocol heartbeat, not a timed file poll.
IFS= read -r acknowledgement
if kill -0 "$worker_pid" 2>/dev/null; then
  worker_alive=1
else
  worker_alive=0
fi
printf '%s %s\n' "$worker_pid" "$worker_alive" >"$3"
if [ "$4" = crash ]; then
  kill -KILL "$$"
fi
"#;
    let mut parent = Command::new("/bin/sh")
        .args([
            "-c",
            helper,
            "marker-worker-parent",
            env!("CARGO_BIN_EXE_harness-desktop"),
            home.to_str().expect("UTF-8 fixture path"),
            handoff_path.to_str().expect("UTF-8 fixture path"),
            if parent_crashes { "crash" } else { "exit" },
        ])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .expect("direct-parent fixture process");
    // Keep the unread pipe open through the parent-end check so a broken-pipe
    // exit cannot substitute for the direct-parent liveness monitor.
    let mut worker_output = parent.stdout.take().expect("worker output pipe");
    let (heartbeat_tx, heartbeat_rx) = std::sync::mpsc::channel();
    thread::spawn(move || {
        let mut heartbeat = [0; 9];
        let result = worker_output.read_exact(&mut heartbeat);
        let _ = heartbeat_tx.send((worker_output, heartbeat, result));
    });
    let (_worker_output, heartbeat, heartbeat_result) = heartbeat_rx
        .recv_timeout(Duration::from_secs(3))
        .unwrap_or_else(|error| {
            let _ = parent.kill();
            let _ = parent.wait();
            panic!("worker must publish its heartbeat before parent exit: {error}");
        });
    heartbeat_result.expect("worker heartbeat");
    assert_eq!(heartbeat[0], b'H');
    parent
        .stdin
        .take()
        .expect("parent acknowledgement pipe")
        .write_all(b"heartbeat observed\n")
        .expect("acknowledge heartbeat");
    assert_eq!(
        parent.wait().expect("direct-parent exit").success(),
        !parent_crashes
    );
    let handoff = std::fs::read_to_string(handoff_path).expect("fixture handoff");
    let mut fields = handoff.split_whitespace();
    let worker_pid = fields.next().expect("worker PID");
    assert_eq!(fields.next().expect("worker liveness handoff"), "1");

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
