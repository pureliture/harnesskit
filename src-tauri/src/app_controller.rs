use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::sync::Mutex;

use crate::contexts::ai::{
    AiExplanation, AiExplanationContext, AiExplanationError, AiExplanationInput,
    ProviderConfigHeader, ProviderConfigState, MAX_AI_SOURCE_BYTES,
};
use crate::contexts::appearance::{
    AppearanceContext, AppearanceContextError, AppearanceDiagnostic, AppearanceState,
    AppearanceUpdate, BootstrapInstruction, BootstrapRecovery, LogicalMode, ResolvedMode,
};
use crate::contexts::correlation::{CorrelationContext, CorrelationProjection, CorrelationState};
use crate::contexts::install::{
    InstallApplyOutcome, InstallApprovals, InstallCoordinator, InstallPreview,
};
use crate::contexts::layout::{
    workspace_layout_probe_matches, WorkspaceLayoutContext, WorkspaceLayoutContextError,
    WorkspaceLayoutDiagnostic, WorkspaceLayoutGeometryProbe, WorkspaceLayoutState,
    WorkspaceLayoutUpdate,
};
#[cfg(test)]
use crate::contexts::layout::{
    WorkspaceLayoutFrame, WorkspaceLayoutMode, WorkspaceLayoutPaneFrames,
};
use crate::contexts::local::{
    get_local_instance_detail, query_local_instances, LocalContext, LocalContextError,
    LocalInstanceDetail, LocalPathAction, LocalPathActionError, LocalPathActionOutcome,
    LocalQueryRequest, LocalQueryResult, LocalRemovalApplyOutcome, LocalRemovalPlanPreview,
    LocalRemovalReconciliation, LocalSessionState, OpenSourcePreviewJob, OpenSourceRequest,
    ProjectLocationRecord, ReadSourceChunkRequest, ReadSourcePreviewJob, SourceInspectionError,
    SourcePreviewClose, StartLocalScanOutcome,
};
use crate::contexts::sot::checkout_picker::{CheckoutDirectoryPickerPort, PickerOutcome};
use crate::contexts::sot::{SotContext, SotSessionHeaders, SotSnapshot};
use crate::contexts::typography::{
    TypographyContext, TypographyContextError, TypographyDiagnostic, TypographyPreset,
    TypographyState, TypographyUpdate,
};
use crate::controller::{AppController as CheckoutController, RegisterCheckoutResponse};
use crate::operation_coordinator::OperationCoordinator;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct AppBootstrapState {
    pub appearance: AppearanceState,
    pub workspace_layout: WorkspaceLayoutState,
    pub typography: TypographyState,
    pub appearance_diagnostic: Option<AppearanceDiagnostic>,
    pub workspace_layout_diagnostic: Option<WorkspaceLayoutDiagnostic>,
    pub typography_diagnostic: Option<TypographyDiagnostic>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct AppBootstrapCompletion {
    pub instruction: BootstrapInstruction,
    pub appearance: AppearanceState,
    pub workspace_layout: WorkspaceLayoutState,
    pub typography: TypographyState,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub(crate) struct WorkspaceLayoutBootstrapProbe {
    pub applied_typography_revision: u64,
    pub typography_preset: TypographyPreset,
    pub geometry: WorkspaceLayoutGeometryProbe,
}

fn complete_bootstrap_after_layout_validation(
    appearance: &AppearanceContext,
    workspace_layout: WorkspaceLayoutState,
    appearance_revision: u64,
    layout_revision: u64,
    resolved_mode: ResolvedMode,
    probe: WorkspaceLayoutGeometryProbe,
) -> Result<AppBootstrapCompletion, AppearanceContextError> {
    if layout_revision != workspace_layout.revision
        || !workspace_layout_probe_matches(workspace_layout, &probe)
    {
        return Ok(AppBootstrapCompletion {
            instruction: BootstrapInstruction::Refresh,
            appearance: appearance.bootstrap_state(),
            workspace_layout,
            typography: TypographyState {
                preset: TypographyPreset::Default,
                revision: 0,
                persisted: true,
            },
        });
    }
    let appearance_completion =
        appearance.complete_bootstrap(appearance_revision, resolved_mode)?;
    Ok(AppBootstrapCompletion {
        instruction: appearance_completion.instruction,
        appearance: appearance_completion.state,
        workspace_layout,
        typography: TypographyState {
            preset: TypographyPreset::Default,
            revision: 0,
            persisted: true,
        },
    })
}

pub(crate) struct AppController {
    appearance: AppearanceContext,
    workspace_layout: WorkspaceLayoutContext,
    typography: TypographyContext,
    sot: SotContext,
    local: LocalContext,
    ai: AiExplanationContext,
    checkout_picker: Arc<dyn CheckoutDirectoryPickerPort>,
    correlation: CorrelationContext,
    correlation_revision_gate: Mutex<()>,
    workspace_layout_gate: Mutex<()>,
    operations: Arc<OperationCoordinator>,
    next_install_operation: AtomicU64,
    bootstrap_diagnostic: Mutex<Option<AppearanceDiagnostic>>,
    workspace_layout_diagnostic: Mutex<Option<WorkspaceLayoutDiagnostic>>,
    typography_diagnostic: Mutex<Option<TypographyDiagnostic>>,
}

impl AppController {
    // This composition root receives independent bounded-context services; grouping them would
    // only move the same dependency list into a one-use parameter bag.
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn new(
        checkout: CheckoutController,
        install: Option<InstallCoordinator>,
        appearance: AppearanceContext,
        workspace_layout: WorkspaceLayoutContext,
        local: LocalContext,
        ai: AiExplanationContext,
        checkout_picker: Arc<dyn CheckoutDirectoryPickerPort>,
        operations: Arc<OperationCoordinator>,
        bootstrap_diagnostic: Option<AppearanceDiagnostic>,
        workspace_layout_diagnostic: Option<WorkspaceLayoutDiagnostic>,
    ) -> Result<Self, String> {
        Self::new_with_typography(
            checkout,
            install,
            appearance,
            workspace_layout,
            TypographyContext::from_loaded(
                TypographyPreset::Default,
                true,
                Arc::new(crate::contexts::typography::TypographyStore::new(
                    std::env::temp_dir().join("harness-desktop-typography-unconfigured.json"),
                )),
            ),
            local,
            ai,
            checkout_picker,
            operations,
            bootstrap_diagnostic,
            workspace_layout_diagnostic,
            None,
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub(crate) fn new_with_typography(
        checkout: CheckoutController,
        install: Option<InstallCoordinator>,
        appearance: AppearanceContext,
        workspace_layout: WorkspaceLayoutContext,
        typography: TypographyContext,
        local: LocalContext,
        ai: AiExplanationContext,
        checkout_picker: Arc<dyn CheckoutDirectoryPickerPort>,
        operations: Arc<OperationCoordinator>,
        bootstrap_diagnostic: Option<AppearanceDiagnostic>,
        workspace_layout_diagnostic: Option<WorkspaceLayoutDiagnostic>,
        typography_diagnostic: Option<TypographyDiagnostic>,
    ) -> Result<Self, String> {
        Ok(Self {
            appearance,
            workspace_layout,
            typography,
            sot: SotContext::with_services(checkout, install)?,
            local,
            ai,
            checkout_picker,
            correlation: CorrelationContext::default(),
            correlation_revision_gate: Mutex::new(()),
            workspace_layout_gate: Mutex::new(()),
            operations,
            next_install_operation: AtomicU64::new(0),
            bootstrap_diagnostic: Mutex::new(bootstrap_diagnostic),
            workspace_layout_diagnostic: Mutex::new(workspace_layout_diagnostic),
            typography_diagnostic: Mutex::new(typography_diagnostic),
        })
    }

    pub(crate) fn bootstrap_state(&self) -> AppBootstrapState {
        let _layout_publication = self
            .workspace_layout_gate
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let appearance_diagnostic = self
            .bootstrap_diagnostic
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone();
        let workspace_layout_diagnostic = self
            .workspace_layout_diagnostic
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone();
        let typography_diagnostic = self
            .typography_diagnostic
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .clone();
        AppBootstrapState {
            appearance: self.appearance.bootstrap_state(),
            workspace_layout: self.workspace_layout.state(),
            typography: self.typography.state(),
            appearance_diagnostic,
            workspace_layout_diagnostic,
            typography_diagnostic,
        }
    }

    pub(crate) fn set_appearance_mode(
        &self,
        logical_mode: LogicalMode,
    ) -> Result<AppearanceUpdate, AppearanceContextError> {
        self.appearance.set_mode(logical_mode)
    }

    pub(crate) fn set_typography_preset(
        &self,
        expected_revision: u64,
        preset: TypographyPreset,
    ) -> Result<TypographyUpdate, TypographyContextError> {
        let update = self.typography.set_preset(expected_revision, preset)?;
        *self
            .typography_diagnostic
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = update.diagnostic.clone();
        Ok(update)
    }

    pub(crate) fn set_workspace_layout(
        &self,
        expected_revision: u64,
        preferred_left_width_px: u32,
        preferred_right_width_px: u32,
        left_collapsed: bool,
        right_collapsed: bool,
    ) -> Result<WorkspaceLayoutUpdate, WorkspaceLayoutContextError> {
        let _layout_publication = self
            .workspace_layout_gate
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let update = self.workspace_layout.set_preference_with_disclosure(
            expected_revision,
            preferred_left_width_px,
            preferred_right_width_px,
            left_collapsed,
            right_collapsed,
        )?;
        *self
            .workspace_layout_diagnostic
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner) = update.diagnostic.clone();
        Ok(update)
    }

    pub(crate) fn complete_bootstrap(
        &self,
        appearance_revision: u64,
        layout_revision: u64,
        typography_revision: u64,
        resolved_mode: ResolvedMode,
        probe: WorkspaceLayoutBootstrapProbe,
    ) -> Result<AppBootstrapCompletion, AppearanceContextError> {
        let _layout_publication = self
            .workspace_layout_gate
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let workspace_layout = self.workspace_layout.state();
        let typography = self.typography.state();
        if typography.revision != typography_revision
            || probe.applied_typography_revision != typography.revision
            || typography.preset != probe.typography_preset
        {
            return Ok(AppBootstrapCompletion {
                instruction: BootstrapInstruction::Refresh,
                appearance: self.appearance.bootstrap_state(),
                workspace_layout,
                typography,
            });
        }
        let mut completion = complete_bootstrap_after_layout_validation(
            &self.appearance,
            workspace_layout,
            appearance_revision,
            layout_revision,
            resolved_mode,
            probe.geometry,
        )?;
        completion.typography = typography;
        Ok(completion)
    }

    pub(crate) fn enqueue_system_theme_changed(
        &self,
        resolved_mode: ResolvedMode,
    ) -> Result<(), AppearanceContextError> {
        self.appearance.enqueue_system_theme_changed(resolved_mode)
    }

    pub(crate) fn enqueue_main_window_reactivation(&self) -> Result<(), AppearanceContextError> {
        self.appearance.enqueue_main_window_reactivation()
    }

    pub(crate) fn bootstrap_watchdog_generation(&self) -> u64 {
        self.appearance.bootstrap_watchdog_generation()
    }

    pub(crate) fn handle_bootstrap_timeout(
        &self,
        generation: u64,
    ) -> Result<Option<BootstrapRecovery>, AppearanceContextError> {
        self.appearance.handle_bootstrap_timeout(generation)
    }

    pub(crate) fn sot_session_state(&self) -> Result<SotSessionHeaders, String> {
        self.sot.session_headers()
    }

    pub(crate) fn load_sot_snapshot(&self, checkout_id: &str) -> Result<SotSnapshot, String> {
        let _revision = self.lock_correlation_revision()?;
        self.correlation.invalidate()?;
        self.sot.load_active(checkout_id)
    }

    pub(crate) fn local_scan_state(&self) -> LocalSessionState {
        self.local.state()
    }

    pub(crate) fn start_local_scan(&self) -> Result<StartLocalScanOutcome, LocalContextError> {
        self.local.start_scan()
    }

    pub(crate) fn get_project_ignore(
        &self,
        snapshot_id: &str,
        project_id: &str,
    ) -> Result<crate::contexts::local::ProjectIgnoreSource, LocalContextError> {
        self.local.get_project_ignore(snapshot_id, project_id)
    }

    pub(crate) fn save_project_ignore_and_rescan(
        &self,
        snapshot_id: &str,
        project_id: &str,
        expected_source_revision: &str,
        exact_text: &str,
    ) -> Result<
        (
            crate::contexts::local::ProjectIgnoreSource,
            crate::contexts::local::IgnoreSaveRescanOutcome,
        ),
        LocalContextError,
    > {
        self.local.save_project_ignore_and_rescan(
            snapshot_id,
            project_id,
            expected_source_revision,
            exact_text,
        )
    }

    pub(crate) fn get_correlation_projection(
        &self,
        local_snapshot_id: &str,
        sot_snapshot_id: Option<&str>,
        install_evidence_id: Option<&str>,
    ) -> Result<CorrelationProjection, String> {
        let _revision = self.lock_correlation_revision()?;
        let local = self
            .local
            .publication(local_snapshot_id)
            .map_err(|_| "projection_stale".to_string())?;
        let snapshot_id = sot_snapshot_id.ok_or_else(|| "projection_stale".to_string())?;
        self.sot
            .require_snapshot(snapshot_id)
            .map_err(|_| "projection_stale".to_string())?;
        let qualified_artifact_projection = self.sot.qualified_artifact_projection(snapshot_id)?;
        let evidence = match install_evidence_id {
            Some(evidence_id) => Some(
                self.sot
                    .require_install_evidence(snapshot_id, evidence_id)
                    .map_err(|error| match error.as_str() {
                        "snapshot_expired" | "evidence_expired" => error,
                        _ => "projection_stale".to_string(),
                    })?,
            ),
            None => None,
        };
        self.correlation.project(
            &local.snapshot,
            &qualified_artifact_projection,
            evidence.as_ref(),
        )
    }

    pub(crate) fn query_local_instances(
        &self,
        request: LocalQueryRequest,
    ) -> Result<
        (
            LocalQueryResult,
            Vec<ProjectLocationRecord>,
            Option<CorrelationProjection>,
        ),
        String,
    > {
        let _revision = request
            .correlation_projection_id
            .as_ref()
            .map(|_| self.lock_correlation_revision())
            .transpose()?;
        let publication = self.local.publication(&request.snapshot_id)?;
        let projection = match request.correlation_projection_id.as_deref() {
            Some(projection_id) => Some(
                self.correlation
                    .require(projection_id, &request.snapshot_id)?,
            ),
            None => None,
        };
        let verified_instance_ids = projection.as_ref().map(|projection| {
            request
                .verified_component_id
                .as_deref()
                .map(|component_id| projection.verified_instance_ids(component_id))
                .unwrap_or_default()
        });
        let result = query_local_instances(
            &publication.snapshot,
            &request,
            verified_instance_ids.as_ref(),
        )?;
        let mut projects = publication
            .project_locations
            .into_values()
            .collect::<Vec<_>>();
        projects.sort_by(|left, right| {
            left.canonical_path
                .as_os_str()
                .as_encoded_bytes()
                .cmp(right.canonical_path.as_os_str().as_encoded_bytes())
                .then_with(|| left.project_id.cmp(&right.project_id))
        });
        Ok((result, projects, projection))
    }

    pub(crate) fn get_local_instance_detail(
        &self,
        snapshot_id: &str,
        correlation_projection_id: Option<&str>,
        instance_id: &str,
    ) -> Result<(LocalInstanceDetail, CorrelationState), String> {
        let _revision = correlation_projection_id
            .map(|_| self.lock_correlation_revision())
            .transpose()?;
        let publication = self.local.publication(snapshot_id)?;
        let detail = get_local_instance_detail(&publication.snapshot, snapshot_id, instance_id)?;
        let correlation = match correlation_projection_id {
            Some(projection_id) => self
                .correlation
                .require(projection_id, snapshot_id)?
                .correlation_for(instance_id),
            None => CorrelationState::Uncorrelated,
        };
        Ok((detail, correlation))
    }

    pub(crate) fn act_on_local_instance(
        &self,
        snapshot_id: &str,
        instance_id: &str,
        action: LocalPathAction,
    ) -> Result<LocalPathActionOutcome, LocalPathActionError> {
        self.local.act_on_instance(snapshot_id, instance_id, action)
    }

    pub(crate) fn prepare_local_removal(
        &self,
        snapshot_id: &str,
        instance_ids: &[String],
    ) -> Result<LocalRemovalPlanPreview, LocalContextError> {
        self.local.prepare_local_removal(snapshot_id, instance_ids)
    }

    pub(crate) fn apply_local_removal(
        &self,
        plan_id: &str,
        plan_digest: &str,
        confirmed: bool,
    ) -> Result<LocalRemovalApplyOutcome, LocalContextError> {
        self.local
            .apply_local_removal(plan_id, plan_digest, confirmed)
    }

    pub(crate) fn reconcile_local_removal(
        &self,
        snapshot_id: &str,
        expected_attempt_id: &str,
        instance_ids: &[String],
    ) -> Result<LocalRemovalReconciliation, LocalContextError> {
        self.local
            .reconcile_local_removal(snapshot_id, expected_attempt_id, instance_ids)
    }

    pub(crate) fn prepare_open_local_source_preview(
        &self,
        request: OpenSourceRequest,
    ) -> Result<OpenSourcePreviewJob, SourceInspectionError> {
        self.local.prepare_open_source_preview(request)
    }

    pub(crate) fn prepare_read_local_source_preview(
        &self,
        request: ReadSourceChunkRequest,
    ) -> Result<ReadSourcePreviewJob, SourceInspectionError> {
        self.local.prepare_read_source_preview(request)
    }

    pub(crate) fn close_local_source_preview(
        &self,
        view_generation: u64,
        preview_session_id: Option<&str>,
    ) -> SourcePreviewClose {
        self.local
            .close_source_preview(view_generation, preview_session_id)
    }

    pub(crate) fn ai_provider_state(&self) -> Result<ProviderConfigState, AiExplanationError> {
        self.ai.provider_state()
    }

    pub(crate) fn save_ai_provider_config(
        &self,
        expected_provider_revision: Option<&str>,
        base_url: &str,
        model: &str,
        api_key: Option<&str>,
    ) -> Result<ProviderConfigHeader, AiExplanationError> {
        self.ai
            .save_provider_config(expected_provider_revision, base_url, model, api_key)
    }

    pub(crate) fn delete_ai_provider_key(
        &self,
        provider_revision: &str,
    ) -> Result<ProviderConfigHeader, AiExplanationError> {
        self.ai.delete_provider_key(provider_revision)
    }

    pub(crate) fn explain_local_source(
        &self,
        snapshot_id: String,
        instance_id: String,
        source_revision: String,
        provider_revision: String,
    ) -> Result<AiExplanation, AiExplanationError> {
        self.ai.ensure_active_revision(&provider_revision)?;
        let captured = self
            .local
            .capture_revision_bound_source(
                &snapshot_id,
                &instance_id,
                &source_revision,
                MAX_AI_SOURCE_BYTES,
            )
            .map_err(|error| AiExplanationError::from_code(error.code))?;
        self.ai.explain(AiExplanationInput::new(
            snapshot_id,
            instance_id,
            captured.source_revision().to_string(),
            provider_revision,
            captured.into_bytes(),
        ))
    }

    pub(crate) fn pick_checkout_directory(&self) -> PickerOutcome {
        self.checkout_picker.pick_directory()
    }

    pub(crate) fn clone_default_checkout(
        &self,
        app_data_dir: &Path,
    ) -> Result<RegisterCheckoutResponse, String> {
        let _revision = self.lock_correlation_revision()?;
        let registration = self.sot.clone_default_checkout(app_data_dir)?;
        self.correlation.invalidate()?;
        Ok(registration)
    }

    pub(crate) fn register_checkout(
        &self,
        path: impl AsRef<Path>,
    ) -> Result<RegisterCheckoutResponse, String> {
        let _revision = self.lock_correlation_revision()?;
        let registration = self.sot.register_checkout(path)?;
        self.correlation.invalidate()?;
        Ok(registration)
    }

    pub(crate) fn preview_install(
        &self,
        checkout_id: &str,
        sot_snapshot_id: &str,
        profile_id: &str,
        scope: &str,
        target_root: std::path::PathBuf,
        target_ids: std::collections::BTreeSet<String>,
    ) -> Result<InstallPreview, String> {
        let operation_id = self.next_install_operation_id();
        let _guard = self
            .operations
            .begin_install(&operation_id, "preview", None)
            .map_err(|_| "operation_busy".to_string())?;
        self.sot.preview_install(
            checkout_id,
            sot_snapshot_id,
            profile_id,
            scope,
            target_root,
            target_ids,
        )
    }

    pub(crate) fn apply_install(
        &self,
        preview_id: &str,
        approvals: InstallApprovals,
    ) -> Result<InstallApplyOutcome, String> {
        let operation_id = self.next_install_operation_id();
        let _guard = self
            .operations
            .begin_install(&operation_id, "apply", Some(preview_id.to_string()))
            .map_err(|_| "operation_busy".to_string())?;
        let _revision = self.lock_correlation_revision()?;
        self.correlation.invalidate()?;
        self.sot.apply_install(preview_id, approvals)
    }

    fn next_install_operation_id(&self) -> String {
        let sequence = self.next_install_operation.fetch_add(1, Ordering::Relaxed) + 1;
        format!("install-operation-{sequence}")
    }

    fn lock_correlation_revision(&self) -> Result<std::sync::MutexGuard<'_, ()>, String> {
        self.correlation_revision_gate
            .lock()
            .map_err(|_| "correlation_revision_unavailable".to_string())
    }
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::path::Path;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    use std::sync::{Condvar, Mutex};
    use std::time::Duration;

    use tempfile::TempDir;

    use super::*;
    use crate::contexts::ai::{
        AiTransportError, NormalizedProviderConfig, OpenAiCompatibleTransportPort,
        ParsedAiExplanation,
    };
    use crate::contexts::appearance::{
        AppearanceChanged, AppearanceEventPort, AppearanceStore, AppearanceWindowError,
        AppearanceWindowPort, BootstrapRecoveryAction, WindowThemeOverride,
    };
    use crate::contexts::local::scanner::LocalScanner;
    use crate::contexts::local::verified_path::{VerifiedLocalFile, VerifiedReadError};
    use crate::contexts::local::{
        AdapterCatalog, LocalScanExecutor, LocalScanExecutorResult, LocalScanProgressPort,
        LocalScanPublication, LocalSnapshot, LocalSnapshotStatus, SourceReadPort, ToolId,
    };

    const WAIT_TIMEOUT: Duration = Duration::from_secs(2);

    fn layout_frame(x: f64, y: f64, width: f64, height: f64) -> WorkspaceLayoutFrame {
        WorkspaceLayoutFrame {
            x,
            y,
            width,
            height,
        }
    }

    fn three_pane_layout_probe() -> WorkspaceLayoutBootstrapProbe {
        WorkspaceLayoutBootstrapProbe {
            applied_typography_revision: 0,
            typography_preset: TypographyPreset::Default,
            geometry: WorkspaceLayoutGeometryProbe {
                applied_layout_revision: 7,
                preferred_left_width_px: 304,
                preferred_right_width_px: 368,
                left_collapsed: false,
                right_collapsed: false,
                mode: WorkspaceLayoutMode::ThreePane,
                shell: layout_frame(0.0, 0.0, 1200.0, 800.0),
                panes: WorkspaceLayoutPaneFrames {
                    left: Some(layout_frame(0.0, 0.0, 304.0, 800.0)),
                    center: layout_frame(316.0, 0.0, 504.0, 800.0),
                    right: Some(layout_frame(832.0, 0.0, 368.0, 800.0)),
                },
                active_separator_count: 2,
                disabled_separator_count: 0,
            },
        }
    }

    fn layout_state() -> crate::contexts::layout::WorkspaceLayoutState {
        crate::contexts::layout::WorkspaceLayoutState {
            preferred_left_width_px: 304,
            preferred_right_width_px: 368,
            left_collapsed: false,
            right_collapsed: false,
            revision: 7,
            persisted: true,
        }
    }

    struct UnusedExecutor;

    impl LocalScanExecutor for UnusedExecutor {
        fn execute(
            &self,
            _attempt_id: &str,
            _progress: &dyn LocalScanProgressPort,
        ) -> LocalScanExecutorResult {
            panic!("fixture publishes directly through OperationCoordinator")
        }
    }

    struct CancelledPicker;

    impl CheckoutDirectoryPickerPort for CancelledPicker {
        fn pick_directory(&self) -> PickerOutcome {
            PickerOutcome::Cancelled
        }
    }

    struct NoopAppearancePort;

    impl AppearanceWindowPort for NoopAppearancePort {
        fn current_system_mode(&self) -> Result<ResolvedMode, AppearanceWindowError> {
            Ok(ResolvedMode::Light)
        }

        fn apply_theme(
            &self,
            _theme_override: WindowThemeOverride,
            _resolved_mode: ResolvedMode,
        ) -> Result<(), AppearanceWindowError> {
            Ok(())
        }

        fn show(&self) -> Result<(), AppearanceWindowError> {
            Ok(())
        }

        fn restore_and_focus(&self) -> Result<(), AppearanceWindowError> {
            Ok(())
        }

        fn prompt_bootstrap_recovery(
            &self,
        ) -> Result<BootstrapRecoveryAction, AppearanceWindowError> {
            Ok(BootstrapRecoveryAction::Quit)
        }

        fn reload(&self) -> Result<(), AppearanceWindowError> {
            Ok(())
        }

        fn quit(&self) {}
    }

    struct NoopAppearanceEvents;

    impl AppearanceEventPort for NoopAppearanceEvents {
        fn emit_changed(&self, _event: &AppearanceChanged) -> Result<(), AppearanceWindowError> {
            Ok(())
        }
    }

    #[derive(Default)]
    struct ShowCountingAppearancePort {
        show_count: AtomicUsize,
    }

    impl AppearanceWindowPort for ShowCountingAppearancePort {
        fn current_system_mode(&self) -> Result<ResolvedMode, AppearanceWindowError> {
            Ok(ResolvedMode::Light)
        }

        fn apply_theme(
            &self,
            _theme_override: WindowThemeOverride,
            _resolved_mode: ResolvedMode,
        ) -> Result<(), AppearanceWindowError> {
            Ok(())
        }

        fn show(&self) -> Result<(), AppearanceWindowError> {
            self.show_count.fetch_add(1, Ordering::SeqCst);
            Ok(())
        }

        fn restore_and_focus(&self) -> Result<(), AppearanceWindowError> {
            Ok(())
        }

        fn prompt_bootstrap_recovery(
            &self,
        ) -> Result<BootstrapRecoveryAction, AppearanceWindowError> {
            Ok(BootstrapRecoveryAction::Quit)
        }

        fn reload(&self) -> Result<(), AppearanceWindowError> {
            Ok(())
        }

        fn quit(&self) {}
    }

    #[test]
    fn workspace_layout_mismatch_returns_refresh_before_appearance_show() {
        let temp = TempDir::new().unwrap();
        let window = Arc::new(ShowCountingAppearancePort::default());
        let appearance = AppearanceContext::new(
            LogicalMode::System,
            ResolvedMode::Light,
            Arc::new(AppearanceStore::new(temp.path().join("appearance.json"))),
            window.clone(),
            Arc::new(NoopAppearanceEvents),
        );
        let state = layout_state();
        let mut invalid = three_pane_layout_probe();
        invalid.geometry.panes.center.width = 479.0;

        let completion = complete_bootstrap_after_layout_validation(
            &appearance,
            state,
            0,
            state.revision,
            ResolvedMode::Light,
            invalid.geometry,
        )
        .unwrap();
        assert_eq!(completion.instruction, BootstrapInstruction::Refresh);
        assert_eq!(window.show_count.load(Ordering::SeqCst), 0);

        let completion = complete_bootstrap_after_layout_validation(
            &appearance,
            state,
            0,
            state.revision,
            ResolvedMode::Light,
            three_pane_layout_probe().geometry,
        )
        .unwrap();
        assert_eq!(completion.instruction, BootstrapInstruction::Show);
        assert_eq!(window.show_count.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn bootstrap_refreshes_when_the_applied_typography_preset_is_stale() {
        let fixture = fixture(Arc::new(CountingTransport::default()));
        let mut probe = three_pane_layout_probe();
        probe.geometry.applied_layout_revision = 0;
        probe.typography_preset = TypographyPreset::Large;

        let completion = fixture
            .controller
            .complete_bootstrap(0, 0, 0, ResolvedMode::Light, probe)
            .unwrap();

        assert_eq!(completion.instruction, BootstrapInstruction::Refresh);
        assert_eq!(completion.typography.preset, TypographyPreset::Default);
    }

    #[test]
    fn bootstrap_refreshes_when_the_applied_typography_revision_is_stale() {
        let fixture = fixture(Arc::new(CountingTransport::default()));
        let mut probe = three_pane_layout_probe();
        probe.geometry.applied_layout_revision = 0;
        probe.applied_typography_revision = 1;

        let completion = fixture
            .controller
            .complete_bootstrap(0, 0, 0, ResolvedMode::Light, probe)
            .unwrap();

        assert_eq!(completion.instruction, BootstrapInstruction::Refresh);
        assert_eq!(completion.typography.revision, 0);
    }

    #[derive(Default)]
    struct BlockingSourceReader {
        armed: AtomicBool,
        blocked_once: AtomicBool,
        entered: (Mutex<bool>, Condvar),
        released: (Mutex<bool>, Condvar),
    }

    impl BlockingSourceReader {
        fn arm(&self) {
            *self.entered.0.lock().unwrap() = false;
            *self.released.0.lock().unwrap() = false;
            self.blocked_once.store(false, Ordering::Release);
            self.armed.store(true, Ordering::Release);
        }

        fn wait_until_entered(&self) {
            let entered = self.entered.0.lock().unwrap();
            let (entered, timeout) = self
                .entered
                .1
                .wait_timeout_while(entered, WAIT_TIMEOUT, |entered| !*entered)
                .unwrap();
            assert!(
                *entered && !timeout.timed_out(),
                "source capture did not start"
            );
        }

        fn release(&self) {
            *self.released.0.lock().unwrap() = true;
            self.released.1.notify_all();
        }
    }

    impl SourceReadPort for BlockingSourceReader {
        fn read_at(
            &self,
            file: &VerifiedLocalFile,
            buffer: &mut [u8],
            offset: u64,
        ) -> Result<usize, VerifiedReadError> {
            if self.armed.load(Ordering::Acquire) && !self.blocked_once.swap(true, Ordering::AcqRel)
            {
                *self.entered.0.lock().unwrap() = true;
                self.entered.1.notify_all();
                let released = self.released.0.lock().unwrap();
                let _released = self
                    .released
                    .1
                    .wait_while(released, |released| !*released)
                    .unwrap();
                self.armed.store(false, Ordering::Release);
            }
            file.read_at(buffer, offset)
        }
    }

    struct BlockingTransport {
        calls: AtomicUsize,
        entered: (Mutex<bool>, Condvar),
        released: (Mutex<bool>, Condvar),
    }

    impl BlockingTransport {
        fn new() -> Self {
            Self {
                calls: AtomicUsize::new(0),
                entered: (Mutex::new(false), Condvar::new()),
                released: (Mutex::new(false), Condvar::new()),
            }
        }

        fn wait_until_entered(&self) {
            let entered = self.entered.0.lock().unwrap();
            let (entered, timeout) = self
                .entered
                .1
                .wait_timeout_while(entered, WAIT_TIMEOUT, |entered| !*entered)
                .unwrap();
            assert!(
                *entered && !timeout.timed_out(),
                "AI transport did not start"
            );
        }

        fn release(&self) {
            *self.released.0.lock().unwrap() = true;
            self.released.1.notify_all();
        }
    }

    impl OpenAiCompatibleTransportPort for BlockingTransport {
        fn explain(
            &self,
            _config: &NormalizedProviderConfig,
            _api_key: Option<&[u8]>,
            _source: &[u8],
        ) -> Result<ParsedAiExplanation, AiTransportError> {
            self.calls.fetch_add(1, Ordering::SeqCst);
            *self.entered.0.lock().unwrap() = true;
            self.entered.1.notify_all();
            let released = self.released.0.lock().unwrap();
            let _released = self
                .released
                .1
                .wait_while(released, |released| !*released)
                .unwrap();
            Err(AiTransportError::new("provider_request_failed"))
        }
    }

    #[derive(Default)]
    struct CountingTransport {
        calls: AtomicUsize,
    }

    impl OpenAiCompatibleTransportPort for CountingTransport {
        fn explain(
            &self,
            _config: &NormalizedProviderConfig,
            _api_key: Option<&[u8]>,
            _source: &[u8],
        ) -> Result<ParsedAiExplanation, AiTransportError> {
            self.calls.fetch_add(1, Ordering::SeqCst);
            Err(AiTransportError::new("provider_request_failed"))
        }
    }

    struct ControllerFixture {
        _temp: TempDir,
        controller: Arc<AppController>,
        operations: Arc<OperationCoordinator>,
        source_reader: Arc<BlockingSourceReader>,
        snapshot_id: String,
        instance_id: String,
        provider_revision: String,
    }

    fn write(path: &Path, body: &str) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, body).unwrap();
    }

    fn fixture(transport: Arc<dyn OpenAiCompatibleTransportPort>) -> ControllerFixture {
        let temp = tempfile::tempdir().unwrap();
        let temp_root = fs::canonicalize(temp.path()).unwrap();
        let home = temp_root.join("home");
        write(
            &home.join(".codex/skills/release/SKILL.md"),
            "---\nname: release\ndescription: fixture\n---\n# Release\nbody\n",
        );
        let catalog = AdapterCatalog::load_embedded().unwrap();
        let adapter = catalog.for_tool(ToolId::Codex).unwrap().clone();
        let output = LocalScanner::scan_with_handles(&home, &[adapter]).unwrap();
        let instance_id = output
            .result
            .instances
            .iter()
            .find(|instance| instance.name.as_deref() == Some("release"))
            .unwrap()
            .instance_id
            .clone();
        let snapshot_id = "app-controller-ai-race-snapshot".to_string();
        let publication = LocalScanPublication {
            snapshot: LocalSnapshot {
                snapshot_id: snapshot_id.clone(),
                attempt_id: "app-controller-ai-race-scan".to_string(),
                status: LocalSnapshotStatus::Complete,
                scan_timestamp: "2026-07-14T00:00:00Z".to_string(),
                app_version: "test".to_string(),
                adapter_set_fingerprint: "a".repeat(64),
                content_fingerprint: "b".repeat(64),
                qualified_tools: output.result.qualified_tools,
                instances: output.result.instances,
                coverage: output.result.coverage,
                skipped_paths: output.result.skipped_paths,
                issues: output.result.issues,
                project_ignore_summaries: output.result.project_ignore_summaries,
            },
            instance_handle_ids: vec![instance_id.clone()],
            instance_handles: output.instance_handles,
            project_locations: output.project_locations,
        };
        let operations = Arc::new(OperationCoordinator::default());
        let source_reader = Arc::new(BlockingSourceReader::default());
        let local = LocalContext::with_executor_operations_and_source_reader(
            Arc::new(UnusedExecutor),
            Arc::clone(&operations),
            source_reader.clone(),
        );
        local
            .publish_scan_result_for_test(
                "app-controller-ai-race-scan",
                LocalScanExecutorResult::Complete(publication),
            )
            .unwrap();
        let ai = AiExplanationContext::with_app_data_and_transport(
            temp_root.join("app-data"),
            transport,
        );
        let provider_revision = ai
            .save_provider_config(
                None,
                "http://127.0.0.1:11434/v1",
                "fixture-model",
                Some("fixture-secret"),
            )
            .unwrap()
            .provider_revision()
            .to_string();
        let appearance = AppearanceContext::new(
            LogicalMode::System,
            ResolvedMode::Light,
            Arc::new(AppearanceStore::new(temp_root.join("appearance.json"))),
            Arc::new(NoopAppearancePort),
            Arc::new(NoopAppearanceEvents),
        );
        let controller = Arc::new(AppController {
            appearance,
            workspace_layout: WorkspaceLayoutContext::new(
                304,
                368,
                Arc::new(crate::contexts::layout::WorkspaceLayoutStore::new(
                    temp_root.join("layout.json"),
                )),
            ),
            typography: TypographyContext::from_loaded(
                TypographyPreset::Default,
                true,
                Arc::new(crate::contexts::typography::TypographyStore::new(
                    temp_root.join("typography.json"),
                )),
            ),
            sot: SotContext::default(),
            local,
            ai,
            checkout_picker: Arc::new(CancelledPicker),
            correlation: CorrelationContext::default(),
            correlation_revision_gate: Mutex::new(()),
            workspace_layout_gate: Mutex::new(()),
            operations: Arc::clone(&operations),
            next_install_operation: AtomicU64::new(0),
            bootstrap_diagnostic: Mutex::new(None),
            workspace_layout_diagnostic: Mutex::new(None),
            typography_diagnostic: Mutex::new(None),
        });

        ControllerFixture {
            _temp: temp,
            controller,
            operations,
            source_reader,
            snapshot_id,
            instance_id,
            provider_revision,
        }
    }

    fn preview_revision(fixture: &ControllerFixture) -> String {
        fixture
            .controller
            .prepare_open_local_source_preview(OpenSourceRequest {
                snapshot_id: fixture.snapshot_id.clone(),
                instance_id: fixture.instance_id.clone(),
                view_generation: 1,
            })
            .unwrap()
            .run()
            .unwrap()
            .source_revision
    }

    fn ai_error_code(result: Result<AiExplanation, AiExplanationError>) -> &'static str {
        match result {
            Ok(_) => panic!("AI explanation was expected to fail closed"),
            Err(error) => error.code(),
        }
    }

    #[test]
    fn explain_releases_the_local_read_lease_before_waiting_on_http_transport() {
        let transport = Arc::new(BlockingTransport::new());
        let fixture = fixture(transport.clone());
        let source_revision = preview_revision(&fixture);
        let controller = Arc::clone(&fixture.controller);
        let snapshot_id = fixture.snapshot_id.clone();
        let instance_id = fixture.instance_id.clone();
        let provider_revision = fixture.provider_revision.clone();
        let worker = std::thread::spawn(move || {
            controller.explain_local_source(
                snapshot_id,
                instance_id,
                source_revision,
                provider_revision,
            )
        });

        transport.wait_until_entered();
        let active_local_reads = fixture.operations.probe_active_local_reads();
        let install = fixture
            .operations
            .begin_install("install-during-http", "preview", None);

        transport.release();
        assert_eq!(active_local_reads, 0);
        let install =
            install.expect("exclusive install must start after source capture releases its lease");
        assert_eq!(
            ai_error_code(worker.join().unwrap()),
            "provider_request_failed"
        );
        assert_eq!(transport.calls.load(Ordering::SeqCst), 1);
        drop(install);
    }

    #[test]
    fn provider_revision_changed_during_source_capture_fails_before_transport() {
        let transport = Arc::new(CountingTransport::default());
        let fixture = fixture(transport.clone());
        let source_revision = preview_revision(&fixture);
        fixture.source_reader.arm();
        let controller = Arc::clone(&fixture.controller);
        let snapshot_id = fixture.snapshot_id.clone();
        let instance_id = fixture.instance_id.clone();
        let provider_revision = fixture.provider_revision.clone();
        let worker = std::thread::spawn(move || {
            controller.explain_local_source(
                snapshot_id,
                instance_id,
                source_revision,
                provider_revision,
            )
        });

        fixture.source_reader.wait_until_entered();
        let active_local_reads = fixture.operations.probe_active_local_reads();
        let provider_update = fixture.controller.save_ai_provider_config(
            Some(&fixture.provider_revision),
            "http://127.0.0.1:11434/v1",
            "new-fixture-model",
            None,
        );
        fixture.source_reader.release();

        assert_eq!(active_local_reads, 1);
        provider_update.unwrap();
        assert_eq!(ai_error_code(worker.join().unwrap()), "provider_stale");
        assert_eq!(transport.calls.load(Ordering::SeqCst), 0);
        assert_eq!(fixture.operations.probe_active_local_reads(), 0);
    }
}
