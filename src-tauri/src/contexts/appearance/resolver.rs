use super::{LogicalMode, ResolvedMode};

pub fn resolve_mode(logical: LogicalMode, system: ResolvedMode) -> ResolvedMode {
    match logical {
        LogicalMode::System => system,
        LogicalMode::Light => ResolvedMode::Light,
        LogicalMode::Dark => ResolvedMode::Dark,
    }
}
