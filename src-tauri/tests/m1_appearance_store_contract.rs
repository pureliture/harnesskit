use std::fs;

use harness_desktop_lib::contexts::appearance::{AppearanceStore, LogicalMode};
use tempfile::tempdir;

#[test]
fn missing_preference_falls_back_to_system_without_a_diagnostic() {
    let temp = tempdir().unwrap();
    let loaded = AppearanceStore::new(temp.path().join("appearance.json")).load();

    assert_eq!(loaded.logical_mode, LogicalMode::System);
    assert_eq!(loaded.diagnostic, None);
}

#[test]
fn invalid_or_unreadable_preference_falls_back_safely() {
    let temp = tempdir().unwrap();
    let path = temp.path().join("appearance.json");
    fs::write(&path, "not json").unwrap();

    let corrupt = AppearanceStore::new(path.clone()).load();
    assert_eq!(corrupt.logical_mode, LogicalMode::System);
    assert_eq!(
        corrupt.diagnostic.unwrap().code,
        "appearance_preference_invalid"
    );

    fs::write(&path, r#"{"schema_version":2,"logical_mode":"Dark"}"#).unwrap();
    let schema_mismatch = AppearanceStore::new(path.clone()).load();
    assert_eq!(schema_mismatch.logical_mode, LogicalMode::System);
    assert_eq!(
        schema_mismatch.diagnostic.unwrap().code,
        "appearance_preference_invalid"
    );

    fs::remove_file(&path).unwrap();
    fs::create_dir(&path).unwrap();
    let unreadable = AppearanceStore::new(path).load();
    assert_eq!(unreadable.logical_mode, LogicalMode::System);
    let diagnostic = unreadable.diagnostic.unwrap();
    assert_eq!(diagnostic.code, "appearance_preference_unreadable");
    assert!(!diagnostic
        .safe_message
        .contains(temp.path().to_str().unwrap()));
}

#[test]
fn preference_round_trips_through_owner_only_atomic_file() {
    let temp = tempdir().unwrap();
    let path = temp.path().join("state").join("appearance.json");
    let store = AppearanceStore::new(path.clone());

    store.save(LogicalMode::Dark).unwrap();
    assert_eq!(store.load().logical_mode, LogicalMode::Dark);
    assert_eq!(
        fs::read_to_string(&path).unwrap(),
        "{\"schema_version\":1,\"logical_mode\":\"Dark\"}\n"
    );

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }

    store.save(LogicalMode::Light).unwrap();
    assert_eq!(store.load().logical_mode, LogicalMode::Light);
}

#[test]
fn save_failure_is_safe_and_does_not_expose_the_path() {
    let temp = tempdir().unwrap();
    let parent_file = temp.path().join("not-a-directory");
    fs::write(&parent_file, "occupied").unwrap();
    let store = AppearanceStore::new(parent_file.join("appearance.json"));

    let error = store.save(LogicalMode::Dark).unwrap_err();
    assert_eq!(error.code, "appearance_preference_write_failed");
    assert_eq!(error.safe_message, "설정 저장 실패");
    assert!(!error.to_string().contains(temp.path().to_str().unwrap()));
}

#[cfg(unix)]
#[test]
fn write_failure_preserves_the_last_valid_preference() {
    use std::os::unix::fs::PermissionsExt;

    let temp = tempdir().unwrap();
    let path = temp.path().join("appearance.json");
    let store = AppearanceStore::new(path);
    store.save(LogicalMode::Dark).unwrap();

    fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o500)).unwrap();
    let failed = store.save(LogicalMode::Light);
    fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o700)).unwrap();

    assert!(failed.is_err());
    assert_eq!(store.load().logical_mode, LogicalMode::Dark);
}
