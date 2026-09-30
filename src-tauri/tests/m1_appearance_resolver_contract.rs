use harness_desktop_lib::api::dto::appearance::{LogicalModeDto, ResolvedModeDto};
use harness_desktop_lib::contexts::appearance::{resolve_mode, LogicalMode, ResolvedMode};

#[test]
fn logical_mode_serialization_is_the_public_contract() {
    for (mode, expected) in [
        (LogicalModeDto::System, "\"System\""),
        (LogicalModeDto::Light, "\"Light\""),
        (LogicalModeDto::Dark, "\"Dark\""),
    ] {
        assert_eq!(serde_json::to_string(&mode).unwrap(), expected);
        assert_eq!(
            serde_json::from_str::<LogicalModeDto>(expected).unwrap(),
            mode
        );
        assert_eq!(LogicalMode::from(mode), mode.into());
    }
}

#[test]
fn resolved_mode_serialization_never_exposes_system() {
    for (mode, expected) in [
        (ResolvedModeDto::Light, "\"Light\""),
        (ResolvedModeDto::Dark, "\"Dark\""),
    ] {
        assert_eq!(serde_json::to_string(&mode).unwrap(), expected);
        assert_eq!(
            serde_json::from_str::<ResolvedModeDto>(expected).unwrap(),
            mode
        );
        assert_eq!(ResolvedMode::from(mode), mode.into());
    }

    assert!(serde_json::from_str::<ResolvedModeDto>("\"System\"").is_err());
}

#[test]
fn resolver_covers_all_logical_and_os_mode_pairs() {
    assert_eq!(
        resolve_mode(LogicalMode::System, ResolvedMode::Light),
        ResolvedMode::Light
    );
    assert_eq!(
        resolve_mode(LogicalMode::System, ResolvedMode::Dark),
        ResolvedMode::Dark
    );
    assert_eq!(
        resolve_mode(LogicalMode::Light, ResolvedMode::Light),
        ResolvedMode::Light
    );
    assert_eq!(
        resolve_mode(LogicalMode::Light, ResolvedMode::Dark),
        ResolvedMode::Light
    );
    assert_eq!(
        resolve_mode(LogicalMode::Dark, ResolvedMode::Light),
        ResolvedMode::Dark
    );
    assert_eq!(
        resolve_mode(LogicalMode::Dark, ResolvedMode::Dark),
        ResolvedMode::Dark
    );
}
