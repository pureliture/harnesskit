use std::ffi::CString;
use std::fs::File;
use std::io::{Read, Write};
use std::os::fd::{AsFd, BorrowedFd, OwnedFd};
use std::path::{Component, Path, PathBuf};
use std::sync::Mutex;

#[cfg(test)]
use std::sync::{Arc, Barrier};

use getrandom::getrandom;
use rustix::fs::{
    fchmod, flock, fstat, fsync, mkdirat, open, openat, renameat, statat, unlinkat, AtFlags, Dir,
    FileType, FlockOperation, Mode, OFlags, Stat,
};
use rustix::io::Errno;
use rustix::process::geteuid;
use serde::{Deserialize, Serialize};

use super::provider::{normalize_provider_config, NormalizedProviderConfig};

const PROVIDER_ROOT: &str = "ai-provider";
const REVISIONS: &str = "revisions";
const STAGING: &str = "staging";
const ACTIVE_FILE: &str = "active.json";
const ACTIVE_TEMP_FILE: &str = ".active-next.tmp";
const ACTIVE_BACKUP_FILE: &str = ".active-backup.json";
const ACTIVE_PENDING_FILE: &str = ".active-pending.json";
const ACTIVE_ROLLBACK_FILE: &str = ".active-rollback.tmp";
const STORE_LOCK_FILE: &str = ".provider-store.lock";
const CONFIG_FILE: &str = "config.json";
const API_KEY_FILE: &str = "api-key";
const MAX_CONFIG_BYTES: usize = 16 * 1024;
const MAX_API_KEY_BYTES: usize = 64 * 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ProviderStoreError {
    code: &'static str,
}

impl ProviderStoreError {
    pub(crate) const fn code(&self) -> &'static str {
        self.code
    }

    const fn new(code: &'static str) -> Self {
        Self { code }
    }

    const fn unsafe_store() -> Self {
        Self::new("provider_store_unsafe")
    }

    const fn unavailable() -> Self {
        Self::new("provider_store_unavailable")
    }

    const fn stale() -> Self {
        Self::new("provider_stale")
    }

    const fn invalid_config() -> Self {
        Self::new("provider_config_invalid")
    }

    const fn indeterminate() -> Self {
        Self::new("provider_commit_indeterminate")
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ProviderConfigHeader {
    base_url: String,
    model: String,
    api_key_present: bool,
    provider_revision: String,
}

impl ProviderConfigHeader {
    pub(crate) fn base_url(&self) -> &str {
        &self.base_url
    }

    pub(crate) fn model(&self) -> &str {
        &self.model
    }

    pub(crate) const fn api_key_present(&self) -> bool {
        self.api_key_present
    }

    pub(crate) fn provider_revision(&self) -> &str {
        &self.provider_revision
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum ProviderConfigState {
    Unconfigured,
    Configured(ProviderConfigHeader),
}

pub(crate) struct ActiveProvider {
    header: ProviderConfigHeader,
    api_key: Option<Vec<u8>>,
}

impl ActiveProvider {
    pub(crate) fn header(&self) -> &ProviderConfigHeader {
        &self.header
    }

    pub(crate) fn api_key(&self) -> Option<&[u8]> {
        self.api_key.as_deref()
    }
}

pub(crate) trait ProviderStorePort: Send + Sync + 'static {
    fn state(&self) -> Result<ProviderConfigState, ProviderStoreError>;
    fn active(&self) -> Result<ActiveProvider, ProviderStoreError>;
    fn save(
        &self,
        expected_provider_revision: Option<&str>,
        config: NormalizedProviderConfig,
        api_key: Option<&str>,
    ) -> Result<ProviderConfigHeader, ProviderStoreError>;
    fn delete_key(
        &self,
        expected_provider_revision: &str,
    ) -> Result<ProviderConfigHeader, ProviderStoreError>;
}

pub(crate) struct FileProviderStore {
    app_data_dir: PathBuf,
    gate: Mutex<()>,
    #[cfg(test)]
    fault_operations: Mutex<Vec<ProviderIoOperation>>,
    #[cfg(test)]
    revision_check_pause: Mutex<Option<(Arc<Barrier>, Arc<Barrier>)>>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum ProviderIoOperation {
    PrepareRootSync,
    ActiveRename,
    CommitMarkerUnlink,
    CommitRootSync,
    RollbackIntentRootSync,
    RollbackRename,
    RollbackActiveUnlink,
    RollbackMarkerUnlink,
    RollbackRootSync,
    RecoveryRename,
    RecoveryActiveUnlink,
    RecoveryMarkerUnlink,
    RecoveryRootSync,
}

impl FileProviderStore {
    pub(crate) fn new(app_data_dir: PathBuf) -> Self {
        Self {
            app_data_dir,
            gate: Mutex::new(()),
            #[cfg(test)]
            fault_operations: Mutex::new(Vec::new()),
            #[cfg(test)]
            revision_check_pause: Mutex::new(None),
        }
    }

    #[cfg(test)]
    pub(crate) fn fail_once_at(&self, operation: ProviderIoOperation) {
        self.fault_operations
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .push(operation);
    }

    #[cfg(test)]
    pub(crate) fn pause_after_revision_check(&self, entered: Arc<Barrier>, release: Arc<Barrier>) {
        *self
            .revision_check_pause
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = Some((entered, release));
    }

    #[cfg(test)]
    fn test_pause_after_revision_check(&self) {
        let pause = self
            .revision_check_pause
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .take();
        if let Some((entered, release)) = pause {
            entered.wait();
            release.wait();
        }
    }

    #[cfg(not(test))]
    fn test_pause_after_revision_check(&self) {}

    fn io_operation(&self, operation: ProviderIoOperation) -> Result<(), ProviderStoreError> {
        #[cfg(test)]
        {
            let mut armed = self
                .fault_operations
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            if let Some(index) = armed.iter().position(|candidate| *candidate == operation) {
                armed.remove(index);
                return Err(ProviderStoreError::unavailable());
            }
        }
        let _ = operation;
        Ok(())
    }

    #[cfg(test)]
    pub(crate) fn state(&self) -> Result<ProviderConfigState, ProviderStoreError> {
        ProviderStorePort::state(self)
    }

    #[cfg(test)]
    pub(crate) fn active(&self) -> Result<ActiveProvider, ProviderStoreError> {
        ProviderStorePort::active(self)
    }

    #[cfg(test)]
    pub(crate) fn save(
        &self,
        expected_provider_revision: Option<&str>,
        config: NormalizedProviderConfig,
        api_key: Option<&str>,
    ) -> Result<ProviderConfigHeader, ProviderStoreError> {
        ProviderStorePort::save(self, expected_provider_revision, config, api_key)
    }

    #[cfg(test)]
    pub(crate) fn delete_key(
        &self,
        expected_provider_revision: &str,
    ) -> Result<ProviderConfigHeader, ProviderStoreError> {
        ProviderStorePort::delete_key(self, expected_provider_revision)
    }
}

impl ProviderStorePort for FileProviderStore {
    fn state(&self) -> Result<ProviderConfigState, ProviderStoreError> {
        let _gate = self
            .gate
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let handles = StoreHandles::open(&self.app_data_dir)?;
        recover_before_load(&handles, |operation| self.io_operation(operation))?;
        let active = load_active(&handles)?;
        cleanup_inactive_or_defer(
            &handles,
            active
                .as_ref()
                .map(|value| value.header.provider_revision.as_str()),
        );
        Ok(
            active.map_or(ProviderConfigState::Unconfigured, |provider| {
                ProviderConfigState::Configured(provider.header)
            }),
        )
    }

    fn active(&self) -> Result<ActiveProvider, ProviderStoreError> {
        let _gate = self
            .gate
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let handles = StoreHandles::open(&self.app_data_dir)?;
        recover_before_load(&handles, |operation| self.io_operation(operation))?;
        let active = load_active(&handles)?.ok_or_else(ProviderStoreError::stale)?;
        cleanup_inactive_or_defer(&handles, Some(active.header.provider_revision.as_str()));
        Ok(active)
    }

    fn save(
        &self,
        expected_provider_revision: Option<&str>,
        config: NormalizedProviderConfig,
        api_key: Option<&str>,
    ) -> Result<ProviderConfigHeader, ProviderStoreError> {
        let _gate = self
            .gate
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let handles = StoreHandles::open(&self.app_data_dir)?;
        recover_before_load(&handles, |operation| self.io_operation(operation))?;
        let previous = load_active(&handles)?;
        require_expected_revision(previous.as_ref(), expected_provider_revision)?;
        self.test_pause_after_revision_check();
        let key = replacement_key(previous.as_ref(), api_key)?;
        publish(
            &handles,
            previous
                .as_ref()
                .map(|active| active.header.provider_revision.as_str()),
            config,
            key,
            |operation| self.io_operation(operation),
        )
    }

    fn delete_key(
        &self,
        expected_provider_revision: &str,
    ) -> Result<ProviderConfigHeader, ProviderStoreError> {
        let _gate = self
            .gate
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let handles = StoreHandles::open(&self.app_data_dir)?;
        recover_before_load(&handles, |operation| self.io_operation(operation))?;
        let previous = load_active(&handles)?.ok_or_else(ProviderStoreError::stale)?;
        require_expected_revision(Some(&previous), Some(expected_provider_revision))?;
        self.test_pause_after_revision_check();
        let config = normalize_provider_config(previous.header.base_url(), previous.header.model())
            .map_err(|_| ProviderStoreError::unsafe_store())?;
        publish(
            &handles,
            Some(previous.header.provider_revision.as_str()),
            config,
            None,
            |operation| self.io_operation(operation),
        )
    }
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct PersistedActive {
    provider_revision: String,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct PersistedPending {
    previous_provider_revision: Option<String>,
    new_provider_revision: String,
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct PersistedConfig {
    provider_revision: String,
    base_url: String,
    model: String,
}

struct StoreHandles {
    app: OwnedFd,
    root: OwnedFd,
    lock: OwnedFd,
    revisions: OwnedFd,
    staging: OwnedFd,
    app_stat: Stat,
    root_stat: Stat,
    lock_stat: Stat,
    revisions_stat: Stat,
    staging_stat: Stat,
}

impl StoreHandles {
    fn open(app_data_dir: &Path) -> Result<Self, ProviderStoreError> {
        let app = open_or_create_app_data(app_data_dir)?;
        let app_stat = validate_directory(&app, None, false)?;
        let root = open_or_create_child_directory(&app, PROVIDER_ROOT)?;
        let root_stat = validate_directory(&root, Some(0o700), true)?;
        // The lock fd remains live for this StoreHandles value, so every
        // recovery/read/CAS/publish sequence is one cross-process critical section.
        let (lock, lock_stat) = open_and_lock_store(&root)?;
        let revisions = open_or_create_child_directory(&root, REVISIONS)?;
        let staging = open_or_create_child_directory(&root, STAGING)?;
        let revisions_stat = validate_directory(&revisions, Some(0o700), true)?;
        let staging_stat = validate_directory(&staging, Some(0o700), true)?;
        let handles = Self {
            app,
            root,
            lock,
            revisions,
            staging,
            app_stat,
            root_stat,
            lock_stat,
            revisions_stat,
            staging_stat,
        };
        handles.validate()?;
        Ok(handles)
    }

    fn validate(&self) -> Result<(), ProviderStoreError> {
        validate_same_directory(&self.app, &self.app_stat, None, false)?;
        validate_same_directory(&self.root, &self.root_stat, Some(0o700), true)?;
        validate_regular_file_entry(&self.root, STORE_LOCK_FILE, &self.lock_stat)?;
        let lock_stat = fstat(&self.lock).map_err(|_| ProviderStoreError::unsafe_store())?;
        validate_regular_stat(&lock_stat)?;
        if !same_identity(&self.lock_stat, &lock_stat) {
            return Err(ProviderStoreError::unsafe_store());
        }
        validate_same_directory(&self.revisions, &self.revisions_stat, Some(0o700), true)?;
        validate_same_directory(&self.staging, &self.staging_stat, Some(0o700), true)?;
        validate_directory_entry(&self.app, PROVIDER_ROOT, &self.root_stat)?;
        validate_directory_entry(&self.root, REVISIONS, &self.revisions_stat)?;
        validate_directory_entry(&self.root, STAGING, &self.staging_stat)?;
        if self.root_stat.st_dev != self.revisions_stat.st_dev
            || self.root_stat.st_dev != self.staging_stat.st_dev
        {
            return Err(ProviderStoreError::unsafe_store());
        }
        Ok(())
    }
}

fn require_expected_revision(
    active: Option<&ActiveProvider>,
    expected: Option<&str>,
) -> Result<(), ProviderStoreError> {
    match (active, expected) {
        (None, None) => Ok(()),
        (Some(active), Some(expected))
            if active.header.provider_revision() == expected && revision_name_valid(expected) =>
        {
            Ok(())
        }
        _ => Err(ProviderStoreError::stale()),
    }
}

fn replacement_key(
    previous: Option<&ActiveProvider>,
    input: Option<&str>,
) -> Result<Option<Vec<u8>>, ProviderStoreError> {
    let trimmed = input.map(str::trim).unwrap_or_default();
    if trimmed.is_empty() {
        return Ok(previous
            .and_then(ActiveProvider::api_key)
            .map(ToOwned::to_owned));
    }
    if trimmed.len() > MAX_API_KEY_BYTES {
        return Err(ProviderStoreError::invalid_config());
    }
    Ok(Some(trimmed.as_bytes().to_vec()))
}

fn load_active(handles: &StoreHandles) -> Result<Option<ActiveProvider>, ProviderStoreError> {
    handles.validate()?;
    let Some(active_bytes) = read_optional_file(&handles.root, ACTIVE_FILE, MAX_CONFIG_BYTES)?
    else {
        return Ok(None);
    };
    let pointer: PersistedActive =
        serde_json::from_slice(&active_bytes).map_err(|_| ProviderStoreError::unsafe_store())?;
    if !revision_name_valid(&pointer.provider_revision) {
        return Err(ProviderStoreError::unsafe_store());
    }
    let revision = open_existing_directory(&handles.revisions, &pointer.provider_revision)?;
    let config_bytes = read_required_file(&revision, CONFIG_FILE, MAX_CONFIG_BYTES)?;
    let persisted: PersistedConfig =
        serde_json::from_slice(&config_bytes).map_err(|_| ProviderStoreError::unsafe_store())?;
    if persisted.provider_revision != pointer.provider_revision {
        return Err(ProviderStoreError::unsafe_store());
    }
    let normalized = normalize_provider_config(&persisted.base_url, &persisted.model)
        .map_err(|_| ProviderStoreError::unsafe_store())?;
    if normalized.base_url() != persisted.base_url || normalized.model() != persisted.model {
        return Err(ProviderStoreError::unsafe_store());
    }
    let api_key = read_optional_file(&revision, API_KEY_FILE, MAX_API_KEY_BYTES)?;
    if api_key.as_ref().is_some_and(|value| value.is_empty()) {
        return Err(ProviderStoreError::unsafe_store());
    }
    handles.validate()?;
    Ok(Some(ActiveProvider {
        header: ProviderConfigHeader {
            base_url: persisted.base_url,
            model: persisted.model,
            api_key_present: api_key.is_some(),
            provider_revision: persisted.provider_revision,
        },
        api_key,
    }))
}

fn publish(
    handles: &StoreHandles,
    previous_revision: Option<&str>,
    config: NormalizedProviderConfig,
    key: Option<Vec<u8>>,
    io: impl Fn(ProviderIoOperation) -> Result<(), ProviderStoreError>,
) -> Result<ProviderConfigHeader, ProviderStoreError> {
    handles.validate()?;
    let revision = opaque_revision()?;
    let stage = create_new_directory(&handles.staging, &revision)?;
    let persisted = PersistedConfig {
        provider_revision: revision.clone(),
        base_url: config.base_url().to_string(),
        model: config.model().to_string(),
    };
    let config_bytes =
        serde_json::to_vec(&persisted).map_err(|_| ProviderStoreError::unavailable())?;
    create_file(&stage, CONFIG_FILE, &config_bytes)?;
    if let Some(secret) = key.as_ref() {
        create_file(&stage, API_KEY_FILE, secret.as_slice())?;
    }
    fsync(&stage).map_err(|_| ProviderStoreError::unavailable())?;
    handles.validate()?;
    renameat(
        &handles.staging,
        revision.as_str(),
        &handles.revisions,
        revision.as_str(),
    )
    .map_err(|_| ProviderStoreError::unavailable())?;
    fsync(&handles.revisions).map_err(|_| ProviderStoreError::unavailable())?;
    let immutable = open_existing_directory(&handles.revisions, &revision)?;
    validate_directory_entry(
        &handles.revisions,
        &revision,
        &fstat(&immutable).map_err(|_| ProviderStoreError::unsafe_store())?,
    )?;

    let active_bytes = serde_json::to_vec(&PersistedActive {
        provider_revision: revision.clone(),
    })
    .map_err(|_| ProviderStoreError::unavailable())?;
    create_file(&handles.root, ACTIVE_TEMP_FILE, &active_bytes)?;
    let backup_bytes = previous_revision
        .map(|previous| {
            serde_json::to_vec(&PersistedActive {
                provider_revision: previous.to_string(),
            })
            .map_err(|_| ProviderStoreError::unavailable())
        })
        .transpose()?
        .unwrap_or_default();
    create_file(&handles.root, ACTIVE_BACKUP_FILE, &backup_bytes)?;
    let pending_bytes = serde_json::to_vec(&PersistedPending {
        previous_provider_revision: previous_revision.map(ToOwned::to_owned),
        new_provider_revision: revision.clone(),
    })
    .map_err(|_| ProviderStoreError::unavailable())?;
    create_file(&handles.root, ACTIVE_PENDING_FILE, &pending_bytes)?;
    if let Err(error) = io(ProviderIoOperation::PrepareRootSync)
        .and_then(|()| fsync(&handles.root).map_err(|_| ProviderStoreError::unavailable()))
    {
        if recover_pending_transaction(handles, RecoveryMode::Rollback, &io).is_err() {
            log::warn!("AI provider prepared transaction cleanup deferred");
        }
        return Err(error);
    }
    handles.validate()?;

    if let Err(error) = io(ProviderIoOperation::ActiveRename).and_then(|()| {
        renameat(&handles.root, ACTIVE_TEMP_FILE, &handles.root, ACTIVE_FILE).map_err(rename_error)
    }) {
        if recover_pending_transaction(handles, RecoveryMode::Rollback, &io).is_err() {
            log::warn!("AI provider inactive transaction cleanup deferred");
        }
        return Err(error);
    }

    let mut marker_removed = false;
    let commit_result = (|| {
        validate_active_pointer(&handles.root, ACTIVE_FILE, &revision)?;
        handles.validate()?;
        io(ProviderIoOperation::CommitMarkerUnlink)?;
        unlinkat(&handles.root, ACTIVE_PENDING_FILE, AtFlags::empty())
            .map_err(|_| ProviderStoreError::unavailable())?;
        marker_removed = true;
        // Pending marker removal plus this root sync is the only commit point.
        io(ProviderIoOperation::CommitRootSync)?;
        fsync(&handles.root).map_err(|_| ProviderStoreError::unavailable())
    })();
    if let Err(error) = commit_result {
        if marker_removed {
            create_file(&handles.root, ACTIVE_PENDING_FILE, &pending_bytes)
                .map_err(|_| ProviderStoreError::indeterminate())?;
            io(ProviderIoOperation::RollbackIntentRootSync)
                .and_then(|()| fsync(&handles.root).map_err(|_| ProviderStoreError::unavailable()))
                .map_err(|_| ProviderStoreError::indeterminate())?;
        }
        if recover_pending_transaction(handles, RecoveryMode::Rollback, &io).is_err() {
            return Err(ProviderStoreError::indeterminate());
        }
        return Err(error);
    }

    if cleanup_committed_transaction_artifacts(handles).is_err() {
        log::warn!("AI provider committed transaction cleanup deferred");
    }
    cleanup_inactive_or_defer(handles, Some(&revision));
    Ok(ProviderConfigHeader {
        base_url: persisted.base_url,
        model: persisted.model,
        api_key_present: key.is_some(),
        provider_revision: revision,
    })
}

#[derive(Clone, Copy)]
enum RecoveryMode {
    Rollback,
    Startup,
}

fn recover_before_load(
    handles: &StoreHandles,
    io: impl Fn(ProviderIoOperation) -> Result<(), ProviderStoreError>,
) -> Result<(), ProviderStoreError> {
    if read_optional_file(&handles.root, ACTIVE_PENDING_FILE, MAX_CONFIG_BYTES)?.is_some() {
        recover_pending_transaction(handles, RecoveryMode::Startup, &io)
    } else {
        cleanup_committed_transaction_artifacts(handles)
    }
}

fn recover_pending_transaction(
    handles: &StoreHandles,
    mode: RecoveryMode,
    io: &impl Fn(ProviderIoOperation) -> Result<(), ProviderStoreError>,
) -> Result<(), ProviderStoreError> {
    handles.validate()?;
    let pending_bytes = read_required_file(&handles.root, ACTIVE_PENDING_FILE, MAX_CONFIG_BYTES)?;
    let pending: PersistedPending =
        serde_json::from_slice(&pending_bytes).map_err(|_| ProviderStoreError::unsafe_store())?;
    if !revision_name_valid(&pending.new_provider_revision)
        || pending
            .previous_provider_revision
            .as_deref()
            .is_some_and(|previous| {
                !revision_name_valid(previous) || previous == pending.new_provider_revision
            })
    {
        return Err(ProviderStoreError::unsafe_store());
    }
    let backup = read_required_file(&handles.root, ACTIVE_BACKUP_FILE, MAX_CONFIG_BYTES)?;
    match pending.previous_provider_revision.as_deref() {
        Some(previous) => {
            let pointer: PersistedActive =
                serde_json::from_slice(&backup).map_err(|_| ProviderStoreError::unsafe_store())?;
            if pointer.provider_revision != previous {
                return Err(ProviderStoreError::unsafe_store());
            }
        }
        None if !backup.is_empty() => return Err(ProviderStoreError::unsafe_store()),
        None => {}
    }

    let active_revision =
        read_active_pointer(&handles.root, ACTIVE_FILE)?.map(|pointer| pointer.provider_revision);
    let (rename_operation, unlink_operation, marker_operation, sync_operation) = match mode {
        RecoveryMode::Rollback => (
            ProviderIoOperation::RollbackRename,
            ProviderIoOperation::RollbackActiveUnlink,
            ProviderIoOperation::RollbackMarkerUnlink,
            ProviderIoOperation::RollbackRootSync,
        ),
        RecoveryMode::Startup => (
            ProviderIoOperation::RecoveryRename,
            ProviderIoOperation::RecoveryActiveUnlink,
            ProviderIoOperation::RecoveryMarkerUnlink,
            ProviderIoOperation::RecoveryRootSync,
        ),
    };

    match pending.previous_provider_revision.as_deref() {
        Some(previous) => match active_revision.as_deref() {
            Some(active) if active == previous => {}
            Some(active) if active == pending.new_provider_revision => {
                prepare_rollback_file(handles, &backup)?;
                io(rename_operation)?;
                renameat(
                    &handles.root,
                    ACTIVE_ROLLBACK_FILE,
                    &handles.root,
                    ACTIVE_FILE,
                )
                .map_err(rename_error)?;
                validate_active_pointer(&handles.root, ACTIVE_FILE, previous)?;
            }
            None => {
                prepare_rollback_file(handles, &backup)?;
                io(rename_operation)?;
                renameat(
                    &handles.root,
                    ACTIVE_ROLLBACK_FILE,
                    &handles.root,
                    ACTIVE_FILE,
                )
                .map_err(rename_error)?;
                validate_active_pointer(&handles.root, ACTIVE_FILE, previous)?;
            }
            _ => return Err(ProviderStoreError::unsafe_store()),
        },
        None => match active_revision.as_deref() {
            Some(active) if active == pending.new_provider_revision => {
                io(unlink_operation)?;
                unlinkat(&handles.root, ACTIVE_FILE, AtFlags::empty())
                    .map_err(|_| ProviderStoreError::unavailable())?;
            }
            None => {}
            _ => return Err(ProviderStoreError::unsafe_store()),
        },
    }

    remove_optional_transaction_file(handles, ACTIVE_TEMP_FILE)?;
    remove_optional_transaction_file(handles, ACTIVE_ROLLBACK_FILE)?;
    io(marker_operation)?;
    unlinkat(&handles.root, ACTIVE_PENDING_FILE, AtFlags::empty())
        .map_err(|_| ProviderStoreError::unavailable())?;
    io(sync_operation)?;
    fsync(&handles.root).map_err(|_| ProviderStoreError::unavailable())?;
    cleanup_committed_transaction_artifacts(handles)?;
    handles.validate()?;
    Ok(())
}

fn prepare_rollback_file(handles: &StoreHandles, backup: &[u8]) -> Result<(), ProviderStoreError> {
    match read_optional_file(&handles.root, ACTIVE_ROLLBACK_FILE, MAX_CONFIG_BYTES)? {
        Some(existing) if existing == backup => Ok(()),
        Some(_) => Err(ProviderStoreError::unsafe_store()),
        None => create_file(&handles.root, ACTIVE_ROLLBACK_FILE, backup),
    }
}

fn cleanup_committed_transaction_artifacts(
    handles: &StoreHandles,
) -> Result<(), ProviderStoreError> {
    let mut changed = false;
    for name in [ACTIVE_BACKUP_FILE, ACTIVE_TEMP_FILE, ACTIVE_ROLLBACK_FILE] {
        changed |= remove_optional_transaction_file(handles, name)?;
    }
    if changed {
        fsync(&handles.root).map_err(|_| ProviderStoreError::unavailable())?;
    }
    Ok(())
}

fn remove_optional_transaction_file(
    handles: &StoreHandles,
    name: &str,
) -> Result<bool, ProviderStoreError> {
    if read_optional_file(&handles.root, name, MAX_CONFIG_BYTES)?.is_none() {
        return Ok(false);
    }
    unlinkat(&handles.root, name, AtFlags::empty())
        .map_err(|_| ProviderStoreError::unavailable())?;
    Ok(true)
}

fn validate_active_pointer(
    root: &OwnedFd,
    name: &str,
    expected_revision: &str,
) -> Result<(), ProviderStoreError> {
    let bytes = read_required_file(root, name, MAX_CONFIG_BYTES)?;
    let pointer: PersistedActive =
        serde_json::from_slice(&bytes).map_err(|_| ProviderStoreError::unsafe_store())?;
    if pointer.provider_revision != expected_revision || !revision_name_valid(expected_revision) {
        return Err(ProviderStoreError::unsafe_store());
    }
    Ok(())
}

fn read_active_pointer(
    root: &OwnedFd,
    name: &str,
) -> Result<Option<PersistedActive>, ProviderStoreError> {
    let Some(bytes) = read_optional_file(root, name, MAX_CONFIG_BYTES)? else {
        return Ok(None);
    };
    let pointer: PersistedActive =
        serde_json::from_slice(&bytes).map_err(|_| ProviderStoreError::unsafe_store())?;
    if !revision_name_valid(&pointer.provider_revision) {
        return Err(ProviderStoreError::unsafe_store());
    }
    Ok(Some(pointer))
}

fn rename_error(error: Errno) -> ProviderStoreError {
    if error == Errno::XDEV {
        ProviderStoreError::unsafe_store()
    } else {
        ProviderStoreError::unavailable()
    }
}

fn cleanup_inactive(
    handles: &StoreHandles,
    active_revision: Option<&str>,
) -> Result<(), ProviderStoreError> {
    handles.validate()?;
    for name in directory_names(handles.staging.as_fd())? {
        let name = name
            .to_str()
            .map_err(|_| ProviderStoreError::unsafe_store())?;
        if !revision_name_valid(name) {
            return Err(ProviderStoreError::unsafe_store());
        }
        remove_revision_directory(&handles.staging, name, true)?;
    }
    for name in directory_names(handles.revisions.as_fd())? {
        let name = name
            .to_str()
            .map_err(|_| ProviderStoreError::unsafe_store())?;
        if !revision_name_valid(name) {
            return Err(ProviderStoreError::unsafe_store());
        }
        if Some(name) != active_revision {
            remove_revision_directory(&handles.revisions, name, false)?;
        }
    }
    handles.validate()?;
    Ok(())
}

fn cleanup_inactive_or_defer(handles: &StoreHandles, active_revision: Option<&str>) {
    if cleanup_inactive(handles, active_revision).is_err() {
        log::warn!("AI provider inactive revision cleanup deferred");
    }
}

fn remove_revision_directory(
    parent: &OwnedFd,
    name: &str,
    partial: bool,
) -> Result<(), ProviderStoreError> {
    let directory = open_existing_directory(parent, name)?;
    let names = directory_names(directory.as_fd())?;
    for child in names {
        let child = child
            .to_str()
            .map_err(|_| ProviderStoreError::unsafe_store())?;
        if !matches!(child, CONFIG_FILE | API_KEY_FILE) {
            return Err(ProviderStoreError::unsafe_store());
        }
        validate_regular_file_path(&directory, child)?;
        unlinkat(&directory, child, AtFlags::empty())
            .map_err(|_| ProviderStoreError::unavailable())?;
    }
    if !partial && read_optional_file(&directory, CONFIG_FILE, MAX_CONFIG_BYTES)?.is_some() {
        return Err(ProviderStoreError::unsafe_store());
    }
    fsync(&directory).map_err(|_| ProviderStoreError::unavailable())?;
    unlinkat(parent, name, AtFlags::REMOVEDIR).map_err(|_| ProviderStoreError::unavailable())?;
    fsync(parent).map_err(|_| ProviderStoreError::unavailable())?;
    Ok(())
}

fn opaque_revision() -> Result<String, ProviderStoreError> {
    let mut random = [0_u8; 32];
    getrandom(&mut random).map_err(|_| ProviderStoreError::unavailable())?;
    let mut revision = String::with_capacity(9 + random.len() * 2);
    revision.push_str("provider-");
    for byte in random {
        use std::fmt::Write as _;
        write!(&mut revision, "{byte:02x}").map_err(|_| ProviderStoreError::unavailable())?;
    }
    Ok(revision)
}

fn revision_name_valid(value: &str) -> bool {
    value.len() == 73
        && value.starts_with("provider-")
        && value.as_bytes()[9..]
            .iter()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
}

fn open_or_create_app_data(path: &Path) -> Result<OwnedFd, ProviderStoreError> {
    if let Ok(directory) = open_directory_lineage(path) {
        validate_directory(&directory, None, false)?;
        return Ok(directory);
    }
    let parent = path.parent().ok_or_else(ProviderStoreError::unsafe_store)?;
    let name = path
        .file_name()
        .ok_or_else(ProviderStoreError::unsafe_store)?;
    let parent = open_directory_lineage(parent)?;
    match mkdirat(&parent, name, Mode::from(0o700)) {
        Ok(()) => {}
        Err(Errno::EXIST) => return Err(ProviderStoreError::unsafe_store()),
        Err(_) => return Err(ProviderStoreError::unavailable()),
    }
    let directory = openat(&parent, name, directory_flags(), Mode::empty())
        .map_err(|_| ProviderStoreError::unsafe_store())?;
    fchmod(&directory, Mode::from(0o700)).map_err(|_| ProviderStoreError::unavailable())?;
    validate_directory(&directory, Some(0o700), true)?;
    Ok(directory)
}

fn open_directory_lineage(path: &Path) -> Result<OwnedFd, ProviderStoreError> {
    let mut current = open(
        if path.is_absolute() {
            Path::new("/")
        } else {
            Path::new(".")
        },
        directory_flags(),
        Mode::empty(),
    )
    .map_err(|_| ProviderStoreError::unsafe_store())?;
    for component in path.components() {
        match component {
            Component::RootDir | Component::CurDir => continue,
            Component::Normal(name) => {
                current = openat(&current, name, directory_flags(), Mode::empty())
                    .map_err(|_| ProviderStoreError::unsafe_store())?;
            }
            Component::ParentDir | Component::Prefix(_) => {
                return Err(ProviderStoreError::unsafe_store());
            }
        }
    }
    Ok(current)
}

fn open_or_create_child_directory(
    parent: &OwnedFd,
    name: &str,
) -> Result<OwnedFd, ProviderStoreError> {
    match openat(parent, name, directory_flags(), Mode::empty()) {
        Ok(directory) => {
            validate_directory(&directory, Some(0o700), true)?;
            Ok(directory)
        }
        Err(Errno::NOENT) => {
            mkdirat(parent, name, Mode::from(0o700))
                .map_err(|_| ProviderStoreError::unavailable())?;
            let directory = openat(parent, name, directory_flags(), Mode::empty())
                .map_err(|_| ProviderStoreError::unsafe_store())?;
            fchmod(&directory, Mode::from(0o700)).map_err(|_| ProviderStoreError::unavailable())?;
            validate_directory(&directory, Some(0o700), true)?;
            fsync(parent).map_err(|_| ProviderStoreError::unavailable())?;
            Ok(directory)
        }
        Err(_) => Err(ProviderStoreError::unsafe_store()),
    }
}

fn create_new_directory(parent: &OwnedFd, name: &str) -> Result<OwnedFd, ProviderStoreError> {
    if !revision_name_valid(name) {
        return Err(ProviderStoreError::unsafe_store());
    }
    mkdirat(parent, name, Mode::from(0o700)).map_err(|error| {
        if error == Errno::EXIST {
            ProviderStoreError::unsafe_store()
        } else {
            ProviderStoreError::unavailable()
        }
    })?;
    let directory = openat(parent, name, directory_flags(), Mode::empty())
        .map_err(|_| ProviderStoreError::unsafe_store())?;
    fchmod(&directory, Mode::from(0o700)).map_err(|_| ProviderStoreError::unavailable())?;
    validate_directory(&directory, Some(0o700), true)?;
    fsync(parent).map_err(|_| ProviderStoreError::unavailable())?;
    Ok(directory)
}

fn open_existing_directory(parent: &OwnedFd, name: &str) -> Result<OwnedFd, ProviderStoreError> {
    if !revision_name_valid(name) {
        return Err(ProviderStoreError::unsafe_store());
    }
    let directory = openat(parent, name, directory_flags(), Mode::empty())
        .map_err(|_| ProviderStoreError::unsafe_store())?;
    validate_directory(&directory, Some(0o700), true)?;
    let stat = fstat(&directory).map_err(|_| ProviderStoreError::unsafe_store())?;
    validate_directory_entry(parent, name, &stat)?;
    Ok(directory)
}

fn open_and_lock_store(root: &OwnedFd) -> Result<(OwnedFd, Stat), ProviderStoreError> {
    let (lock, created) = match openat(
        root,
        STORE_LOCK_FILE,
        OFlags::RDWR | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::from(0o600),
    ) {
        Ok(lock) => (lock, true),
        Err(Errno::EXIST) => (
            openat(
                root,
                STORE_LOCK_FILE,
                OFlags::RDWR | OFlags::NOFOLLOW | OFlags::CLOEXEC,
                Mode::empty(),
            )
            .map_err(|_| ProviderStoreError::unsafe_store())?,
            false,
        ),
        Err(_) => return Err(ProviderStoreError::unavailable()),
    };
    if created {
        fchmod(&lock, Mode::from(0o600)).map_err(|_| ProviderStoreError::unavailable())?;
    }
    let before = fstat(&lock).map_err(|_| ProviderStoreError::unsafe_store())?;
    validate_regular_stat(&before)?;
    if before.st_size != 0 {
        return Err(ProviderStoreError::unsafe_store());
    }
    validate_regular_file_entry(root, STORE_LOCK_FILE, &before)?;
    flock(&lock, FlockOperation::LockExclusive).map_err(|_| ProviderStoreError::unavailable())?;
    let locked = fstat(&lock).map_err(|_| ProviderStoreError::unsafe_store())?;
    validate_regular_stat(&locked)?;
    if locked.st_size != 0 || !same_identity(&before, &locked) {
        return Err(ProviderStoreError::unsafe_store());
    }
    validate_regular_file_entry(root, STORE_LOCK_FILE, &locked)?;
    if created {
        fsync(&lock).map_err(|_| ProviderStoreError::unavailable())?;
        fsync(root).map_err(|_| ProviderStoreError::unavailable())?;
    }
    Ok((lock, locked))
}

fn create_file(parent: &OwnedFd, name: &str, bytes: &[u8]) -> Result<(), ProviderStoreError> {
    let fd = openat(
        parent,
        name,
        OFlags::WRONLY | OFlags::CREATE | OFlags::EXCL | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::from(0o600),
    )
    .map_err(|_| ProviderStoreError::unavailable())?;
    fchmod(&fd, Mode::from(0o600)).map_err(|_| ProviderStoreError::unavailable())?;
    let mut file = File::from(fd);
    file.write_all(bytes)
        .map_err(|_| ProviderStoreError::unavailable())?;
    file.sync_all()
        .map_err(|_| ProviderStoreError::unavailable())?;
    let stat = fstat(&file).map_err(|_| ProviderStoreError::unsafe_store())?;
    validate_regular_stat(&stat)?;
    if usize::try_from(stat.st_size).ok() != Some(bytes.len()) {
        return Err(ProviderStoreError::unsafe_store());
    }
    validate_regular_file_entry(parent, name, &stat)?;
    Ok(())
}

fn read_required_file(
    parent: &OwnedFd,
    name: &str,
    limit: usize,
) -> Result<Vec<u8>, ProviderStoreError> {
    read_optional_file(parent, name, limit)?.ok_or_else(ProviderStoreError::unsafe_store)
}

fn read_optional_file(
    parent: &OwnedFd,
    name: &str,
    limit: usize,
) -> Result<Option<Vec<u8>>, ProviderStoreError> {
    let fd = match openat(
        parent,
        name,
        OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::empty(),
    ) {
        Ok(fd) => fd,
        Err(Errno::NOENT) => return Ok(None),
        Err(_) => return Err(ProviderStoreError::unsafe_store()),
    };
    let before = fstat(&fd).map_err(|_| ProviderStoreError::unsafe_store())?;
    validate_regular_stat(&before)?;
    if before.st_size < 0 || usize::try_from(before.st_size).map_or(true, |size| size > limit) {
        return Err(ProviderStoreError::unsafe_store());
    }
    let mut file = File::from(fd);
    let mut bytes = Vec::with_capacity(usize::try_from(before.st_size).unwrap_or(0));
    (&mut file)
        .take((limit + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| ProviderStoreError::unsafe_store())?;
    let after = fstat(&file).map_err(|_| ProviderStoreError::unsafe_store())?;
    if !same_identity(&before, &after)
        || before.st_size != after.st_size
        || bytes.len() > limit
        || usize::try_from(after.st_size).ok() != Some(bytes.len())
    {
        return Err(ProviderStoreError::unsafe_store());
    }
    validate_regular_file_entry(parent, name, &after)?;
    Ok(Some(bytes))
}

fn validate_regular_file_entry(
    parent: &OwnedFd,
    name: &str,
    expected: &Stat,
) -> Result<(), ProviderStoreError> {
    let entry = statat(parent, name, AtFlags::SYMLINK_NOFOLLOW)
        .map_err(|_| ProviderStoreError::unsafe_store())?;
    validate_regular_stat(&entry)?;
    if !same_identity(expected, &entry) {
        return Err(ProviderStoreError::unsafe_store());
    }
    Ok(())
}

fn validate_regular_file_path(parent: &OwnedFd, name: &str) -> Result<(), ProviderStoreError> {
    let entry = statat(parent, name, AtFlags::SYMLINK_NOFOLLOW)
        .map_err(|_| ProviderStoreError::unsafe_store())?;
    validate_regular_stat(&entry)
}

#[cfg(test)]
pub(crate) fn validate_regular_file_swap_for_test(
    parent_path: &Path,
    name: &str,
    replacement: &Path,
) -> Result<(), ProviderStoreError> {
    let parent = open_directory_lineage(parent_path)?;
    let opened = openat(
        &parent,
        name,
        OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::CLOEXEC,
        Mode::empty(),
    )
    .map_err(|_| ProviderStoreError::unsafe_store())?;
    let expected = fstat(&opened).map_err(|_| ProviderStoreError::unsafe_store())?;
    std::fs::rename(replacement, parent_path.join(name))
        .map_err(|_| ProviderStoreError::unavailable())?;
    validate_regular_file_entry(&parent, name, &expected)
}

fn validate_regular_stat(stat: &Stat) -> Result<(), ProviderStoreError> {
    if FileType::from_raw_mode(stat.st_mode) != FileType::RegularFile
        || stat.st_uid != geteuid().as_raw()
        || stat.st_mode as u32 & 0o777 != 0o600
    {
        return Err(ProviderStoreError::unsafe_store());
    }
    Ok(())
}

fn validate_directory(
    directory: &OwnedFd,
    exact_mode: Option<u32>,
    owner_only: bool,
) -> Result<Stat, ProviderStoreError> {
    let stat = fstat(directory).map_err(|_| ProviderStoreError::unsafe_store())?;
    validate_directory_stat(&stat, exact_mode, owner_only)?;
    Ok(stat)
}

fn validate_same_directory(
    directory: &OwnedFd,
    expected: &Stat,
    exact_mode: Option<u32>,
    owner_only: bool,
) -> Result<(), ProviderStoreError> {
    let current = validate_directory(directory, exact_mode, owner_only)?;
    if !same_identity(expected, &current) {
        return Err(ProviderStoreError::unsafe_store());
    }
    Ok(())
}

fn validate_directory_stat(
    stat: &Stat,
    exact_mode: Option<u32>,
    owner_only: bool,
) -> Result<(), ProviderStoreError> {
    let mode = stat.st_mode as u32 & 0o777;
    if FileType::from_raw_mode(stat.st_mode) != FileType::Directory
        || stat.st_uid != geteuid().as_raw()
        || exact_mode.is_some_and(|expected| mode != expected)
        || (!owner_only && mode & 0o022 != 0)
        || (owner_only && mode != 0o700)
    {
        return Err(ProviderStoreError::unsafe_store());
    }
    Ok(())
}

fn validate_directory_entry(
    parent: &OwnedFd,
    name: &str,
    expected: &Stat,
) -> Result<(), ProviderStoreError> {
    let entry = statat(parent, name, AtFlags::SYMLINK_NOFOLLOW)
        .map_err(|_| ProviderStoreError::unsafe_store())?;
    validate_directory_stat(&entry, Some(0o700), true)?;
    if !same_identity(expected, &entry) {
        return Err(ProviderStoreError::unsafe_store());
    }
    Ok(())
}

fn same_identity(left: &Stat, right: &Stat) -> bool {
    left.st_dev == right.st_dev
        && left.st_ino == right.st_ino
        && FileType::from_raw_mode(left.st_mode) == FileType::from_raw_mode(right.st_mode)
}

fn directory_names(directory: BorrowedFd<'_>) -> Result<Vec<CString>, ProviderStoreError> {
    let mut stream = Dir::read_from(directory).map_err(|_| ProviderStoreError::unsafe_store())?;
    let mut names = Vec::new();
    while let Some(entry) = stream.read() {
        let entry = entry.map_err(|_| ProviderStoreError::unsafe_store())?;
        let name = entry.file_name();
        if name.to_bytes() == b"." || name.to_bytes() == b".." {
            continue;
        }
        if name.to_bytes().is_empty() || name.to_bytes().contains(&b'/') {
            return Err(ProviderStoreError::unsafe_store());
        }
        names.push(name.to_owned());
    }
    names.sort_unstable_by(|left, right| left.as_bytes().cmp(right.as_bytes()));
    Ok(names)
}

fn directory_flags() -> OFlags {
    OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC
}
