use harness_desktop_lib::api::dto::appearance::{
    AppearanceChangeSourceDto, AppearanceChangedDto, AppearanceStateDto, LogicalModeDto,
    ResolvedModeDto,
};
use harness_desktop_lib::api::dto::bootstrap::BootstrapInstructionDto;
use harness_desktop_lib::contexts::appearance::{
    AppearanceChangeSource, AppearanceChanged, AppearanceState, LogicalMode, ResolvedMode,
};

#[test]
fn state_and_event_dtos_preserve_the_exact_revision_envelope() {
    let state = AppearanceState {
        logical_mode: LogicalMode::System,
        resolved_mode: ResolvedMode::Dark,
        revision: 17,
        persisted: false,
    };
    let dto = AppearanceStateDto::from(state);

    assert_eq!(dto.logical_mode, LogicalModeDto::System);
    assert_eq!(dto.resolved_mode, ResolvedModeDto::Dark);
    assert_eq!(dto.revision, 17);
    assert!(!dto.persisted);
    assert_eq!(
        serde_json::to_value(&dto).unwrap(),
        serde_json::json!({
            "logical_mode": "System",
            "resolved_mode": "Dark",
            "revision": 17,
            "persisted": false,
        })
    );

    let event = AppearanceChangedDto::from(AppearanceChanged {
        state,
        source: AppearanceChangeSource::System,
    });
    assert_eq!(event.source, AppearanceChangeSourceDto::System);
    assert_eq!(event.logical_mode, dto.logical_mode);
    assert_eq!(event.resolved_mode, dto.resolved_mode);
    assert_eq!(event.revision, dto.revision);
    assert_eq!(event.persisted, dto.persisted);
}

#[test]
fn bootstrap_instruction_serialization_is_show_or_refresh_only() {
    assert_eq!(
        serde_json::to_string(&BootstrapInstructionDto::Show).unwrap(),
        "\"Show\""
    );
    assert_eq!(
        serde_json::to_string(&BootstrapInstructionDto::Refresh).unwrap(),
        "\"Refresh\""
    );
    assert!(serde_json::from_str::<BootstrapInstructionDto>("\"Fallback\"").is_err());
}
