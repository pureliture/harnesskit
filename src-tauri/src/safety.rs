const SENSITIVE_KEY_PARTS: [&str; 5] = [
    "TOKEN",
    "PASSWORD",
    "CREDENTIAL",
    "SECRET",
    "PRIVATE_ENDPOINT",
];

pub fn redact_message(message: &str) -> String {
    message
        .split_whitespace()
        .map(|token| {
            let Some((key, _value)) = token.split_once('=') else {
                return token.to_string();
            };
            let normalized_key = key.to_ascii_uppercase();
            if SENSITIVE_KEY_PARTS
                .iter()
                .any(|sensitive| normalized_key.contains(sensitive))
            {
                format!("{key}=[REDACTED]")
            } else {
                token.to_string()
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}
