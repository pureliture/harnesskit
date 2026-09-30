use serde::{Deserialize, Serialize};

use crate::contexts::ai::{AiExplanation, ProviderConfigHeader, ProviderConfigState};

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SaveAiProviderConfigRequestDto {
    pub expected_provider_revision: Option<String>,
    pub base_url: String,
    pub model: String,
    pub api_key: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct DeleteAiProviderKeyRequestDto {
    pub provider_revision: String,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ExplainLocalSourceRequestDto {
    pub snapshot_id: String,
    pub instance_id: String,
    pub source_revision: String,
    pub provider_revision: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "state", rename_all = "snake_case")]
pub enum ProviderConfigStateDto {
    Unconfigured,
    Configured {
        base_url: String,
        model: String,
        api_key_present: bool,
        provider_revision: String,
    },
}

#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AiExplanationDto {
    pub snapshot_id: String,
    pub instance_id: String,
    pub source_revision: String,
    pub provider_revision: String,
    pub doing: String,
    pub when_used: String,
    pub capabilities: String,
    pub cautions: String,
}

impl std::fmt::Debug for AiExplanationDto {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("AiExplanationDto")
            .field("snapshot_id", &self.snapshot_id)
            .field("instance_id", &self.instance_id)
            .field("source_revision", &self.source_revision)
            .field("provider_revision", &self.provider_revision)
            .field("content", &"<redacted>")
            .finish()
    }
}

impl From<ProviderConfigState> for ProviderConfigStateDto {
    fn from(value: ProviderConfigState) -> Self {
        match value {
            ProviderConfigState::Unconfigured => Self::Unconfigured,
            ProviderConfigState::Configured(header) => header.into(),
        }
    }
}

impl From<ProviderConfigHeader> for ProviderConfigStateDto {
    fn from(value: ProviderConfigHeader) -> Self {
        Self::Configured {
            base_url: value.base_url().to_string(),
            model: value.model().to_string(),
            api_key_present: value.api_key_present(),
            provider_revision: value.provider_revision().to_string(),
        }
    }
}

impl From<AiExplanation> for AiExplanationDto {
    fn from(value: AiExplanation) -> Self {
        let (
            snapshot_id,
            instance_id,
            source_revision,
            provider_revision,
            doing,
            when_used,
            capabilities,
            cautions,
        ) = value.into_parts();
        Self {
            snapshot_id,
            instance_id,
            source_revision,
            provider_revision,
            doing,
            when_used,
            capabilities,
            cautions,
        }
    }
}
