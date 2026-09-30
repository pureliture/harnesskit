use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ApiErrorDto {
    pub code: String,
    pub retryable: bool,
    pub safe_message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub details: Option<ApiErrorDetailsDto>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ApiErrorDetailsDto {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub active_chunk_index: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub line: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub column: Option<usize>,
}

impl ApiErrorDto {
    pub fn feature_unavailable(code: impl Into<String>, safe_message: impl Into<String>) -> Self {
        Self {
            code: code.into(),
            retryable: false,
            safe_message: safe_message.into(),
            details: None,
        }
    }
}
