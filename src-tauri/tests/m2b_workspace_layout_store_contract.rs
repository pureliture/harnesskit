use std::fs;

use harness_desktop_lib::contexts::layout::WorkspaceLayoutStore;
use harness_desktop_lib::contexts::layout::{DEFAULT_LEFT_WIDTH_PX, DEFAULT_RIGHT_WIDTH_PX};
use tempfile::tempdir;

#[test]
fn preference_round_trips_as_one_versioned_pair() {
    let temp = tempdir().unwrap();
    let path = temp.path().join("state").join("layout.json");
    let store = WorkspaceLayoutStore::new(path.clone());

    store.save(420, 500).unwrap();

    let loaded = store.load();
    assert_eq!(loaded.preferred_left_width_px, 420);
    assert_eq!(loaded.preferred_right_width_px, 500);
    assert!(loaded.persisted);
    assert_eq!(loaded.diagnostic, None);
    assert_eq!(
        fs::read_to_string(path).unwrap(),
        "{\"schema_version\":2,\"preferred_left_width_px\":420,\"preferred_right_width_px\":500,\"left_collapsed\":false,\"right_collapsed\":false}\n"
    );
}

#[test]
fn compact_panel_minimums_round_trip_and_lower_values_are_rejected() {
    let temp = tempdir().unwrap();
    let path = temp.path().join("layout.json");
    let store = WorkspaceLayoutStore::new(path);

    store.save(200, 184).unwrap();
    let loaded = store.load();
    assert_eq!(loaded.preferred_left_width_px, 200);
    assert_eq!(loaded.preferred_right_width_px, 184);

    assert_eq!(
        store.save(199, 184).unwrap_err().code,
        "workspace_layout_preference_invalid"
    );
    assert_eq!(
        store.save(200, 183).unwrap_err().code,
        "workspace_layout_preference_invalid"
    );
}

#[test]
fn invalid_preference_falls_back_as_a_complete_pair_with_a_safe_diagnostic() {
    let temp = tempdir().unwrap();
    let path = temp.path().join("layout.json");
    let invalid_sources = [
        "not json",
        r#"{"schema_version":2,"preferred_left_width_px":420,"preferred_right_width_px":500}"#,
        r#"{"schema_version":1,"preferred_left_width_px":199,"preferred_right_width_px":500}"#,
        r#"{"schema_version":1,"preferred_left_width_px":420,"preferred_right_width_px":561}"#,
        r#"{"schema_version":1,"preferred_left_width_px":420}"#,
    ];

    for source in invalid_sources {
        fs::write(&path, source).unwrap();
        let loaded = WorkspaceLayoutStore::new(path.clone()).load();

        assert_eq!(loaded.preferred_left_width_px, DEFAULT_LEFT_WIDTH_PX);
        assert_eq!(loaded.preferred_right_width_px, DEFAULT_RIGHT_WIDTH_PX);
        assert!(!loaded.persisted);
        let diagnostic = loaded
            .diagnostic
            .expect("invalid pair must explain fallback");
        assert_eq!(diagnostic.code, "workspace_layout_preference_invalid");
        assert!(!diagnostic
            .safe_message
            .contains(temp.path().to_str().unwrap()));
    }
}

#[cfg(unix)]
#[test]
fn unreadable_or_symlinked_preference_falls_back_without_following_it() {
    use std::os::unix::fs::symlink;

    let temp = tempdir().unwrap();
    let path = temp.path().join("layout.json");
    fs::create_dir(&path).unwrap();

    let unreadable = WorkspaceLayoutStore::new(path.clone()).load();
    assert_eq!(unreadable.preferred_left_width_px, DEFAULT_LEFT_WIDTH_PX);
    assert_eq!(unreadable.preferred_right_width_px, DEFAULT_RIGHT_WIDTH_PX);
    assert!(!unreadable.persisted);
    assert_eq!(
        unreadable.diagnostic.unwrap().code,
        "workspace_layout_preference_unreadable"
    );

    fs::remove_dir(&path).unwrap();
    let target = temp.path().join("foreign-layout.json");
    fs::write(
        &target,
        "{\"schema_version\":1,\"preferred_left_width_px\":420,\"preferred_right_width_px\":500}\n",
    )
    .unwrap();
    symlink(&target, &path).unwrap();

    let symlinked = WorkspaceLayoutStore::new(path).load();
    assert_eq!(symlinked.preferred_left_width_px, DEFAULT_LEFT_WIDTH_PX);
    assert_eq!(symlinked.preferred_right_width_px, DEFAULT_RIGHT_WIDTH_PX);
    assert!(!symlinked.persisted);
    assert_eq!(
        symlinked.diagnostic.unwrap().code,
        "workspace_layout_preference_unreadable"
    );
}

#[test]
fn missing_preference_uses_the_default_pair_without_claiming_a_read_failure() {
    let temp = tempdir().unwrap();
    let loaded = WorkspaceLayoutStore::new(temp.path().join("layout.json")).load();

    assert_eq!(loaded.preferred_left_width_px, DEFAULT_LEFT_WIDTH_PX);
    assert_eq!(loaded.preferred_right_width_px, DEFAULT_RIGHT_WIDTH_PX);
    assert!(loaded.persisted);
    assert_eq!(loaded.diagnostic, None);
}

#[cfg(unix)]
#[test]
fn save_creates_owner_only_directory_and_file() {
    use std::os::unix::fs::PermissionsExt;

    let temp = tempdir().unwrap();
    let parent = temp.path().join("state");
    let path = parent.join("layout.json");

    WorkspaceLayoutStore::new(path.clone())
        .save(420, 500)
        .unwrap();

    assert_eq!(
        fs::metadata(parent).unwrap().permissions().mode() & 0o777,
        0o700
    );
    assert_eq!(
        fs::metadata(path).unwrap().permissions().mode() & 0o777,
        0o600
    );
}

#[cfg(unix)]
#[test]
fn write_failure_preserves_the_last_valid_pair() {
    use std::os::unix::fs::PermissionsExt;

    let temp = tempdir().unwrap();
    let path = temp.path().join("layout.json");
    let store = WorkspaceLayoutStore::new(path);
    store.save(420, 500).unwrap();

    fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o500)).unwrap();
    let failed = store.save(430, 510);
    fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700)).unwrap();

    assert_eq!(
        failed.unwrap_err().code,
        "workspace_layout_preference_write_failed"
    );
    let loaded = store.load();
    assert_eq!(loaded.preferred_left_width_px, 420);
    assert_eq!(loaded.preferred_right_width_px, 500);
}

#[cfg(unix)]
#[test]
fn save_rejects_a_symlink_destination_without_mutating_its_target() {
    use std::os::unix::fs::symlink;

    let temp = tempdir().unwrap();
    let target = temp.path().join("foreign-layout.json");
    fs::write(
        &target,
        "{\"schema_version\":1,\"preferred_left_width_px\":420,\"preferred_right_width_px\":500}\n",
    )
    .unwrap();
    let path = temp.path().join("layout.json");
    symlink(&target, &path).unwrap();

    let failed = WorkspaceLayoutStore::new(path).save(430, 510);

    assert_eq!(
        failed.unwrap_err().code,
        "workspace_layout_preference_write_failed"
    );
    assert_eq!(
        fs::read_to_string(target).unwrap(),
        "{\"schema_version\":1,\"preferred_left_width_px\":420,\"preferred_right_width_px\":500}\n"
    );
}

#[test]
fn store_uses_sibling_temp_sync_and_atomic_rename() {
    let source = fs::read_to_string(
        std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/contexts/layout/store.rs"),
    )
    .unwrap();

    for required in [
        "create_new(true)",
        "file.sync_all()",
        "fs::rename(temporary, destination)",
        "File::open(parent)?.sync_all()",
    ] {
        assert!(
            source.contains(required),
            "layout publication must include atomic primitive: {required}"
        );
    }
}

#[test]
fn save_rejects_an_out_of_range_pair_without_replacing_the_valid_file() {
    let temp = tempdir().unwrap();
    let path = temp.path().join("layout.json");
    let store = WorkspaceLayoutStore::new(path);
    store.save(420, 500).unwrap();

    let failed = store.save(199, 500);

    assert_eq!(
        failed.unwrap_err().code,
        "workspace_layout_preference_invalid"
    );
    let loaded = store.load();
    assert_eq!(loaded.preferred_left_width_px, 420);
    assert_eq!(loaded.preferred_right_width_px, 500);
}
