use std::collections::BTreeMap;
use std::path::Path;

use regex::Regex;
use serde_json::Value;

const COMPONENT_SCHEMA: &str = "schemas/component.schema.json";
const AGENT_SCHEMA: &str = "schemas/agent.schema.json";
const WORKFLOW_SCHEMA: &str = "schemas/workflow.schema.json";
const COMPOSITE_SCHEMA: &str = "schemas/composite.schema.json";
const PROFILE_SCHEMA: &str = "schemas/profile.schema.json";

pub(super) struct CanonicalSchemas {
    component: Value,
    agent: Value,
    workflow: Value,
    composite: Value,
    profile: Value,
}

impl CanonicalSchemas {
    pub(super) fn load(root: &Path) -> Result<(Self, BTreeMap<String, Vec<u8>>), String> {
        let mut sources = BTreeMap::new();
        let component = load_schema(root, COMPONENT_SCHEMA, &mut sources)?;
        let agent = load_schema(root, AGENT_SCHEMA, &mut sources)?;
        let workflow = load_schema(root, WORKFLOW_SCHEMA, &mut sources)?;
        let composite = load_schema(root, COMPOSITE_SCHEMA, &mut sources)?;
        let profile = load_schema(root, PROFILE_SCHEMA, &mut sources)?;
        Ok((
            Self {
                component,
                agent,
                workflow,
                composite,
                profile,
            },
            sources,
        ))
    }

    pub(super) fn manifest_accepts(&self, kind: &str, value: &serde_yaml::Value) -> bool {
        let schema = match kind {
            "agent" => &self.agent,
            "workflow" => &self.workflow,
            "composite" => &self.composite,
            _ => &self.component,
        };
        yaml_accepts(schema, value)
    }

    pub(super) fn profile_accepts(&self, value: &serde_yaml::Value) -> bool {
        yaml_accepts(&self.profile, value)
    }
}

fn load_schema(
    root: &Path,
    relative: &str,
    sources: &mut BTreeMap<String, Vec<u8>>,
) -> Result<Value, String> {
    let candidate = root.join(relative);
    let canonical = std::fs::canonicalize(candidate)
        .map_err(|_| "Canonical SoT schema is unavailable".to_string())?;
    if !canonical.starts_with(root) || !canonical.is_file() {
        return Err("Canonical SoT schema is unavailable".to_string());
    }
    let bytes =
        std::fs::read(canonical).map_err(|_| "Canonical SoT schema is unavailable".to_string())?;
    let schema = serde_json::from_slice::<Value>(&bytes)
        .map_err(|_| "Canonical SoT schema is malformed".to_string())?;
    if !schema.is_object() {
        return Err("Canonical SoT schema is malformed".to_string());
    }
    sources.insert(relative.to_string(), bytes);
    Ok(schema)
}

fn yaml_accepts(schema: &Value, value: &serde_yaml::Value) -> bool {
    serde_json::to_value(value)
        .ok()
        .is_some_and(|instance| schema_accepts(schema, &instance))
}

fn schema_accepts(schema: &Value, instance: &Value) -> bool {
    let Some(schema) = schema.as_object() else {
        return schema.as_bool().unwrap_or(false);
    };

    if let Some(expected) = schema.get("const") {
        if instance != expected {
            return false;
        }
    }
    if let Some(values) = schema.get("enum").and_then(Value::as_array) {
        if !values.iter().any(|value| value == instance) {
            return false;
        }
    }
    if let Some(expected_type) = schema.get("type") {
        if !type_accepts(expected_type, instance) {
            return false;
        }
    }
    if let Some(pattern) = schema.get("pattern").and_then(Value::as_str) {
        let Some(value) = instance.as_str() else {
            return false;
        };
        if !Regex::new(pattern)
            .ok()
            .is_some_and(|pattern| pattern.is_match(value))
        {
            return false;
        }
    }
    if let Some(min_length) = schema.get("minLength").and_then(Value::as_u64) {
        if instance
            .as_str()
            .is_none_or(|value| value.chars().count() < min_length as usize)
        {
            return false;
        }
    }

    if let Some(object) = instance.as_object() {
        if let Some(required) = schema.get("required").and_then(Value::as_array) {
            if !required
                .iter()
                .filter_map(Value::as_str)
                .all(|key| object.contains_key(key))
            {
                return false;
            }
        }
        let properties = schema.get("properties").and_then(Value::as_object);
        if let Some(properties) = properties {
            for (key, property_schema) in properties {
                if let Some(value) = object.get(key) {
                    if !schema_accepts(property_schema, value) {
                        return false;
                    }
                }
            }
        }
        if let Some(property_names) = schema.get("propertyNames") {
            if object
                .keys()
                .any(|key| !schema_accepts(property_names, &Value::String(key.clone())))
            {
                return false;
            }
        }
        if let Some(additional) = schema.get("additionalProperties") {
            for (key, value) in object {
                if properties.is_some_and(|properties| properties.contains_key(key)) {
                    continue;
                }
                match additional {
                    Value::Bool(true) => {}
                    Value::Bool(false) => return false,
                    nested if !schema_accepts(nested, value) => return false,
                    _ => {}
                }
            }
        }
    }

    if let Some(array) = instance.as_array() {
        if let Some(min_items) = schema.get("minItems").and_then(Value::as_u64) {
            if array.len() < min_items as usize {
                return false;
            }
        }
        if schema.get("uniqueItems").and_then(Value::as_bool) == Some(true)
            && array
                .iter()
                .enumerate()
                .any(|(index, value)| array[index + 1..].contains(value))
        {
            return false;
        }
        if let Some(item_schema) = schema.get("items") {
            if !array.iter().all(|value| schema_accepts(item_schema, value)) {
                return false;
            }
        }
    }

    if let Some(all_of) = schema.get("allOf").and_then(Value::as_array) {
        if !all_of
            .iter()
            .all(|subschema| schema_accepts(subschema, instance))
        {
            return false;
        }
    }
    if let Some(condition) = schema.get("if") {
        if schema_accepts(condition, instance) {
            if let Some(consequence) = schema.get("then") {
                if !schema_accepts(consequence, instance) {
                    return false;
                }
            }
        } else if let Some(alternative) = schema.get("else") {
            if !schema_accepts(alternative, instance) {
                return false;
            }
        }
    }

    true
}

fn type_accepts(expected: &Value, instance: &Value) -> bool {
    match expected {
        Value::String(expected) => value_has_type(instance, expected),
        Value::Array(expected) => expected
            .iter()
            .filter_map(Value::as_str)
            .any(|expected| value_has_type(instance, expected)),
        _ => false,
    }
}

fn value_has_type(value: &Value, expected: &str) -> bool {
    match expected {
        "object" => value.is_object(),
        "array" => value.is_array(),
        "string" => value.is_string(),
        "boolean" => value.is_boolean(),
        "null" => value.is_null(),
        "number" => value.is_number(),
        "integer" => value.as_i64().is_some() || value.as_u64().is_some(),
        _ => false,
    }
}
