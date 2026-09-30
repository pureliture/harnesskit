use serde::{Deserialize, Serialize};

use crate::contexts::typography::{
    TypographyDiagnostic, TypographyPreset, TypographyState, TypographyUpdate,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum TypographyPresetDto {
    Small,
    Default,
    Large,
}

impl From<TypographyPresetDto> for TypographyPreset {
    fn from(value: TypographyPresetDto) -> Self {
        match value {
            TypographyPresetDto::Small => Self::Small,
            TypographyPresetDto::Default => Self::Default,
            TypographyPresetDto::Large => Self::Large,
        }
    }
}
impl From<TypographyPreset> for TypographyPresetDto {
    fn from(value: TypographyPreset) -> Self {
        match value {
            TypographyPreset::Small => Self::Small,
            TypographyPreset::Default => Self::Default,
            TypographyPreset::Large => Self::Large,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct TypographyStateDto {
    pub preset: TypographyPresetDto,
    pub revision: u64,
    pub persisted: bool,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TypographyDiagnosticDto {
    pub code: String,
    pub safe_message: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TypographyUpdateDto {
    pub typography: TypographyStateDto,
    pub diagnostic: Option<TypographyDiagnosticDto>,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SetTypographyPresetRequestDto {
    pub expected_typography_revision: u64,
    pub preset: TypographyPresetDto,
}

impl From<TypographyState> for TypographyStateDto {
    fn from(value: TypographyState) -> Self {
        Self {
            preset: value.preset.into(),
            revision: value.revision,
            persisted: value.persisted,
        }
    }
}
impl From<TypographyDiagnostic> for TypographyDiagnosticDto {
    fn from(value: TypographyDiagnostic) -> Self {
        Self {
            code: value.code.to_string(),
            safe_message: value.safe_message.to_string(),
        }
    }
}
impl From<TypographyUpdate> for TypographyUpdateDto {
    fn from(value: TypographyUpdate) -> Self {
        Self {
            typography: value.state.into(),
            diagnostic: value.diagnostic.map(Into::into),
        }
    }
}
