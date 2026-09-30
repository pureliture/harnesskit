use std::path::PathBuf;

fn manifest_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

#[test]
fn local_action_command_is_id_only_and_registered_exactly_once() {
    let root = manifest_dir();
    let commands = std::fs::read_to_string(root.join("src/api/commands.rs")).unwrap();
    let local_dto = std::fs::read_to_string(root.join("src/api/dto/local.rs")).unwrap();
    let lib = std::fs::read_to_string(root.join("src/lib.rs")).unwrap();

    assert!(commands.contains("fn act_on_local_instance("));
    assert!(commands.contains("snapshot_id: String"));
    assert!(commands.contains("instance_id: String"));
    assert!(commands.contains("action: LocalInstanceActionDto"));
    assert!(local_dto.contains("enum LocalInstanceActionDto"));
    assert!(local_dto.contains("enum LocalInstanceActionOutcomeDto"));
    assert!(!local_dto.contains("ViewerPayloadDto"));
    assert!(!local_dto.contains("Viewer"));

    let action_start = commands
        .find("fn act_on_local_instance(")
        .expect("typed Local action command must exist");
    let action_tail = &commands[action_start..];
    let action_end = action_tail
        .find("\n}\n")
        .expect("Local action command must have a bounded body");
    let action_command = &action_tail[..action_end];
    for forbidden in ["raw_path", "absolute_path", "PathBuf", "path: String"] {
        assert!(
            !action_command.contains(forbidden),
            "Local action command must not accept frontend path authority: {forbidden}"
        );
    }

    assert_eq!(
        lib.matches("api::commands::act_on_local_instance").count(),
        1,
        "the approved command must be registered exactly once"
    );
}

#[test]
fn local_action_native_ports_have_no_shell_or_generic_opener_fallback() {
    let root = manifest_dir();
    let cargo = std::fs::read_to_string(root.join("Cargo.toml")).unwrap();
    let action = std::fs::read_to_string(root.join("src/contexts/local/action.rs")).unwrap();

    assert!(cargo.contains("\"NSPasteboard\""));
    assert!(cargo.contains("\"NSWorkspace\""));
    assert!(action.contains("NSPasteboard"));
    assert!(action.contains("NSWorkspace"));

    for forbidden in [
        "std::process::Command",
        "Command::new",
        "/usr/bin/open",
        "tauri_plugin_opener",
        "shell::open",
    ] {
        assert!(
            !action.contains(forbidden),
            "Local path actions must not fall back to {forbidden}"
        );
    }
}

#[test]
fn frontend_action_bridge_forwards_only_snapshot_instance_and_action() {
    let manifest = manifest_dir();
    let root = manifest
        .parent()
        .expect("src-tauri must live below the repository root");
    let backend = std::fs::read_to_string(root.join("src-frontend/backend.js")).unwrap();

    assert!(backend.contains("act_on_local_instance"));
    assert!(backend.contains("snapshotId"));
    assert!(backend.contains("instanceId"));
    for forbidden in ["rawPath", "absolutePath"] {
        assert!(
            !backend.contains(forbidden),
            "frontend must not forward path authority: {forbidden}"
        );
    }
}

#[test]
fn local_batch_removal_commands_are_opaque_id_only_and_registered_once() {
    let root = manifest_dir();
    let commands = std::fs::read_to_string(root.join("src/api/commands.rs")).unwrap();
    let local_dto = std::fs::read_to_string(root.join("src/api/dto/local.rs")).unwrap();
    let lib = std::fs::read_to_string(root.join("src/lib.rs")).unwrap();
    let backend = std::fs::read_to_string(
        root.parent()
            .expect("src-tauri must live below the repository root")
            .join("src-frontend/backend.js"),
    )
    .unwrap();

    for command in [
        "prepare_local_removal",
        "apply_local_removal",
        "reconcile_local_removal",
    ] {
        assert!(commands.contains(&format!("fn {command}(")));
        assert_eq!(
            lib.matches(&format!("api::commands::{command}")).count(),
            1,
            "{command} must be registered exactly once"
        );
    }
    assert!(local_dto.contains("PrepareLocalRemovalRequestDto"));
    assert!(local_dto.contains("ApplyLocalRemovalRequestDto"));
    assert!(local_dto.contains("ReconcileLocalRemovalRequestDto"));
    assert!(local_dto.contains("RemovalPlanDto"));
    assert!(local_dto.contains("LocalRemovalOutcomeDto"));
    assert!(local_dto.contains("LocalRemovalReconciliationDto"));

    let prepare_start = commands
        .find("fn prepare_local_removal(")
        .expect("prepare command must exist");
    let prepare_tail = &commands[prepare_start..];
    let prepare_end = prepare_tail
        .find("\n}\n")
        .expect("prepare command must have a bounded body");
    let prepare_command = &prepare_tail[..prepare_end];
    for forbidden in [
        "raw_path",
        "absolute_path",
        "PathBuf",
        "source_body",
        "pointer",
    ] {
        assert!(
            !prepare_command.contains(forbidden),
            "prepare command must not accept frontend filesystem authority: {forbidden}"
        );
    }

    for forbidden in ["rawPath", "absolutePath", "sourceBody", "jsonPointer"] {
        assert!(
            !backend.contains(forbidden),
            "frontend must not forward removal filesystem authority: {forbidden}"
        );
    }
    assert!(backend.contains("prepareLocalRemoval"));
    assert!(backend.contains("applyLocalRemoval"));
    assert!(backend.contains("reconcileLocalRemoval"));
}
