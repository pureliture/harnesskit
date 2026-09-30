use harness_desktop_lib::hash_engine::{HashComparisonError, HashEngine};
use harness_desktop_lib::models::InstallStatus;
use std::fs;
use tempfile::tempdir;

#[test]
fn missing_canonical_is_an_error_instead_of_missing_installation() {
    let fixture = tempdir().expect("create fixture");
    let canonical = fixture.path().join("missing-canonical");
    let destination = fixture.path().join("destination");
    fs::write(&destination, b"installed").expect("write destination");

    let error = HashEngine::compare_checked(&canonical, &destination)
        .expect_err("missing canonical output must block comparison");

    assert!(matches!(
        error,
        HashComparisonError::CanonicalMissing { .. }
    ));
}

#[test]
fn unreadable_canonical_is_a_typed_error() {
    let fixture = tempdir().expect("create fixture");
    let canonical_directory = fixture.path().join("canonical-directory");
    let destination = fixture.path().join("destination");
    fs::create_dir(&canonical_directory).expect("create canonical directory");
    fs::write(&destination, b"installed").expect("write destination");

    let error = HashEngine::compare_checked(&canonical_directory, &destination)
        .expect_err("a directory cannot be hashed as a canonical file");

    assert!(matches!(
        error,
        HashComparisonError::CanonicalUnreadable { .. }
    ));
}

#[test]
fn missing_destination_remains_a_missing_installation() {
    let fixture = tempdir().expect("create fixture");
    let canonical = fixture.path().join("canonical");
    let destination = fixture.path().join("missing-destination");
    fs::write(&canonical, b"canonical").expect("write canonical");

    let status = HashEngine::compare_checked(&canonical, &destination)
        .expect("a missing destination is a valid install state");

    assert_eq!(status, InstallStatus::Missing);
}

#[test]
fn unreadable_destination_is_a_typed_error() {
    let fixture = tempdir().expect("create fixture");
    let canonical = fixture.path().join("canonical");
    let destination_directory = fixture.path().join("destination-directory");
    fs::write(&canonical, b"canonical").expect("write canonical");
    fs::create_dir(&destination_directory).expect("create destination directory");

    let error = HashEngine::compare_checked(&canonical, &destination_directory)
        .expect_err("a directory cannot be hashed as an installed file");

    assert!(matches!(
        error,
        HashComparisonError::DestinationUnreadable { .. }
    ));
}

#[test]
fn identical_files_still_match() {
    let fixture = tempdir().expect("create fixture");
    let canonical = fixture.path().join("canonical");
    let destination = fixture.path().join("destination");
    fs::write(&canonical, b"same").expect("write canonical");
    fs::write(&destination, b"same").expect("write destination");

    let status =
        HashEngine::compare_checked(&canonical, &destination).expect("readable files must compare");

    assert_eq!(status, InstallStatus::Match);
}

#[test]
fn different_files_still_drift() {
    let fixture = tempdir().expect("create fixture");
    let canonical = fixture.path().join("canonical");
    let destination = fixture.path().join("destination");
    fs::write(&canonical, b"canonical").expect("write canonical");
    fs::write(&destination, b"installed").expect("write destination");

    let status =
        HashEngine::compare_checked(&canonical, &destination).expect("readable files must compare");

    assert_eq!(status, InstallStatus::Drift);
}
