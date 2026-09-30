use harness_desktop_lib::api::dto::install::{
    ApplyInstallApprovalsDto, ApplyInstallResponseDto, InstallApplyStateDto,
    InstallOperationStatusDto, InstallScopeDto, InstallVerifyStateDto, PreviewInstallResponseDto,
};
use harness_desktop_lib::contexts::install::{
    DestinationApplyState, DestinationVerifyState, InstallApplyOutcome, InstallDestinationResult,
    InstallOperationStatus, InstallPreview, InstallPreviewArtifact, InstallRequiredApprovals,
    InstallRuntimeGate,
};

#[test]
fn preview_projection_is_typed_and_excludes_backend_only_source_material() {
    let dto = PreviewInstallResponseDto::from(InstallPreview {
        preview_id: "preview-1".to_string(),
        fingerprint: "a".repeat(64),
        sot_snapshot_id: "sot-1".to_string(),
        profile_id: "harnesskit.profile.engineering".to_string(),
        scope: "project".to_string(),
        target_root: "/tmp/project".to_string(),
        targets: vec!["codex".to_string()],
        components: vec!["harnesskit.skill.fixture".to_string()],
        artifacts: vec![InstallPreviewArtifact {
            component_id: "harnesskit.skill.fixture".to_string(),
            component_ids: vec!["harnesskit.skill.fixture".to_string()],
            target: "codex".to_string(),
            destination: ".agents/skills/fixture/SKILL.md".to_string(),
            merge_strategy: None,
        }],
        skipped_writes: vec![],
        warnings: vec!["fixture_warning".to_string()],
        runtime_gates: vec![InstallRuntimeGate {
            gate_id: "manual_activation".to_string(),
            target: "codex".to_string(),
            safe_reason: "사용자 확인이 필요합니다.".to_string(),
            required_before_apply: false,
            required_before_runtime: true,
        }],
        non_atomic_boundary: true,
        required_approvals: InstallRequiredApprovals {
            overwrite: false,
            runtime_hooks: true,
        },
    });

    assert_eq!(dto.scope, InstallScopeDto::Project);
    let serialized = serde_json::to_value(dto).unwrap();
    assert_eq!(serialized["previewId"], "preview-1");
    assert_eq!(
        serialized["artifacts"][0]["destination"],
        ".agents/skills/fixture/SKILL.md"
    );
    for forbidden in ["source", "sourceSha256", "contentSha256", "mode"] {
        assert!(serialized.get(forbidden).is_none());
        assert!(serialized["artifacts"][0].get(forbidden).is_none());
    }
}

#[test]
fn apply_projection_preserves_changed_unchanged_partial_and_verify_truth() {
    let dto = ApplyInstallResponseDto::from(InstallApplyOutcome {
        operation_id: "operation-1".to_string(),
        status: InstallOperationStatus::Partial,
        destinations: vec![
            InstallDestinationResult {
                target: "codex".to_string(),
                destination: ".agents/skills/changed/SKILL.md".to_string(),
                apply_state: DestinationApplyState::AppliedWithWarning,
                verify_state: DestinationVerifyState::Verified,
                code: Some("metadata_sync_warning".to_string()),
            },
            InstallDestinationResult {
                target: "codex".to_string(),
                destination: ".agents/skills/same/SKILL.md".to_string(),
                apply_state: DestinationApplyState::Unchanged,
                verify_state: DestinationVerifyState::NotApplied,
                code: None,
            },
        ],
        install_evidence_id: None,
    });

    assert_eq!(dto.status, InstallOperationStatusDto::Partial);
    assert_eq!(
        dto.destinations[0].apply_state,
        InstallApplyStateDto::Changed
    );
    assert_eq!(
        dto.destinations[0].verify_state,
        InstallVerifyStateDto::Verified
    );
    assert_eq!(
        dto.destinations[1].apply_state,
        InstallApplyStateDto::Unchanged
    );
    assert_eq!(
        dto.destinations[1].verify_state,
        InstallVerifyStateDto::NotApplied
    );
    assert!(dto.install_evidence_id.is_none());
}

#[test]
fn public_install_commands_use_nested_typed_requests_and_api_errors() {
    let commands = include_str!("../src/api/commands.rs");
    let runtime = include_str!("../src/lib.rs");

    assert!(commands.contains("request: InstallPreviewRequestDto"));
    assert!(commands.contains("approvals: ApplyInstallApprovalsDto"));
    assert!(commands.contains("Result<PreviewInstallResponseDto, ApiErrorDto>"));
    assert!(commands.contains("Result<ApplyInstallResponseDto, ApiErrorDto>"));
    assert!(runtime.contains("api::commands::preview_install"));
    assert!(runtime.contains("api::commands::apply_install"));
    assert!(!runtime.contains("async fn preview_install"));
    assert!(!runtime.contains("async fn apply_install"));
}

#[test]
fn apply_approval_carries_the_exact_preview_fingerprint() {
    let approvals: ApplyInstallApprovalsDto = serde_json::from_value(serde_json::json!({
        "confirmed": true,
        "semanticFingerprint": "b".repeat(64),
        "overwrite": false,
        "allowRuntimeHooks": false
    }))
    .unwrap();

    assert_eq!(approvals.semantic_fingerprint, "b".repeat(64));
}
