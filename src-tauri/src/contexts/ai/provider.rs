use serde::Deserialize;
use serde_json::Value;
use url::Url;

const MAX_PROVIDER_RESPONSE_BYTES: usize = 256 * 1024;
const MAX_PROVIDER_URL_BYTES: usize = 4 * 1024;
const MAX_MODEL_BYTES: usize = 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ProviderConfigError {
    code: &'static str,
}

impl ProviderConfigError {
    pub(crate) const fn code(&self) -> &'static str {
        self.code
    }

    const fn invalid_config() -> Self {
        Self {
            code: "provider_config_invalid",
        }
    }

    const fn invalid_response() -> Self {
        Self {
            code: "provider_response_invalid",
        }
    }
}

#[derive(Clone, PartialEq, Eq)]
pub(crate) struct NormalizedProviderConfig {
    base_url: String,
    chat_completions_url: String,
    model: String,
}

impl NormalizedProviderConfig {
    pub(crate) fn base_url(&self) -> &str {
        &self.base_url
    }

    pub(crate) fn chat_completions_url(&self) -> &str {
        &self.chat_completions_url
    }

    pub(crate) fn model(&self) -> &str {
        &self.model
    }
}

#[derive(PartialEq, Eq)]
pub(crate) struct ParsedAiExplanation {
    doing: String,
    when_used: String,
    capabilities: String,
    cautions: String,
}

impl ParsedAiExplanation {
    #[cfg(test)]
    pub(crate) fn doing(&self) -> &str {
        &self.doing
    }

    #[cfg(test)]
    pub(crate) fn when_used(&self) -> &str {
        &self.when_used
    }

    #[cfg(test)]
    pub(crate) fn capabilities(&self) -> &str {
        &self.capabilities
    }

    #[cfg(test)]
    pub(crate) fn cautions(&self) -> &str {
        &self.cautions
    }

    pub(crate) fn into_parts(self) -> (String, String, String, String) {
        (self.doing, self.when_used, self.capabilities, self.cautions)
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ExplanationSections {
    doing: String,
    when_used: String,
    capabilities: String,
    cautions: String,
}

pub(crate) fn normalize_provider_config(
    base_url: &str,
    model: &str,
) -> Result<NormalizedProviderConfig, ProviderConfigError> {
    let base_url = base_url.trim();
    let model = model.trim();
    if base_url.is_empty()
        || base_url.len() > MAX_PROVIDER_URL_BYTES
        || model.is_empty()
        || model.len() > MAX_MODEL_BYTES
        || model.chars().any(char::is_control)
    {
        return Err(ProviderConfigError::invalid_config());
    }
    let authority = base_url
        .split_once("://")
        .map(|(_, authority)| authority)
        .ok_or_else(ProviderConfigError::invalid_config)?;
    if authority.starts_with('/') {
        return Err(ProviderConfigError::invalid_config());
    }

    let mut parsed = Url::parse(base_url).map_err(|_| ProviderConfigError::invalid_config())?;
    if !matches!(parsed.scheme(), "http" | "https")
        || parsed.host_str().is_none()
        || !parsed.username().is_empty()
        || parsed.password().is_some()
        || parsed.query().is_some()
        || parsed.fragment().is_some()
    {
        return Err(ProviderConfigError::invalid_config());
    }

    let normalized_path = parsed.path().trim_end_matches('/').to_string();
    if normalized_path.ends_with("/chat/completions") {
        return Err(ProviderConfigError::invalid_config());
    }
    parsed.set_path(&normalized_path);
    let normalized_base = parsed.to_string().trim_end_matches('/').to_string();
    let chat_completions_url = format!("{normalized_base}/chat/completions");
    let chat_url =
        Url::parse(&chat_completions_url).map_err(|_| ProviderConfigError::invalid_config())?;
    if parsed.origin() != chat_url.origin() {
        return Err(ProviderConfigError::invalid_config());
    }

    Ok(NormalizedProviderConfig {
        base_url: normalized_base,
        chat_completions_url,
        model: model.to_string(),
    })
}

pub(crate) fn parse_provider_response(
    response: &[u8],
) -> Result<ParsedAiExplanation, ProviderConfigError> {
    if response.len() > MAX_PROVIDER_RESPONSE_BYTES {
        return Err(ProviderConfigError::invalid_response());
    }
    let envelope: Value =
        serde_json::from_slice(response).map_err(|_| ProviderConfigError::invalid_response())?;
    let choices = envelope
        .get("choices")
        .and_then(Value::as_array)
        .filter(|choices| choices.len() == 1)
        .ok_or_else(ProviderConfigError::invalid_response)?;
    let message = choices[0]
        .get("message")
        .and_then(Value::as_object)
        .ok_or_else(ProviderConfigError::invalid_response)?;
    if message.get("role").and_then(Value::as_str) != Some("assistant")
        || message.contains_key("tool_calls")
        || message.contains_key("function_call")
    {
        return Err(ProviderConfigError::invalid_response());
    }
    let content = message
        .get("content")
        .and_then(Value::as_str)
        .ok_or_else(ProviderConfigError::invalid_response)?;
    let sections: ExplanationSections =
        serde_json::from_str(content).map_err(|_| ProviderConfigError::invalid_response())?;
    let doing = checked_section(sections.doing)?;
    let when_used = checked_section(sections.when_used)?;
    let capabilities = checked_section(sections.capabilities)?;
    let cautions = checked_section(sections.cautions)?;
    Ok(ParsedAiExplanation {
        doing,
        when_used,
        capabilities,
        cautions,
    })
}

fn checked_section(value: String) -> Result<String, ProviderConfigError> {
    let value = value.trim();
    if value.is_empty() || contains_html_markup(value) {
        return Err(ProviderConfigError::invalid_response());
    }
    Ok(value.to_string())
}

fn contains_html_markup(value: &str) -> bool {
    let bytes = value.as_bytes();
    let mut tag_like = false;
    for (index, byte) in bytes.iter().copied().enumerate() {
        if tag_like {
            if byte == b'>' {
                return true;
            }
            continue;
        }
        if byte == b'<' {
            tag_like = bytes
                .get(index + 1)
                .is_some_and(|next| next.is_ascii_alphabetic() || matches!(next, b'/' | b'!'));
        }
    }
    false
}

#[cfg(test)]
mod tests {
    use super::{
        normalize_provider_config, parse_provider_response, ParsedAiExplanation,
        ProviderConfigError,
    };

    fn error_code<T>(result: Result<T, ProviderConfigError>) -> &'static str {
        match result {
            Ok(_) => panic!("expected a fail-closed provider error"),
            Err(error) => error.code(),
        }
    }

    fn provider_response(content: &str) -> Vec<u8> {
        serde_json::json!({
            "id": "response-1",
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": content
                }
            }]
        })
        .to_string()
        .into_bytes()
    }

    #[test]
    fn provider_url_is_trimmed_and_normalized_without_exposing_a_second_origin() {
        let config =
            normalize_provider_config("  http://127.0.0.1:11434/v1///  ", "  qwen2.5-coder:7b  ")
                .expect("valid OpenAI-compatible endpoint");

        assert_eq!(config.base_url(), "http://127.0.0.1:11434/v1");
        assert_eq!(
            config.chat_completions_url(),
            "http://127.0.0.1:11434/v1/chat/completions"
        );
        assert_eq!(config.model(), "qwen2.5-coder:7b");
    }

    #[test]
    fn provider_url_rejects_ambiguous_or_credential_bearing_inputs() {
        for input in [
            "ftp://provider.example/v1",
            "https://user@provider.example/v1",
            "https://provider.example/v1?tenant=two",
            "https://provider.example/v1#alternate",
            "https:///v1",
            "https://provider.example/v1/chat/completions",
        ] {
            assert_eq!(
                error_code(normalize_provider_config(input, "model")),
                "provider_config_invalid",
                "unexpected acceptance for {input}"
            );
        }

        assert_eq!(
            error_code(normalize_provider_config(
                "https://provider.example/v1",
                "   "
            )),
            "provider_config_invalid"
        );
    }

    #[test]
    fn provider_response_accepts_only_the_four_required_nonempty_strings() {
        let content = serde_json::json!({
            "doing": "파일이 수행하는 일",
            "when_used": "도구가 이 파일을 읽을 때",
            "capabilities": "검증 작업을 수행함",
            "cautions": "생성 설명은 runtime 검증이 아님"
        })
        .to_string();

        let explanation =
            parse_provider_response(&provider_response(&content)).expect("strict response");

        assert_eq!(explanation.doing(), "파일이 수행하는 일");
        assert_eq!(explanation.when_used(), "도구가 이 파일을 읽을 때");
        assert_eq!(explanation.capabilities(), "검증 작업을 수행함");
        assert_eq!(explanation.cautions(), "생성 설명은 runtime 검증이 아님");
    }

    #[test]
    fn provider_response_rejects_extra_empty_tool_and_html_content() {
        let invalid_contents = [
            serde_json::json!({
                "doing": "일",
                "when_used": "때",
                "capabilities": "기능",
                "cautions": "주의",
                "extra": "거부"
            })
            .to_string(),
            serde_json::json!({
                "doing": " ",
                "when_used": "때",
                "capabilities": "기능",
                "cautions": "주의"
            })
            .to_string(),
            serde_json::json!({
                "doing": "<script>alert(1)</script>",
                "when_used": "때",
                "capabilities": "기능",
                "cautions": "주의"
            })
            .to_string(),
        ];

        for content in invalid_contents {
            assert_eq!(
                error_code(parse_provider_response(&provider_response(&content))),
                "provider_response_invalid"
            );
        }

        let with_tool_call = serde_json::json!({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "{}",
                    "tool_calls": [{"id": "unsafe"}]
                }
            }]
        })
        .to_string();
        assert_eq!(
            error_code(parse_provider_response(with_tool_call.as_bytes())),
            "provider_response_invalid"
        );
    }

    #[test]
    fn provider_response_enforces_the_body_bound_before_parsing() {
        let oversized = vec![b' '; (256 * 1024) + 1];
        assert_eq!(
            error_code::<ParsedAiExplanation>(parse_provider_response(&oversized)),
            "provider_response_invalid"
        );
    }
}
