use std::cell::RefCell;
use std::fs;
use std::os::unix::fs::{symlink, MetadataExt, PermissionsExt};
use std::path::Path;
use std::sync::{mpsc, Arc, Barrier, Once};
use std::thread;
use std::time::Duration;

use tempfile::tempdir;

use super::provider::normalize_provider_config;
use super::provider_store::{
    validate_regular_file_swap_for_test, FileProviderStore, ProviderConfigState,
    ProviderIoOperation, ProviderStoreError,
};

const PENDING_FILE: &str = ".active-pending.json";
const BACKUP_FILE: &str = ".active-backup.json";
const INACTIVE_CLEANUP_WARNING: &str = "AI provider inactive revision cleanup deferred";

thread_local! {
    static CAPTURED_WARNINGS: RefCell<Option<Vec<String>>> = const { RefCell::new(None) };
}

struct ThreadLocalWarningLogger;

impl log::Log for ThreadLocalWarningLogger {
    fn enabled(&self, metadata: &log::Metadata<'_>) -> bool {
        metadata.level() <= log::Level::Warn
    }

    fn log(&self, record: &log::Record<'_>) {
        if self.enabled(record.metadata())
            && record.target().contains("contexts::ai::provider_store")
        {
            CAPTURED_WARNINGS.with(|warnings| {
                if let Some(warnings) = warnings.borrow_mut().as_mut() {
                    warnings.push(record.args().to_string());
                }
            });
        }
    }

    fn flush(&self) {}
}

static WARNING_LOGGER: ThreadLocalWarningLogger = ThreadLocalWarningLogger;
static INSTALL_WARNING_LOGGER: Once = Once::new();

fn capture_provider_warnings<T>(action: impl FnOnce() -> T) -> (T, Vec<String>) {
    INSTALL_WARNING_LOGGER.call_once(|| {
        assert!(log::set_logger(&WARNING_LOGGER).is_ok());
        log::set_max_level(log::LevelFilter::Warn);
    });
    CAPTURED_WARNINGS.with(|warnings| {
        *warnings.borrow_mut() = Some(Vec::new());
    });
    let result = action();
    let warnings = CAPTURED_WARNINGS.with(|warnings| {
        warnings
            .borrow_mut()
            .take()
            .expect("warning capture was initialized")
    });
    (result, warnings)
}

fn error_code<T>(result: Result<T, ProviderStoreError>) -> &'static str {
    match result {
        Ok(_) => panic!("expected provider store to fail closed"),
        Err(error) => error.code(),
    }
}

fn app_data() -> tempfile::TempDir {
    let temp_root = std::env::temp_dir()
        .canonicalize()
        .expect("canonical temporary root");
    let directory = tempfile::Builder::new()
        .prefix("harness-ai-provider-")
        .tempdir_in(temp_root)
        .expect("temporary app data");
    fs::set_permissions(directory.path(), fs::Permissions::from_mode(0o700))
        .expect("owner-only app data");
    directory
}

fn configured_store() -> (tempfile::TempDir, FileProviderStore, String) {
    let app_data = app_data();
    let store = FileProviderStore::new(app_data.path().to_path_buf());
    let header = store
        .save(
            None,
            normalize_provider_config("https://provider.example/v1", "model-a").unwrap(),
            Some("secret-one"),
        )
        .expect("configured provider");
    let revision = header.provider_revision().to_string();
    (app_data, store, revision)
}

fn provider_root(app_data: &tempfile::TempDir) -> std::path::PathBuf {
    app_data.path().join("ai-provider")
}

fn inactive_revision_name(active: &str, digit: char) -> String {
    let candidate = format!("provider-{}", digit.to_string().repeat(64));
    if candidate == active {
        format!(
            "provider-{}",
            if digit == 'a' { "b" } else { "a" }.repeat(64)
        )
    } else {
        candidate
    }
}

fn create_owner_directory(path: &Path) {
    fs::create_dir(path).expect("create owner directory");
    fs::set_permissions(path, fs::Permissions::from_mode(0o700)).expect("set owner directory mode");
}

fn write_owner_file(path: &Path, bytes: &[u8]) {
    fs::write(path, bytes).expect("write owner file");
    fs::set_permissions(path, fs::Permissions::from_mode(0o600)).expect("set owner file mode");
}

fn assert_original_active(store: &FileProviderStore, revision: &str) {
    let active = store.active().expect("original active provider remains");
    assert_eq!(active.header().provider_revision(), revision);
    assert_eq!(active.api_key(), Some(b"secret-one".as_slice()));
}

fn assert_no_active_transaction_files(app_data: &tempfile::TempDir) {
    let transaction_files = fs::read_dir(provider_root(app_data))
        .unwrap()
        .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
        .filter(|name| name.starts_with(".active-"))
        .collect::<Vec<_>>();
    assert_eq!(transaction_files, Vec::<String>::new());
}

fn assert_only_expected_revision_remains(
    app_data: &tempfile::TempDir,
    expected_revision: Option<&str>,
) {
    assert_eq!(
        fs::read_dir(provider_root(app_data).join("staging"))
            .unwrap()
            .count(),
        0
    );
    let revisions = fs::read_dir(provider_root(app_data).join("revisions"))
        .unwrap()
        .map(|entry| entry.unwrap().file_name().to_string_lossy().into_owned())
        .collect::<Vec<_>>();
    assert_eq!(
        revisions,
        expected_revision
            .map(|revision| vec![revision.to_string()])
            .unwrap_or_default()
    );
}

#[test]
fn startup_inactive_cleanup_failure_preserves_active_state_and_retries_next_call() {
    let (app_data, store, active_revision) = configured_store();
    let inactive_revision = inactive_revision_name(&active_revision, '7');
    write_revision(
        &app_data,
        &inactive_revision,
        "inactive-model",
        Some(b"inactive-secret-must-not-log"),
    );
    let inactive_root = provider_root(&app_data)
        .join("revisions")
        .join(&inactive_revision);
    fs::set_permissions(&inactive_root, fs::Permissions::from_mode(0o000)).unwrap();

    let (state, state_warnings) = capture_provider_warnings(|| store.state());
    let state = state.expect("cleanup failure must not replace configured state");
    let ProviderConfigState::Configured(header) = state else {
        panic!("valid active provider must remain configured");
    };
    assert_eq!(header.provider_revision(), active_revision);
    assert!(header.api_key_present());
    assert_eq!(state_warnings, [INACTIVE_CLEANUP_WARNING]);
    assert!(inactive_root.exists());

    let (active, active_warnings) = capture_provider_warnings(|| store.active());
    let active = active.expect("cleanup failure must not replace active provider");
    assert_eq!(active.header().provider_revision(), active_revision);
    assert_eq!(active.api_key(), Some(b"secret-one".as_slice()));
    assert_eq!(active_warnings, [INACTIVE_CLEANUP_WARNING]);
    assert!(inactive_root.exists());
    for warning in state_warnings.iter().chain(&active_warnings) {
        assert!(!warning.contains(&inactive_root.to_string_lossy().to_string()));
        assert!(!warning.contains("secret-one"));
        assert!(!warning.contains("inactive-secret-must-not-log"));
    }

    fs::set_permissions(&inactive_root, fs::Permissions::from_mode(0o700)).unwrap();
    let (retried, retry_warnings) = capture_provider_warnings(|| store.state());
    assert!(matches!(
        retried,
        Ok(ProviderConfigState::Configured(ref header))
            if header.provider_revision() == active_revision
    ));
    assert!(retry_warnings.is_empty());
    assert!(!inactive_root.exists());
    assert_only_expected_revision_remains(&app_data, Some(&active_revision));
}

#[test]
fn startup_cleanup_deferral_never_masks_pending_recovery_failure() {
    let (app_data, store, previous_revision) = configured_store();
    let new_revision = inactive_revision_name(&previous_revision, '8');
    write_revision(&app_data, &new_revision, "model-b", Some(b"secret-two"));
    reconstruct_pending_publish(&app_data, Some(&previous_revision), &new_revision);
    store.fail_once_at(ProviderIoOperation::RecoveryRename);

    let (result, warnings) = capture_provider_warnings(|| store.state());

    assert_eq!(error_code(result), "provider_store_unavailable");
    assert!(warnings.is_empty());
    assert!(provider_root(&app_data).join(PENDING_FILE).exists());
    let restarted = FileProviderStore::new(app_data.path().to_path_buf());
    assert_original_active(&restarted, &previous_revision);
}

#[test]
fn startup_cleanup_deferral_never_masks_active_validation_failure() {
    let (app_data, store, revision) = configured_store();
    let active_path = provider_root(&app_data).join("active.json");
    let active_before = fs::read(&active_path).unwrap();
    write_owner_file(&active_path, b"{invalid-active-pointer");

    let (result, warnings) = capture_provider_warnings(|| store.state());

    assert_eq!(error_code(result), "provider_store_unsafe");
    assert!(warnings.is_empty());
    write_owner_file(&active_path, &active_before);
    assert_original_active(&store, &revision);
}

fn write_revision(
    app_data: &tempfile::TempDir,
    revision: &str,
    model: &str,
    secret: Option<&[u8]>,
) {
    let directory = provider_root(app_data).join("revisions").join(revision);
    create_owner_directory(&directory);
    write_owner_file(
        &directory.join("config.json"),
        &serde_json::to_vec(&serde_json::json!({
            "provider_revision": revision,
            "base_url": "https://replacement.example/v1",
            "model": model,
        }))
        .unwrap(),
    );
    if let Some(secret) = secret {
        write_owner_file(&directory.join("api-key"), secret);
    }
}

fn reconstruct_pending_publish(
    app_data: &tempfile::TempDir,
    previous_revision: Option<&str>,
    new_revision: &str,
) {
    let root = provider_root(app_data);
    let previous_pointer = previous_revision
        .map(|revision| {
            serde_json::to_vec(&serde_json::json!({ "provider_revision": revision })).unwrap()
        })
        .unwrap_or_default();
    write_owner_file(&root.join(BACKUP_FILE), &previous_pointer);
    write_owner_file(
        &root.join(PENDING_FILE),
        &serde_json::to_vec(&serde_json::json!({
            "previous_provider_revision": previous_revision,
            "new_provider_revision": new_revision,
        }))
        .unwrap(),
    );
    write_owner_file(
        &root.join("active.json"),
        &serde_json::to_vec(&serde_json::json!({ "provider_revision": new_revision })).unwrap(),
    );
}

#[test]
fn first_configure_blank_preserve_replace_and_delete_publish_new_revisions() {
    let app_data = app_data();
    let store = FileProviderStore::new(app_data.path().to_path_buf());
    assert_eq!(
        store.state().expect("initial state"),
        ProviderConfigState::Unconfigured
    );

    let first = store
        .save(
            None,
            normalize_provider_config("http://127.0.0.1:11434/v1", "model-a").unwrap(),
            Some("secret-one"),
        )
        .expect("first configure");
    assert!(first.api_key_present());
    let first_revision = first.provider_revision().to_string();
    assert_eq!(
        store.active().expect("active provider").api_key(),
        Some(b"secret-one".as_slice())
    );

    let preserved = store
        .save(
            Some(&first_revision),
            normalize_provider_config("https://provider.example/v1", "model-b").unwrap(),
            Some("   "),
        )
        .expect("blank preserves prior key");
    assert_ne!(preserved.provider_revision(), first_revision);
    assert_eq!(
        store.active().expect("preserved provider").api_key(),
        Some(b"secret-one".as_slice())
    );

    let replaced = store
        .save(
            Some(preserved.provider_revision()),
            normalize_provider_config("https://provider.example/v1", "model-b").unwrap(),
            Some("secret-two"),
        )
        .expect("nonblank replaces key");
    assert_eq!(
        store.active().expect("replaced provider").api_key(),
        Some(b"secret-two".as_slice())
    );

    let deleted = store
        .delete_key(replaced.provider_revision())
        .expect("explicit key deletion");
    assert!(!deleted.api_key_present());
    assert_eq!(store.active().expect("keyless provider").api_key(), None);
    assert_only_expected_revision_remains(&app_data, Some(deleted.provider_revision()));
    assert!(!provider_root(&app_data)
        .join("revisions")
        .join(deleted.provider_revision())
        .join("api-key")
        .exists());
}

#[test]
fn stale_compare_and_swap_never_changes_the_active_provider() {
    let app_data = app_data();
    let store = FileProviderStore::new(app_data.path().to_path_buf());
    let first = store
        .save(
            None,
            normalize_provider_config("https://provider.example/v1", "model-a").unwrap(),
            Some("secret"),
        )
        .unwrap();

    assert_eq!(
        error_code(store.save(
            Some("stale-revision"),
            normalize_provider_config("https://other.example/v1", "model-b").unwrap(),
            Some("replacement"),
        )),
        "provider_stale"
    );
    let active = store.active().expect("old active remains");
    assert_eq!(
        active.header().provider_revision(),
        first.provider_revision()
    );
    assert_eq!(active.api_key(), Some(b"secret".as_slice()));
}

#[test]
fn valid_superseded_revision_and_missing_expected_revision_are_stale_for_save_and_delete() {
    let app_data = app_data();
    let store = FileProviderStore::new(app_data.path().to_path_buf());
    let first = store
        .save(
            None,
            normalize_provider_config("https://provider.example/v1", "model-a").unwrap(),
            Some("secret-one"),
        )
        .unwrap();
    let second = store
        .save(
            Some(first.provider_revision()),
            normalize_provider_config("https://provider.example/v1", "model-b").unwrap(),
            Some("secret-two"),
        )
        .unwrap();

    assert_eq!(
        error_code(store.save(
            Some(first.provider_revision()),
            normalize_provider_config("https://other.example/v1", "model-c").unwrap(),
            Some("secret-three"),
        )),
        "provider_stale"
    );
    assert_eq!(
        error_code(store.save(
            None,
            normalize_provider_config("https://other.example/v1", "model-c").unwrap(),
            Some("secret-three"),
        )),
        "provider_stale"
    );
    assert_eq!(
        error_code(store.delete_key(first.provider_revision())),
        "provider_stale"
    );
    let active = store.active().expect("newest provider remains active");
    assert_eq!(
        active.header().provider_revision(),
        second.provider_revision()
    );
    assert_eq!(active.api_key(), Some(b"secret-two".as_slice()));
}

#[test]
fn separate_store_instances_serialize_compare_and_swap_across_the_shared_root() {
    let (app_data, initial_store, revision) = configured_store();
    drop(initial_store);
    let app_data_path = app_data.path().to_path_buf();
    let entered = Arc::new(Barrier::new(2));
    let release = Arc::new(Barrier::new(2));
    let first_store = FileProviderStore::new(app_data_path.clone());
    first_store.pause_after_revision_check(Arc::clone(&entered), Arc::clone(&release));
    let first_revision = revision.clone();
    let first = thread::spawn(move || {
        first_store.save(
            Some(&first_revision),
            normalize_provider_config("https://first.example/v1", "model-b").unwrap(),
            Some("secret-first"),
        )
    });

    entered.wait();
    let second_store = FileProviderStore::new(app_data_path.clone());
    let second_revision = revision.clone();
    let (result_tx, result_rx) = mpsc::channel();
    let second = thread::spawn(move || {
        let result = second_store.save(
            Some(&second_revision),
            normalize_provider_config("https://second.example/v1", "model-c").unwrap(),
            Some("secret-second"),
        );
        result_tx.send(result).unwrap();
    });

    let early = result_rx.recv_timeout(Duration::from_millis(500));
    release.wait();
    let first_header = first.join().unwrap().expect("first CAS commits");
    let (second_result, second_waited_for_lock) = match early {
        Ok(result) => (result, false),
        Err(mpsc::RecvTimeoutError::Timeout) => (
            result_rx
                .recv_timeout(Duration::from_secs(5))
                .expect("second CAS completes after lock release"),
            true,
        ),
        Err(mpsc::RecvTimeoutError::Disconnected) => panic!("second CAS worker disconnected"),
    };
    second.join().unwrap();

    assert!(
        second_waited_for_lock,
        "second store bypassed the root lock"
    );
    assert_eq!(error_code(second_result), "provider_stale");
    let restarted = FileProviderStore::new(app_data_path);
    let active = restarted.active().expect("first provider remains active");
    assert_eq!(
        active.header().provider_revision(),
        first_header.provider_revision()
    );
    assert_eq!(active.api_key(), Some(b"secret-first".as_slice()));
}

#[test]
fn every_precommit_publish_failure_preserves_the_previous_revision_and_secret() {
    for operation in [
        ProviderIoOperation::PrepareRootSync,
        ProviderIoOperation::ActiveRename,
        ProviderIoOperation::CommitMarkerUnlink,
        ProviderIoOperation::CommitRootSync,
    ] {
        let (app_data, store, revision) = configured_store();
        let pointer_before = fs::read(provider_root(&app_data).join("active.json")).unwrap();
        store.fail_once_at(operation);

        assert_eq!(
            error_code(store.save(
                Some(&revision),
                normalize_provider_config("https://replacement.example/v1", "model-b").unwrap(),
                Some("secret-two"),
            )),
            "provider_store_unavailable",
            "{operation:?} did not report the injected syscall failure"
        );

        assert_eq!(
            fs::read(provider_root(&app_data).join("active.json")).unwrap(),
            pointer_before,
            "{operation:?} changed the active pointer on Err"
        );
        let restarted = FileProviderStore::new(app_data.path().to_path_buf());
        assert_original_active(&restarted, &revision);
        assert_no_active_transaction_files(&app_data);
        assert_only_expected_revision_remains(&app_data, Some(&revision));
    }
}

#[test]
fn active_publish_failure_restores_unconfigured_first_run() {
    let app_data = app_data();
    let store = FileProviderStore::new(app_data.path().to_path_buf());
    store.fail_once_at(ProviderIoOperation::CommitRootSync);

    assert_eq!(
        error_code(store.save(
            None,
            normalize_provider_config("https://provider.example/v1", "model-a").unwrap(),
            Some("first-secret"),
        )),
        "provider_store_unavailable"
    );
    let restarted = FileProviderStore::new(app_data.path().to_path_buf());
    assert_eq!(
        restarted.state().unwrap(),
        ProviderConfigState::Unconfigured
    );
    assert!(!provider_root(&app_data).join("active.json").exists());
    assert_no_active_transaction_files(&app_data);
    assert_only_expected_revision_remains(&app_data, None);
}

#[test]
fn active_publish_failure_during_key_delete_retains_the_old_secret() {
    let (app_data, store, revision) = configured_store();
    store.fail_once_at(ProviderIoOperation::CommitRootSync);

    assert_eq!(
        error_code(store.delete_key(&revision)),
        "provider_store_unavailable"
    );
    let restarted = FileProviderStore::new(app_data.path().to_path_buf());
    assert_original_active(&restarted, &revision);
    assert_no_active_transaction_files(&app_data);
    assert_only_expected_revision_remains(&app_data, Some(&revision));
}

#[test]
fn marker_removal_and_root_sync_commit_the_new_revision() {
    let (app_data, store, revision) = configured_store();

    let saved = store
        .save(
            Some(&revision),
            normalize_provider_config("https://replacement.example/v1", "model-b").unwrap(),
            Some("secret-two"),
        )
        .expect("root sync is the commit point");

    assert_ne!(saved.provider_revision(), revision);
    let restarted = FileProviderStore::new(app_data.path().to_path_buf());
    let active = restarted
        .active()
        .expect("committed provider remains active");
    assert_eq!(
        active.header().provider_revision(),
        saved.provider_revision()
    );
    assert_eq!(active.api_key(), Some(b"secret-two".as_slice()));
    assert_no_active_transaction_files(&app_data);
    assert_only_expected_revision_remains(&app_data, Some(saved.provider_revision()));
}

#[test]
fn rollback_rename_failure_is_indeterminate_until_startup_recovers_old() {
    let (app_data, store, revision) = configured_store();
    store.fail_once_at(ProviderIoOperation::CommitRootSync);
    store.fail_once_at(ProviderIoOperation::RollbackRename);

    assert_eq!(
        error_code(store.save(
            Some(&revision),
            normalize_provider_config("https://replacement.example/v1", "model-b").unwrap(),
            Some("secret-two"),
        )),
        "provider_commit_indeterminate"
    );

    let restarted = FileProviderStore::new(app_data.path().to_path_buf());
    assert_original_active(&restarted, &revision);
    assert_no_active_transaction_files(&app_data);
    assert_only_expected_revision_remains(&app_data, Some(&revision));
}

#[test]
fn rollback_root_sync_failure_is_indeterminate_but_never_exposes_new_active() {
    let (app_data, store, revision) = configured_store();
    store.fail_once_at(ProviderIoOperation::CommitRootSync);
    store.fail_once_at(ProviderIoOperation::RollbackRootSync);

    assert_eq!(
        error_code(store.save(
            Some(&revision),
            normalize_provider_config("https://replacement.example/v1", "model-b").unwrap(),
            Some("secret-two"),
        )),
        "provider_commit_indeterminate"
    );

    let restarted = FileProviderStore::new(app_data.path().to_path_buf());
    assert_original_active(&restarted, &revision);
    assert_no_active_transaction_files(&app_data);
    assert_only_expected_revision_remains(&app_data, Some(&revision));
}

#[test]
fn rollback_intent_sync_failure_is_indeterminate_until_startup_recovers_old() {
    let (app_data, store, revision) = configured_store();
    store.fail_once_at(ProviderIoOperation::CommitRootSync);
    store.fail_once_at(ProviderIoOperation::RollbackIntentRootSync);

    assert_eq!(
        error_code(store.save(
            Some(&revision),
            normalize_provider_config("https://replacement.example/v1", "model-b").unwrap(),
            Some("secret-two"),
        )),
        "provider_commit_indeterminate"
    );

    let restarted = FileProviderStore::new(app_data.path().to_path_buf());
    assert_original_active(&restarted, &revision);
    assert_no_active_transaction_files(&app_data);
    assert_only_expected_revision_remains(&app_data, Some(&revision));
}

#[test]
fn rollback_unlink_failures_remain_indeterminate_and_restart_recovers() {
    let app_data = app_data();
    let first_store = FileProviderStore::new(app_data.path().to_path_buf());
    first_store.fail_once_at(ProviderIoOperation::CommitRootSync);
    first_store.fail_once_at(ProviderIoOperation::RollbackActiveUnlink);
    assert_eq!(
        error_code(first_store.save(
            None,
            normalize_provider_config("https://provider.example/v1", "model-a").unwrap(),
            Some("first-secret"),
        )),
        "provider_commit_indeterminate"
    );
    let restarted = FileProviderStore::new(app_data.path().to_path_buf());
    assert_eq!(
        restarted.state().unwrap(),
        ProviderConfigState::Unconfigured
    );

    let (app_data, store, revision) = configured_store();
    store.fail_once_at(ProviderIoOperation::CommitRootSync);
    store.fail_once_at(ProviderIoOperation::RollbackMarkerUnlink);
    assert_eq!(
        error_code(store.save(
            Some(&revision),
            normalize_provider_config("https://replacement.example/v1", "model-b").unwrap(),
            Some("secret-two"),
        )),
        "provider_commit_indeterminate"
    );
    let restarted = FileProviderStore::new(app_data.path().to_path_buf());
    assert_original_active(&restarted, &revision);
    assert_no_active_transaction_files(&app_data);
}

#[test]
fn restart_rolls_back_a_reconstructed_pending_update_before_loading_active() {
    let (app_data, _store, previous_revision) = configured_store();
    let new_revision = inactive_revision_name(&previous_revision, 'd');
    write_revision(&app_data, &new_revision, "model-b", Some(b"secret-two"));
    reconstruct_pending_publish(&app_data, Some(&previous_revision), &new_revision);

    let restarted = FileProviderStore::new(app_data.path().to_path_buf());
    assert_original_active(&restarted, &previous_revision);
    assert!(!provider_root(&app_data).join(PENDING_FILE).exists());
    assert!(!provider_root(&app_data).join(BACKUP_FILE).exists());
    assert_only_expected_revision_remains(&app_data, Some(&previous_revision));
}

#[test]
fn startup_recovery_failure_preserves_the_pending_evidence_for_the_next_restart() {
    let (app_data, _store, previous_revision) = configured_store();
    let new_revision = inactive_revision_name(&previous_revision, '9');
    write_revision(&app_data, &new_revision, "model-b", Some(b"secret-two"));
    reconstruct_pending_publish(&app_data, Some(&previous_revision), &new_revision);

    let failed_recovery = FileProviderStore::new(app_data.path().to_path_buf());
    failed_recovery.fail_once_at(ProviderIoOperation::RecoveryRename);
    assert_eq!(
        error_code(failed_recovery.state()),
        "provider_store_unavailable"
    );
    assert!(provider_root(&app_data).join(PENDING_FILE).exists());

    let restarted = FileProviderStore::new(app_data.path().to_path_buf());
    assert_original_active(&restarted, &previous_revision);
    assert_no_active_transaction_files(&app_data);
}

#[test]
fn restart_rolls_back_a_reconstructed_pending_first_configure_to_unconfigured() {
    let app_data = app_data();
    let store = FileProviderStore::new(app_data.path().to_path_buf());
    assert_eq!(store.state().unwrap(), ProviderConfigState::Unconfigured);
    let new_revision = format!("provider-{}", "e".repeat(64));
    write_revision(&app_data, &new_revision, "model-a", Some(b"first-secret"));
    reconstruct_pending_publish(&app_data, None, &new_revision);

    let restarted = FileProviderStore::new(app_data.path().to_path_buf());
    assert_eq!(
        restarted.state().unwrap(),
        ProviderConfigState::Unconfigured
    );
    assert!(!provider_root(&app_data).join("active.json").exists());
    assert!(!provider_root(&app_data).join(PENDING_FILE).exists());
    assert!(!provider_root(&app_data).join(BACKUP_FILE).exists());
    assert_only_expected_revision_remains(&app_data, None);
}

#[test]
fn restart_keeps_a_reconstructed_commit_after_pending_marker_removal() {
    let (app_data, _store, previous_revision) = configured_store();
    let new_revision = inactive_revision_name(&previous_revision, 'f');
    write_revision(&app_data, &new_revision, "model-b", Some(b"secret-two"));
    reconstruct_pending_publish(&app_data, Some(&previous_revision), &new_revision);
    fs::remove_file(provider_root(&app_data).join(PENDING_FILE)).unwrap();

    let restarted = FileProviderStore::new(app_data.path().to_path_buf());
    let active = restarted
        .active()
        .expect("committed provider remains active");
    assert_eq!(active.header().provider_revision(), new_revision);
    assert_eq!(active.api_key(), Some(b"secret-two".as_slice()));
    assert!(!provider_root(&app_data).join(BACKUP_FILE).exists());
    assert_only_expected_revision_remains(&app_data, Some(&new_revision));
}

#[test]
fn same_mode_same_owner_inode_swap_is_rejected_against_the_opened_file() {
    let directory = app_data();
    write_owner_file(&directory.path().join("original"), b"original");
    write_owner_file(&directory.path().join("replacement"), b"replacement");

    assert_eq!(
        error_code(validate_regular_file_swap_for_test(
            directory.path(),
            "original",
            &directory.path().join("replacement"),
        )),
        "provider_store_unsafe"
    );
}

#[test]
fn published_store_is_owner_only_and_retains_only_the_active_revision() {
    let app_data = app_data();
    let store = FileProviderStore::new(app_data.path().to_path_buf());
    let first = store
        .save(
            None,
            normalize_provider_config("https://provider.example/v1", "model-a").unwrap(),
            Some("secret"),
        )
        .unwrap();
    let second = store
        .save(
            Some(first.provider_revision()),
            normalize_provider_config("https://provider.example/v1", "model-b").unwrap(),
            None,
        )
        .unwrap();

    let root = app_data.path().join("ai-provider");
    for path in [root.clone(), root.join("revisions"), root.join("staging")] {
        let metadata = fs::symlink_metadata(path).unwrap();
        assert!(metadata.is_dir());
        assert_eq!(metadata.mode() & 0o777, 0o700);
        assert_eq!(metadata.uid(), rustix::process::geteuid().as_raw());
    }
    for path in [
        root.join(".provider-store.lock"),
        root.join("active.json"),
        root.join("revisions")
            .join(second.provider_revision())
            .join("config.json"),
        root.join("revisions")
            .join(second.provider_revision())
            .join("api-key"),
    ] {
        let metadata = fs::symlink_metadata(path).unwrap();
        assert!(metadata.is_file());
        assert_eq!(metadata.mode() & 0o777, 0o600);
        assert_eq!(metadata.uid(), rustix::process::geteuid().as_raw());
    }
    let revisions = fs::read_dir(root.join("revisions"))
        .unwrap()
        .map(|entry| entry.unwrap().file_name())
        .collect::<Vec<_>>();
    assert_eq!(revisions, [second.provider_revision()]);
    assert_eq!(fs::read_dir(root.join("staging")).unwrap().count(), 0);
}

#[test]
fn symlinked_provider_root_or_active_pointer_is_rejected() {
    let app_data = app_data();
    let outside = tempdir().unwrap();
    symlink(outside.path(), app_data.path().join("ai-provider")).unwrap();
    let store = FileProviderStore::new(app_data.path().to_path_buf());
    assert_eq!(error_code(store.state()), "provider_store_unsafe");

    fs::remove_file(app_data.path().join("ai-provider")).unwrap();
    let store = FileProviderStore::new(app_data.path().to_path_buf());
    assert_eq!(store.state().unwrap(), ProviderConfigState::Unconfigured);
    let root = app_data.path().join("ai-provider");
    symlink(outside.path().join("active.json"), root.join("active.json")).unwrap();
    assert_eq!(error_code(store.state()), "provider_store_unsafe");
}

#[test]
fn wrong_owner_only_modes_fail_closed_without_replacing_the_active_provider() {
    for target in [
        "root",
        "revisions",
        "staging",
        "lock",
        "revision",
        "active",
        "config",
        "api-key",
    ] {
        let (app_data, store, revision) = configured_store();
        let root = provider_root(&app_data);
        let revision_root = root.join("revisions").join(&revision);
        let (path, bad_mode, restored_mode) = match target {
            "root" => (root.clone(), 0o755, 0o700),
            "revisions" => (root.join("revisions"), 0o750, 0o700),
            "staging" => (root.join("staging"), 0o755, 0o700),
            "lock" => (root.join(".provider-store.lock"), 0o640, 0o600),
            "revision" => (revision_root.clone(), 0o750, 0o700),
            "active" => (root.join("active.json"), 0o640, 0o600),
            "config" => (revision_root.join("config.json"), 0o644, 0o600),
            "api-key" => (revision_root.join("api-key"), 0o640, 0o600),
            _ => unreachable!(),
        };
        let pointer_before = fs::read(root.join("active.json")).unwrap();

        fs::set_permissions(&path, fs::Permissions::from_mode(bad_mode)).unwrap();

        assert_eq!(
            error_code(store.state()),
            "provider_store_unsafe",
            "{target} accepted mode {bad_mode:o}"
        );
        assert_eq!(fs::read(root.join("active.json")).unwrap(), pointer_before);
        assert!(revision_root.exists());

        fs::set_permissions(&path, fs::Permissions::from_mode(restored_mode)).unwrap();
        assert_original_active(&store, &revision);
    }
}

#[test]
fn transaction_artifact_symlinks_and_wrong_modes_fail_closed() {
    for name in [
        PENDING_FILE,
        BACKUP_FILE,
        ".active-next.tmp",
        ".active-rollback.tmp",
    ] {
        let (app_data, store, revision) = configured_store();
        let artifact = provider_root(&app_data).join(name);
        let outside = tempdir().unwrap();
        let outside_file = outside.path().join("artifact");
        fs::write(&outside_file, b"outside").unwrap();
        symlink(&outside_file, &artifact).unwrap();
        assert_eq!(error_code(store.state()), "provider_store_unsafe", "{name}");
        fs::remove_file(&artifact).unwrap();
        assert_original_active(&store, &revision);

        write_owner_file(&artifact, b"invalid");
        fs::set_permissions(&artifact, fs::Permissions::from_mode(0o640)).unwrap();
        assert_eq!(error_code(store.state()), "provider_store_unsafe", "{name}");
        fs::remove_file(&artifact).unwrap();
        assert_original_active(&store, &revision);
    }
}

#[test]
fn revisions_and_staging_directory_symlinks_fail_closed() {
    for child in ["revisions", "staging"] {
        let app_data = app_data();
        let store = FileProviderStore::new(app_data.path().to_path_buf());
        assert_eq!(store.state().unwrap(), ProviderConfigState::Unconfigured);
        let root = provider_root(&app_data);
        let outside = tempdir().unwrap();
        fs::remove_dir(root.join(child)).unwrap();
        symlink(outside.path(), root.join(child)).unwrap();

        assert_eq!(
            error_code(store.state()),
            "provider_store_unsafe",
            "{child} symlink was accepted"
        );
    }
}

#[test]
fn revision_and_staging_entry_symlinks_defer_cleanup_and_preserve_the_active_provider() {
    for parent_name in ["revisions", "staging"] {
        let (app_data, store, revision) = configured_store();
        let root = provider_root(&app_data);
        let pointer_before = fs::read(root.join("active.json")).unwrap();
        let outside = tempdir().unwrap();
        let inactive = inactive_revision_name(&revision, 'a');
        let link = root.join(parent_name).join(&inactive);
        symlink(outside.path(), &link).unwrap();

        let (state, warnings) = capture_provider_warnings(|| store.state());

        assert!(matches!(
            state,
            Ok(ProviderConfigState::Configured(ref header))
                if header.provider_revision() == revision
        ));
        assert_eq!(warnings, [INACTIVE_CLEANUP_WARNING]);
        assert_eq!(fs::read(root.join("active.json")).unwrap(), pointer_before);
        assert!(link.is_symlink());

        fs::remove_file(link).unwrap();
        let (active, retry_warnings) = capture_provider_warnings(|| store.active());
        let active = active.expect("corrected inactive artifact retries cleanup");
        assert_eq!(active.header().provider_revision(), revision);
        assert_eq!(active.api_key(), Some(b"secret-one".as_slice()));
        assert!(retry_warnings.is_empty());
        assert_only_expected_revision_remains(&app_data, Some(&revision));
    }
}

#[test]
fn missing_active_target_does_not_fall_back_to_another_valid_revision() {
    let (app_data, store, revision) = configured_store();
    let root = provider_root(&app_data);
    let revisions = root.join("revisions");
    let pointer_before = fs::read(root.join("active.json")).unwrap();
    let decoy = inactive_revision_name(&revision, 'b');
    let decoy_root = revisions.join(&decoy);

    fs::rename(revisions.join(&revision), &decoy_root).unwrap();
    let config_path = decoy_root.join("config.json");
    let config = fs::read_to_string(&config_path)
        .unwrap()
        .replace(&revision, &decoy);
    fs::write(&config_path, config).unwrap();

    assert_eq!(error_code(store.state()), "provider_store_unsafe");
    assert_eq!(fs::read(root.join("active.json")).unwrap(), pointer_before);
    assert!(decoy_root.exists());
    assert!(!revisions.join(&revision).exists());
}

#[test]
fn corrupt_or_missing_active_files_fail_closed_without_rewriting_the_pointer() {
    for mutation in ["missing-config", "corrupt-config", "corrupt-active"] {
        let (app_data, store, revision) = configured_store();
        let root = provider_root(&app_data);
        let active_path = root.join("active.json");
        let config_path = root.join("revisions").join(&revision).join("config.json");
        let pointer_before = fs::read(&active_path).unwrap();
        let config_before = fs::read(&config_path).unwrap();

        match mutation {
            "missing-config" => fs::remove_file(&config_path).unwrap(),
            "corrupt-config" => fs::write(&config_path, b"{not-json").unwrap(),
            "corrupt-active" => fs::write(&active_path, b"{not-json").unwrap(),
            _ => unreachable!(),
        }

        assert_eq!(
            error_code(store.state()),
            "provider_store_unsafe",
            "{mutation} was accepted"
        );
        if mutation == "corrupt-active" {
            assert_eq!(fs::read(&active_path).unwrap(), b"{not-json");
        } else {
            assert_eq!(fs::read(&active_path).unwrap(), pointer_before);
        }

        write_owner_file(&active_path, &pointer_before);
        write_owner_file(&config_path, &config_before);
        assert_original_active(&store, &revision);
    }
}

#[test]
fn unexpected_inactive_entries_defer_cleanup_and_leave_the_old_active_usable() {
    for parent_name in ["revisions", "staging"] {
        let (app_data, store, revision) = configured_store();
        let root = provider_root(&app_data);
        let pointer_before = fs::read(root.join("active.json")).unwrap();
        let inactive = inactive_revision_name(&revision, 'c');
        let inactive_root = root.join(parent_name).join(inactive);
        create_owner_directory(&inactive_root);
        write_owner_file(&inactive_root.join("unexpected-entry"), b"unexpected");

        let (active, warnings) = capture_provider_warnings(|| store.active());

        let active = active.expect("inactive cleanup failure must preserve active provider");
        assert_eq!(active.header().provider_revision(), revision);
        assert_eq!(active.api_key(), Some(b"secret-one".as_slice()));
        assert_eq!(warnings, [INACTIVE_CLEANUP_WARNING]);
        assert_eq!(fs::read(root.join("active.json")).unwrap(), pointer_before);
        assert!(root.join("revisions").join(&revision).exists());
        assert!(inactive_root.exists());

        fs::remove_dir_all(inactive_root).unwrap();
        let (state, retry_warnings) = capture_provider_warnings(|| store.state());
        assert!(matches!(
            state,
            Ok(ProviderConfigState::Configured(ref header))
                if header.provider_revision() == revision
        ));
        assert!(retry_warnings.is_empty());
        assert_only_expected_revision_remains(&app_data, Some(&revision));
    }
}
