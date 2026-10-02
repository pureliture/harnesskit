use std::collections::BTreeSet;
use std::fmt;

use serde_json::{Map, Value};

pub(crate) const HARNESSKIT_BEGIN: &str = "<!-- BEGIN HARNESSKIT GENERATED CONTEXT -->";
pub(crate) const HARNESSKIT_END: &str = "<!-- END HARNESSKIT GENERATED CONTEXT -->";
const LEGACY_BEGIN: &str = "<!-- BEGIN ROUTINE-HARNESS GENERATED CONTEXT -->";
const LEGACY_END: &str = "<!-- END ROUTINE-HARNESS GENERATED CONTEXT -->";

const CLAUDE_MANAGED_COMMAND_TOKENS: &[&str] = &[
    "human_doc_turn_scan.py",
    ".harnesskit/scripts/human_doc_turn_scan.py",
    ".claude/skills/optimal-response/hooks/stop-prompt-submit.cjs",
    ".claude/skills/optimal-response/hooks/stop-session-start.cjs",
    "/.claude/skills/optimal-response/hooks/stop-prompt-submit.cjs",
    "/.claude/skills/optimal-response/hooks/stop-session-start.cjs",
];
const CODEX_MANAGED_COMMAND_TOKENS: &[&str] = &[
    "human_doc_turn_scan.py",
    ".harnesskit/scripts/human_doc_turn_scan.py",
    ".codex/skills/optimal-response/hooks/stop-prompt-submit.cjs",
    "/.codex/skills/optimal-response/hooks/stop-prompt-submit.cjs",
    concat!(
        "$",
        "{CODEX_HOME:-$HOME/.codex}/skills/optimal-response/hooks/stop-prompt-submit.cjs"
    ),
];

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MergeError {
    code: &'static str,
    detail: String,
}

impl MergeError {
    fn new(code: &'static str, detail: impl Into<String>) -> Self {
        Self {
            code,
            detail: detail.into(),
        }
    }

    pub fn code(&self) -> &'static str {
        self.code
    }
}

impl fmt::Display for MergeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}: {}", self.code, self.detail)
    }
}

impl std::error::Error for MergeError {}

/// A singleton event/handler has no positional ambiguity. Unknown fields stay unowned.
pub(crate) fn selected_hook(document: &Value, event: &str) -> Result<Value, MergeError> {
    let groups = document["hooks"][event]
        .as_array()
        .filter(|a| a.len() == 1)
        .ok_or_else(|| {
            MergeError::new("ambiguous_hook_item", "event must have exactly one group")
        })?;
    let handlers = groups[0]["hooks"]
        .as_array()
        .filter(|a| a.len() == 1)
        .ok_or_else(|| {
            MergeError::new("ambiguous_hook_item", "group must have exactly one handler")
        })?;
    let handler = &handlers[0];
    if !handler.is_object() {
        return Err(MergeError::new("ambiguous_hook_item", "handler missing"));
    }
    Ok(
        serde_json::json!({"type":handler["type"],"command":handler["command"],"timeout":handler["timeout"]}),
    )
}
pub(crate) fn merge_selected_hook(
    current: &str,
    body: &str,
    event: &str,
) -> Result<String, MergeError> {
    let mut document: Value = serde_json::from_str(current)
        .map_err(|e| MergeError::new("invalid_json", e.to_string()))?;
    selected_hook(&document, event)?;
    let incoming: Value =
        serde_json::from_str(body).map_err(|e| MergeError::new("invalid_json", e.to_string()))?;
    let selected = selected_hook(&incoming, event)?;
    for key in ["type", "command", "timeout"] {
        document["hooks"][event][0]["hooks"][0][key] = selected[key].clone();
    }
    Ok(serde_json::to_string_pretty(&document).unwrap() + "\n")
}
pub fn exact_copy(source: &[u8]) -> Vec<u8> {
    source.to_vec()
}

/// Only an existing, unique, canonical project block grants imported ownership.
pub(crate) fn selected_project_rule(bytes: &[u8]) -> Result<String, MergeError> {
    let text = std::str::from_utf8(bytes)
        .map_err(|_| MergeError::new("ambiguous_managed_block", "invalid UTF-8"))?;
    let fail = || {
        MergeError::new(
            "ambiguous_managed_block",
            "existing canonical block required",
        )
    };
    if text.contains(LEGACY_BEGIN)
        || text.contains(LEGACY_END)
        || text.match_indices(HARNESSKIT_BEGIN).count() != 1
        || text.match_indices(HARNESSKIT_END).count() != 1
    {
        return Err(fail());
    }
    let begin = text.find(HARNESSKIT_BEGIN).ok_or_else(fail)?;
    let end = text.find(HARNESSKIT_END).ok_or_else(fail)?;
    let start = begin + HARNESSKIT_BEGIN.len();
    if end <= start
        || !text[start..].starts_with('\n')
        || (begin != 0 && !text[..begin].ends_with('\n'))
        || !text[..end].ends_with('\n')
        || !(text[end + HARNESSKIT_END.len()..].is_empty()
            || text[end + HARNESSKIT_END.len()..].starts_with('\n'))
    {
        return Err(fail());
    }
    Ok(text[start + 1..end].to_string())
}

pub fn merge_managed_block(
    current: &str,
    body: &str,
    begin_marker: &str,
    end_marker: &str,
) -> Result<String, MergeError> {
    if begin_marker.is_empty() || end_marker.is_empty() || begin_marker == end_marker {
        return Err(MergeError::new(
            "invalid_managed_block_marker",
            "managed block markers must be non-empty and distinct",
        ));
    }
    let body = body.trim_end();
    let block = format!("{begin_marker}\n{body}\n{end_marker}");
    let marker_pair = find_existing_managed_block_markers(current, begin_marker, end_marker)?;
    let Some((existing_begin, existing_end)) = marker_pair else {
        return Ok(format!("{}\n\n{block}\n", current.trim_end()));
    };

    let begin_index = current
        .find(existing_begin)
        .expect("validated managed block begin marker");
    let end_index = current
        .find(existing_end)
        .expect("validated managed block end marker");
    if end_index <= begin_index {
        return Err(MergeError::new(
            "ambiguous_managed_block",
            "managed block end marker must appear after begin marker",
        ));
    }
    let after_index = end_index + existing_end.len();
    Ok(format!(
        "{}{block}{}",
        &current[..begin_index],
        &current[after_index..]
    ))
}

fn find_existing_managed_block_markers<'a>(
    current: &str,
    begin_marker: &'a str,
    end_marker: &'a str,
) -> Result<Option<(&'a str, &'a str)>, MergeError> {
    let mut pairs = vec![(begin_marker, end_marker)];
    if (begin_marker, end_marker) == (HARNESSKIT_BEGIN, HARNESSKIT_END) {
        pairs.push((LEGACY_BEGIN, LEGACY_END));
    }

    let mut found = Vec::new();
    for (begin, end) in pairs {
        let begin_count = current.match_indices(begin).count();
        let end_count = current.match_indices(end).count();
        if begin_count == 0 && end_count == 0 {
            continue;
        }
        if begin_count != 1 || end_count != 1 {
            return Err(MergeError::new(
                "ambiguous_managed_block",
                "managed block markers must be absent or appear exactly once",
            ));
        }
        found.push((begin, end));
    }
    if found.len() > 1 {
        return Err(MergeError::new(
            "ambiguous_managed_block",
            "canonical and legacy marker pairs cannot coexist",
        ));
    }
    Ok(found.into_iter().next())
}

pub fn merge_json_deep(current: &str, body: &str, merge_key: &str) -> Result<String, MergeError> {
    let managed_tokens = match merge_key {
        "claude-settings-hooks" => CLAUDE_MANAGED_COMMAND_TOKENS,
        "codex-hooks" => CODEX_MANAGED_COMMAND_TOKENS,
        _ => {
            return Err(MergeError::new(
                "unsupported_merge_key",
                "unsupported JSON merge key",
            ))
        }
    };

    let source = parse_json_object(body, "source")?;
    let mut existing = if current.trim().is_empty() {
        Map::new()
    } else {
        parse_json_object(current, "destination")?
    };
    deep_merge_json(&mut existing, source, managed_tokens)?;
    let rendered = serde_json::to_string_pretty(&Value::Object(existing))
        .map_err(|error| MergeError::new("invalid_json", error.to_string()))?;
    Ok(format!("{rendered}\n"))
}

fn parse_json_object(text: &str, side: &str) -> Result<Map<String, Value>, MergeError> {
    match serde_json::from_str(text) {
        Ok(Value::Object(object)) => Ok(object),
        Ok(_) => Err(MergeError::new(
            "invalid_json_merge_shape",
            format!("json-deep-merge {side} must be a JSON object"),
        )),
        Err(error) => Err(MergeError::new(
            "invalid_json",
            format!("json-deep-merge {side} is invalid: {error}"),
        )),
    }
}

fn deep_merge_json(
    existing: &mut Map<String, Value>,
    source: Map<String, Value>,
    managed_tokens: &[&str],
) -> Result<(), MergeError> {
    for (key, value) in source {
        if key == "hooks" {
            let source_hooks = value.as_object().ok_or_else(|| {
                MergeError::new(
                    "invalid_json_merge_shape",
                    "json-deep-merge source hooks must be a JSON object",
                )
            })?;
            let existing_hooks = match existing.remove("hooks") {
                None => Map::new(),
                Some(Value::Object(object)) => object,
                Some(_) => {
                    return Err(MergeError::new(
                        "invalid_json_merge_shape",
                        "json-deep-merge destination hooks must be a JSON object",
                    ))
                }
            };
            existing.insert(
                "hooks".to_string(),
                Value::Object(merge_json_hooks(
                    existing_hooks,
                    source_hooks,
                    managed_tokens,
                )?),
            );
            continue;
        }
        match (existing.get_mut(&key), value) {
            (Some(Value::Object(current)), Value::Object(incoming)) => {
                deep_merge_json(current, incoming, managed_tokens)?;
            }
            (_, incoming) => {
                existing.insert(key, incoming);
            }
        }
    }
    Ok(())
}

fn merge_json_hooks(
    existing: Map<String, Value>,
    source: &Map<String, Value>,
    managed_tokens: &[&str],
) -> Result<Map<String, Value>, MergeError> {
    let source_commands: BTreeSet<String> = source
        .values()
        .flat_map(hook_commands_from_groups)
        .map(ToOwned::to_owned)
        .collect();
    let mut merged = Map::new();

    for (event, groups_value) in existing {
        let groups = groups_value.as_array().ok_or_else(|| {
            MergeError::new(
                "invalid_json_merge_shape",
                format!("settings hooks event must be a list: {event}"),
            )
        })?;
        let retained: Vec<_> = groups
            .iter()
            .filter(|group| !hook_group_is_managed(group, &source_commands, managed_tokens))
            .cloned()
            .collect();
        if !retained.is_empty() {
            merged.insert(event, Value::Array(retained));
        }
    }

    for (event, groups_value) in source {
        let groups = groups_value.as_array().ok_or_else(|| {
            MergeError::new(
                "invalid_json_merge_shape",
                format!("settings hooks event must be a list: {event}"),
            )
        })?;
        let target = merged
            .entry(event.clone())
            .or_insert_with(|| Value::Array(Vec::new()))
            .as_array_mut()
            .expect("merge-created hooks event must be an array");
        for group in groups {
            if !target.contains(group) {
                target.push(group.clone());
            }
        }
    }
    Ok(merged)
}

fn hook_commands_from_groups(value: &Value) -> impl Iterator<Item = &str> {
    value
        .as_array()
        .into_iter()
        .flatten()
        .flat_map(hook_commands_from_group)
}

fn hook_commands_from_group(group: &Value) -> impl Iterator<Item = &str> {
    group
        .as_object()
        .and_then(|object| object.get("hooks"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|hook| {
            hook.as_object()
                .and_then(|object| object.get("command"))
                .and_then(Value::as_str)
        })
}

fn hook_group_is_managed(
    group: &Value,
    source_commands: &BTreeSet<String>,
    managed_tokens: &[&str],
) -> bool {
    hook_commands_from_group(group).any(|command| {
        source_commands.contains(command)
            || managed_tokens.iter().any(|token| command.contains(token))
    })
}

#[cfg(test)]
mod selected_codex_tests {
    #[test]
    fn registration_path_alias_cannot_hide_second_owner() {
        let text = "[agents.reviewer]\nconfig_file = 'agents/reviewer.toml'\n[agents.other]\nconfig_file = 'agents/./reviewer.toml'\n";
        assert!(super::selected_codex_agent(text.as_bytes(), "reviewer").is_err());
    }
}

/// Typed selected registration, not the surrounding settings file.
pub(crate) fn selected_codex_agent(bytes: &[u8], name: &str) -> Result<Value, MergeError> {
    let fail = || {
        MergeError::new(
            "codex_registration_ambiguous",
            "selected Codex registration is missing, invalid or aliased",
        )
    };
    let text = std::str::from_utf8(bytes).map_err(|_| fail())?;
    let doc: toml::Value = toml::from_str(text).map_err(|_| fail())?;
    let agents = doc
        .get("agents")
        .and_then(toml::Value::as_table)
        .ok_or_else(fail)?;
    let selected = agents
        .get(name)
        .and_then(toml::Value::as_table)
        .ok_or_else(fail)?;
    let path = selected
        .get("config_file")
        .and_then(toml::Value::as_str)
        .ok_or_else(fail)?;
    if path != format!("agents/{name}.toml")
        || agents.iter().any(|(k, v)| {
            k != name
                && v.get("config_file")
                    .and_then(toml::Value::as_str)
                    .is_some_and(|other| {
                        // Fail closed on alternate spellings instead of normalizing a selected path.
                        other == path
                            || other.contains("\\")
                            || other.split('/').any(|part| matches!(part, "" | "." | ".."))
                            || other.starts_with('/')
                            || other.starts_with('~')
                    })
        })
    {
        return Err(fail());
    }
    serde_json::to_value(selected).map_err(|_| fail())
}

pub(crate) fn merge_selected_codex_agent(
    current: &str,
    body: &str,
    name: &str,
) -> Result<String, MergeError> {
    let fail = || {
        MergeError::new(
            "codex_registration_ambiguous",
            "selected Codex registration cannot be safely replaced",
        )
    };
    selected_codex_agent(current.as_bytes(), name)?;
    selected_codex_agent(body.as_bytes(), name)?;
    let mut destination: toml_edit::Document = current.parse().map_err(|_| fail())?;
    let source: toml_edit::Document = body.parse().map_err(|_| fail())?;
    let table = destination["agents"][name]
        .as_table_mut()
        .ok_or_else(fail)?;
    let incoming = source["agents"][name].as_table().ok_or_else(fail)?;
    // Keep table/key comments and unowned tables byte-preserving through toml_edit.
    table.retain(|k, _| incoming.contains_key(k));
    for (key, value) in incoming.iter() {
        let mut value = value.clone();
        if let (Some(old), Some(new)) = (
            table.get(key).and_then(toml_edit::Item::as_value),
            value.as_value_mut(),
        ) {
            *new.decor_mut() = old.decor().clone();
        }
        let key_decor = table.key_decor(key).cloned();
        table.insert(key, value);
        if let Some(decor) = key_decor {
            *table.key_decor_mut(key).unwrap() = decor;
        }
    }
    let result = destination.to_string();
    selected_codex_agent(result.as_bytes(), name)?;
    Ok(result)
}

pub fn merge_toml_agents(current: &str, body: &str, merge_key: &str) -> Result<String, MergeError> {
    if merge_key != "codex-agents" {
        return Err(MergeError::new(
            "unsupported_merge_key",
            "unsupported TOML merge key",
        ));
    }
    validate_toml(body, "source")?;
    let owned_names = toml_owned_agent_names(body);
    let source_block = body.trim_matches('\n');
    if current.trim().is_empty() {
        return Ok(if source_block.is_empty() {
            String::new()
        } else {
            format!("{source_block}\n")
        });
    }

    validate_toml(current, "destination")?;
    let preserved = strip_owned_agent_regions(current, &owned_names);
    let preserved_body = preserved.trim_end_matches('\n');
    let merged = match (preserved_body.is_empty(), source_block.is_empty()) {
        (true, true) => String::new(),
        (true, false) => format!("{source_block}\n"),
        (false, true) => format!("{preserved_body}\n"),
        (false, false) => format!("{preserved_body}\n{source_block}\n"),
    };
    validate_toml(&merged, "result")?;
    Ok(merged)
}

fn toml_owned_agent_names(text: &str) -> BTreeSet<String> {
    text.lines().filter_map(agent_table_name).collect()
}

fn agent_table_name(line: &str) -> Option<String> {
    let trimmed = line.trim();
    let closing = trimmed.find(']')?;
    let header = &trimmed[..=closing];
    let rest = trimmed[closing + 1..].trim();
    if !rest.is_empty() && !rest.starts_with('#') {
        return None;
    }
    let token = header.strip_prefix("[agents.")?.strip_suffix(']')?;
    let quoted = (token.starts_with('"') && token.ends_with('"'))
        || (token.starts_with('\'') && token.ends_with('\''));
    (quoted && token.len() >= 2).then(|| token.to_string())
}

fn strip_owned_agent_regions(current: &str, owned_names: &BTreeSet<String>) -> String {
    let lines: Vec<_> = current.split_inclusive('\n').collect();
    let mut kept = String::new();
    let mut index = 0;
    while index < lines.len() {
        if agent_table_name(lines[index])
            .as_ref()
            .is_some_and(|name| owned_names.contains(name))
        {
            index += 1;
            while index < lines.len() && !is_top_level_table_header(lines[index]) {
                index += 1;
            }
            continue;
        }
        kept.push_str(lines[index]);
        index += 1;
    }
    kept
}

fn is_top_level_table_header(line: &str) -> bool {
    line.trim_start().starts_with('[')
}

fn validate_toml(text: &str, side: &str) -> Result<(), MergeError> {
    let mut tables = BTreeSet::new();
    let mut bracket_depth = 0_i32;
    for (line_index, raw_line) in text.lines().enumerate() {
        let line = raw_line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if line.starts_with('[') {
            if bracket_depth != 0 {
                return Err(invalid_toml(
                    side,
                    line_index,
                    "table inside unfinished value",
                ));
            }
            let header = parse_toml_table_header(line)
                .ok_or_else(|| invalid_toml(side, line_index, "malformed table header"))?;
            if !tables.insert(header.to_string()) {
                return Err(invalid_toml(side, line_index, "duplicate table header"));
            }
            continue;
        }
        if bracket_depth == 0 {
            let Some((key, value)) = split_toml_assignment(line) else {
                return Err(invalid_toml(side, line_index, "malformed assignment"));
            };
            if key.trim().is_empty() || value.trim().is_empty() {
                return Err(invalid_toml(side, line_index, "empty assignment"));
            }
        }
        bracket_depth += toml_bracket_delta(line);
        if bracket_depth < 0 {
            return Err(invalid_toml(side, line_index, "unbalanced value"));
        }
    }
    if bracket_depth != 0 {
        return Err(MergeError::new(
            "invalid_toml",
            format!("toml-agents-merge {side} has an unfinished value"),
        ));
    }
    Ok(())
}

fn invalid_toml(side: &str, line_index: usize, reason: &str) -> MergeError {
    MergeError::new(
        "invalid_toml",
        format!(
            "toml-agents-merge {side} is not valid TOML at line {}: {reason}",
            line_index + 1
        ),
    )
}

fn parse_toml_table_header(line: &str) -> Option<&str> {
    let (open, close) = if line.starts_with("[[") {
        ("[[", "]]")
    } else {
        ("[", "]")
    };
    let closing_index = line.find(close)?;
    let end = closing_index + close.len();
    let header = &line[..end];
    let rest = line[end..].trim();
    let body = header.strip_prefix(open)?.strip_suffix(close)?.trim();
    if body.is_empty() || (!rest.is_empty() && !rest.starts_with('#')) || !quotes_are_balanced(body)
    {
        return None;
    }
    Some(header)
}

fn quotes_are_balanced(text: &str) -> bool {
    let mut single = false;
    let mut double = false;
    let mut escaped = false;
    for character in text.chars() {
        if escaped {
            escaped = false;
            continue;
        }
        if character == '\\' && double {
            escaped = true;
        } else if character == '"' && !single {
            double = !double;
        } else if character == '\'' && !double {
            single = !single;
        }
    }
    !single && !double && !escaped
}

fn split_toml_assignment(line: &str) -> Option<(&str, &str)> {
    let mut single = false;
    let mut double = false;
    let mut escaped = false;
    for (index, character) in line.char_indices() {
        if escaped {
            escaped = false;
            continue;
        }
        if character == '\\' && double {
            escaped = true;
        } else if character == '"' && !single {
            double = !double;
        } else if character == '\'' && !double {
            single = !single;
        } else if character == '=' && !single && !double {
            return Some((&line[..index], &line[index + 1..]));
        }
    }
    None
}

fn toml_bracket_delta(line: &str) -> i32 {
    let value = split_toml_assignment(line)
        .map(|(_, value)| value)
        .unwrap_or(line);
    let mut delta = 0;
    let mut single = false;
    let mut double = false;
    let mut escaped = false;
    for character in value.chars() {
        if escaped {
            escaped = false;
            continue;
        }
        if character == '\\' && double {
            escaped = true;
        } else if character == '"' && !single {
            double = !double;
        } else if character == '\'' && !double {
            single = !single;
        } else if !single && !double {
            if character == '[' || character == '{' {
                delta += 1;
            } else if character == ']' || character == '}' {
                delta -= 1;
            }
        }
    }
    delta
}
