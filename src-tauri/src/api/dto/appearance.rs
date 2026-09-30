use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum LogicalModeDto {
    System,
    Light,
    Dark,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum ResolvedModeDto {
    Light,
    Dark,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AppearanceStateDto {
    pub logical_mode: LogicalModeDto,
    pub resolved_mode: ResolvedModeDto,
    pub revision: u64,
    pub persisted: bool,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum AppearanceChangeSourceDto {
    User,
    System,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AppearanceChangedDto {
    pub logical_mode: LogicalModeDto,
    pub resolved_mode: ResolvedModeDto,
    pub revision: u64,
    pub source: AppearanceChangeSourceDto,
    pub persisted: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AppearanceDiagnosticDto {
    pub code: String,
    pub safe_message: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AppearanceUpdateDto {
    pub appearance: AppearanceStateDto,
    pub diagnostic: Option<AppearanceDiagnosticDto>,
}

impl From<LogicalModeDto> for crate::contexts::appearance::LogicalMode {
    fn from(value: LogicalModeDto) -> Self {
        match value {
            LogicalModeDto::System => Self::System,
            LogicalModeDto::Light => Self::Light,
            LogicalModeDto::Dark => Self::Dark,
        }
    }
}

impl From<crate::contexts::appearance::LogicalMode> for LogicalModeDto {
    fn from(value: crate::contexts::appearance::LogicalMode) -> Self {
        match value {
            crate::contexts::appearance::LogicalMode::System => Self::System,
            crate::contexts::appearance::LogicalMode::Light => Self::Light,
            crate::contexts::appearance::LogicalMode::Dark => Self::Dark,
        }
    }
}

impl From<ResolvedModeDto> for crate::contexts::appearance::ResolvedMode {
    fn from(value: ResolvedModeDto) -> Self {
        match value {
            ResolvedModeDto::Light => Self::Light,
            ResolvedModeDto::Dark => Self::Dark,
        }
    }
}

impl From<crate::contexts::appearance::ResolvedMode> for ResolvedModeDto {
    fn from(value: crate::contexts::appearance::ResolvedMode) -> Self {
        match value {
            crate::contexts::appearance::ResolvedMode::Light => Self::Light,
            crate::contexts::appearance::ResolvedMode::Dark => Self::Dark,
        }
    }
}

impl From<crate::contexts::appearance::AppearanceState> for AppearanceStateDto {
    fn from(value: crate::contexts::appearance::AppearanceState) -> Self {
        Self {
            logical_mode: value.logical_mode.into(),
            resolved_mode: value.resolved_mode.into(),
            revision: value.revision,
            persisted: value.persisted,
        }
    }
}

impl From<crate::contexts::appearance::AppearanceChangeSource> for AppearanceChangeSourceDto {
    fn from(value: crate::contexts::appearance::AppearanceChangeSource) -> Self {
        match value {
            crate::contexts::appearance::AppearanceChangeSource::User => Self::User,
            crate::contexts::appearance::AppearanceChangeSource::System => Self::System,
        }
    }
}

impl From<crate::contexts::appearance::AppearanceChanged> for AppearanceChangedDto {
    fn from(value: crate::contexts::appearance::AppearanceChanged) -> Self {
        Self {
            logical_mode: value.state.logical_mode.into(),
            resolved_mode: value.state.resolved_mode.into(),
            revision: value.state.revision,
            source: value.source.into(),
            persisted: value.state.persisted,
        }
    }
}

impl From<crate::contexts::appearance::AppearanceDiagnostic> for AppearanceDiagnosticDto {
    fn from(value: crate::contexts::appearance::AppearanceDiagnostic) -> Self {
        Self {
            code: value.code.to_owned(),
            safe_message: value.safe_message.to_owned(),
        }
    }
}
