use crate::api::dto::ai::{
    AiExplanationDto, DeleteAiProviderKeyRequestDto, ExplainLocalSourceRequestDto,
    ProviderConfigStateDto, SaveAiProviderConfigRequestDto,
};
use crate::api::dto::appearance::{AppearanceUpdateDto, LogicalModeDto};
use crate::api::dto::bootstrap::{BootstrapCompletionDto, BootstrapStateDto, BootstrapUiProbeDto};
use crate::api::dto::common::{ApiErrorDetailsDto, ApiErrorDto};
use crate::api::dto::install::{
    ApplyInstallApprovalsDto, ApplyInstallResponseDto, InstallPreviewRequestDto,
    PreviewInstallResponseDto,
};
use crate::api::dto::layout::{SetWorkspaceLayoutRequestDto, WorkspaceLayoutUpdateDto};
use crate::api::dto::local::{
    ApplyLocalRemovalRequestDto, CloseLocalSourcePreviewRequestDto, CorrelationProjectionDto,
    GetProjectIgnoreRequestDto, LocalInstanceActionDto, LocalInstanceActionOutcomeDto,
    LocalInstanceDetailDto, LocalQueryRequestDto, LocalQueryResultDto, LocalRemovalOutcomeDto,
    LocalRemovalReconciliationDto, LocalScanStateDto, LocalSourcePreviewChunkDto,
    LocalSourcePreviewCloseDto, LocalSourcePreviewHeaderDto, OpenLocalSourcePreviewRequestDto,
    PrepareLocalRemovalRequestDto, ProjectIgnoreDto, ReadLocalSourcePreviewChunkRequestDto,
    ReconcileLocalRemovalRequestDto, RemovalPlanDto, SaveProjectIgnoreAndRescanRequestDto,
    SaveProjectIgnoreAndRescanResponseDto, StartLocalScanOutcomeDto,
};
use crate::api::dto::sot::{
    CheckoutDirectoryPickerOutcomeDto, CheckoutRegistrationDto, SotSessionStateDto, SotSnapshotDto,
};
use crate::api::dto::typography::{SetTypographyPresetRequestDto, TypographyUpdateDto};
use crate::app_controller::{AppController, WorkspaceLayoutBootstrapProbe};
use crate::contexts::appearance::{AppearanceChanged, AppearanceEventPort, AppearanceWindowError};
use crate::contexts::layout::{
    WorkspaceLayoutFrame, WorkspaceLayoutGeometryProbe, WorkspaceLayoutMode,
    WorkspaceLayoutPaneFrames,
};
use crate::contexts::local::{
    LocalContextError, LocalScanEventEnvelope, LocalScanEventError, LocalScanEventPort,
    SourceInspectionError,
};
use std::collections::BTreeSet;
use std::path::PathBuf;
use std::sync::Arc;
use tauri::{Emitter, EventTarget, Manager};

pub(crate) struct TauriAppearanceEventPort {
    window: tauri::WebviewWindow,
}

impl TauriAppearanceEventPort {
    pub(crate) fn new(window: tauri::WebviewWindow) -> Self {
        Self { window }
    }
}

impl AppearanceEventPort for TauriAppearanceEventPort {
    fn emit_changed(&self, event: &AppearanceChanged) -> Result<(), AppearanceWindowError> {
        self.window
            .emit_to(
                EventTarget::webview_window("main"),
                "appearance_changed",
                crate::api::dto::appearance::AppearanceChangedDto::from(event.clone()),
            )
            .map_err(|_| AppearanceWindowError {
                code: "appearance_event_emit_failed",
                safe_message: "외관 변경 알림을 전달하지 못했습니다.",
            })
    }
}

pub(crate) struct TauriLocalScanEventPort {
    window: tauri::WebviewWindow,
}

impl TauriLocalScanEventPort {
    pub(crate) fn new(window: tauri::WebviewWindow) -> Self {
        Self { window }
    }
}

impl LocalScanEventPort for TauriLocalScanEventPort {
    fn emit_changed(&self, event: &LocalScanEventEnvelope) -> Result<(), LocalScanEventError> {
        self.window
            .emit_to(
                EventTarget::webview_window("main"),
                "local_scan_changed",
                event,
            )
            .map_err(|_| LocalScanEventError {
                code: "local_scan_event_emit_failed",
            })
    }
}

#[tauri::command]
pub(crate) async fn get_bootstrap_state(
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<BootstrapStateDto, ApiErrorDto> {
    log::info!("appearance bootstrap state requested");
    let controller = Arc::clone(state.inner());
    let bootstrap = tauri::async_runtime::spawn_blocking(move || controller.bootstrap_state())
        .await
        .map_err(|_| task_error("bootstrap_state_task_failed"))?;
    Ok(BootstrapStateDto {
        appearance: bootstrap.appearance.into(),
        workspace_layout: bootstrap.workspace_layout.into(),
        typography: bootstrap.typography.into(),
        app_version: env!("CARGO_PKG_VERSION").to_owned(),
        appearance_diagnostic: bootstrap.appearance_diagnostic.map(Into::into),
        workspace_layout_diagnostic: bootstrap.workspace_layout_diagnostic.map(Into::into),
        typography_diagnostic: bootstrap.typography_diagnostic.map(Into::into),
    })
}

#[tauri::command]
pub(crate) async fn set_typography_preset(
    request: SetTypographyPresetRequestDto,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<TypographyUpdateDto, ApiErrorDto> {
    require_main_window(&window)?;
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || {
        controller
            .set_typography_preset(request.expected_typography_revision, request.preset.into())
    })
    .await
    .map_err(|_| task_error("typography_update_task_failed"))?
    .map(Into::into)
    .map_err(typography_context_error)
}

#[tauri::command]
pub(crate) async fn set_appearance_mode(
    logical_mode: LogicalModeDto,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<AppearanceUpdateDto, ApiErrorDto> {
    require_main_window(&window)?;
    let controller = Arc::clone(state.inner());
    let update = tauri::async_runtime::spawn_blocking(move || {
        controller.set_appearance_mode(logical_mode.into())
    })
    .await
    .map_err(|_| task_error("appearance_update_task_failed"))?
    .map_err(context_error)?;
    Ok(AppearanceUpdateDto {
        appearance: update.state.into(),
        diagnostic: update.diagnostic.map(Into::into),
    })
}

#[tauri::command]
pub(crate) async fn set_workspace_layout(
    request: SetWorkspaceLayoutRequestDto,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<WorkspaceLayoutUpdateDto, ApiErrorDto> {
    require_main_window(&window)?;
    log::info!("Workspace layout update requested");
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || {
        controller.set_workspace_layout(
            request.expected_layout_revision,
            request.preferred_left_width_px,
            request.preferred_right_width_px,
            request.left_collapsed,
            request.right_collapsed,
        )
    })
    .await
    .map_err(|_| task_error("workspace_layout_update_task_failed"))?
    .map(Into::into)
    .map_err(layout_context_error)
}

#[tauri::command]
pub(crate) async fn complete_bootstrap(
    appearance_revision: u64,
    layout_revision: u64,
    typography_revision: u64,
    ui_probe: BootstrapUiProbeDto,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<BootstrapCompletionDto, ApiErrorDto> {
    log::info!(
        "bootstrap completion requested for appearance revision {appearance_revision} and layout revision {layout_revision}"
    );
    require_main_window(&window)?;
    let resolved_mode = validate_bootstrap_ui_probe(&ui_probe).ok_or_else(|| {
        ApiErrorDto::feature_unavailable(
            "appearance_shell_not_ready",
            "앱 화면이 준비되지 않아 창을 표시하지 않았습니다.",
        )
    })?;
    log::info!(
        "frontend shell ready: children={}, text_length={}, desktop_app={}, resolved_mode={}, viewport={}x{}, desktop={}x{}, layout_visible={}",
        ui_probe.child_element_count,
        ui_probe.text_length,
        ui_probe.desktop_app_present,
        ui_probe.resolved_mode,
        ui_probe.viewport_width,
        ui_probe.viewport_height,
        ui_probe.desktop_width,
        ui_probe.desktop_height,
        ui_probe.layout_visible
    );
    let layout_probe = workspace_layout_probe(&ui_probe);
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || {
        controller.complete_bootstrap(
            appearance_revision,
            layout_revision,
            typography_revision,
            resolved_mode,
            layout_probe,
        )
    })
    .await
    .map_err(|_| task_error("appearance_bootstrap_task_failed"))?
    .map(|completion| BootstrapCompletionDto {
        instruction: completion.instruction.into(),
        appearance: completion.appearance.into(),
        workspace_layout: completion.workspace_layout.into(),
        typography: completion.typography.into(),
    })
    .map_err(context_error)
}

fn workspace_layout_probe(ui_probe: &BootstrapUiProbeDto) -> WorkspaceLayoutBootstrapProbe {
    let frame = |value: crate::api::dto::layout::FrameDto| WorkspaceLayoutFrame {
        x: value.x,
        y: value.y,
        width: value.width,
        height: value.height,
    };
    WorkspaceLayoutBootstrapProbe {
        applied_typography_revision: ui_probe.applied_typography_revision,
        typography_preset: ui_probe.applied_typography_preset.into(),
        geometry: WorkspaceLayoutGeometryProbe {
            applied_layout_revision: ui_probe.applied_layout_revision,
            preferred_left_width_px: ui_probe.applied_preferred_pair.preferred_left_width_px,
            preferred_right_width_px: ui_probe.applied_preferred_pair.preferred_right_width_px,
            left_collapsed: ui_probe.applied_collapsed_pair.left_collapsed,
            right_collapsed: ui_probe.applied_collapsed_pair.right_collapsed,
            mode: match ui_probe.layout_mode {
                crate::api::dto::layout::LayoutModeDto::ThreePane => WorkspaceLayoutMode::ThreePane,
                crate::api::dto::layout::LayoutModeDto::TwoColumn => WorkspaceLayoutMode::TwoColumn,
                crate::api::dto::layout::LayoutModeDto::Stacked => WorkspaceLayoutMode::Stacked,
            },
            shell: frame(ui_probe.shell_frame),
            panes: WorkspaceLayoutPaneFrames {
                left: ui_probe.pane_frames.left.map(frame),
                center: frame(ui_probe.pane_frames.center),
                right: ui_probe.pane_frames.right.map(frame),
            },
            active_separator_count: ui_probe.active_separator_count,
            disabled_separator_count: ui_probe.disabled_separator_count,
        },
    }
}

fn validate_bootstrap_ui_probe(
    ui_probe: &BootstrapUiProbeDto,
) -> Option<crate::contexts::appearance::ResolvedMode> {
    let resolved_mode = match ui_probe.resolved_mode.as_str() {
        "light" => crate::contexts::appearance::ResolvedMode::Light,
        "dark" => crate::contexts::appearance::ResolvedMode::Dark,
        _ => return None,
    };
    let dimensions = [
        ui_probe.viewport_width,
        ui_probe.viewport_height,
        ui_probe.desktop_width,
        ui_probe.desktop_height,
    ];
    if ui_probe.child_element_count == 0
        || ui_probe.text_length == 0
        || !ui_probe.desktop_app_present
        || dimensions
            .into_iter()
            .any(|dimension| !dimension.is_finite() || dimension <= 0.0)
        || !ui_probe.layout_visible
    {
        return None;
    }
    Some(resolved_mode)
}

#[tauri::command]
pub(crate) async fn get_sot_session_state(
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<SotSessionStateDto, ApiErrorDto> {
    log::info!("SoT session state requested");
    let controller = Arc::clone(state.inner());
    let headers = tauri::async_runtime::spawn_blocking(move || controller.sot_session_state())
        .await
        .map_err(|_| sot_task_error("sot_session_task_failed"))?
        .map_err(|_| sot_task_error("sot_session_unavailable"))?;
    Ok(SotSessionStateDto {
        checkout_id: headers.checkout_id,
        snapshot_id: headers.snapshot_id,
        install_evidence_id: headers.install_evidence_id,
    })
}

#[tauri::command]
pub(crate) async fn read_imported_skill(
    request: crate::contexts::sot::import::ImportedSkillRequest,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<crate::contexts::sot::import::ImportedSkillDetail, ApiErrorDto> {
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || controller.read_imported_skill(request))
        .await
        .map_err(|_| sot_task_error("import_task_failed"))?
        .map_err(|code| ApiErrorDto::feature_unavailable(code, "가져온 스킬을 읽을 수 없습니다."))
}

#[tauri::command]
pub(crate) async fn save_imported_skill(
    request: crate::contexts::sot::import::ImportedSkillEdit,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<SotSnapshotDto, ApiErrorDto> {
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || controller.save_imported_skill(request))
        .await
        .map_err(|_| sot_task_error("import_task_failed"))?
        .map(Into::into)
        .map_err(|code| {
            ApiErrorDto::feature_unavailable(
                code,
                "저장 결과를 확인하세요. 원본 도구 파일은 변경하지 않았습니다.",
            )
        })
}

#[tauri::command]
pub(crate) async fn preview_component_import(
    request: crate::contexts::sot::import::ImportRequest,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<crate::contexts::sot::import::ImportPreview, ApiErrorDto> {
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || controller.preview_component_import(request))
        .await
        .map_err(|_| sot_task_error("import_task_failed"))?
        .map_err(|code| {
            ApiErrorDto::feature_unavailable(
                code,
                "가져오기 후보를 다시 확인하세요. 원본은 변경하지 않았습니다.",
            )
        })
}

#[tauri::command]
pub(crate) async fn confirm_component_import(
    request: crate::contexts::sot::import::ImportConfirmation,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<SotSnapshotDto, ApiErrorDto> {
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || controller.confirm_component_import(request))
        .await
        .map_err(|_| sot_task_error("import_task_failed"))?
        .map(Into::into)
        .map_err(|code| {
            ApiErrorDto::feature_unavailable(
                code,
                "가져오기 후보를 다시 확인하세요. 원본은 변경하지 않았습니다.",
            )
        })
}

#[tauri::command]
pub(crate) async fn load_sot_snapshot(
    checkout_id: String,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<SotSnapshotDto, ApiErrorDto> {
    log::info!("SoT snapshot requested");
    log::logger().flush();
    if checkout_id.trim().is_empty() {
        return Err(sot_task_error("checkout_id_required"));
    }
    let controller = Arc::clone(state.inner());
    let snapshot =
        tauri::async_runtime::spawn_blocking(move || controller.load_sot_snapshot(&checkout_id))
            .await
            .map_err(|_| sot_task_error("sot_snapshot_task_failed"))?
            .map_err(|_| sot_task_error("sot_snapshot_unavailable"))?;
    log::info!(
        "SoT snapshot loaded: {} components, {} relations, {} profiles, {} issues",
        snapshot.components.len(),
        snapshot.relations.len(),
        snapshot.profiles.len(),
        snapshot.issues.len()
    );
    Ok(snapshot.into())
}

#[tauri::command]
pub(crate) async fn get_local_scan_state(
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<LocalScanStateDto, ApiErrorDto> {
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || controller.local_scan_state())
        .await
        .map(Into::into)
        .map_err(|_| local_task_error("local_scan_state_task_failed", true))
}

#[tauri::command]
pub(crate) async fn start_local_scan(
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<StartLocalScanOutcomeDto, ApiErrorDto> {
    log::info!("Local scan start requested");
    log::logger().flush();
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || controller.start_local_scan())
        .await
        .map_err(|_| local_task_error("local_scan_start_task_failed", true))?
        .map(Into::into)
        .map_err(|error| local_task_error(&error.code, false))
}

#[tauri::command]
pub(crate) async fn get_project_ignore(
    request: GetProjectIgnoreRequestDto,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<ProjectIgnoreDto, ApiErrorDto> {
    require_main_window(&window)?;
    if request.snapshot_id.trim().is_empty() || request.project_id.trim().is_empty() {
        return Err(local_task_error("project_ignore_request_invalid", false));
    }
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || {
        controller.get_project_ignore(&request.snapshot_id, &request.project_id)
    })
    .await
    .map_err(|_| local_task_error("project_ignore_read_task_failed", true))?
    .map(Into::into)
    .map_err(|error| project_ignore_task_error(&error))
}

#[tauri::command]
pub(crate) async fn save_project_ignore_and_rescan(
    request: SaveProjectIgnoreAndRescanRequestDto,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<SaveProjectIgnoreAndRescanResponseDto, ApiErrorDto> {
    require_main_window(&window)?;
    if request.snapshot_id.trim().is_empty()
        || request.project_id.trim().is_empty()
        || request.expected_source_revision.trim().is_empty()
    {
        return Err(local_task_error("project_ignore_request_invalid", false));
    }
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || {
        controller.save_project_ignore_and_rescan(
            &request.snapshot_id,
            &request.project_id,
            &request.expected_source_revision,
            &request.exact_text,
        )
    })
    .await
    .map_err(|_| local_task_error("project_ignore_save_task_failed", true))?
    .map(|(source, rescan)| SaveProjectIgnoreAndRescanResponseDto {
        source: source.into(),
        rescan: rescan.into(),
    })
    .map_err(|error| project_ignore_task_error(&error))
}

#[tauri::command]
pub(crate) async fn get_correlation_projection(
    local_snapshot_id: String,
    sot_snapshot_id: Option<String>,
    install_evidence_id: Option<String>,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<CorrelationProjectionDto, ApiErrorDto> {
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || {
        controller.get_correlation_projection(
            &local_snapshot_id,
            sot_snapshot_id.as_deref(),
            install_evidence_id.as_deref(),
        )
    })
    .await
    .map_err(|_| local_task_error("correlation_projection_task_failed", true))?
    .map(Into::into)
    .map_err(|error| local_task_error(&error, true))
}

#[tauri::command]
pub(crate) async fn query_local_instances(
    request: LocalQueryRequestDto,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<LocalQueryResultDto, ApiErrorDto> {
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || controller.query_local_instances(request.into()))
        .await
        .map_err(|_| local_task_error("local_query_task_failed", true))?
        .map(|(result, projects, projection)| {
            LocalQueryResultDto::from_domain_with_projects(result, projection.as_ref(), &projects)
        })
        .map_err(|error| local_task_error(&error, true))
}

#[tauri::command]
pub(crate) async fn get_local_instance_detail(
    snapshot_id: String,
    correlation_projection_id: Option<String>,
    instance_id: String,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<LocalInstanceDetailDto, ApiErrorDto> {
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || {
        controller.get_local_instance_detail(
            &snapshot_id,
            correlation_projection_id.as_deref(),
            &instance_id,
        )
    })
    .await
    .map_err(|_| local_task_error("local_detail_task_failed", true))?
    .map(|(detail, correlation)| LocalInstanceDetailDto::from_domain(detail, correlation))
    .map_err(|error| local_task_error(&error, true))
}

#[tauri::command]
pub(crate) async fn open_local_source_preview(
    request: OpenLocalSourcePreviewRequestDto,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<LocalSourcePreviewHeaderDto, ApiErrorDto> {
    require_main_window(&window)?;
    let controller = Arc::clone(state.inner());
    let job = controller
        .prepare_open_local_source_preview(request.into())
        .map_err(local_source_error)?;
    tauri::async_runtime::spawn_blocking(move || job.run())
        .await
        .map_err(|_| local_task_error("local_source_open_task_failed", true))?
        .map(Into::into)
        .map_err(local_source_error)
}

#[tauri::command]
pub(crate) async fn read_local_source_preview_chunk(
    request: ReadLocalSourcePreviewChunkRequestDto,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<LocalSourcePreviewChunkDto, ApiErrorDto> {
    require_main_window(&window)?;
    let controller = Arc::clone(state.inner());
    let job = controller
        .prepare_read_local_source_preview(request.into())
        .map_err(local_source_error)?;
    let chunk = tauri::async_runtime::spawn_blocking(move || job.run())
        .await
        .map_err(|_| local_task_error("local_source_read_task_failed", true))?
        .map_err(local_source_error)?;
    let dto: LocalSourcePreviewChunkDto = chunk.into();
    dto.ensure_serialized_bound().map_err(|code| {
        local_source_error(SourceInspectionError {
            code,
            active_chunk_index: None,
        })
    })?;
    Ok(dto)
}

#[tauri::command]
pub(crate) async fn close_local_source_preview(
    request: CloseLocalSourcePreviewRequestDto,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<LocalSourcePreviewCloseDto, ApiErrorDto> {
    require_main_window(&window)?;
    let controller = Arc::clone(state.inner());
    Ok(controller
        .close_local_source_preview(
            request.view_generation,
            request.preview_session_id.as_deref(),
        )
        .into())
}

#[tauri::command]
pub(crate) async fn act_on_local_instance(
    snapshot_id: String,
    instance_id: String,
    action: LocalInstanceActionDto,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<LocalInstanceActionOutcomeDto, ApiErrorDto> {
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || {
        controller.act_on_local_instance(&snapshot_id, &instance_id, action.into())
    })
    .await
    .map_err(|_| local_task_error("local_action_task_failed", true))?
    .map(Into::into)
    .map_err(|error| local_task_error(&error.code, true))
}

#[tauri::command]
pub(crate) async fn prepare_local_removal(
    request: PrepareLocalRemovalRequestDto,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<RemovalPlanDto, ApiErrorDto> {
    require_main_window(&window)?;
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || {
        controller.prepare_local_removal(&request.snapshot_id, &request.instance_ids)
    })
    .await
    .map_err(|_| local_task_error("local_removal_prepare_task_failed", true))?
    .map(Into::into)
    .map_err(|error| local_task_error(&error.code, true))
}

#[tauri::command]
pub(crate) async fn apply_local_removal(
    request: ApplyLocalRemovalRequestDto,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<LocalRemovalOutcomeDto, ApiErrorDto> {
    require_main_window(&window)?;
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || {
        controller.apply_local_removal(&request.plan_id, &request.plan_digest, request.confirmed)
    })
    .await
    .map_err(|_| local_task_error("local_removal_apply_task_failed", true))?
    .map(Into::into)
    .map_err(|error| local_task_error(&error.code, true))
}

#[tauri::command]
pub(crate) async fn reconcile_local_removal(
    request: ReconcileLocalRemovalRequestDto,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<LocalRemovalReconciliationDto, ApiErrorDto> {
    require_main_window(&window)?;
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || {
        controller.reconcile_local_removal(
            &request.snapshot_id,
            &request.expected_attempt_id,
            &request.instance_ids,
        )
    })
    .await
    .map_err(|_| local_task_error("local_removal_reconciliation_task_failed", true))?
    .map(Into::into)
    .map_err(|error| local_task_error(&error.code, true))
}

#[tauri::command]
pub(crate) async fn get_ai_provider_config(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<ProviderConfigStateDto, ApiErrorDto> {
    require_main_window(&window)?;
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || controller.ai_provider_state())
        .await
        .map_err(|_| ai_task_error("ai_provider_task_failed"))?
        .map(Into::into)
        .map_err(ai_context_error)
}

#[tauri::command]
pub(crate) async fn save_ai_provider_config(
    request: SaveAiProviderConfigRequestDto,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<ProviderConfigStateDto, ApiErrorDto> {
    require_main_window(&window)?;
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || {
        controller.save_ai_provider_config(
            request.expected_provider_revision.as_deref(),
            &request.base_url,
            &request.model,
            request.api_key.as_deref(),
        )
    })
    .await
    .map_err(|_| ai_task_error("ai_provider_task_failed"))?
    .map(Into::into)
    .map_err(ai_context_error)
}

#[tauri::command]
pub(crate) async fn delete_ai_provider_key(
    request: DeleteAiProviderKeyRequestDto,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<ProviderConfigStateDto, ApiErrorDto> {
    require_main_window(&window)?;
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || {
        controller.delete_ai_provider_key(&request.provider_revision)
    })
    .await
    .map_err(|_| ai_task_error("ai_provider_task_failed"))?
    .map(Into::into)
    .map_err(ai_context_error)
}

#[tauri::command]
pub(crate) async fn explain_local_source(
    request: ExplainLocalSourceRequestDto,
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<AiExplanationDto, ApiErrorDto> {
    require_main_window(&window)?;
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || {
        controller.explain_local_source(
            request.snapshot_id,
            request.instance_id,
            request.source_revision,
            request.provider_revision,
        )
    })
    .await
    .map_err(|_| ai_task_error("ai_explanation_task_failed"))?
    .map(Into::into)
    .map_err(ai_context_error)
}

#[tauri::command]
pub(crate) async fn clone_checkout(
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<CheckoutRegistrationDto, ApiErrorDto> {
    let app_data_dir = app
        .path()
        .app_data_dir()
        .map_err(|_| checkout_error("checkout_app_data_unavailable", false))?;
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || controller.clone_default_checkout(&app_data_dir))
        .await
        .map_err(|_| checkout_error("checkout_clone_task_failed", true))?
        .map(Into::into)
        .map_err(|_| checkout_error("checkout_clone_failed", true))
}

#[tauri::command]
pub(crate) async fn register_checkout(
    checkout_path: String,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<CheckoutRegistrationDto, ApiErrorDto> {
    if checkout_path.trim().is_empty() {
        return Err(checkout_error("checkout_path_required", false));
    }
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || controller.register_checkout(checkout_path))
        .await
        .map_err(|_| checkout_error("checkout_registration_task_failed", true))?
        .map(Into::into)
        .map_err(|_| checkout_error("checkout_registration_failed", false))
}

#[tauri::command]
pub(crate) async fn pick_checkout_directory(
    window: tauri::WebviewWindow,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<CheckoutDirectoryPickerOutcomeDto, ApiErrorDto> {
    require_main_window(&window)?;
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || controller.pick_checkout_directory())
        .await
        .map(Into::into)
        .map_err(|_| checkout_error("checkout_picker_task_failed", true))
}

#[tauri::command]
pub(crate) async fn preview_install(
    checkout_id: String,
    request: InstallPreviewRequestDto,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<PreviewInstallResponseDto, ApiErrorDto> {
    if checkout_id.trim().is_empty()
        || request.sot_snapshot_id.trim().is_empty()
        || request.profile_id.trim().is_empty()
        || request.target_root.trim().is_empty()
        || request.target_ids.is_empty()
        || request
            .target_ids
            .iter()
            .any(|target| target.trim().is_empty())
        || request.target_ids.iter().collect::<BTreeSet<_>>().len() != request.target_ids.len()
    {
        return Err(install_error("install_request_invalid", false));
    }
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || {
        controller.preview_install(
            &checkout_id,
            &request.sot_snapshot_id,
            &request.profile_id,
            request.scope.as_str(),
            PathBuf::from(request.target_root),
            request.target_ids.into_iter().collect(),
        )
    })
    .await
    .map_err(|_| install_error("install_preview_task_failed", true))?
    .map(Into::into)
    .map_err(|code| install_error(&code, install_retryable(&code)))
}

#[tauri::command]
pub(crate) async fn apply_install(
    preview_id: String,
    approvals: ApplyInstallApprovalsDto,
    state: tauri::State<'_, Arc<AppController>>,
) -> Result<ApplyInstallResponseDto, ApiErrorDto> {
    if preview_id.trim().is_empty() || approvals.semantic_fingerprint.len() != 64 {
        return Err(install_error("install_request_invalid", false));
    }
    let controller = Arc::clone(state.inner());
    tauri::async_runtime::spawn_blocking(move || {
        controller.apply_install(
            &preview_id,
            crate::contexts::install::InstallApprovals {
                confirmed: approvals.confirmed,
                semantic_fingerprint: approvals.semantic_fingerprint,
                overwrite: approvals.overwrite,
                allow_runtime_hooks: approvals.allow_runtime_hooks,
                adopt_management: approvals.adopt_management,
                replace_managed: approvals.replace_managed,
            },
        )
    })
    .await
    .map_err(|_| install_error("install_apply_task_failed", true))?
    .map(Into::into)
    .map_err(|code| install_error(&code, install_retryable(&code)))
}

fn require_main_window(window: &tauri::WebviewWindow) -> Result<(), ApiErrorDto> {
    if window.label() == "main" {
        Ok(())
    } else {
        Err(ApiErrorDto::feature_unavailable(
            "command_window_not_allowed",
            "이 창에서는 이 작업을 실행할 수 없습니다.",
        ))
    }
}

fn context_error(error: crate::contexts::appearance::AppearanceContextError) -> ApiErrorDto {
    ApiErrorDto {
        code: error.code.to_owned(),
        retryable: true,
        safe_message: error.safe_message.to_owned(),
        details: None,
    }
}

fn layout_context_error(
    error: crate::contexts::layout::WorkspaceLayoutContextError,
) -> ApiErrorDto {
    ApiErrorDto {
        code: error.code.to_owned(),
        retryable: true,
        safe_message: error.safe_message.to_owned(),
        details: None,
    }
}

fn typography_context_error(
    error: crate::contexts::typography::TypographyContextError,
) -> ApiErrorDto {
    ApiErrorDto {
        code: error.code.to_owned(),
        retryable: true,
        safe_message: error.safe_message.to_owned(),
        details: None,
    }
}

fn task_error(code: &'static str) -> ApiErrorDto {
    ApiErrorDto {
        code: code.to_owned(),
        retryable: true,
        safe_message: "외관 상태 처리 작업이 중단되었습니다.".to_owned(),
        details: None,
    }
}

fn sot_task_error(code: &'static str) -> ApiErrorDto {
    ApiErrorDto {
        code: code.to_owned(),
        retryable: true,
        safe_message: "SoT 상태를 불러오지 못했습니다.".to_owned(),
        details: None,
    }
}

fn checkout_error(code: &'static str, retryable: bool) -> ApiErrorDto {
    ApiErrorDto {
        code: code.to_owned(),
        retryable,
        safe_message: "HarnessKit checkout을 준비하지 못했습니다.".to_owned(),
        details: None,
    }
}

fn local_task_error(code: &str, retryable: bool) -> ApiErrorDto {
    ApiErrorDto {
        code: code.to_owned(),
        retryable,
        safe_message: "로컬 하네스 스캔 상태를 처리하지 못했습니다.".to_owned(),
        details: None,
    }
}

fn project_ignore_task_error(error: &LocalContextError) -> ApiErrorDto {
    let details = (error.line.is_some() || error.column.is_some()).then(|| ApiErrorDetailsDto {
        active_chunk_index: None,
        line: error.line,
        column: error.column,
    });
    let safe_message = match error.code.as_str() {
        "project_ignore_invalid" => "제외 규칙 형식이 올바르지 않습니다.",
        "project_ignore_stale" => "제외 규칙이 변경되어 다시 불러와야 합니다.",
        "project_ignore_unreadable" | "project_ignore_changed_during_read" => {
            "제외 규칙을 안전하게 읽지 못했습니다."
        }
        "project_root_changed" => "프로젝트 위치가 스캔 이후 변경되었습니다.",
        _ => "제외 규칙을 처리하지 못했습니다.",
    };
    ApiErrorDto {
        code: error.code.clone(),
        retryable: false,
        safe_message: safe_message.to_owned(),
        details,
    }
}

fn local_source_error(error: SourceInspectionError) -> ApiErrorDto {
    let retryable = matches!(
        error.code,
        "preview_busy"
            | "already_reading"
            | "operation_busy"
            | "source_read_failed"
            | "local_read_capacity_exhausted"
    );
    let safe_message = match error.code {
        "preview_busy" | "already_reading" | "operation_busy" => {
            "다른 파일 작업이 끝난 뒤 다시 시도하세요."
        }
        "source_invalid_utf8" => "이 파일은 UTF-8 원문으로 표시할 수 없습니다.",
        "source_binary" => "바이너리 파일은 원문 미리보기에 표시할 수 없습니다.",
        "source_chunk_bound_exceeded" => "이 원문 구간은 안전한 표시 크기를 초과했습니다.",
        "source_stale" | "preview_session_stale" | "preview_stale" => {
            "파일이 변경되었습니다. 항목을 다시 선택해 최신 원문을 여세요."
        }
        "chunk_out_of_range" => "요청한 원문 구간을 찾을 수 없습니다.",
        _ => "로컬 원문을 읽지 못했습니다.",
    };
    ApiErrorDto {
        code: error.code.to_owned(),
        retryable,
        safe_message: safe_message.to_owned(),
        details: error
            .active_chunk_index
            .map(|active_chunk_index| ApiErrorDetailsDto {
                active_chunk_index: Some(active_chunk_index),
                line: None,
                column: None,
            }),
    }
}

fn ai_task_error(code: &'static str) -> ApiErrorDto {
    ApiErrorDto {
        code: code.to_owned(),
        retryable: true,
        safe_message: "AI 설명 작업이 중단되었습니다. 다시 시도하세요.".to_owned(),
        details: None,
    }
}

fn ai_context_error(error: crate::contexts::ai::AiExplanationError) -> ApiErrorDto {
    let code = error.code();
    let retryable = matches!(
        code,
        "already_running"
            | "provider_request_failed"
            | "provider_transport_unavailable"
            | "source_read_failed"
            | "source_stale"
            | "provider_stale"
    );
    let safe_message = match code {
        "provider_config_invalid" => "Base URL과 모델 값을 확인하세요.",
        "provider_stale" => "AI 설정이 변경되었습니다. 최신 설정으로 다시 시도하세요.",
        "provider_store_unsafe" | "provider_store_unavailable" => {
            "AI 설명 설정을 안전하게 불러오지 못했습니다."
        }
        "provider_auth_invalid" | "provider_auth_failed" => "API key를 확인한 뒤 다시 시도하세요.",
        "provider_response_invalid" => "AI provider 응답 형식을 확인할 수 없습니다.",
        "provider_request_failed" | "provider_transport_unavailable" => {
            "AI provider에 연결하지 못했습니다."
        }
        "source_too_large_for_ai" => "이 모델이 전체 파일을 처리할 수 없습니다.",
        "source_stale" => "파일이 변경되었습니다. 최신 원문을 다시 여세요.",
        "already_running" => "이 파일의 AI 설명을 이미 생성 중입니다.",
        _ => "AI 설명을 생성하지 못했습니다.",
    };
    ApiErrorDto {
        code: code.to_owned(),
        retryable,
        safe_message: safe_message.to_owned(),
        details: None,
    }
}

fn install_error(code: &str, retryable: bool) -> ApiErrorDto {
    ApiErrorDto {
        code: code.to_owned(),
        retryable,
        safe_message: "설치 계획을 처리하지 못했습니다. Preview 상태와 대상을 확인하세요."
            .to_owned(),
        details: None,
    }
}

fn install_retryable(code: &str) -> bool {
    matches!(
        code,
        "operation_busy"
            | "install_preview_task_failed"
            | "install_apply_task_failed"
            | "install_plan_timeout"
            | "install_plan_launch_failed"
    )
}

#[cfg(test)]
mod tests {
    use super::{
        project_ignore_task_error, validate_bootstrap_ui_probe, workspace_layout_probe,
        BootstrapUiProbeDto, LocalContextError,
    };
    use crate::api::dto::layout::{
        CollapsedPairDto, FrameDto, LayoutModeDto, PaneFramesDto, PreferredPairDto,
    };
    use crate::api::dto::typography::TypographyPresetDto;
    use crate::contexts::appearance::ResolvedMode;

    fn ready_probe() -> BootstrapUiProbeDto {
        BootstrapUiProbeDto {
            child_element_count: 1,
            text_length: 24,
            desktop_app_present: true,
            resolved_mode: "dark".to_owned(),
            viewport_width: 1200.0,
            viewport_height: 768.0,
            desktop_width: 1200.0,
            desktop_height: 768.0,
            layout_visible: true,
            applied_layout_revision: 0,
            applied_typography_revision: 0,
            applied_preferred_pair: PreferredPairDto {
                preferred_left_width_px: 304,
                preferred_right_width_px: 368,
            },
            applied_collapsed_pair: CollapsedPairDto {
                left_collapsed: false,
                right_collapsed: false,
            },
            applied_typography_preset: TypographyPresetDto::Default,
            layout_mode: LayoutModeDto::ThreePane,
            shell_frame: FrameDto {
                x: 0.0,
                y: 0.0,
                width: 1200.0,
                height: 768.0,
            },
            pane_frames: PaneFramesDto {
                left: Some(FrameDto {
                    x: 0.0,
                    y: 0.0,
                    width: 304.0,
                    height: 768.0,
                }),
                center: FrameDto {
                    x: 316.0,
                    y: 0.0,
                    width: 504.0,
                    height: 768.0,
                },
                right: Some(FrameDto {
                    x: 832.0,
                    y: 0.0,
                    width: 368.0,
                    height: 768.0,
                }),
            },
            active_separator_count: 2,
            disabled_separator_count: 0,
        }
    }

    #[test]
    fn bootstrap_ui_probe_accepts_only_a_visible_finite_shell() {
        assert_eq!(
            validate_bootstrap_ui_probe(&ready_probe()),
            Some(ResolvedMode::Dark)
        );

        let mut invalid = ready_probe();
        invalid.desktop_width = 0.0;
        assert_eq!(validate_bootstrap_ui_probe(&invalid), None);

        let mut invalid = ready_probe();
        invalid.viewport_height = f64::NAN;
        assert_eq!(validate_bootstrap_ui_probe(&invalid), None);

        let mut invalid = ready_probe();
        invalid.desktop_height = f64::INFINITY;
        assert_eq!(validate_bootstrap_ui_probe(&invalid), None);

        let mut invalid = ready_probe();
        invalid.layout_visible = false;
        assert_eq!(validate_bootstrap_ui_probe(&invalid), None);

        let mut invalid = ready_probe();
        invalid.resolved_mode = "system".to_owned();
        assert_eq!(validate_bootstrap_ui_probe(&invalid), None);
    }

    #[test]
    fn workspace_layout_probe_preserves_collapsed_frame_absence_and_typography_preset() {
        let mut probe = ready_probe();
        probe.applied_collapsed_pair.left_collapsed = true;
        probe.pane_frames.left = None;
        probe.active_separator_count = 1;
        probe.applied_typography_revision = 3;
        probe.applied_typography_preset = TypographyPresetDto::Large;

        let converted = workspace_layout_probe(&probe);

        assert!(converted.geometry.left_collapsed);
        assert!(!converted.geometry.right_collapsed);
        assert!(converted.geometry.panes.left.is_none());
        assert!(converted.geometry.panes.right.is_some());
        assert_eq!(
            converted.typography_preset,
            crate::contexts::typography::TypographyPreset::Large
        );
        assert_eq!(converted.applied_typography_revision, 3);
        assert_eq!(converted.geometry.active_separator_count, 1);
    }

    #[test]
    fn project_ignore_validation_error_preserves_line_and_column_in_api_details() {
        let error = project_ignore_task_error(&LocalContextError {
            code: "project_ignore_invalid".to_string(),
            line: Some(3),
            column: Some(1),
        });
        let value = serde_json::to_value(error).expect("error DTO");

        assert_eq!(value["details"]["line"], 3);
        assert_eq!(value["details"]["column"], 1);
        assert!(value["details"].get("active_chunk_index").is_none());
    }
}
