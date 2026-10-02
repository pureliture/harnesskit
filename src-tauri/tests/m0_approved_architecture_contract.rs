use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use harness_desktop_lib::api::dto::ai::{
    DeleteAiProviderKeyRequestDto, ExplainLocalSourceRequestDto, SaveAiProviderConfigRequestDto,
};
use harness_desktop_lib::api::dto::local::{
    ApplyLocalRemovalRequestDto, CloseLocalSourcePreviewRequestDto,
    LocalSourceMarkdownBlockKindDto, MarkdownBlockFragmentDto, OpenLocalSourcePreviewRequestDto,
    PrepareLocalRemovalRequestDto, ReadLocalSourcePreviewChunkRequestDto,
    ReconcileLocalRemovalRequestDto,
};
use serde::de::DeserializeOwned;

const EXPECTED_COMMANDS: [&str; 34] = [
    "get_bootstrap_state",
    "set_appearance_mode",
    "set_typography_preset",
    "set_workspace_layout",
    "complete_bootstrap",
    "get_sot_session_state",
    "clone_checkout",
    "register_checkout",
    "pick_checkout_directory",
    "load_sot_snapshot",
    "read_imported_skill",
    "save_imported_skill",
    "preview_component_import",
    "confirm_component_import",
    "get_local_scan_state",
    "start_local_scan",
    "get_project_ignore",
    "save_project_ignore_and_rescan",
    "get_correlation_projection",
    "query_local_instances",
    "get_local_instance_detail",
    "open_local_source_preview",
    "read_local_source_preview_chunk",
    "close_local_source_preview",
    "act_on_local_instance",
    "prepare_local_removal",
    "apply_local_removal",
    "reconcile_local_removal",
    "get_ai_provider_config",
    "save_ai_provider_config",
    "delete_ai_provider_key",
    "explain_local_source",
    "preview_install",
    "apply_install",
];

fn manifest_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn assert_file(path: &Path) {
    assert!(
        path.is_file(),
        "missing approved module: {}",
        path.display()
    );
}

fn rust_files_recursive(root: &Path) -> Vec<PathBuf> {
    let mut pending = vec![root.to_path_buf()];
    let mut files = Vec::new();
    while let Some(directory) = pending.pop() {
        for entry in std::fs::read_dir(directory).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                pending.push(path);
            } else if path.extension().and_then(|value| value.to_str()) == Some("rs") {
                files.push(path);
            }
        }
    }
    files.sort();
    files
}

fn first_party_files_recursive_with_extension(root: &Path, extension: &str) -> Vec<PathBuf> {
    let mut pending = vec![root.to_path_buf()];
    let mut files = Vec::new();
    while let Some(directory) = pending.pop() {
        for entry in std::fs::read_dir(directory).unwrap() {
            let path = entry.unwrap().path();
            if path.is_dir() {
                if path.file_name().and_then(|value| value.to_str()) == Some("node_modules") {
                    continue;
                }
                pending.push(path);
            } else if path.extension().and_then(|value| value.to_str()) == Some(extension) {
                files.push(path);
            }
        }
    }
    files.sort();
    files
}

fn declared_fields(source: &str, name: &str) -> BTreeSet<String> {
    source
        .split_once(&format!("pub struct {name}"))
        .and_then(|(_, tail)| tail.split_once('}'))
        .map(|(body, _)| {
            body.lines()
                .filter_map(|line| line.trim().strip_prefix("pub "))
                .filter_map(|line| line.split_once(':').map(|(field, _)| field.to_owned()))
                .collect()
        })
        .unwrap_or_else(|| panic!("missing struct fields for {name}"))
}

fn section<'a>(source: &'a str, start: &str, end: &str) -> &'a str {
    source
        .split_once(start)
        .and_then(|(_, tail)| tail.split_once(end))
        .map(|(body, _)| body)
        .unwrap_or_else(|| panic!("missing section {start} .. {end}"))
}

fn command_section<'a>(source: &'a str, command: &str) -> &'a str {
    source
        .split_once(&format!("fn {command}"))
        .and_then(|(_, tail)| tail.split_once("#[tauri::command]"))
        .map(|(body, _)| body)
        .unwrap_or_else(|| panic!("missing bounded command section for {command}"))
}

fn normalized_command_parameters(source: &str, command: &str) -> String {
    source
        .split_once(&format!("fn {command}"))
        .and_then(|(_, tail)| tail.split_once(") ->"))
        .map(|(parameters, _)| format!("{parameters})"))
        .map(|parameters| parameters.split_whitespace().collect())
        .unwrap_or_else(|| panic!("missing command parameters for {command}"))
}

fn frontend_invoke_commands(source: &str) -> Vec<String> {
    source
        .split("invokeCommand(\"")
        .skip(1)
        .filter_map(|tail| tail.split_once('"').map(|(command, _)| command.to_owned()))
        .collect()
}

fn handler_commands(source: &str) -> Vec<String> {
    section(source, "tauri::generate_handler![", "])")
        .lines()
        .filter_map(|line| {
            line.trim()
                .trim_end_matches(',')
                .strip_prefix("api::commands::")
                .map(str::to_owned)
        })
        .collect()
}

fn assert_rejects_unknown_field<T: DeserializeOwned>(valid: serde_json::Value) {
    assert!(serde_json::from_value::<T>(valid.clone()).is_ok());
    let mut forbidden = valid;
    forbidden
        .as_object_mut()
        .expect("request fixture must be an object")
        .insert(
            "sourceBody".to_owned(),
            serde_json::Value::String("must-not-pass".to_owned()),
        );
    assert!(serde_json::from_value::<T>(forbidden).is_err());
}

#[test]
fn approved_context_module_roots_are_present() {
    let root = manifest_dir();
    let lib = std::fs::read_to_string(root.join("src/lib.rs")).unwrap();

    for declaration in [
        "pub mod api;",
        "mod app_controller;",
        "pub mod contexts;",
        "mod operation_coordinator;",
        "mod projection;",
        "pub mod support;",
    ] {
        assert!(
            lib.contains(declaration),
            "lib.rs must declare approved module root {declaration}"
        );
    }

    for relative in [
        "src/api/mod.rs",
        "src/api/commands.rs",
        "src/api/dto/mod.rs",
        "src/api/dto/appearance.rs",
        "src/api/dto/bootstrap.rs",
        "src/api/dto/ai.rs",
        "src/api/dto/common.rs",
        "src/api/dto/install.rs",
        "src/api/dto/layout.rs",
        "src/api/dto/local.rs",
        "src/api/dto/sot.rs",
        "src/app_controller.rs",
        "src/contexts/mod.rs",
        "src/contexts/appearance/mod.rs",
        "src/contexts/layout/mod.rs",
        "src/contexts/layout/context.rs",
        "src/contexts/layout/store.rs",
        "src/contexts/ai/mod.rs",
        "src/contexts/local/mod.rs",
        "src/contexts/local/source_inspection.rs",
        "src/contexts/local/source_session.rs",
        "src/contexts/local/source_span.rs",
        "src/contexts/local/yaml_span.rs",
        "src/contexts/sot/mod.rs",
        "src/contexts/sot/checkout_picker.rs",
        "src/operation_coordinator.rs",
        "src/projection/mod.rs",
        "src/support/mod.rs",
        "src/support/content_digest.rs",
    ] {
        assert_file(&root.join(relative));
    }
}

#[test]
fn approved_custom_command_plan_is_exact() {
    let root = manifest_dir();
    let lib = std::fs::read_to_string(root.join("src/lib.rs")).unwrap();
    let frontend = std::fs::read_to_string(
        root.parent()
            .expect("src-tauri must live below repository root")
            .join("src-frontend/backend.js"),
    )
    .unwrap();
    let design = std::fs::read_to_string(
        root.parent()
            .expect("src-tauri must live below repository root")
            .join("docs/harness-requirements/20260709-tauri-harness-desktop-app/design.md"),
    )
    .unwrap();
    let expected = EXPECTED_COMMANDS
        .into_iter()
        .map(str::to_owned)
        .collect::<BTreeSet<_>>();

    let handler = handler_commands(&lib);
    let frontend = frontend_invoke_commands(&frontend);
    let import_design = std::fs::read_to_string(root.parent().unwrap().join("docs/harness-requirements/20260709-tauri-harness-desktop-app/component-import-management-design.md")).unwrap();
    let design = format!("{design}\n{import_design}");
    let mut design_commands = section(&design, "## Public Command Contract", "Progress event")
        .lines()
        .filter_map(|line| line.strip_prefix("| `"))
        .filter_map(|line| line.split_once('`').map(|(command, _)| command.to_owned()))
        .collect::<Vec<_>>();

    design_commands.extend(
        section(
            &import_design,
            "## 가져오기 Command Contract",
            "## 구현 근거",
        )
        .lines()
        .filter_map(|line| line.strip_prefix("| `"))
        .filter_map(|line| line.split_once('`').map(|(command, _)| command.to_owned())),
    );
    for (surface, actual) in [
        ("Tauri handler", handler),
        ("frontend invoke", frontend),
        ("design command table", design_commands),
    ] {
        let actual_set = actual.iter().cloned().collect::<BTreeSet<_>>();
        assert_eq!(
            actual.len(),
            EXPECTED_COMMANDS.len(),
            "{surface} command count drift"
        );
        assert_eq!(
            actual_set.len(),
            actual.len(),
            "{surface} has duplicate commands"
        );
        assert_eq!(actual_set, expected, "{surface} command set drift");
    }
}

#[test]
fn approved_amendment_dependency_seams_are_one_way() {
    let root = manifest_dir();
    let picker = std::fs::read_to_string(root.join("src/contexts/sot/checkout_picker.rs"))
        .expect("checkout picker port module must exist");
    assert!(picker.contains("trait CheckoutDirectoryPickerPort"));
    assert!(picker.contains("Selected"));
    assert!(picker.contains("Cancelled"));
    assert!(picker.contains("Failed"));
    assert!(picker.contains("PickerSafeReason"));
    assert!(!picker.contains("Failed(String)"));
    for forbidden in [
        "crate::checkout",
        "crate::controller",
        "RegisteredCheckout",
        "RepoStatus",
        "SotSnapshot",
        "std::fs",
        "canonicalize",
        ".git",
        "Command",
        "register_checkout",
        "RepoInspector",
    ] {
        assert!(
            !picker.contains(forbidden),
            "picker selection port must not own checkout validation: {forbidden}"
        );
    }

    let local_source_modules = [
        "src/contexts/local/source_inspection.rs",
        "src/contexts/local/source_session.rs",
        "src/contexts/local/source_span.rs",
        "src/contexts/local/yaml_span.rs",
    ]
    .into_iter()
    .map(|relative| std::fs::read_to_string(root.join(relative)).unwrap())
    .collect::<String>();
    for forbidden in [
        "contexts::ai",
        "OpenAiCompatibleTransport",
        "provider_store",
        "reqwest",
        "ureq",
        "hyper",
        "std::net",
        "TcpStream",
    ] {
        assert!(
            !local_source_modules.contains(forbidden),
            "Local source inspection must stay network/provider free: {forbidden}"
        );
    }

    let ai_root = root.join("src/contexts/ai");
    for path in rust_files_recursive(&ai_root) {
        let source = std::fs::read_to_string(&path).unwrap();
        for forbidden in [
            "contexts::local",
            "contexts::sot",
            "contexts::install",
            "api::dto::local",
            "api::dto::sot",
            "api::dto::install",
            "app_controller",
            "LocalContext",
            "SotContext",
            "InstallCoordinator",
        ] {
            assert!(
                !source.contains(forbidden),
                "AI context must receive revision-bound input instead of domain imports: {} -> {forbidden}",
                path.display()
            );
        }
    }
}

#[test]
fn workspace_layout_context_has_a_separate_minimal_boundary() {
    let root = manifest_dir();
    let module = std::fs::read_to_string(root.join("src/contexts/layout/mod.rs")).unwrap();
    let context = std::fs::read_to_string(root.join("src/contexts/layout/context.rs")).unwrap();
    let store = std::fs::read_to_string(root.join("src/contexts/layout/store.rs")).unwrap();

    for required in [
        "WorkspaceLayoutContext",
        "WorkspaceLayoutContextError",
        "WorkspaceLayoutState",
        "WorkspaceLayoutUpdate",
    ] {
        assert!(
            module.contains(required),
            "layout module must export {required}"
        );
    }
    for required in [
        "pub struct WorkspaceLayoutContext",
        "pub struct WorkspaceLayoutContextError",
        "pub struct WorkspaceLayoutState",
        "pub struct WorkspaceLayoutUpdate",
        "pub fn new(",
        "pub fn state(",
        "pub fn set_preference(",
    ] {
        assert!(
            context.contains(required),
            "layout context must define {required}"
        );
    }
    for required in [
        "pub trait WorkspaceLayoutPreferencePort",
        "pub struct WorkspaceLayoutStore",
        "pub fn new(",
        "pub fn load(",
        "pub fn save(",
    ] {
        assert!(
            store.contains(required),
            "layout store must define {required}"
        );
    }

    let layout_modules = rust_files_recursive(&root.join("src/contexts/layout"))
        .into_iter()
        .map(|path| std::fs::read_to_string(path).unwrap())
        .collect::<String>();
    for forbidden in [
        "contexts::appearance",
        "contexts::typography",
        "contexts::sot",
        "contexts::local",
        "contexts::install",
        "tauri::",
        "WebviewWindow",
        "graph",
    ] {
        assert!(
            !layout_modules.contains(forbidden),
            "layout context must stay presentation-preference only: {forbidden}"
        );
    }
}

#[test]
fn workspace_layout_dtos_own_the_exact_composite_bootstrap_contract() {
    let root = manifest_dir();
    let appearance = std::fs::read_to_string(root.join("src/api/dto/appearance.rs")).unwrap();
    let layout = std::fs::read_to_string(root.join("src/api/dto/layout.rs")).unwrap();
    let bootstrap = std::fs::read_to_string(root.join("src/api/dto/bootstrap.rs")).unwrap();

    for moved in [
        "BootstrapStateDto",
        "BootstrapUiProbeDto",
        "BootstrapInstructionDto",
        "BootstrapCompletionDto",
    ] {
        assert!(
            !appearance.contains(moved),
            "appearance DTO must not own composite bootstrap type {moved}"
        );
        assert!(bootstrap.contains(moved), "bootstrap DTO must own {moved}");
    }

    assert_eq!(
        declared_fields(&layout, "WorkspaceLayoutStateDto"),
        [
            "preferred_left_width_px",
            "preferred_right_width_px",
            "left_collapsed",
            "right_collapsed",
            "revision",
            "persisted",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect()
    );
    assert_eq!(
        declared_fields(&layout, "SetWorkspaceLayoutRequestDto"),
        [
            "expected_layout_revision",
            "preferred_left_width_px",
            "preferred_right_width_px",
            "left_collapsed",
            "right_collapsed",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect()
    );
    assert_eq!(
        declared_fields(&layout, "PaneFramesDto"),
        ["left", "center", "right"]
            .into_iter()
            .map(str::to_owned)
            .collect()
    );
    assert_eq!(
        declared_fields(&bootstrap, "BootstrapUiProbeDto"),
        [
            "child_element_count",
            "text_length",
            "desktop_app_present",
            "resolved_mode",
            "viewport_width",
            "viewport_height",
            "desktop_width",
            "desktop_height",
            "layout_visible",
            "applied_layout_revision",
            "applied_typography_revision",
            "applied_preferred_pair",
            "applied_collapsed_pair",
            "applied_typography_preset",
            "layout_mode",
            "shell_frame",
            "pane_frames",
            "active_separator_count",
            "disabled_separator_count",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect()
    );
    assert_eq!(
        declared_fields(&bootstrap, "BootstrapStateDto"),
        [
            "appearance",
            "workspace_layout",
            "typography",
            "app_version",
            "appearance_diagnostic",
            "workspace_layout_diagnostic",
            "typography_diagnostic",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect()
    );
    assert_eq!(
        declared_fields(&bootstrap, "BootstrapCompletionDto"),
        [
            "instruction",
            "appearance",
            "workspace_layout",
            "typography"
        ]
        .into_iter()
        .map(str::to_owned)
        .collect()
    );
}

#[test]
fn approved_amendment_commands_are_main_window_only_and_strictly_typed() {
    let root = manifest_dir();
    let commands = std::fs::read_to_string(root.join("src/api/commands.rs")).unwrap();
    let local_dto = std::fs::read_to_string(root.join("src/api/dto/local.rs")).unwrap();
    let ai_dto = std::fs::read_to_string(root.join("src/api/dto/ai.rs")).unwrap();

    for command in [
        "pick_checkout_directory",
        "open_local_source_preview",
        "read_local_source_preview_chunk",
        "close_local_source_preview",
        "prepare_local_removal",
        "apply_local_removal",
        "reconcile_local_removal",
        "get_ai_provider_config",
        "save_ai_provider_config",
        "delete_ai_provider_key",
        "explain_local_source",
    ] {
        let body = command_section(&commands, command);
        assert!(
            body.contains("window: tauri::WebviewWindow"),
            "{command} must receive the invoking WebviewWindow"
        );
        assert!(
            body.contains("require_main_window(&window)?"),
            "{command} must reject non-main windows"
        );
        assert!(
            body.contains("state: tauri::State<'_, Arc<AppController>>"),
            "{command} must reserve the AppController injection seam"
        );
        let executable = body
            .split_once('{')
            .map(|(_, executable)| executable)
            .expect("command body must exist");
        let guard = executable
            .find("require_main_window(&window)?")
            .expect("main-window guard must exist");
        for state_access in ["state.inner()", "Arc::clone(state.inner())"] {
            if let Some(access) = executable.find(state_access) {
                assert!(
                    guard < access,
                    "{command} must authorize before controller access"
                );
            }
        }
        assert!(
            !body.contains("log::"),
            "{command} must not log sensitive inputs"
        );
    }

    for (command, request) in [
        (
            "open_local_source_preview",
            "OpenLocalSourcePreviewRequestDto",
        ),
        (
            "read_local_source_preview_chunk",
            "ReadLocalSourcePreviewChunkRequestDto",
        ),
        (
            "close_local_source_preview",
            "CloseLocalSourcePreviewRequestDto",
        ),
        ("prepare_local_removal", "PrepareLocalRemovalRequestDto"),
        ("apply_local_removal", "ApplyLocalRemovalRequestDto"),
        ("reconcile_local_removal", "ReconcileLocalRemovalRequestDto"),
        ("save_ai_provider_config", "SaveAiProviderConfigRequestDto"),
        ("delete_ai_provider_key", "DeleteAiProviderKeyRequestDto"),
        ("explain_local_source", "ExplainLocalSourceRequestDto"),
    ] {
        assert!(
            command_section(&commands, command).contains(&format!("request: {request}")),
            "{command} must bind the approved strict request DTO {request}"
        );
    }

    for (request, expected_fields) in [
        (
            "OpenLocalSourcePreviewRequestDto",
            &["snapshot_id", "instance_id", "view_generation"][..],
        ),
        (
            "ReadLocalSourcePreviewChunkRequestDto",
            &["preview_session_id", "source_revision", "chunk_index"][..],
        ),
        (
            "CloseLocalSourcePreviewRequestDto",
            &["view_generation", "preview_session_id"][..],
        ),
        (
            "PrepareLocalRemovalRequestDto",
            &["snapshot_id", "instance_ids"][..],
        ),
        (
            "ApplyLocalRemovalRequestDto",
            &["plan_id", "plan_digest", "confirmed"][..],
        ),
        (
            "ReconcileLocalRemovalRequestDto",
            &["snapshot_id", "expected_attempt_id", "instance_ids"][..],
        ),
    ] {
        assert_eq!(
            declared_fields(&local_dto, request),
            expected_fields.iter().copied().map(str::to_owned).collect(),
            "{request} field set drift"
        );
    }
    for (request, expected_fields) in [
        (
            "SaveAiProviderConfigRequestDto",
            &["expected_provider_revision", "base_url", "model", "api_key"][..],
        ),
        ("DeleteAiProviderKeyRequestDto", &["provider_revision"][..]),
        (
            "ExplainLocalSourceRequestDto",
            &[
                "snapshot_id",
                "instance_id",
                "source_revision",
                "provider_revision",
            ][..],
        ),
    ] {
        assert_eq!(
            declared_fields(&ai_dto, request),
            expected_fields.iter().copied().map(str::to_owned).collect(),
            "{request} field set drift"
        );
    }

    for no_input_command in ["pick_checkout_directory", "get_ai_provider_config"] {
        assert_eq!(
            normalized_command_parameters(&commands, no_input_command),
            "(window:tauri::WebviewWindow,state:tauri::State<'_,Arc<AppController>>,)".to_owned(),
            "{no_input_command} must accept only the invoking window and AppController state"
        );
    }

    let provider_output = section(
        &ai_dto,
        "pub enum ProviderConfigStateDto",
        "pub struct AiExplanationDto",
    );
    let explanation_output = ai_dto
        .split_once("pub struct AiExplanationDto")
        .map(|(_, output)| output)
        .unwrap();
    for sensitive_output in ["api_key:", "source_body:", "source_text:"] {
        assert!(
            !provider_output.contains(sensitive_output)
                && !explanation_output.contains(sensitive_output),
            "AI output DTO must not expose {sensitive_output}"
        );
    }
    assert!(
        !ai_dto.contains("#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]\npub struct AiExplanationDto"),
        "AI explanation content must not derive Debug"
    );
    assert!(
        !local_dto.contains("#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]\npub struct LocalSourcePreviewChunkDto"),
        "source chunk content must not derive Debug"
    );
    assert!(
        !local_dto.contains("#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]\npub struct LocalSourcePreviewHeaderDto"),
        "source header path must not derive raw Debug"
    );
}

#[test]
fn strict_request_dtos_reject_every_unapproved_field_at_deserialization() {
    assert_rejects_unknown_field::<OpenLocalSourcePreviewRequestDto>(serde_json::json!({
        "snapshotId": "snapshot-7",
        "instanceId": "instance-4",
        "viewGeneration": 3
    }));
    assert_rejects_unknown_field::<ReadLocalSourcePreviewChunkRequestDto>(serde_json::json!({
        "previewSessionId": "preview-session-2",
        "sourceRevision": "source-revision-8",
        "chunkIndex": 0
    }));
    assert_rejects_unknown_field::<CloseLocalSourcePreviewRequestDto>(serde_json::json!({
        "viewGeneration": 4,
        "previewSessionId": "preview-session-2"
    }));
    assert_rejects_unknown_field::<PrepareLocalRemovalRequestDto>(serde_json::json!({
        "snapshotId": "snapshot-7",
        "instanceIds": ["instance-4"]
    }));
    assert_rejects_unknown_field::<ApplyLocalRemovalRequestDto>(serde_json::json!({
        "planId": "removal-plan-2",
        "planDigest": "digest-7",
        "confirmed": true
    }));
    assert_rejects_unknown_field::<ReconcileLocalRemovalRequestDto>(serde_json::json!({
        "snapshotId": "snapshot-7",
        "expectedAttemptId": "attempt-8",
        "instanceIds": ["instance-4"]
    }));
    assert_rejects_unknown_field::<SaveAiProviderConfigRequestDto>(serde_json::json!({
        "expectedProviderRevision": null,
        "baseUrl": "https://provider.example/v1",
        "model": "model-a",
        "apiKey": "secret"
    }));
    assert_rejects_unknown_field::<DeleteAiProviderKeyRequestDto>(serde_json::json!({
        "providerRevision": "provider-revision-2"
    }));
    assert_rejects_unknown_field::<ExplainLocalSourceRequestDto>(serde_json::json!({
        "snapshotId": "snapshot-7",
        "instanceId": "instance-4",
        "sourceRevision": "source-revision-8",
        "providerRevision": "provider-revision-2"
    }));
}

#[test]
fn markdown_fragment_kind_is_a_closed_typed_allowlist() {
    for kind in [
        "heading",
        "paragraph",
        "list",
        "table",
        "blockquote",
        "thematic_break",
        "fenced_code",
        "inline_code",
        "literal_text",
    ] {
        let fragment = serde_json::json!({
            "block_id": "block-1",
            "kind": kind,
            "text": "content",
            "starts_block": true,
            "ends_block": true
        });
        assert!(serde_json::from_value::<MarkdownBlockFragmentDto>(fragment).is_ok());
    }
    assert!(
        serde_json::from_value::<LocalSourceMarkdownBlockKindDto>(serde_json::json!("script"))
            .is_err()
    );
}

#[test]
fn approved_security_surface_stays_backend_only_and_capability_neutral() {
    let root = manifest_dir();
    let capability: serde_json::Value = serde_json::from_str(
        &std::fs::read_to_string(root.join("capabilities/default.json")).unwrap(),
    )
    .unwrap();
    let config: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(root.join("tauri.conf.json")).unwrap())
            .unwrap();
    let cargo = std::fs::read_to_string(root.join("Cargo.toml")).unwrap();
    let frontend_root = root
        .parent()
        .expect("src-tauri must live below repository root")
        .join("src-frontend");

    assert_eq!(capability["windows"], serde_json::json!(["main"]));
    assert_eq!(
        capability["permissions"],
        serde_json::json!(["core:default"])
    );
    assert_eq!(
        config["app"]["security"]["csp"],
        concat!(
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self' ipc: http:",
            "//ipc.localhost; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'; media-src 'none'; worker-src 'none'"
        )
    );
    for forbidden in [
        "tauri-plugin-dialog",
        "tauri-plugin-fs",
        "tauri-plugin-shell",
        "tauri-plugin-opener",
        "tauri-plugin-http",
    ] {
        assert!(
            !cargo.contains(forbidden),
            "frontend capability must not expand through {forbidden}"
        );
    }
    for forbidden in [
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "EventSource",
        "/chat/completions",
    ] {
        for path in first_party_files_recursive_with_extension(&frontend_root, "js") {
            if path.components().any(|part| part.as_os_str() == "tests") {
                continue;
            }
            let frontend = std::fs::read_to_string(&path).unwrap();
            assert!(
                !frontend.contains(forbidden),
                "provider traffic must stay in Rust backend: {} -> {forbidden}",
                path.display()
            );
        }
    }
}

#[test]
fn packaged_runtime_counters_have_fixed_non_sensitive_command_markers() {
    let commands = std::fs::read_to_string(manifest_dir().join("src/api/commands.rs")).unwrap();

    for marker in [
        "Workspace layout update requested",
        "Local scan start requested",
        "SoT snapshot requested",
    ] {
        assert!(
            commands.contains(marker),
            "missing fixed runtime marker: {marker}"
        );
    }

    for marker in ["SoT snapshot requested", "Local scan start requested"] {
        let marker_line = format!("log::info!(\"{marker}\");");
        let marker_index = commands
            .find(&marker_line)
            .unwrap_or_else(|| panic!("missing counter marker statement: {marker}"));
        let after_marker = &commands[marker_index + marker_line.len()..];
        assert!(
            after_marker
                .trim_start()
                .starts_with("log::logger().flush();"),
            "runtime counter marker must be flushed immediately: {marker}"
        );
    }
}

#[test]
fn local_scan_contract_is_checkout_and_path_free() {
    let root = manifest_dir();
    let commands = std::fs::read_to_string(root.join("src/api/commands.rs")).unwrap();
    let signature = commands
        .split("fn start_local_scan")
        .nth(1)
        .and_then(|tail| tail.split('{').next())
        .expect("LocalCommandApi must declare start_local_scan");

    assert!(
        signature.contains("Result<StartLocalScanOutcomeDto, ApiErrorDto>"),
        "start_local_scan must expose typed Local/API outcomes: {signature}"
    );
    for forbidden in ["checkout", "path", "root"] {
        assert!(
            !signature.contains(forbidden),
            "start_local_scan input must not contain {forbidden}: {signature}"
        );
    }

    let local_root = root.join("src/contexts/local");
    for path in rust_files_recursive(&local_root) {
        let source = std::fs::read_to_string(&path).unwrap();
        for statement in source.split(';') {
            let normalized = statement.split_whitespace().collect::<String>();
            if !normalized.contains("usecrate::") {
                continue;
            }
            for forbidden in [
                "checkout",
                "registry_reader",
                "install_flow",
                "subprocess_runner",
                "app_service",
                "foreign_detector",
                "hash_engine",
                "inventory_builder",
            ] {
                assert!(
                    !normalized.contains(forbidden),
                    "{} must not import {forbidden}: {statement}",
                    path.display()
                );
            }
        }
        for forbidden in [
            "crate::checkout",
            "crate::registry_reader",
            "crate::install_flow",
            "crate::subprocess_runner",
            "AppService",
            "ForeignDetector",
            "HashEngine",
            "InventoryBuilder",
            "run_build_components",
            "dist/",
        ] {
            assert!(
                !source.contains(forbidden),
                "{} must not depend on {forbidden}",
                path.display()
            );
        }
    }

    let local_dto = std::fs::read_to_string(root.join("src/api/dto/local.rs")).unwrap();
    for forbidden in [
        "Inventory",
        "RegisteredCheckout",
        "std::path",
        "PathBuf",
        "checkout_id",
        "checkout_path",
    ] {
        assert!(
            !local_dto.contains(forbidden),
            "Local public DTO must not expose {forbidden}"
        );
    }
}

#[test]
fn approved_local_scan_shell_is_promoted_to_the_typed_m3_commands() {
    let root = manifest_dir();
    let commands = std::fs::read_to_string(root.join("src/api/commands.rs")).unwrap();
    let lib = std::fs::read_to_string(root.join("src/lib.rs")).unwrap();

    for command in ["get_local_scan_state", "start_local_scan"] {
        assert!(commands.contains(&format!("fn {command}")));
        assert!(lib.contains(&format!("api::commands::{command}")));
    }
    assert!(!commands.contains("local_scan_unavailable"));
}

#[test]
fn approved_requirement_trace_is_exact() {
    let root = manifest_dir()
        .parent()
        .expect("src-tauri must live below the repository root")
        .join("docs/harness-requirements/20260709-tauri-harness-desktop-app");
    let requirements = std::fs::read_to_string(root.join("requirements.md")).unwrap();
    let design = std::fs::read_to_string(root.join("design.md")).unwrap();

    let traceable_prefixes = [
        "LAYOUT-",
        "INSTANCE-",
        "THEME-",
        "SOT-",
        "GOV-",
        "INST-",
        "AUTH-",
        "LOCAL-",
        "ICON-",
        "MACPKG-",
        "TOOL-ID-",
        "PUBLIC-",
        "NOTICE-",
    ];
    let requirement_ids = requirements
        .lines()
        .filter_map(|line| line.trim_start().strip_prefix("- **"))
        .filter_map(|line| line.split("**").next())
        .filter(|value| {
            traceable_prefixes
                .iter()
                .any(|prefix| value.starts_with(prefix))
        })
        .map(str::to_owned)
        .collect::<Vec<_>>();
    let trace_ids = section(
        &design,
        "## Requirement Traceability",
        "### Application Shell Aliases",
    )
    .lines()
    .filter_map(|line| line.strip_prefix("| "))
    .filter_map(|line| line.split(" | ").next())
    .filter(|value| {
        traceable_prefixes
            .iter()
            .any(|prefix| value.starts_with(prefix))
    })
    .map(str::to_owned)
    .collect::<Vec<_>>();
    let app_aliases = section(
        &design,
        "### Application Shell Aliases",
        "### Cross-Cutting Release Gates",
    )
    .lines()
    .filter_map(|line| line.strip_prefix("| APP-"))
    .filter_map(|line| line.split(" | ").next())
    .collect::<Vec<_>>();

    let requirement_set = requirement_ids.iter().cloned().collect::<BTreeSet<_>>();
    let trace_set = trace_ids.iter().cloned().collect::<BTreeSet<_>>();
    let alias_set = app_aliases.iter().copied().collect::<BTreeSet<_>>();

    assert_eq!(requirement_ids.len(), 253);
    assert_eq!(
        requirement_set.len(),
        requirement_ids.len(),
        "duplicate requirement ID"
    );
    assert_eq!(trace_ids.len(), 253);
    assert_eq!(trace_set.len(), trace_ids.len(), "duplicate trace ID");
    assert_eq!(trace_set, requirement_set);
    assert_eq!(app_aliases.len(), 6);
    assert_eq!(alias_set.len(), app_aliases.len(), "duplicate APP alias");
    assert_eq!(alias_set, BTreeSet::from(["1", "2", "3", "4", "5", "6"]));
}
