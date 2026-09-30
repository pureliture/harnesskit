//! Structured harness metadata parsers.

use std::path::Path;

use serde_json::Value as JsonValue;
use serde_yaml::Value as YamlValue;

use super::adapter::{ParserId, SurfaceKind};
use super::domain::{ParseBatch, ParseState, ParsedItem, SafeIssue, SafeSetting};

pub fn parse_document(
    parser_id: ParserId,
    stable_source_locator: &str,
    kind_hint: SurfaceKind,
    bytes: &[u8],
) -> ParseBatch {
    match parser_id {
        ParserId::SkillFrontmatterV1 | ParserId::MarkdownRuleV1 => {
            parse_markdown(stable_source_locator, kind_hint, bytes)
        }
        ParserId::HookJsonV1 | ParserId::ClaudeSettingsV1 => {
            parse_json(stable_source_locator, kind_hint, bytes, true)
        }
        ParserId::AntigravityManifestV1 | ParserId::AntigravityCliJsonV1 => {
            parse_plugin_manifest(stable_source_locator, bytes)
        }
        ParserId::CodexTomlV1 => parse_toml(stable_source_locator, kind_hint, bytes),
        ParserId::ClaudeWorkflowJsMetadataV1 => {
            parse_workflow_metadata(stable_source_locator, kind_hint, bytes)
        }
        ParserId::HermesYamlV1 => parse_yaml(stable_source_locator, kind_hint, bytes),
    }
}

/// Produces the user-visible Local inventory identity without promoting the
/// complete locator to a title. Adapters may provide an explicit name; the
/// fallback is deliberately limited to a short semantic file/component name.
pub fn resolve_local_display_name(declared_name: Option<&str>, locator: &str) -> String {
    let file_name = safe_file_name(locator);
    let declared_name = declared_name
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .filter(|value| {
            !file_name.as_deref().is_some_and(|file_name| {
                value.eq_ignore_ascii_case(file_name) && is_generic_component_file_name(file_name)
            })
        })
        .map(str::to_owned);

    declared_name
        .or_else(|| file_name.filter(|name| !is_generic_component_file_name(name)))
        .or_else(|| enclosing_component_directory(locator))
        .unwrap_or_else(|| "이름 정보 없음".to_string())
}

fn parse_markdown(locator: &str, kind: SurfaceKind, bytes: &[u8]) -> ParseBatch {
    let Ok(text) = std::str::from_utf8(bytes) else {
        return malformed(locator, kind);
    };
    let (name, description, description_source) = if let Some(frontmatter) = frontmatter(text) {
        match serde_yaml::from_str::<YamlValue>(frontmatter) {
            Ok(value) => (
                yaml_string(&value, "name"),
                yaml_string(&value, "description"),
                Some("frontmatter".to_string()),
            ),
            Err(_) => return malformed(locator, kind),
        }
    } else {
        (safe_file_name(locator), None, None)
    };
    one_item(
        locator,
        kind,
        name,
        description,
        description_source,
        Vec::new(),
    )
}

fn frontmatter(text: &str) -> Option<&str> {
    let rest = text.strip_prefix("---\n")?;
    let end = rest.find("\n---")?;
    Some(&rest[..end])
}

fn yaml_string(value: &YamlValue, key: &str) -> Option<String> {
    value
        .get(key)
        .and_then(YamlValue::as_str)
        .map(str::to_owned)
}

fn parse_json(locator: &str, kind: SurfaceKind, bytes: &[u8], hooks_first: bool) -> ParseBatch {
    let value = match serde_json::from_slice::<JsonValue>(bytes) {
        Ok(value) => value,
        Err(_) => return malformed(locator, kind),
    };
    if hooks_first {
        if let Some(hooks) = value.get("hooks").and_then(JsonValue::as_object) {
            return json_hook_entries(locator, hooks);
        }
    }
    let Some(object) = value.as_object() else {
        return one_item(
            locator,
            SurfaceKind::Unclassified,
            safe_file_name(locator),
            None,
            None,
            Vec::new(),
        );
    };
    let mut items = Vec::new();
    for (key, value) in object {
        if let Some(children) = value.as_object() {
            for (child, child_value) in children {
                items.push(ParsedItem {
                    stable_source_locator: format!(
                        "{locator}#/{}/{}",
                        pointer_segment(key),
                        pointer_segment(child)
                    ),
                    kind,
                    name: Some(child.clone()),
                    description: child_value
                        .get("description")
                        .and_then(JsonValue::as_str)
                        .map(str::to_owned),
                    description_source: child_value
                        .get("description")
                        .and_then(JsonValue::as_str)
                        .map(|_| "structured_field".to_string()),
                    settings: safe_json_settings(child_value),
                    parse_state: ParseState::Parsed,
                    issue_codes: Vec::new(),
                });
            }
        } else {
            items.push(ParsedItem {
                stable_source_locator: format!("{locator}#/{}", pointer_segment(key)),
                kind,
                name: Some(key.clone()),
                description: None,
                description_source: None,
                settings: safe_json_settings(value),
                parse_state: ParseState::Parsed,
                issue_codes: Vec::new(),
            });
        }
    }
    if items.is_empty() {
        one_item(
            locator,
            SurfaceKind::Unclassified,
            safe_file_name(locator),
            None,
            None,
            Vec::new(),
        )
    } else {
        ParseBatch {
            items,
            issues: Vec::new(),
        }
    }
}

fn parse_plugin_manifest(locator: &str, bytes: &[u8]) -> ParseBatch {
    let value = match serde_json::from_slice::<JsonValue>(bytes) {
        Ok(value) => value,
        Err(_) => return malformed(locator, SurfaceKind::Unclassified),
    };
    let Some(object) = value.as_object() else {
        return one_item(
            locator,
            SurfaceKind::Unclassified,
            safe_file_name(locator),
            None,
            None,
            Vec::new(),
        );
    };
    let mut items = vec![ParsedItem {
        stable_source_locator: locator.to_string(),
        kind: SurfaceKind::Unclassified,
        name: object
            .get("name")
            .and_then(JsonValue::as_str)
            .map(str::to_owned)
            .or_else(|| safe_file_name(locator)),
        description: object
            .get("description")
            .and_then(JsonValue::as_str)
            .map(str::to_owned),
        description_source: object
            .get("description")
            .and_then(JsonValue::as_str)
            .map(|_| "structured_field".to_string()),
        settings: Vec::new(),
        parse_state: ParseState::Unclassified,
        issue_codes: Vec::new(),
    }];
    for (field, kind) in [
        ("skills", SurfaceKind::Skill),
        ("agents", SurfaceKind::Agent),
        ("hooks", SurfaceKind::Hook),
        ("rules", SurfaceKind::Rule),
        ("commands", SurfaceKind::Command),
        ("workflows", SurfaceKind::Workflow),
    ] {
        let Some(children) = object.get(field).and_then(JsonValue::as_object) else {
            continue;
        };
        for (name, child) in children {
            items.push(ParsedItem {
                stable_source_locator: format!(
                    "{locator}#/{}/{}",
                    pointer_segment(field),
                    pointer_segment(name)
                ),
                kind,
                name: Some(name.clone()),
                description: child
                    .get("description")
                    .and_then(JsonValue::as_str)
                    .map(str::to_owned),
                description_source: child
                    .get("description")
                    .and_then(JsonValue::as_str)
                    .map(|_| "structured_field".to_string()),
                settings: safe_json_settings(child),
                parse_state: ParseState::Parsed,
                issue_codes: Vec::new(),
            });
        }
    }
    ParseBatch {
        items,
        issues: Vec::new(),
    }
}

fn json_hook_entries(locator: &str, hooks: &serde_json::Map<String, JsonValue>) -> ParseBatch {
    let mut items = Vec::new();
    for (event, entries) in hooks {
        let entries = entries.as_array().map(Vec::as_slice).unwrap_or(&[]);
        if entries.is_empty() {
            items.push(ParsedItem {
                stable_source_locator: format!("{locator}#/hooks/{}", pointer_segment(event)),
                kind: SurfaceKind::Hook,
                name: Some(event.clone()),
                description: None,
                description_source: None,
                settings: Vec::new(),
                parse_state: ParseState::Parsed,
                issue_codes: Vec::new(),
            });
        }
        for (index, entry) in entries.iter().enumerate() {
            let nested = entry.get("hooks").and_then(JsonValue::as_array);
            if let Some(handlers) = nested.filter(|handlers| !handlers.is_empty()) {
                for (handler_index, handler) in handlers.iter().enumerate() {
                    items.push(json_hook_item(
                        format!(
                            "{locator}#/hooks/{}/{index}/hooks/{handler_index}",
                            pointer_segment(event)
                        ),
                        event,
                        handler,
                    ));
                }
            } else {
                items.push(json_hook_item(
                    format!("{locator}#/hooks/{}/{index}", pointer_segment(event)),
                    event,
                    entry,
                ));
            }
        }
    }
    ParseBatch {
        items,
        issues: Vec::new(),
    }
}

fn json_hook_item(locator: String, event: &str, value: &JsonValue) -> ParsedItem {
    ParsedItem {
        stable_source_locator: locator,
        kind: SurfaceKind::Hook,
        name: Some(event.to_string()),
        description: value
            .get("description")
            .and_then(JsonValue::as_str)
            .map(str::to_owned),
        description_source: value
            .get("description")
            .and_then(JsonValue::as_str)
            .map(|_| "structured_field".to_string()),
        settings: safe_json_settings(value),
        parse_state: ParseState::Parsed,
        issue_codes: Vec::new(),
    }
}

fn safe_json_settings(value: &JsonValue) -> Vec<SafeSetting> {
    let Some(object) = value.as_object() else {
        return Vec::new();
    };
    let mut settings = object
        .iter()
        .filter_map(|(key, value)| {
            if key == "description" {
                return None;
            }
            let redacted = key == "command" || secret_like(key);
            let safe_value = (!redacted && safe_setting_key(key))
                .then(|| scalar_string(value))
                .flatten();
            (redacted || safe_value.is_some()).then(|| SafeSetting {
                key: key.clone(),
                present: true,
                redacted,
                value: safe_value,
            })
        })
        .collect::<Vec<_>>();
    settings.sort_by(|left, right| left.key.cmp(&right.key));
    settings
}

fn secret_like(key: &str) -> bool {
    let key = key.to_ascii_lowercase();
    [
        "secret",
        "token",
        "password",
        "credential",
        "api_key",
        "apikey",
        "private_key",
    ]
    .iter()
    .any(|needle| key.contains(needle))
}

fn safe_setting_key(key: &str) -> bool {
    matches!(
        key,
        "event" | "model" | "enabled" | "timeout" | "tool" | "grant" | "permission"
    )
}

fn scalar_string(value: &JsonValue) -> Option<String> {
    match value {
        JsonValue::String(value) => Some(value.clone()),
        JsonValue::Bool(value) => Some(value.to_string()),
        JsonValue::Number(value) => Some(value.to_string()),
        _ => None,
    }
}

fn parse_toml(locator: &str, kind: SurfaceKind, bytes: &[u8]) -> ParseBatch {
    let Ok(text) = std::str::from_utf8(bytes) else {
        return malformed(locator, kind);
    };
    if text.trim().is_empty() || text.contains('\0') {
        return malformed(locator, kind);
    }
    one_item(
        locator,
        kind,
        safe_file_name(locator),
        None,
        None,
        Vec::new(),
    )
}

fn parse_workflow_metadata(locator: &str, kind: SurfaceKind, bytes: &[u8]) -> ParseBatch {
    let Ok(text) = std::str::from_utf8(bytes) else {
        return malformed(locator, kind);
    };
    let mut name = None;
    let mut description = None;
    for line in text.lines().take(32) {
        let Some(metadata) = line.trim().strip_prefix("//") else {
            break;
        };
        if let Some(value) = metadata.trim().strip_prefix("name:") {
            name = Some(value.trim().to_string());
        }
        if let Some(value) = metadata.trim().strip_prefix("description:") {
            description = Some(value.trim().to_string());
        }
    }
    one_item(
        locator,
        kind,
        name.or_else(|| safe_file_name(locator)),
        description,
        Some("metadata_comment".to_string()),
        Vec::new(),
    )
}

fn parse_yaml(locator: &str, kind: SurfaceKind, bytes: &[u8]) -> ParseBatch {
    let value = match serde_yaml::from_slice::<YamlValue>(bytes) {
        Ok(value) => value,
        Err(_) => return malformed(locator, kind),
    };
    let name = yaml_string(&value, "name")
        .or_else(|| yaml_string(&value, "event"))
        .or_else(|| safe_file_name(locator));
    let description = yaml_string(&value, "description");
    let mut batch = one_item(
        locator,
        kind,
        name,
        description,
        Some("structured_field".to_string()),
        safe_yaml_settings(&value),
    );
    let Some(hooks) = value.get("hooks").and_then(YamlValue::as_mapping) else {
        return batch;
    };
    for (event, handler) in hooks {
        let Some(event) = event.as_str() else {
            continue;
        };
        batch.items.push(ParsedItem {
            stable_source_locator: format!("{locator}#/hooks/{}", pointer_segment(event)),
            kind: SurfaceKind::Hook,
            name: Some(event.to_string()),
            description: handler
                .get("description")
                .and_then(YamlValue::as_str)
                .map(str::to_owned),
            description_source: handler
                .get("description")
                .and_then(YamlValue::as_str)
                .map(|_| "structured_field".to_string()),
            settings: safe_yaml_settings(handler),
            parse_state: ParseState::Parsed,
            issue_codes: Vec::new(),
        });
    }
    batch
}

fn safe_yaml_settings(value: &YamlValue) -> Vec<SafeSetting> {
    let Some(mapping) = value.as_mapping() else {
        return Vec::new();
    };
    let mut settings = mapping
        .iter()
        .filter_map(|(key, value)| {
            let key = key.as_str()?;
            if key == "description" {
                return None;
            }
            let redacted = key == "command" || secret_like(key);
            let safe_value = (!redacted && safe_setting_key(key))
                .then(|| match value {
                    YamlValue::String(value) => Some(value.clone()),
                    YamlValue::Bool(value) => Some(value.to_string()),
                    YamlValue::Number(value) => Some(value.to_string()),
                    _ => None,
                })
                .flatten();
            (redacted || safe_value.is_some()).then(|| SafeSetting {
                key: key.to_string(),
                present: true,
                redacted,
                value: safe_value,
            })
        })
        .collect::<Vec<_>>();
    settings.sort_by(|left, right| left.key.cmp(&right.key));
    settings
}

fn malformed(locator: &str, kind: SurfaceKind) -> ParseBatch {
    ParseBatch {
        items: vec![ParsedItem {
            stable_source_locator: locator.to_string(),
            kind,
            name: safe_file_name(locator),
            description: None,
            description_source: None,
            settings: Vec::new(),
            parse_state: ParseState::Malformed,
            issue_codes: vec!["malformed_document".to_string()],
        }],
        issues: vec![SafeIssue {
            project_id: None,
            code: "malformed_document".to_string(),
            safe_message: "A harness definition could not be parsed".to_string(),
            safe_relative_locator: Some(locator.to_string()),
        }],
    }
}

fn one_item(
    locator: &str,
    kind: SurfaceKind,
    name: Option<String>,
    description: Option<String>,
    description_source: Option<String>,
    settings: Vec<SafeSetting>,
) -> ParseBatch {
    ParseBatch {
        items: vec![ParsedItem {
            stable_source_locator: locator.to_string(),
            kind,
            name,
            description,
            description_source,
            settings,
            parse_state: if kind == SurfaceKind::Unclassified {
                ParseState::Unclassified
            } else {
                ParseState::Parsed
            },
            issue_codes: Vec::new(),
        }],
        issues: Vec::new(),
    }
}

fn safe_file_name(locator: &str) -> Option<String> {
    let path = locator.split('#').next().unwrap_or(locator);
    Path::new(path)
        .file_stem()
        .and_then(|name| name.to_str())
        .map(str::to_owned)
}

fn enclosing_component_directory(locator: &str) -> Option<String> {
    let path = locator.split('#').next().unwrap_or(locator);
    Path::new(path)
        .parent()
        .and_then(Path::file_name)
        .and_then(|name| name.to_str())
        .filter(|name| !name.is_empty() && *name != ".")
        .map(str::to_owned)
}

fn is_generic_component_file_name(name: &str) -> bool {
    matches!(
        name.to_ascii_lowercase().as_str(),
        "skill" | "readme" | "index" | "agent" | "agents" | "workflow" | "component" | "manifest"
    )
}

fn pointer_segment(value: &str) -> String {
    value.replace('~', "~0").replace('/', "~1")
}
