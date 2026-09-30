use harness_desktop_lib::api::dto::appearance::{
    AppearanceStateDto, LogicalModeDto, ResolvedModeDto,
};
use harness_desktop_lib::api::dto::bootstrap::{
    BootstrapCompletionDto, BootstrapInstructionDto, BootstrapStateDto, BootstrapUiProbeDto,
};
use harness_desktop_lib::api::dto::layout::{
    CollapsedPairDto, FrameDto, LayoutModeDto, PaneFramesDto, PreferredPairDto,
    SetWorkspaceLayoutRequestDto, WorkspaceLayoutDiagnosticDto, WorkspaceLayoutStateDto,
    WorkspaceLayoutUpdateDto,
};
use harness_desktop_lib::api::dto::typography::{TypographyPresetDto, TypographyStateDto};
use harness_desktop_lib::contexts::layout::{
    WorkspaceLayoutDiagnostic, WorkspaceLayoutState, WorkspaceLayoutUpdate,
};

fn appearance() -> AppearanceStateDto {
    AppearanceStateDto {
        logical_mode: LogicalModeDto::System,
        resolved_mode: ResolvedModeDto::Dark,
        revision: 4,
        persisted: true,
    }
}

fn layout() -> WorkspaceLayoutStateDto {
    WorkspaceLayoutStateDto {
        preferred_left_width_px: 320,
        preferred_right_width_px: 400,
        left_collapsed: false,
        right_collapsed: true,
        revision: 9,
        persisted: false,
    }
}

fn typography() -> TypographyStateDto {
    TypographyStateDto {
        preset: TypographyPresetDto::Default,
        revision: 3,
        persisted: true,
    }
}

#[test]
fn typography_preset_wire_contract_uses_approved_pascal_case() {
    assert_eq!(
        serde_json::to_value(TypographyPresetDto::Default).unwrap(),
        serde_json::json!("Default")
    );
    assert_eq!(
        serde_json::from_value::<TypographyPresetDto>(serde_json::json!("Large")).unwrap(),
        TypographyPresetDto::Large
    );
    assert!(serde_json::from_value::<TypographyPresetDto>(serde_json::json!("default")).is_err());
}

fn frame(x: f64, y: f64, width: f64, height: f64) -> FrameDto {
    FrameDto {
        x,
        y,
        width,
        height,
    }
}

#[test]
fn layout_state_and_update_preserve_the_exact_revision_envelope() {
    let state = WorkspaceLayoutState {
        preferred_left_width_px: 320,
        preferred_right_width_px: 400,
        left_collapsed: false,
        right_collapsed: true,
        revision: 9,
        persisted: false,
    };
    let dto = WorkspaceLayoutStateDto::from(state);
    assert_eq!(dto, layout());
    assert_eq!(
        serde_json::to_value(dto).unwrap(),
        serde_json::json!({
            "preferred_left_width_px": 320,
            "preferred_right_width_px": 400,
            "left_collapsed": false,
            "right_collapsed": true,
            "revision": 9,
            "persisted": false,
        })
    );

    let update = WorkspaceLayoutUpdateDto::from(WorkspaceLayoutUpdate {
        state,
        diagnostic: Some(WorkspaceLayoutDiagnostic {
            code: "workspace_layout_preference_write_failed",
            safe_message: "레이아웃 설정 저장 실패",
        }),
    });
    assert_eq!(update.workspace_layout, layout());
    assert_eq!(
        update.diagnostic,
        Some(WorkspaceLayoutDiagnosticDto {
            code: "workspace_layout_preference_write_failed".to_owned(),
            safe_message: "레이아웃 설정 저장 실패".to_owned(),
        })
    );
}

#[test]
fn layout_command_request_is_camel_case_and_rejects_unknown_fields() {
    let json = serde_json::json!({
        "expectedLayoutRevision": 9,
        "preferredLeftWidthPx": 320,
        "preferredRightWidthPx": 400,
        "leftCollapsed": false,
        "rightCollapsed": true,
    });
    let request: SetWorkspaceLayoutRequestDto = serde_json::from_value(json.clone()).unwrap();
    assert_eq!(request.expected_layout_revision, 9);
    assert_eq!(serde_json::to_value(request).unwrap(), json);

    let mut unknown = json;
    unknown["effectiveLeftWidthPx"] = serde_json::json!(300);
    assert!(serde_json::from_value::<SetWorkspaceLayoutRequestDto>(unknown).is_err());
}

#[test]
fn composite_bootstrap_contract_serializes_both_authorities_and_strict_probe() {
    let bootstrap = BootstrapStateDto {
        appearance: appearance(),
        workspace_layout: layout(),
        typography: typography(),
        app_version: "0.1.0".to_owned(),
        appearance_diagnostic: None,
        workspace_layout_diagnostic: Some(WorkspaceLayoutDiagnosticDto {
            code: "workspace_layout_preference_unreadable".to_owned(),
            safe_message: "레이아웃 설정을 읽지 못해 기본값을 사용합니다.".to_owned(),
        }),
        typography_diagnostic: None,
    };
    let value = serde_json::to_value(&bootstrap).unwrap();
    assert_eq!(value["appearance"]["revision"], 4);
    assert_eq!(value["workspace_layout"]["revision"], 9);
    assert_eq!(value["app_version"], "0.1.0");
    assert!(value["appearance_diagnostic"].is_null());

    let probe = BootstrapUiProbeDto {
        child_element_count: 1,
        text_length: 24,
        desktop_app_present: true,
        resolved_mode: "dark".to_owned(),
        viewport_width: 1200.0,
        viewport_height: 800.0,
        desktop_width: 1200.0,
        desktop_height: 800.0,
        layout_visible: true,
        applied_layout_revision: 9,
        applied_typography_revision: 3,
        applied_preferred_pair: PreferredPairDto {
            preferred_left_width_px: 320,
            preferred_right_width_px: 400,
        },
        applied_collapsed_pair: CollapsedPairDto {
            left_collapsed: false,
            right_collapsed: true,
        },
        applied_typography_preset: TypographyPresetDto::Default,
        layout_mode: LayoutModeDto::ThreePane,
        shell_frame: frame(0.0, 0.0, 1200.0, 800.0),
        pane_frames: PaneFramesDto {
            left: Some(frame(0.0, 0.0, 320.0, 800.0)),
            center: frame(332.0, 0.0, 868.0, 800.0),
            right: None,
        },
        active_separator_count: 2,
        disabled_separator_count: 0,
    };
    let probe_json = serde_json::to_value(&probe).unwrap();
    assert_eq!(probe_json["layoutMode"], "three-pane");
    assert_eq!(probe_json["appliedLayoutRevision"], 9);
    assert_eq!(probe_json["appliedCollapsedPair"]["rightCollapsed"], true);
    assert!(probe_json["paneFrames"]["right"].is_null());
    assert_eq!(
        serde_json::from_value::<BootstrapUiProbeDto>(probe_json.clone()).unwrap(),
        probe
    );
    let mut unknown = probe_json;
    unknown["snapshotId"] = serde_json::json!("forbidden");
    assert!(serde_json::from_value::<BootstrapUiProbeDto>(unknown).is_err());
}

#[test]
fn completion_instruction_and_layout_mode_are_closed_enums() {
    let completion = BootstrapCompletionDto {
        instruction: BootstrapInstructionDto::Refresh,
        appearance: appearance(),
        workspace_layout: layout(),
        typography: typography(),
    };
    assert_eq!(
        serde_json::to_value(completion).unwrap()["instruction"],
        "Refresh"
    );
    assert_eq!(
        serde_json::to_string(&LayoutModeDto::TwoColumn).unwrap(),
        "\"two-column\""
    );
    assert_eq!(
        serde_json::to_string(&LayoutModeDto::Stacked).unwrap(),
        "\"stacked\""
    );
    assert!(serde_json::from_str::<LayoutModeDto>("\"wide\"").is_err());
}
