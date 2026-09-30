use std::io::Read;
use std::time::Duration;

use reqwest::blocking::Client;
use reqwest::header::{HeaderValue, AUTHORIZATION, CONTENT_TYPE};
use serde::Serialize;

use super::provider::{parse_provider_response, NormalizedProviderConfig, ParsedAiExplanation};

pub(crate) const MAX_AI_SOURCE_BYTES: usize = 256 * 1024;
const MAX_PROVIDER_RESPONSE_BYTES: usize = 256 * 1024;
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const TOTAL_TIMEOUT: Duration = Duration::from_secs(60);
const SYSTEM_PROMPT: &str = "Explain the exact harness source supplied by the user. Return only one JSON object with exactly four non-empty string fields: doing, when_used, capabilities, cautions. Do not return Markdown, HTML, links, tool calls, function calls, or additional fields. State uncertainty and do not claim runtime verification.";

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct AiTransportError {
    code: &'static str,
}

impl AiTransportError {
    pub(crate) const fn code(&self) -> &'static str {
        self.code
    }

    pub(crate) const fn new(code: &'static str) -> Self {
        Self { code }
    }
}

pub(crate) trait OpenAiCompatibleTransportPort: Send + Sync + 'static {
    fn explain(
        &self,
        config: &NormalizedProviderConfig,
        api_key: Option<&[u8]>,
        source: &[u8],
    ) -> Result<ParsedAiExplanation, AiTransportError>;
}

pub(crate) struct ReqwestOpenAiCompatibleTransport {
    client: Client,
}

impl ReqwestOpenAiCompatibleTransport {
    pub(crate) fn new() -> Result<Self, AiTransportError> {
        let client = Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .referer(false)
            .retry(reqwest::retry::never())
            .connect_timeout(CONNECT_TIMEOUT)
            .timeout(TOTAL_TIMEOUT)
            .no_gzip()
            .no_brotli()
            .no_deflate()
            .no_zstd()
            .build()
            .map_err(|_| AiTransportError::new("provider_transport_unavailable"))?;
        Ok(Self { client })
    }
}

impl OpenAiCompatibleTransportPort for ReqwestOpenAiCompatibleTransport {
    fn explain(
        &self,
        config: &NormalizedProviderConfig,
        api_key: Option<&[u8]>,
        source: &[u8],
    ) -> Result<ParsedAiExplanation, AiTransportError> {
        if source.len() > MAX_AI_SOURCE_BYTES {
            return Err(AiTransportError::new("source_too_large_for_ai"));
        }
        let source =
            std::str::from_utf8(source).map_err(|_| AiTransportError::new("source_not_utf8"))?;
        let body = serde_json::to_vec(&ChatCompletionRequest {
            model: config.model(),
            stream: false,
            messages: [
                ChatMessage {
                    role: "system",
                    content: SYSTEM_PROMPT,
                },
                ChatMessage {
                    role: "user",
                    content: source,
                },
            ],
        })
        .map_err(|_| AiTransportError::new("provider_request_failed"))?;
        let mut request = self
            .client
            .post(config.chat_completions_url())
            .header(CONTENT_TYPE, "application/json")
            .body(body);
        if let Some(api_key) = api_key {
            let mut value = Vec::with_capacity(7 + api_key.len());
            value.extend_from_slice(b"Bearer ");
            value.extend_from_slice(api_key);
            let mut value = HeaderValue::from_bytes(&value)
                .map_err(|_| AiTransportError::new("provider_auth_invalid"))?;
            value.set_sensitive(true);
            request = request.header(AUTHORIZATION, value);
        }

        let response = request
            .send()
            .map_err(|_| AiTransportError::new("provider_request_failed"))?;
        let status = response.status();
        if matches!(status.as_u16(), 401 | 403) {
            return Err(AiTransportError::new("provider_auth_failed"));
        }
        if status.as_u16() == 413 {
            return Err(AiTransportError::new("source_too_large_for_ai"));
        }
        if !status.is_success() {
            return Err(AiTransportError::new("provider_request_failed"));
        }
        if response
            .content_length()
            .is_some_and(|length| length > MAX_PROVIDER_RESPONSE_BYTES as u64)
        {
            return Err(AiTransportError::new("provider_response_invalid"));
        }
        let mut body = Vec::with_capacity(
            response
                .content_length()
                .unwrap_or_default()
                .min(MAX_PROVIDER_RESPONSE_BYTES as u64) as usize,
        );
        response
            .take((MAX_PROVIDER_RESPONSE_BYTES + 1) as u64)
            .read_to_end(&mut body)
            .map_err(|_| AiTransportError::new("provider_response_invalid"))?;
        if body.len() > MAX_PROVIDER_RESPONSE_BYTES {
            return Err(AiTransportError::new("provider_response_invalid"));
        }
        parse_provider_response(&body)
            .map_err(|_| AiTransportError::new("provider_response_invalid"))
    }
}

#[derive(Serialize)]
struct ChatCompletionRequest<'a> {
    model: &'a str,
    stream: bool,
    messages: [ChatMessage<'a>; 2],
}

#[derive(Serialize)]
struct ChatMessage<'a> {
    role: &'static str,
    content: &'a str,
}
