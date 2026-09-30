use std::collections::BTreeSet;
use std::fmt;

use serde_json::{Map, Value};

const HARNESSKIT_BEGIN: &str = "<!-- BEGIN HARNESSKIT GENERATED CONTEXT -->";
const HARNESSKIT_END: &str = "<!-- END HARNESSKIT GENERATED CONTEXT -->";
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

pub fn exact_copy(source: &[u8]) -> Vec<u8> {
    source.to_vec()
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
