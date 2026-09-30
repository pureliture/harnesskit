// Prevents additional console window on Windows in release, DO NOT REMOVE!!
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    if harness_desktop_lib::contexts::local::roots::run_marker_walk_worker_if_requested() {
        return;
    }
    harness_desktop_lib::run();
}
