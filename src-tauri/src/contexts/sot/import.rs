//! Explicit, revision-bound Local component import. Never applies to its source.
use super::{schema::CanonicalSchemas, snapshot::SotSnapshotService};
use crate::contexts::install::workspace::SourceRevisionManifest;
use crate::contexts::local::{LocalContext, SurfaceKind};
use serde::{Deserialize, Serialize};
use serde_json::json;
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    path::PathBuf,
    sync::Mutex,
    time::{Duration, Instant},
};

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct ImportedSkillRequest {
    pub checkout_id: String,
    pub sot_snapshot_id: String,
    pub component_id: String,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct ImportedSkillEdit {
    pub checkout_id: String,
    pub sot_snapshot_id: String,
    pub component_id: String,
    pub content: String,
    #[serde(default)]
    pub document: Option<String>,
}
#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ImportedSkillDetail {
    pub content: String,
    pub managed: bool,
    pub source_locator: String,
    pub documents: Vec<ImportedDocument>,
}
#[derive(Serialize)]
pub(crate) struct ImportedDocument {
    pub path: String,
    pub content: String,
    pub managed: bool,
}

#[derive(Debug, Clone, Serialize)]
pub(crate) struct ImportPreview {
    pub preview_id: String,
    pub fingerprint: String,
    pub component_id: String,
    pub kind: String,
    pub name: String,
    pub content: String,
    pub manifest: String,
    pub generated_artifact_count: usize,
    pub source_unchanged: bool,
    pub support_documents: Vec<SupportDocument>,
}
#[derive(Debug, Clone, Serialize)]
pub(crate) struct SupportDocument {
    pub path: String,
    pub content: String,
    pub source_sha256: String,
    pub source_revision: String,
}
#[derive(Default)]
pub(super) struct ImportState(pub Mutex<BTreeMap<String, PreparedImport>>);
pub(super) struct PreparedImport {
    pub checkout_id: String,
    pub sot_id: String,
    pub snapshot_id: String,
    pub instance_id: String,
    pub source_revision: String,
    pub checkout_revision: String,
    pub preview: ImportPreview,
    pub registry_before: Vec<u8>,
    pub registry_after: Vec<u8>,
    pub source_link: serde_json::Value,
    pub expires: Instant,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct ImportRequest {
    pub checkout_id: String,
    pub sot_snapshot_id: String,
    pub snapshot_id: String,
    pub instance_id: String,
    pub source_revision: String,
}
#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(crate) struct ImportConfirmation {
    pub preview_id: String,
    pub fingerprint: String,
    pub confirmed: bool,
}
fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

// Standard metadata is retained on its source target; Claude extensions are not
// reinterpreted as portable permissions or mapped to another tool.
fn skill_metadata_field(key: Option<&str>, tool: crate::contexts::local::ToolId) -> bool {
    matches!(
        key,
        Some("license" | "compatibility" | "metadata" | "allowed-tools")
    ) || (tool == crate::contexts::local::ToolId::ClaudeCode
        && matches!(
            key,
            Some(
                "argument-hint"
                    | "arguments"
                    | "disable-model-invocation"
                    | "user-invocable"
                    | "model"
                    | "effort"
                    | "context"
                    | "agent"
                    | "background"
                    | "when_to_use"
                    | "disallowed-tools"
                    | "paths"
                    | "shell"
            )
        ))
}

fn validate_skill_metadata(
    key: &str,
    value: &serde_yaml::Value,
    front: &serde_yaml::Value,
    tool: crate::contexts::local::ToolId,
) -> Result<(), String> {
    let nonempty = |v: &serde_yaml::Value| v.as_str().is_some_and(|s| !s.trim().is_empty());
    let string_or_list = || {
        nonempty(value)
            || value
                .as_sequence()
                .is_some_and(|items| !items.is_empty() && items.iter().all(nonempty))
    };
    let valid = match key {
        "metadata" => value
            .as_mapping()
            .is_some_and(|m| m.iter().all(|(k, v)| nonempty(k) && v.is_string())),
        "compatibility" => value
            .as_str()
            .is_some_and(|s| !s.trim().is_empty() && s.chars().count() <= 500),
        "disable-model-invocation" | "user-invocable" | "background" => value.is_bool(),
        "allowed-tools" => {
            if tool == crate::contexts::local::ToolId::ClaudeCode {
                string_or_list()
            } else {
                nonempty(value)
            }
        }
        "arguments" | "disallowed-tools" | "paths" => string_or_list(),
        "effort" => matches!(
            value.as_str(),
            Some("low" | "medium" | "high" | "xhigh" | "max")
        ),
        "context" => value.as_str() == Some("fork"),
        "shell" => matches!(value.as_str(), Some("bash" | "powershell")),
        "agent" => {
            if !matches!(value.as_str(), Some("Explore" | "Plan" | "general-purpose")) {
                return Err("import_skill_dependency_unresolved".into());
            }
            front.get("context").and_then(|v| v.as_str()) == Some("fork")
        }
        "model" => value.as_str().is_some_and(|s| {
            regex::Regex::new(r"^[A-Za-z][A-Za-z0-9_.-]*$")
                .unwrap()
                .is_match(s)
        }),
        _ => nonempty(value),
    };
    if !valid
        || (key == "background" && front.get("context").and_then(|v| v.as_str()) != Some("fork"))
    {
        return Err("import_skill_option_invalid".into());
    }
    Ok(())
}

pub(super) fn prepare(
    local: &LocalContext,
    checkout_id: &str,
    sot_id: &str,
    snapshot_id: &str,
    instance_id: &str,
    source_revision: &str,
    root: &std::path::Path,
) -> Result<PreparedImport, String> {
    let publication = local.publication(snapshot_id)?;
    let instance = publication
        .snapshot
        .instances
        .iter()
        .find(|i| i.instance_id == instance_id)
        .ok_or("instance_unavailable")?;
    let handle = publication
        .instance_handles
        .get(instance_id)
        .ok_or("instance_unavailable")?;
    if instance.kind == SurfaceKind::Rule {
        return prepare_rule(
            local,
            checkout_id,
            sot_id,
            snapshot_id,
            instance_id,
            source_revision,
            root,
        );
    }
    if instance.kind == SurfaceKind::Hook
        && instance.tool_id == crate::contexts::local::ToolId::ClaudeCode
    {
        return prepare_hook(
            local,
            checkout_id,
            sot_id,
            snapshot_id,
            instance_id,
            source_revision,
            root,
        );
    }
    let antigravity_agent = instance.kind == SurfaceKind::Agent
        && instance.tool_id == crate::contexts::local::ToolId::Antigravity
        && instance.scope == crate::contexts::local::domain::Scope::Project;
    let codex_agent = instance.kind == SurfaceKind::Agent
        && instance.tool_id == crate::contexts::local::ToolId::Codex;
    let agent = instance.kind == SurfaceKind::Agent
        && (instance.tool_id == crate::contexts::local::ToolId::ClaudeCode
            || antigravity_agent
            || codex_agent);
    let kind = if agent { "agent" } else { "skill" };
    let body_file = if agent { "prompt.md" } else { "SKILL.md" };
    if (!agent && instance.kind != SurfaceKind::Skill) || handle.config_entry_locator.is_some() {
        return Err("import_standalone_skill_required".into());
    }
    let captured = local
        .capture_revision_bound_source(snapshot_id, instance_id, source_revision, 256 * 1024)
        .map_err(|e| e.code)?;
    let bytes = captured.into_bytes();
    let original_content = String::from_utf8(bytes).map_err(|_| "import_invalid_utf8")?;
    let mut codex_registration = None;
    let mut codex_options = serde_json::Map::new();
    let content = if codex_agent {
        let doc: toml::Value =
            toml::from_str(&original_content).map_err(|_| "import_agent_toml_invalid")?;
        let table = doc.as_table().ok_or("import_agent_toml_invalid")?;
        if table.keys().any(|k| {
            !matches!(
                k.as_str(),
                "name"
                    | "description"
                    | "model"
                    | "model_reasoning_effort"
                    | "developer_instructions"
            )
        }) {
            return Err("import_agent_option_unsupported".into());
        }
        let name = doc
            .get("name")
            .and_then(toml::Value::as_str)
            .ok_or("import_name_required")?;
        let description = doc
            .get("description")
            .and_then(toml::Value::as_str)
            .ok_or("import_description_required")?;
        let body = doc
            .get("developer_instructions")
            .and_then(toml::Value::as_str)
            .ok_or("import_body_required")?;
        let source_path =
            handle
                .raw_relative_components
                .iter()
                .fold(handle.canonical_root.clone(), |p, c| {
                    use std::os::unix::ffi::OsStringExt;
                    p.join(std::ffi::OsString::from_vec(c.clone()))
                });
        if source_path.file_name().and_then(|s| s.to_str()) != Some(&format!("{name}.toml")) {
            return Err("import_agent_name_path_mismatch".into());
        }
        for key in ["model", "model_reasoning_effort"] {
            if let Some(v) = table.get(key) {
                let s = v.as_str().ok_or("import_agent_option_unsupported")?;
                if !regex::Regex::new(r"^[A-Za-z][A-Za-z0-9_.-]*$")
                    .unwrap()
                    .is_match(s)
                    || (key == "model_reasoning_effort"
                        && !matches!(s, "minimal" | "low" | "medium" | "high" | "xhigh"))
                {
                    return Err("import_agent_option_unsupported".into());
                }
                codex_options.insert(key.into(), json!(s));
            }
        }
        codex_options.insert("name".into(), json!(name));
        codex_options.insert("description".into(), json!(description));
        let config_path = source_path
            .parent()
            .and_then(|p| p.parent())
            .ok_or("import_source_unsafe")?
            .join("config.toml");
        let matching: Vec<_> = publication
            .instance_handles
            .iter()
            .filter(|(_, h)| {
                h.canonical_root == handle.canonical_root
                    && h.raw_relative_components
                        .iter()
                        .fold(h.canonical_root.clone(), |p, c| {
                            use std::os::unix::ffi::OsStringExt;
                            p.join(std::ffi::OsString::from_vec(c.clone()))
                        })
                        == config_path
                    && h.config_entry_locator.is_none()
            })
            .collect();
        if matching.len() != 1 {
            return Err("import_agent_registration_ambiguous".into());
        }
        let (config_id, _) = matching[0];
        let captured_config = local
            .capture_current_source(snapshot_id, config_id, 256 * 1024)
            .map_err(|e| e.code)?;
        let config = captured_config.into_bytes();
        let selected = crate::contexts::install::merge::selected_codex_agent(&config, name)
            .map_err(|_| "import_agent_registration_ambiguous")?;
        if selected["description"] != json!(description)
            || selected["config_file"] != json!(format!("agents/{name}.toml"))
        {
            return Err("import_agent_registration_mismatch".into());
        }
        if selected.as_object().is_none_or(|m| {
            m.keys().any(|k| {
                !matches!(
                    k.as_str(),
                    "description" | "config_file" | "nickname_candidates"
                )
            })
        }) || selected.get("nickname_candidates").is_some_and(|v| {
            v.as_array().is_none_or(|a| {
                a.iter()
                    .any(|v| v.as_str().is_none_or(|s| s.trim().is_empty()))
            })
        }) {
            return Err("import_agent_option_unsupported".into());
        }
        validate_agent_edit(&serde_json::to_string(&selected).unwrap())?;
        codex_options.insert("registration".into(), selected.clone());
        let config_locator = publication
            .snapshot
            .instances
            .iter()
            .find(|i| &i.instance_id == config_id)
            .ok_or("instance_unavailable")?
            .stable_source_locator
            .clone();
        codex_registration = Some(
            json!({"tool":instance.tool_id,"scope":instance.scope,"project_id":instance.project_id,"source_path":config_path,"source_locator":config_locator,"source_revision":digest(&serde_json::to_vec(&selected).unwrap()),"content_sha256":digest(&serde_json::to_vec(&selected).unwrap()),"selected_codex_agent":name,"config_entry_locator":format!("/agents/{name}"),"source_role":"registration","managed":false}),
        );
        format!(
            "---\n{}---\n{}",
            serde_yaml::to_string(&json!({"name":name,"description":description})).unwrap(),
            body
        )
    } else {
        original_content.clone()
    };
    // A frontmatter delimiter is a whole line, never text inside a YAML scalar.
    let mut lines = content.split_inclusive('\n');
    if lines.next().map(|line| line.trim_end_matches(['\r', '\n'])) != Some("---") {
        return Err("import_frontmatter_required".into());
    }
    let mut front_text = String::new();
    let mut closed = false;
    for line in lines.by_ref() {
        if line.trim_end_matches(['\r', '\n']) == "---" {
            closed = true;
            break;
        }
        front_text.push_str(line);
    }
    if !closed {
        return Err("import_frontmatter_required".into());
    }
    let front: serde_yaml::Value =
        serde_yaml::from_str(&front_text).map_err(|_| "import_frontmatter_invalid")?;
    let body: String = lines.collect();
    let name = front
        .get("name")
        .and_then(|v| v.as_str())
        .ok_or("import_name_required")?
        .to_string();
    if !regex::Regex::new("^[a-z][a-z0-9-]{1,63}$")
        .unwrap()
        .is_match(&name)
    {
        return Err("import_name_invalid".into());
    }
    let summary = front
        .get("description")
        .and_then(|v| v.as_str())
        .filter(|v| !v.trim().is_empty())
        .ok_or("import_description_required")?;
    // Retain qualified source-tool metadata; unknown fields are never silently discarded.
    if front.as_mapping().is_none_or(|m| {
        m.keys().any(|k| {
            !(matches!(k.as_str(), Some("name" | "description"))
                || (agent
                    && (k.as_str() == Some("tools")
                        || (!antigravity_agent && matches!(k.as_str(), Some("model" | "color")))))
                || (!agent && skill_metadata_field(k.as_str(), instance.tool_id)))
        })
    }) {
        return Err("import_frontmatter_unsupported".into());
    }
    let source_path =
        handle
            .raw_relative_components
            .iter()
            .fold(handle.canonical_root.clone(), |p, c| {
                use std::os::unix::ffi::OsStringExt;
                p.join(std::ffi::OsString::from_vec(c.clone()))
            });
    if antigravity_agent
        && source_path.file_name().and_then(|s| s.to_str()) != Some(&format!("{name}.md"))
    {
        return Err("import_agent_name_path_mismatch".into());
    }
    let support_documents = if agent {
        vec![]
    } else {
        capture_support_documents(
            source_path.parent().ok_or("import_source_unsafe")?,
            &content,
        )?
    };
    let safety_content = if agent {
        content.clone()
    } else {
        strip_document_links(&content)?
    };
    let unsafe_content = regex::Regex::new(r"(?i)(-----BEGIN .*PRIVATE KEY|\b(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*\S+|(?:/Users/|/home/|/private/|file://)|\b(?:scripts|references|assets|templates)/|\]\([^)]*\)|\{\{|\.\./)").unwrap();
    if unsafe_content.is_match(&safety_content) {
        return Err("import_sensitive_or_dependency_content".into());
    }
    if body.trim().is_empty() {
        return Err("import_body_required".into());
    }
    let mut options = serde_json::Map::new();
    if !agent {
        let mut metadata = serde_json::Map::new();
        for (key, value) in front.as_mapping().unwrap() {
            let key = key.as_str().ok_or("import_frontmatter_unsupported")?;
            if matches!(key, "name" | "description") {
                continue;
            }
            validate_skill_metadata(key, value, &front, instance.tool_id)?;
            metadata.insert(
                key.into(),
                serde_json::to_value(value).map_err(|_| "import_skill_option_invalid")?,
            );
        }
        if !metadata.is_empty() {
            options.insert("skill_frontmatter".into(), json!(metadata));
        }
    }
    if antigravity_agent {
        let tools = front
            .get("tools")
            .and_then(|v| v.as_mapping())
            .ok_or("import_agent_option_unsupported")?;
        let fields = [
            "enable_write_tools",
            "enable_mcp_tools",
            "enable_subagent_tools",
        ];
        if tools.len() != fields.len()
            || fields.iter().any(|k| {
                tools
                    .get(serde_yaml::Value::String((*k).into()))
                    .is_none_or(|v| !v.is_bool())
            })
        {
            return Err("import_agent_option_unsupported".into());
        }
        options.insert(
            "tools".into(),
            serde_json::to_value(tools).map_err(|_| "import_agent_option_unsupported")?,
        );
        // Consume original typed description without the generic generator's whitespace folding.
        options.insert("description".into(), json!(summary));
    }
    if agent && !antigravity_agent && !codex_agent {
        for field in ["tools", "model", "color"] {
            if let Some(value) = front.get(field) {
                let value = value.as_str().ok_or("import_agent_option_unsupported")?;
                let pattern = if field == "tools" {
                    r"^[A-Za-z][A-Za-z0-9_(), .*-]*$"
                } else {
                    r"^[A-Za-z][A-Za-z0-9_.-]*$"
                };
                // Existing Claude templates render these values as plain YAML scalars.
                // Reject values that would change type or structure rather than silently alter semantics.
                if !regex::Regex::new(pattern).unwrap().is_match(value)
                    || serde_yaml::from_str::<serde_yaml::Value>(value).ok()
                        != Some(serde_yaml::Value::String(value.into()))
                {
                    return Err("import_agent_option_unsupported".into());
                }
                options.insert(field.into(), json!(value));
            }
        }
    }
    if codex_agent {
        options.insert("imported_agent".into(), json!(codex_options));
    }
    let component_id = format!("harnesskit.{kind}.{name}");
    let directory = format!("components/{kind}s/{name}");
    if std::fs::symlink_metadata(root.join(&directory)).is_ok() {
        return Err("import_collision".into());
    }
    let registry_before = std::fs::read(root.join("components/registry.yml"))
        .map_err(|_| "import_registry_unavailable")?;
    let mut registry: serde_yaml::Value =
        serde_yaml::from_slice(&registry_before).map_err(|_| "import_registry_invalid")?;
    let components = registry
        .get_mut("components")
        .and_then(|v| v.as_mapping_mut())
        .ok_or("import_registry_invalid")?;
    if components.contains_key(serde_yaml::Value::String(component_id.clone())) {
        return Err("import_collision".into());
    }
    let target = match instance.tool_id {
        crate::contexts::local::ToolId::Codex => "codex",
        crate::contexts::local::ToolId::ClaudeCode => "claude",
        crate::contexts::local::ToolId::Antigravity => "antigravity",
        crate::contexts::local::ToolId::AntigravityCli => "antigravity-cli",
        crate::contexts::local::ToolId::Hermes => {
            return Err("import_hermes_package_policy_unqualified".into())
        }
    };
    let adapter: serde_yaml::Value = serde_yaml::from_slice(
        &std::fs::read(root.join(format!("adapters/{target}/adapter.yml")))
            .map_err(|_| "import_adapter_unavailable")?,
    )
    .map_err(|_| "import_adapter_invalid")?;
    let output_root = adapter["output_root"]
        .as_str()
        .ok_or("import_adapter_invalid")?;
    let template = adapter["structures"][format!("{kind}s")]["path_template"]
        .as_str()
        .ok_or("import_adapter_skill_unavailable")?;
    let output_path = format!(
        "{output_root}/{}",
        template.replace(if agent { "{agent_id}" } else { "{skill_id}" }, &name)
    );
    // Canonical output comes from the existing adapter, not the Local source's arbitrary path.
    // The management gate still checks the generated scoped destination against the exact source.
    if instance.tool_id == crate::contexts::local::ToolId::AntigravityCli
        && instance.scope == crate::contexts::local::domain::Scope::User
    {
        return Err("import_skill_install_path_conflict".into());
    }
    let mut targets = serde_json::Map::new();
    targets.insert(target.into(), json!({"output_path":output_path}));
    let mut manifest_value = json!({"component_id":component_id,"kind":kind,"status":"draft","title":name,"summary":summary,"scopes":["user","project"],"owned_files":[format!("{directory}/component.yml"),format!("{directory}/{body_file}")],"targets":targets,"evidence_status":{"status":"build_only","reason":format!("Imported standalone {kind}; tool runtime not exercised")}});
    if codex_agent {
        manifest_value["merge_artifacts"] = json!([{"destination":".codex/config.toml","strategy":"toml-agents-merge","toml_merge_key":"codex-agents","ownership":{"type":"managed-config-key","selected_codex_agent":name}}]);
    }
    if !support_documents.is_empty() {
        manifest_value["bundled_files"] = json!(support_documents.iter().map(|d| json!({
            "source":format!("{directory}/{}",d.path),
            "output_path":format!("{}/{}",std::path::Path::new(&output_path).parent().unwrap().display(),d.path),
            "kind":"doc", "mode":"0644", "surface":target
        })).collect::<Vec<_>>());
        for d in &support_documents {
            manifest_value["owned_files"]
                .as_array_mut()
                .unwrap()
                .push(json!(format!("{directory}/{}", d.path)));
        }
    }
    if !options.is_empty() {
        manifest_value["adapter"] = json!({target:options});
    }
    let manifest_yaml =
        serde_yaml::to_value(manifest_value).map_err(|_| "import_manifest_invalid")?;
    let (schemas, _) = CanonicalSchemas::load(root)?;
    if !schemas.manifest_accepts(kind, &manifest_yaml) {
        return Err("import_manifest_schema_invalid".into());
    }
    let manifest = serde_yaml::to_string(&manifest_yaml).map_err(|_| "import_manifest_invalid")?;
    components.insert(
        serde_yaml::Value::String(component_id.clone()),
        serde_yaml::to_value(
            json!({"kind":kind,"status":"draft","path":format!("{directory}/component.yml")}),
        )
        .unwrap(),
    );
    // Keep all existing registry comments, ordering and bytes; add one root mapping entry.
    let registry_text =
        std::str::from_utf8(&registry_before).map_err(|_| "import_registry_invalid")?;
    if registry_text
        .lines()
        .any(|line| matches!(line.trim(), "---" | "..."))
    {
        return Err("import_registry_shape_unsupported".into());
    }
    let entry = format!("\n  {component_id}:\n    kind: {kind}\n    status: draft\n    path: {directory}/component.yml\n");
    let mut registry_after = registry_before.clone();
    registry_after.extend_from_slice(entry.as_bytes());
    let reparsed: serde_yaml::Value =
        serde_yaml::from_slice(&registry_after).map_err(|_| "import_registry_invalid")?;
    if reparsed != registry {
        return Err("import_registry_shape_unsupported".into());
    }
    let checkout_revision = SourceRevisionManifest::observe_root(root)
        .map_err(|e| e.code())?
        .sha256();
    let fingerprint = digest(
        &serde_json::to_vec(&(
            checkout_id,
            sot_id,
            snapshot_id,
            instance_id,
            source_revision,
            &checkout_revision,
            &manifest,
            &content,
            &support_documents,
            &codex_registration,
        ))
        .unwrap(),
    );
    if !agent {
        let source_parent = source_path.parent().ok_or("import_source_unsafe")?;
        let mut allowed = std::collections::BTreeSet::from([source_path.clone()]);
        let mut directories = std::collections::BTreeSet::from([source_parent.to_path_buf()]);
        for document in &support_documents {
            let path = source_parent.join(&document.path);
            allowed.insert(path.clone());
            let mut parent = path.parent();
            while let Some(p) = parent {
                if p == source_parent {
                    break;
                }
                allowed.insert(p.to_path_buf());
                directories.insert(p.to_path_buf());
                parent = p.parent();
            }
        }
        for directory in directories {
            for entry in std::fs::read_dir(directory).map_err(|_| "import_source_unsafe")? {
                let entry = entry.map_err(|_| "import_source_unsafe")?;
                if !allowed.contains(&entry.path()) {
                    return Err("import_support_files_unsupported".into());
                }
            }
        }
    }
    let mut source_link = json!({"component_id":component_id,"tool":instance.tool_id,"scope":instance.scope,"project_id":instance.project_id,"source_path":source_path,"source_locator":instance.stable_source_locator,"source_revision":source_revision,"content_sha256":digest(original_content.as_bytes()),"managed":false});
    if let Some(mut registration) = codex_registration {
        registration["component_id"] = json!(component_id);
        source_link["registration_link"] = registration;
    }
    Ok(PreparedImport {
        checkout_id: checkout_id.into(),
        sot_id: sot_id.into(),
        snapshot_id: snapshot_id.into(),
        instance_id: instance_id.into(),
        source_revision: source_revision.into(),
        checkout_revision,
        preview: ImportPreview {
            preview_id: format!("component-import-{fingerprint}"),
            fingerprint,
            component_id,
            kind: kind.into(),
            name,
            content: if agent { body } else { content },
            manifest,
            generated_artifact_count: 0,
            source_unchanged: true,
            support_documents,
        },
        registry_before,
        registry_after,
        source_link,
        expires: Instant::now() + Duration::from_secs(300),
    })
}

pub(super) fn document_references(content: &str) -> Result<Vec<String>, String> {
    // Only explicit inline local document links are qualified. Do not silently
    // drop dependencies expressed through Markdown reference links or autolinks.
    if regex::Regex::new(r"(?m)^\s*\[[^\]\n]+\]:|\]\s*\[[^\]\n]*\]|<[^>\n]*(?::|/)[^>\n]*>")
        .unwrap()
        .is_match(content)
    {
        return Err("import_support_reference_unsupported".into());
    }
    let links = regex::Regex::new(r"\]\(([^)]*)\)").unwrap();
    let mut refs = vec![];
    for link in links.captures_iter(content) {
        let path = &link[1];
        if !regex::Regex::new(r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\.(?:md|txt)$")
            .unwrap()
            .is_match(path)
            || path == "SKILL.md"
            || path.contains(':')
        {
            return Err("import_support_reference_unsupported".into());
        }
        refs.push(path.into());
    }
    Ok(refs)
}
fn strip_document_links(content: &str) -> Result<String, String> {
    document_references(content)?;
    Ok(regex::Regex::new(r"\]\([^)]*\)")
        .unwrap()
        .replace_all(content, "]")
        .into_owned())
}
fn capture_support_documents(
    root: &std::path::Path,
    content: &str,
) -> Result<Vec<SupportDocument>, String> {
    let mut pending = document_references(content)?;
    let mut captured = BTreeMap::new();
    let mut total = 0usize;
    while let Some(path) = pending.pop() {
        if captured.contains_key(&path) {
            continue;
        }
        if captured.len() >= 32 {
            return Err("import_content_limit".into());
        }
        let (bytes, revision) =
            crate::contexts::install::writer::capture_import_document(root, &path)?;
        total += bytes.len();
        if total > 1024 * 1024 {
            return Err("import_content_limit".into());
        }
        let text = String::from_utf8(bytes).map_err(|_| "import_invalid_utf8")?;
        validate_document(&text)?;
        // References inside support documents are resolved relative to that document.
        for child in document_references(&text)? {
            let relative = std::path::Path::new(&path).parent().unwrap().join(child);
            pending.push(relative.to_str().ok_or("import_source_unsafe")?.to_string());
        }
        captured.insert(
            path.clone(),
            SupportDocument {
                path,
                source_sha256: digest(text.as_bytes()),
                source_revision: revision,
                content: text,
            },
        );
    }
    Ok(captured.into_values().collect())
}
pub(super) fn validate_document(content: &str) -> Result<(), String> {
    if content.len() > 256 * 1024 || content.contains('\0') {
        return Err("import_content_limit".into());
    }
    let stripped = strip_document_links(content)?;
    if regex::Regex::new(r"(?i)(-----BEGIN .*PRIVATE KEY|\b(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*\S+|/Users/|/home/|/private/|file://|\{\{|\.\./|\b(?:scripts|references|assets|templates)/)").unwrap().is_match(&stripped) {
        return Err("import_sensitive_or_dependency_content".into());
    }
    Ok(())
}

#[allow(clippy::too_many_arguments)]
fn prepare_rule(
    local: &LocalContext,
    checkout_id: &str,
    sot_id: &str,
    snapshot_id: &str,
    instance_id: &str,
    source_revision: &str,
    root: &std::path::Path,
) -> Result<PreparedImport, String> {
    use crate::contexts::local::{domain::Scope, ToolId};
    let publication = local.publication(snapshot_id)?;
    let instance = publication
        .snapshot
        .instances
        .iter()
        .find(|i| i.instance_id == instance_id)
        .ok_or("instance_unavailable")?;
    let handle = publication
        .instance_handles
        .get(instance_id)
        .ok_or("instance_unavailable")?;
    let filename = match instance.tool_id {
        ToolId::Codex | ToolId::Antigravity | ToolId::AntigravityCli => "AGENTS.md",
        ToolId::ClaudeCode => "CLAUDE.md",
        _ => return Err("import_rule_surface_unqualified".into()),
    };
    let project = instance
        .project_id
        .as_ref()
        .and_then(|id| publication.project_locations.get(id))
        .ok_or("import_rule_project_required")?;
    if instance.scope != Scope::Project
        || handle.config_entry_locator.is_some()
        || handle.canonical_root != project.canonical_path
        || handle.raw_relative_components != vec![filename.as_bytes().to_vec()]
        || instance.stable_source_locator != filename
    {
        return Err("import_rule_surface_unqualified".into());
    }
    let bytes = local
        .capture_revision_bound_source(snapshot_id, instance_id, source_revision, 256 * 1024)
        .map_err(|e| e.code)?
        .into_bytes();
    let text = std::str::from_utf8(&bytes).map_err(|_| "import_invalid_utf8")?;
    let whole_file = instance.tool_id == ToolId::Codex
        && !text.contains("HARNESSKIT GENERATED CONTEXT")
        && !text.contains("ROUTINE-HARNESS GENERATED CONTEXT");
    let content = if whole_file {
        text.to_owned()
    } else {
        crate::contexts::install::merge::selected_project_rule(&bytes)
            .map_err(|_| "import_rule_block_ambiguous")?
    };
    validate_rule_edit(&content)?;
    let tool = match instance.tool_id {
        ToolId::Codex => "codex",
        ToolId::ClaudeCode => "claude",
        ToolId::AntigravityCli => "antigravity-cli",
        _ => "antigravity",
    };
    let name = format!(
        "imported-{}-{tool}",
        filename.trim_end_matches(".md").to_ascii_lowercase()
    );
    let component_id = format!("harnesskit.rule.{name}");
    let directory = format!("components/rules/{name}");
    if std::fs::symlink_metadata(root.join(&directory)).is_ok() {
        return Err("import_collision".into());
    }
    let registry_before = std::fs::read(root.join("components/registry.yml"))
        .map_err(|_| "import_registry_unavailable")?;
    let mut registry: serde_yaml::Value =
        serde_yaml::from_slice(&registry_before).map_err(|_| "import_registry_invalid")?;
    let components = registry
        .get_mut("components")
        .and_then(|v| v.as_mapping_mut())
        .ok_or("import_registry_invalid")?;
    if components.contains_key(serde_yaml::Value::String(component_id.clone())) {
        return Err("import_collision".into());
    }
    let begin = crate::contexts::install::merge::HARNESSKIT_BEGIN;
    let end = crate::contexts::install::merge::HARNESSKIT_END;
    let mut manifest_value = json!({
        "component_id":component_id,"kind":"rule","status":"draft","title":name,
        "summary":"Imported project managed rule block","scopes":["project"],
        "owned_files":[format!("{directory}/component.yml"),format!("{directory}/rule.md")],
        "targets":{"project":{"output_path":format!("dist/project/{filename}")}},
        "bundled_files":[{"source":format!("{directory}/rule.md"),"output_path":format!("dist/project/{filename}")}],
        "merge_artifacts":[{"destination":filename,"strategy":"managed-block","begin_marker":begin,"end_marker":end}],
        "evidence_status":{"status":"build_only","reason":"Imported project managed rule block; tool runtime not exercised"}
    });
    if whole_file {
        manifest_value
            .as_object_mut()
            .unwrap()
            .remove("merge_artifacts");
        manifest_value["ownership"] = json!({"type":"path","merge_policy":"full-path-overwrite"});
        manifest_value["bundled_files"][0]["kind"] = json!("doc");
        manifest_value["summary"] = json!("Imported Codex project whole file");
        manifest_value["evidence_status"]["reason"] =
            json!("Imported Codex project whole file; tool runtime not exercised");
    }
    let manifest_yaml =
        serde_yaml::to_value(manifest_value).map_err(|_| "import_manifest_invalid")?;
    let (schemas, _) = CanonicalSchemas::load(root)?;
    if !schemas.manifest_accepts("rule", &manifest_yaml) {
        return Err("import_manifest_schema_invalid".into());
    }
    let manifest = serde_yaml::to_string(&manifest_yaml).map_err(|_| "import_manifest_invalid")?;
    components.insert(
        component_id.clone().into(),
        serde_yaml::to_value(
            json!({"kind":"rule","status":"draft","path":format!("{directory}/component.yml")}),
        )
        .unwrap(),
    );
    let mut registry_after = registry_before.clone();
    registry_after.extend_from_slice(format!("\n  {component_id}:\n    kind: rule\n    status: draft\n    path: {directory}/component.yml\n").as_bytes());
    if serde_yaml::from_slice::<serde_yaml::Value>(&registry_after)
        .map_err(|_| "import_registry_invalid")?
        != registry
    {
        return Err("import_registry_shape_unsupported".into());
    }
    let checkout_revision = SourceRevisionManifest::observe_root(root)
        .map_err(|e| e.code())?
        .sha256();
    let fingerprint = digest(
        &serde_json::to_vec(&(
            checkout_id,
            sot_id,
            snapshot_id,
            instance_id,
            source_revision,
            &checkout_revision,
            &manifest,
            &content,
        ))
        .unwrap(),
    );
    let mut source_link = json!({"component_id":component_id,"tool":instance.tool_id,"scope":instance.scope,"project_id":instance.project_id,
        "source_path":project.canonical_path.join(filename),"source_locator":filename,"source_revision":source_revision,
        "content_sha256":digest(content.as_bytes()),"selected_project_rule":true,"begin_marker":begin,"end_marker":end,"managed":false});
    if whole_file {
        source_link
            .as_object_mut()
            .unwrap()
            .remove("selected_project_rule");
        source_link.as_object_mut().unwrap().remove("begin_marker");
        source_link.as_object_mut().unwrap().remove("end_marker");
        source_link["selected_project_whole_file"] = true.into();
    }
    Ok(PreparedImport {
        checkout_id: checkout_id.into(),
        sot_id: sot_id.into(),
        snapshot_id: snapshot_id.into(),
        instance_id: instance_id.into(),
        source_revision: source_revision.into(),
        checkout_revision,
        registry_before,
        registry_after,
        source_link,
        preview: ImportPreview {
            preview_id: format!("component-import-{fingerprint}"),
            fingerprint,
            component_id,
            kind: "rule".into(),
            name,
            content,
            manifest,
            generated_artifact_count: 0,
            source_unchanged: true,
            support_documents: vec![],
        },
        expires: Instant::now() + Duration::from_secs(300),
    })
}
pub(super) fn validate_rule_edit(content: &str) -> Result<(), String> {
    validate_agent_edit(content)?;
    if content.contains("HARNESSKIT GENERATED CONTEXT")
        || content.contains("ROUTINE-HARNESS GENERATED CONTEXT")
    {
        return Err("import_rule_block_ambiguous".into());
    }
    Ok(())
}

fn prepare_hook(
    local: &LocalContext,
    checkout_id: &str,
    sot_id: &str,
    snapshot_id: &str,
    instance_id: &str,
    source_revision: &str,
    root: &std::path::Path,
) -> Result<PreparedImport, String> {
    let publication = local.publication(snapshot_id)?;
    let handle = &publication.instance_handles[instance_id];
    let pointer = handle
        .config_entry_locator
        .as_deref()
        .ok_or("import_hook_item_unsupported")?;
    let segments: Vec<_> = pointer.trim_start_matches('#').split('/').collect();
    if segments.len() != 6
        || segments[1] != "hooks"
        || segments[3] != "0"
        || segments[4] != "hooks"
        || segments[5] != "0"
    {
        return Err("import_hook_item_ambiguous".into());
    }
    let event = segments[2];
    if !regex::Regex::new("^[A-Za-z][A-Za-z0-9]+$")
        .unwrap()
        .is_match(event)
    {
        return Err("import_hook_event_unsupported".into());
    }
    let bytes = local
        .capture_revision_bound_source(snapshot_id, instance_id, source_revision, 256 * 1024)
        .map_err(|e| e.code)?
        .into_bytes();
    let document: serde_json::Value =
        serde_json::from_slice(&bytes).map_err(|_| "import_hook_json_invalid")?;
    let content = crate::contexts::install::merge::selected_hook(&document, event)
        .map_err(|_| "import_hook_item_ambiguous")?;
    let content = serde_json::to_string_pretty(&content).unwrap() + "\n";
    validate_hook_edit(&content)?;
    let name = format!("imported-{}", event.to_ascii_lowercase());
    let component_id = format!("harnesskit.hook.{name}");
    let directory = format!("components/hooks/{name}");
    if std::fs::symlink_metadata(root.join(&directory)).is_ok() {
        return Err("import_collision".into());
    }
    let registry_before = std::fs::read(root.join("components/registry.yml"))
        .map_err(|_| "import_registry_unavailable")?;
    let mut registry: serde_yaml::Value =
        serde_yaml::from_slice(&registry_before).map_err(|_| "import_registry_invalid")?;
    let components = registry
        .get_mut("components")
        .and_then(|v| v.as_mapping_mut())
        .ok_or("import_registry_invalid")?;
    if components.contains_key(serde_yaml::Value::String(component_id.clone())) {
        return Err("import_collision".into());
    }
    let scope = serde_json::to_value(
        publication
            .snapshot
            .instances
            .iter()
            .find(|i| i.instance_id == instance_id)
            .unwrap()
            .scope,
    )
    .unwrap();
    let manifest_value = json!({"component_id":component_id,"kind":"hook","status":"draft","title":name,"summary":"Imported shared-setting hook","scopes":[scope],"owned_files":[format!("{directory}/component.yml"),format!("{directory}/hook.md")],"targets":{"claude":{"output_path":"dist/claude/.claude/settings.json"}},"adapter":{"claude":{"event":event,"imported_hook":true}},"merge_artifacts":[{"destination":".claude/settings.json","strategy":"json-deep-merge","json_merge_key":"claude-settings-hooks","ownership":{"type":"managed-config-key","selected_hook_event":event}}],"evidence_status":{"status":"build_only","reason":"Imported shared-setting hook; tool runtime not exercised"}});
    let manifest_yaml = serde_yaml::to_value(manifest_value).unwrap();
    let (schemas, _) = CanonicalSchemas::load(root)?;
    if !schemas.manifest_accepts("hook", &manifest_yaml) {
        return Err("import_manifest_schema_invalid".into());
    }
    let manifest = serde_yaml::to_string(&manifest_yaml).unwrap();
    components.insert(
        component_id.clone().into(),
        serde_yaml::to_value(
            json!({"kind":"hook","status":"draft","path":format!("{directory}/component.yml")}),
        )
        .unwrap(),
    );
    let mut registry_after = registry_before.clone();
    registry_after.extend_from_slice(format!("\n  {component_id}:\n    kind: hook\n    status: draft\n    path: {directory}/component.yml\n").as_bytes());
    if serde_yaml::from_slice::<serde_yaml::Value>(&registry_after)
        .map_err(|_| "import_registry_invalid")?
        != registry
    {
        return Err("import_registry_shape_unsupported".into());
    }
    let checkout_revision = SourceRevisionManifest::observe_root(root)
        .map_err(|e| e.code())?
        .sha256();
    let fingerprint = digest(
        &serde_json::to_vec(&(
            checkout_id,
            sot_id,
            snapshot_id,
            instance_id,
            source_revision,
            &checkout_revision,
            &manifest,
            &content,
        ))
        .unwrap(),
    );
    let source_path =
        handle
            .raw_relative_components
            .iter()
            .fold(handle.canonical_root.clone(), |p, c| {
                use std::os::unix::ffi::OsStringExt;
                p.join(std::ffi::OsString::from_vec(c.clone()))
            });
    let locator = publication
        .snapshot
        .instances
        .iter()
        .find(|i| i.instance_id == instance_id)
        .unwrap()
        .stable_source_locator
        .clone();
    let source_link = json!({"component_id":component_id,"tool":"claude_code","scope":scope,"source_path":source_path,"source_locator":locator.split('#').next().unwrap(),"config_entry_locator":pointer,"selected_hook_event":event,"source_revision":source_revision,"content_sha256":digest(content.as_bytes()),"managed":false});
    Ok(PreparedImport {
        checkout_id: checkout_id.into(),
        sot_id: sot_id.into(),
        snapshot_id: snapshot_id.into(),
        instance_id: instance_id.into(),
        source_revision: source_revision.into(),
        checkout_revision,
        preview: ImportPreview {
            preview_id: format!("component-import-{fingerprint}"),
            fingerprint,
            component_id,
            kind: "hook".into(),
            name,
            content,
            manifest,
            generated_artifact_count: 0,
            source_unchanged: true,
            support_documents: vec![],
        },
        registry_before,
        registry_after,
        source_link,
        expires: Instant::now() + Duration::from_secs(300),
    })
}
pub(super) fn validate_hook_edit(content: &str) -> Result<(), String> {
    // This verified slice accepts inert, dependency-free printf declarations only.
    // Other commands need an explicit dependency conversion, not optimistic import.
    let candidate: serde_json::Value =
        serde_json::from_str(content).map_err(|_| "import_hook_json_invalid")?;
    if candidate["command"].as_str().is_none_or(|c| {
        !regex::Regex::new(r"^printf [A-Za-z0-9 _%.-]+$")
            .unwrap()
            .is_match(c)
    }) {
        return Err("import_hook_dependency_unsupported".into());
    }
    let value: serde_json::Value =
        serde_json::from_str(content).map_err(|_| "import_hook_json_invalid")?;
    if value.as_object().is_none_or(|m| m.len()!=3) || value["type"] != "command" || value["timeout"].as_u64().is_none_or(|v| v==0 || v>3600) || value["command"].as_str().is_none_or(|c| c.is_empty() || c.len()>4096 || regex::Regex::new(r"(?i)(/Users/|/home/|/private/|file://|\.\./|secret|password|api[_-]?key|access[_-]?token|PRIVATE KEY)").unwrap().is_match(c)) { return Err("import_hook_content_unsupported".into()); }
    Ok(())
}

pub(super) fn validate_agent_edit(content: &str) -> Result<(), String> {
    if content.len() > 256 * 1024 {
        return Err("import_content_limit".into());
    }
    if content.trim().is_empty() {
        return Err("import_body_required".into());
    }
    if content.starts_with("---") {
        return Err("import_frontmatter_unsupported".into());
    }
    let unsafe_content = regex::Regex::new(r"(?i)(-----BEGIN .*PRIVATE KEY|\b(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*\S+|(?:/Users/|/home/|/private/|file://)|\b(?:scripts|references|assets|templates)/|\]\([^)]*\)|\{\{|\.\./)").unwrap();
    if unsafe_content.is_match(content) {
        return Err("import_sensitive_or_dependency_content".into());
    }
    Ok(())
}

pub(super) fn validate_bundle_edit(name: &str, content: &str, before: &[u8]) -> Result<(), String> {
    let before = std::str::from_utf8(before).map_err(|_| "import_invalid_utf8")?;
    let old: std::collections::BTreeSet<_> = document_references(before)?.into_iter().collect();
    let new: std::collections::BTreeSet<_> = document_references(content)?.into_iter().collect();
    if old != new {
        return Err("import_support_reference_unsupported".into());
    }
    // Body edits cannot add/remove/change operational fields captured at import.
    let parse = |text: &str| -> Result<(serde_yaml::Value, String), String> {
        let mut lines = text.split_inclusive('\n');
        if lines.next().map(|l| l.trim_end_matches(['\r', '\n'])) != Some("---") {
            return Err("import_frontmatter_required".into());
        }
        let mut yaml = String::new();
        let mut closed = false;
        for line in lines.by_ref() {
            if line.trim_end_matches(['\r', '\n']) == "---" {
                closed = true;
                break;
            }
            yaml.push_str(line);
        }
        if !closed {
            return Err("import_frontmatter_required".into());
        }
        Ok((
            serde_yaml::from_str(&yaml).map_err(|_| "import_frontmatter_invalid")?,
            lines.collect(),
        ))
    };
    let (mut old_front, _) = parse(before)?;
    let (mut front, body) = parse(content)?;
    let mut extras = front.clone();
    for value in [&mut old_front, &mut extras] {
        value
            .as_mapping_mut()
            .ok_or("import_frontmatter_invalid")?
            .retain(|k, _| !matches!(k.as_str(), Some("name" | "description")));
    }
    if extras != old_front {
        return Err("import_skill_metadata_edit_unsupported".into());
    }
    let mapping = front.as_mapping_mut().ok_or("import_frontmatter_invalid")?;
    mapping.retain(|k, _| matches!(k.as_str(), Some("name" | "description")));
    let checked = format!(
        "---\n{}---\n{}",
        serde_yaml::to_string(&front).map_err(|_| "import_frontmatter_invalid")?,
        body
    );
    validate_edit(name, &strip_document_links(&checked)?)
}

pub(super) fn validate_edit(name: &str, content: &str) -> Result<(), String> {
    if content.len() > 256 * 1024 {
        return Err("import_content_limit".into());
    }
    let mut lines = content.split_inclusive('\n');
    if lines.next().map(|l| l.trim_end_matches(['\r', '\n'])) != Some("---") {
        return Err("import_frontmatter_required".into());
    }
    let mut yaml = String::new();
    let mut closed = false;
    for line in lines.by_ref() {
        if line.trim_end_matches(['\r', '\n']) == "---" {
            closed = true;
            break;
        }
        yaml.push_str(line);
    }
    let front: serde_yaml::Value =
        serde_yaml::from_str(&yaml).map_err(|_| "import_frontmatter_invalid")?;
    if !closed
        || front["name"].as_str() != Some(name)
        || front["description"]
            .as_str()
            .is_none_or(|s| s.trim().is_empty())
        || front.as_mapping().is_none_or(|m| {
            m.keys()
                .any(|k| !matches!(k.as_str(), Some("name" | "description")))
        })
        || lines.collect::<String>().trim().is_empty()
    {
        return Err("import_frontmatter_unsupported".into());
    }
    let unsafe_content = regex::Regex::new(r"(?i)(-----BEGIN .*PRIVATE KEY|\b(?:api[_-]?key|access[_-]?token|password|secret)\s*[:=]\s*\S+|(?:/Users/|/home/|/private/|file://)|\b(?:scripts|references|assets|templates)/|\]\([^)]*\)|\{\{|\.\./)").unwrap();
    if unsafe_content.is_match(content) {
        return Err("import_sensitive_or_dependency_content".into());
    }
    Ok(())
}

pub(super) fn validate_stage(
    prepared: &PreparedImport,
    root: &std::path::Path,
) -> Result<(), String> {
    let snapshot = SotSnapshotService::load(root)?;
    if !snapshot
        .components
        .iter()
        .any(|c| c.component_id == prepared.preview.component_id)
        || snapshot.issues.iter().any(|i| {
            i.source_path.contains(&format!(
                "components/{}s/{}/",
                prepared.preview.kind, prepared.preview.name
            ))
        })
    {
        return Err("import_registry_validation_failed".into());
    }
    Ok(())
}

pub(super) fn private_link_payload(
    root: PathBuf,
    prepared: &PreparedImport,
) -> Result<(PathBuf, Option<Vec<u8>>, Vec<u8>), String> {
    use std::os::unix::fs::MetadataExt;
    let metadata = std::fs::symlink_metadata(&root).map_err(|_| "import_private_state_unsafe")?;
    if !metadata.is_dir()
        || metadata.file_type().is_symlink()
        || metadata.mode() & 0o077 != 0
        || metadata.uid() != rustix::process::getuid().as_raw()
    {
        return Err("import_private_state_unsafe".into());
    }
    let path = root.join(format!("component-imports-{}.json", prepared.checkout_id));
    match std::fs::symlink_metadata(&path) {
        Ok(m)
            if !m.is_file()
                || m.file_type().is_symlink()
                || m.nlink() != 1
                || m.mode() & 0o077 != 0 =>
        {
            return Err("import_private_state_unsafe".into())
        }
        Ok(_) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(_) => return Err("import_private_state_unsafe".into()),
    }
    let before = match std::fs::read(&path) {
        Ok(b) => Some(b),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
        Err(_) => return Err("import_private_state_unavailable".into()),
    };
    let mut links: Vec<serde_json::Value> = match &before {
        Some(b) => serde_json::from_slice(b).map_err(|_| "import_private_state_invalid")?,
        None => vec![],
    };
    let mut primary = prepared.source_link.clone();
    if let Some(registration) = primary.as_object_mut().unwrap().remove("registration_link") {
        links.push(registration);
    }
    links.push(primary);
    let source = PathBuf::from(
        prepared.source_link["source_path"]
            .as_str()
            .ok_or("import_source_unsafe")?,
    );
    let locator = PathBuf::from(
        prepared.source_link["source_locator"]
            .as_str()
            .ok_or("import_source_unsafe")?,
    );
    for document in &prepared.preview.support_documents {
        let mut link = prepared.source_link.clone();
        link["source_path"] = json!(source.parent().unwrap().join(&document.path));
        link["source_locator"] = json!(locator.parent().unwrap().join(&document.path));
        link["canonical_document"] = json!(document.path);
        link["content_sha256"] = json!(document.source_sha256);
        link["source_revision"] = json!(document.source_revision);
        links.push(link);
    }
    Ok((
        path,
        before,
        serde_json::to_vec(&links).map_err(|_| "import_private_state_invalid")?,
    ))
}
