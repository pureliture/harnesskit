use std::collections::{BTreeMap, BTreeSet};
use std::path::{Component, Path, PathBuf};

use serde_yaml::Value;

use crate::contexts::install::workspace::SourceRevisionManifest;

use super::domain::SotIssue;
use super::schema::CanonicalSchemas;

#[derive(Debug, Clone)]
pub(super) struct SourceRegistryEntry {
    pub component_id: String,
    pub kind: String,
    pub status: String,
    pub planned: bool,
    pub manifest_path: String,
    pub replaced_by: Option<String>,
    pub absorbed_by: Option<String>,
    pub canonical: bool,
}

#[derive(Debug, Clone)]
pub(super) struct SourceManifest {
    pub source_path: String,
    pub raw_yaml: String,
    pub value: Value,
    pub relation_schema_valid: bool,
}

#[derive(Debug, Clone)]
pub(super) struct SourceProfile {
    pub profile_id: String,
    pub status: Option<String>,
    pub title: String,
    pub summary: Option<String>,
    pub component_ids: Vec<String>,
    pub base_component_ids: Vec<String>,
    pub scope_component_ids: BTreeMap<String, Vec<String>>,
}

#[derive(Debug)]
pub(super) struct SotSourceData {
    pub entries: Vec<SourceRegistryEntry>,
    pub manifests: BTreeMap<String, SourceManifest>,
    pub profiles: Vec<SourceProfile>,
    pub issues: Vec<SotIssue>,
    pub source_revision: String,
}

pub(super) struct SotSourceReader;

impl SotSourceReader {
    pub(super) fn load(checkout: &Path) -> Result<SotSourceData, String> {
        Self::load_with_mid_read_hook(checkout, || {})
    }

    fn load_with_mid_read_hook<F>(
        checkout: &Path,
        mid_read_hook: F,
    ) -> Result<SotSourceData, String>
    where
        F: FnOnce(),
    {
        let root = std::fs::canonicalize(checkout)
            .map_err(|_| "SoT checkout is unavailable".to_string())?;
        if !root.is_dir() {
            return Err("SoT checkout is unavailable".to_string());
        }
        let initial_source_manifest = SourceRevisionManifest::observe_root(&root)
            .map_err(|_| "SoT renderer input revision is unavailable".to_string())?;

        let mut source_files = BTreeMap::<String, Vec<u8>>::new();
        let (schemas, schema_sources) = CanonicalSchemas::load(&root)?;
        source_files.extend(schema_sources);
        let registry_path = "components/registry.yml";
        let registry_bytes = read_checkout_file(&root, registry_path)
            .map_err(|_| "SoT registry is unavailable".to_string())?;
        source_files.insert(registry_path.to_string(), registry_bytes.clone());
        let registry: Value = serde_yaml::from_slice(&registry_bytes)
            .map_err(|_| "SoT registry is malformed".to_string())?;
        if text_field(&registry, "version").as_deref() != Some("1") {
            return Err("SoT registry version is unsupported".to_string());
        }
        let components = registry
            .get("components")
            .and_then(Value::as_mapping)
            .ok_or_else(|| "SoT registry components mapping is unavailable".to_string())?;

        let mut issues = Vec::new();
        let mut entries = Vec::new();
        for (key, value) in components {
            let Some(component_id) = key.as_str() else {
                issues.push(issue(
                    "invalid_registry_entry",
                    registry_path,
                    "Registry component identity must be text",
                ));
                continue;
            };
            let kind = text_field(value, "kind").unwrap_or_default();
            let status = text_field(value, "status").unwrap_or_default();
            let manifest_path = text_field(value, "path").unwrap_or_default();
            let canonical =
                registry_entry_shape_valid(component_id, value, &kind, &status, &manifest_path);
            if !canonical {
                issues.push(issue(
                    "invalid_registry_entry",
                    registry_path,
                    "Registry entry does not match the supported canonical shape",
                ));
                continue;
            }
            if registry_status_domain(&status) == RegistryStatusDomain::Invalid {
                issues.push(issue(
                    "invalid_registry_status",
                    &format!("{registry_path}#{component_id}"),
                    "Registry status is outside the supported lifecycle and catalog vocabularies",
                ));
            }
            entries.push(SourceRegistryEntry {
                component_id: component_id.to_string(),
                kind,
                status,
                planned: value
                    .get("planned")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
                manifest_path,
                replaced_by: text_field(value, "replaced_by"),
                absorbed_by: text_field(value, "absorbed_by"),
                canonical,
            });
        }
        entries.sort_by(|left, right| left.component_id.cmp(&right.component_id));

        let mut manifests = BTreeMap::new();
        for entry in &entries {
            let issue_path = safe_issue_path(&entry.manifest_path, &entry.component_id);
            let bytes = match read_checkout_file(&root, &entry.manifest_path) {
                Ok(bytes) => bytes,
                Err(ReadFailure::Escape) => {
                    issues.push(issue(
                        "manifest_path_escape",
                        &issue_path,
                        "Component manifest resolves outside the checkout",
                    ));
                    continue;
                }
                Err(ReadFailure::Missing) => {
                    if !entry.planned {
                        issues.push(issue(
                            "missing_manifest",
                            &issue_path,
                            "Component manifest is missing or inaccessible",
                        ));
                    }
                    continue;
                }
                Err(ReadFailure::Inaccessible) => {
                    issues.push(issue(
                        "missing_manifest",
                        &issue_path,
                        "Component manifest is missing or inaccessible",
                    ));
                    continue;
                }
            };
            source_files.insert(entry.manifest_path.clone(), bytes.clone());
            let parsed: Value = match serde_yaml::from_slice::<Value>(&bytes) {
                Ok(value) if value.is_mapping() => value,
                _ => {
                    issues.push(issue(
                        "malformed_manifest",
                        &issue_path,
                        "Component manifest could not be parsed",
                    ));
                    continue;
                }
            };
            if !manifest_matches_registry(entry, &parsed) {
                issues.push(issue(
                    "manifest_schema_mismatch",
                    &issue_path,
                    "Component manifest identity or kind does not match the registry",
                ));
                continue;
            }
            if !schemas.manifest_accepts(&entry.kind, &parsed) {
                issues.push(issue(
                    "manifest_schema_invalid",
                    &issue_path,
                    "Component manifest does not match its canonical schema",
                ));
                continue;
            }
            if registry_status_domain(&entry.status) == RegistryStatusDomain::Lifecycle
                && text_field(&parsed, "status").is_some_and(|status| status != entry.status)
            {
                issues.push(issue(
                    "manifest_status_mismatch",
                    &issue_path,
                    "Manifest status differs from the registry authority",
                ));
            }
            let relation_schema_valid = relation_manifest_schema_valid(entry, &parsed);
            if !relation_schema_valid {
                issues.push(issue(
                    "manifest_schema_invalid",
                    &issue_path,
                    "Component manifest cannot authorize graph relations",
                ));
            }
            manifests.insert(
                entry.component_id.clone(),
                SourceManifest {
                    source_path: entry.manifest_path.clone(),
                    raw_yaml: String::from_utf8(bytes).unwrap_or_default(),
                    value: parsed,
                    relation_schema_valid,
                },
            );
        }

        let registry_kinds = entries
            .iter()
            .map(|entry| (entry.component_id.clone(), entry.kind.clone()))
            .collect::<BTreeMap<_, _>>();
        let profiles = load_profiles(
            &root,
            &schemas,
            &registry_kinds,
            &manifests,
            &mut source_files,
            &mut issues,
        )?;
        mid_read_hook();
        let final_source_manifest = SourceRevisionManifest::observe_root(&root)
            .map_err(|_| "SoT renderer input revision is unavailable".to_string())?;
        if final_source_manifest != initial_source_manifest {
            return Err("SoT source changed while loading".to_string());
        }
        let source_revision = initial_source_manifest.sha256();

        Ok(SotSourceData {
            entries,
            manifests,
            profiles,
            issues,
            source_revision,
        })
    }
}

fn load_profiles(
    root: &Path,
    schemas: &CanonicalSchemas,
    registry_kinds: &BTreeMap<String, String>,
    manifests: &BTreeMap<String, SourceManifest>,
    source_files: &mut BTreeMap<String, Vec<u8>>,
    issues: &mut Vec<SotIssue>,
) -> Result<Vec<SourceProfile>, String> {
    let profiles_dir = root.join("profiles");
    if !profiles_dir.exists() {
        return Ok(Vec::new());
    }
    let mut paths = std::fs::read_dir(&profiles_dir)
        .map_err(|_| "SoT profiles directory is unavailable".to_string())?
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .filter(|path| path.extension().and_then(|value| value.to_str()) == Some("yml"))
        .collect::<Vec<_>>();
    paths.sort();

    let mut profiles = Vec::new();
    let mut seen_profile_ids = BTreeSet::new();
    for path in paths {
        let Ok(relative) = path.strip_prefix(root) else {
            continue;
        };
        let relative = path_to_slashes(relative);
        let bytes = match read_checkout_file(root, &relative) {
            Ok(bytes) => bytes,
            Err(_) => {
                issues.push(issue(
                    "profile_path_escape",
                    &relative,
                    "Profile source is outside the checkout or inaccessible",
                ));
                continue;
            }
        };
        source_files.insert(relative.clone(), bytes.clone());
        let raw_value: Value = match serde_yaml::from_slice::<Value>(&bytes) {
            Ok(value) if value.is_mapping() => value,
            _ => {
                issues.push(issue(
                    "malformed_profile",
                    &relative,
                    "Profile source could not be parsed",
                ));
                continue;
            }
        };
        let Some(profile_id) = text_field(&raw_value, "profile_id") else {
            issues.push(issue(
                "invalid_profile",
                &relative,
                "Profile identity is missing",
            ));
            continue;
        };
        let mut value = raw_value.clone();
        sanitize_profile_workflow_memberships(
            &mut value,
            &profile_id,
            &relative,
            registry_kinds,
            manifests,
            issues,
        );
        if !schemas.profile_accepts(&value) || !profile_shape_valid(&value, &profile_id) {
            issues.push(issue(
                "invalid_profile_schema",
                &relative,
                "Profile does not match the canonical source shape",
            ));
            continue;
        }
        if !seen_profile_ids.insert(profile_id.clone()) {
            issues.push(issue(
                "duplicate_profile_id",
                &relative,
                "Profile identity is declared more than once",
            ));
            continue;
        }
        let title = text_field(&value, "title").unwrap_or_else(|| profile_id.clone());
        let mut base_component_ids = BTreeSet::new();
        collect_text_sequence(value.get("components"), &mut base_component_ids);
        let mut scope_component_ids = BTreeMap::<String, BTreeSet<String>>::new();
        if let Some(scopes) = value
            .get("install_policy")
            .and_then(|policy| policy.get("scope_components"))
            .and_then(Value::as_mapping)
        {
            for (scope, members) in scopes {
                if let Some(scope) = scope.as_str() {
                    collect_text_sequence(
                        Some(members),
                        scope_component_ids.entry(scope.to_string()).or_default(),
                    );
                }
            }
        }
        let mut member_ids = base_component_ids.clone();
        member_ids.extend(
            scope_component_ids
                .values()
                .flat_map(|members| members.iter().cloned()),
        );
        member_ids.retain(|member| {
            if registry_kinds.contains_key(member) {
                true
            } else {
                issues.push(issue(
                    "unknown_profile_member",
                    &relative,
                    "Profile references a component outside the active registry",
                ));
                false
            }
        });
        base_component_ids.retain(|member| member_ids.contains(member));
        for members in scope_component_ids.values_mut() {
            members.retain(|member| member_ids.contains(member));
        }
        profiles.push(SourceProfile {
            profile_id,
            status: text_field(&value, "status"),
            title,
            summary: text_field(&value, "summary"),
            component_ids: member_ids.into_iter().collect(),
            base_component_ids: base_component_ids.into_iter().collect(),
            scope_component_ids: scope_component_ids
                .into_iter()
                .map(|(scope, members)| (scope, members.into_iter().collect()))
                .collect(),
        });
    }
    profiles.sort_by(|left, right| left.profile_id.cmp(&right.profile_id));
    Ok(profiles)
}

fn sanitize_profile_workflow_memberships(
    value: &mut Value,
    profile_id: &str,
    source_path: &str,
    registry_kinds: &BTreeMap<String, String>,
    manifests: &BTreeMap<String, SourceManifest>,
    issues: &mut Vec<SotIssue>,
) {
    if let Some(Value::Sequence(components)) = mapping_value_mut(value, "components") {
        sanitize_profile_selection_sequence(
            components,
            "components",
            profile_id,
            source_path,
            registry_kinds,
            manifests,
            issues,
        );
    }

    let Some(install_policy) = mapping_value_mut(value, "install_policy") else {
        return;
    };
    let Some(Value::Mapping(scope_components)) =
        mapping_value_mut(install_policy, "scope_components")
    else {
        return;
    };
    for (scope, members) in scope_components {
        let (Some(scope), Value::Sequence(members)) = (scope.as_str(), members) else {
            continue;
        };
        sanitize_profile_selection_sequence(
            members,
            &format!("install_policy.scope_components.{scope}"),
            profile_id,
            source_path,
            registry_kinds,
            manifests,
            issues,
        );
    }
}

fn sanitize_profile_selection_sequence(
    values: &mut Vec<Value>,
    field_prefix: &str,
    profile_id: &str,
    source_path: &str,
    registry_kinds: &BTreeMap<String, String>,
    manifests: &BTreeMap<String, SourceManifest>,
    issues: &mut Vec<SotIssue>,
) {
    let mut retained = Vec::with_capacity(values.len());
    for (index, value) in std::mem::take(values).into_iter().enumerate() {
        let Some(component_id) = value.as_str() else {
            retained.push(value);
            continue;
        };
        let field_path = format!("{field_prefix}[{index}]");
        let sanitized = sanitize_profile_candidate(
            component_id,
            &field_path,
            registry_kinds,
            manifests,
            &BTreeSet::new(),
        );
        if sanitized.violations.is_empty() {
            retained.push(value);
            continue;
        }
        for (violation_path, workflow_id) in sanitized.violations {
            issues.push(issue(
                "profile_workflow_membership_forbidden",
                &format!("{source_path}#{violation_path}"),
                &format!(
                    "Profile {profile_id} install selection cannot include Workflow definition {workflow_id}"
                ),
            ));
        }
        retained.extend(sanitized.retained_ids.into_iter().map(Value::String));
    }
    *values = retained;
}

struct SanitizedProfileCandidate {
    retained_ids: Vec<String>,
    violations: Vec<(String, String)>,
}

fn sanitize_profile_candidate(
    component_id: &str,
    field_path: &str,
    registry_kinds: &BTreeMap<String, String>,
    manifests: &BTreeMap<String, SourceManifest>,
    active_composites: &BTreeSet<String>,
) -> SanitizedProfileCandidate {
    match registry_kinds.get(component_id).map(String::as_str) {
        Some("workflow") => SanitizedProfileCandidate {
            retained_ids: Vec::new(),
            violations: vec![(field_path.to_string(), component_id.to_string())],
        },
        Some("composite") if !active_composites.contains(component_id) => {
            let Some(members) = manifests
                .get(component_id)
                .and_then(|manifest| manifest.value.get("members"))
                .and_then(Value::as_sequence)
            else {
                return retained_profile_candidate(component_id);
            };
            let mut nested_active = active_composites.clone();
            nested_active.insert(component_id.to_string());
            let mut retained_ids = Vec::new();
            let mut violations = Vec::new();
            for (index, member) in members.iter().enumerate() {
                let Some(member_id) = member.as_str() else {
                    continue;
                };
                let sanitized = sanitize_profile_candidate(
                    member_id,
                    &format!("{field_path}.members[{index}]"),
                    registry_kinds,
                    manifests,
                    &nested_active,
                );
                retained_ids.extend(sanitized.retained_ids);
                violations.extend(sanitized.violations);
            }
            if violations.is_empty() {
                retained_profile_candidate(component_id)
            } else {
                SanitizedProfileCandidate {
                    retained_ids,
                    violations,
                }
            }
        }
        _ => retained_profile_candidate(component_id),
    }
}

fn retained_profile_candidate(component_id: &str) -> SanitizedProfileCandidate {
    SanitizedProfileCandidate {
        retained_ids: vec![component_id.to_string()],
        violations: Vec::new(),
    }
}

fn mapping_value_mut<'a>(value: &'a mut Value, key: &str) -> Option<&'a mut Value> {
    value
        .as_mapping_mut()?
        .get_mut(&Value::String(key.to_string()))
}

fn collect_text_sequence(value: Option<&Value>, output: &mut BTreeSet<String>) {
    let Some(values) = value.and_then(Value::as_sequence) else {
        return;
    };
    for value in values {
        if let Some(value) = value.as_str() {
            output.insert(value.to_string());
        }
    }
}

fn profile_shape_valid(value: &Value, profile_id: &str) -> bool {
    let profile_id_valid = profile_id
        .strip_prefix("harnesskit.profile.")
        .is_some_and(is_slug);
    let status_valid = text_field(value, "status").is_some_and(|status| {
        matches!(
            status.as_str(),
            "draft" | "review" | "stable" | "deprecated" | "archived"
        )
    });
    let components_valid = value
        .get("components")
        .and_then(Value::as_sequence)
        .is_some_and(|values| canonical_unique_sequence(values));
    let Some(policy) = value.get("install_policy").and_then(Value::as_mapping) else {
        return false;
    };
    let default_scope_valid = policy
        .get(Value::String("default_scope".to_string()))
        .and_then(Value::as_str)
        .is_some_and(|scope| matches!(scope, "project" | "user"));
    let allowed_scopes_valid = policy
        .get(Value::String("allowed_scopes".to_string()))
        .and_then(Value::as_sequence)
        .is_some_and(|scopes| {
            scopes.iter().all(|scope| {
                scope
                    .as_str()
                    .is_some_and(|scope| matches!(scope, "project" | "user"))
            })
        });
    let activation_valid = policy
        .get(Value::String("activation_policy".to_string()))
        .and_then(Value::as_str)
        .is_some_and(|activation| matches!(activation, "none" | "manual" | "deferred"));
    let scope_components_valid = policy
        .get(Value::String("scope_components".to_string()))
        .is_none_or(|scopes| {
            scopes.as_mapping().is_some_and(|scopes| {
                scopes.iter().all(|(scope, members)| {
                    scope
                        .as_str()
                        .is_some_and(|scope| matches!(scope, "project" | "user"))
                        && members
                            .as_sequence()
                            .is_some_and(|values| canonical_unique_sequence(values))
                })
            })
        });
    profile_id_valid
        && status_valid
        && nonempty_text(value, "title")
        && nonempty_text(value, "summary")
        && components_valid
        && default_scope_valid
        && allowed_scopes_valid
        && activation_valid
        && scope_components_valid
}

fn canonical_unique_sequence(values: &[Value]) -> bool {
    let mut seen = BTreeSet::new();
    values.iter().all(|value| {
        value
            .as_str()
            .is_some_and(|identity| is_canonical_identity(identity) && seen.insert(identity))
    })
}

fn manifest_matches_registry(entry: &SourceRegistryEntry, value: &Value) -> bool {
    let identity_key = match entry.kind.as_str() {
        "composite" => "composite_id",
        "workflow" => "workflow_id",
        _ => "component_id",
    };
    if text_field(value, identity_key).as_deref() != Some(entry.component_id.as_str()) {
        return false;
    }
    match value.get("kind").and_then(Value::as_str) {
        Some(kind) => kind == entry.kind,
        None => true,
    }
}

fn relation_manifest_schema_valid(entry: &SourceRegistryEntry, value: &Value) -> bool {
    if !entry.canonical || !manifest_matches_registry(entry, value) {
        return false;
    }
    let status_valid = text_field(value, "status").is_some_and(|status| !status.is_empty());
    if !status_valid {
        return false;
    }
    match entry.kind.as_str() {
        "composite" => {
            text_field(value, "kind").as_deref() == Some("composite")
                && nonempty_text(value, "title")
                && value
                    .get("members")
                    .and_then(Value::as_sequence)
                    .is_some_and(|members| {
                        !members.is_empty()
                            && members
                                .iter()
                                .all(|member| member.as_str().is_some_and(is_canonical_identity))
                    })
        }
        "workflow" => {
            nonempty_text(value, "description")
                && value.get("steps").is_none_or(workflow_steps_are_typed)
                && value
                    .get("optional_invoked_by")
                    .is_none_or(|invoker| invoker.as_str().is_some_and(is_canonical_identity))
        }
        _ => {
            text_field(value, "kind").as_deref() == Some(entry.kind.as_str())
                && nonempty_text(value, "title")
                && component_relation_fields_are_typed(value)
        }
    }
}

fn component_relation_fields_are_typed(value: &Value) -> bool {
    if !value
        .get("policy_ref")
        .is_none_or(|policy| policy.as_str().is_some_and(is_canonical_identity))
    {
        return false;
    }
    let Some(routes) = value.get("routes") else {
        return true;
    };
    let Some(routes) = routes.as_mapping() else {
        return false;
    };
    if !routes
        .get(Value::String("policy_ref".to_string()))
        .is_none_or(|policy| policy.as_str().is_some_and(is_canonical_identity))
    {
        return false;
    }
    routes
        .get(Value::String("workflows".to_string()))
        .is_none_or(|workflows| {
            workflows.as_sequence().is_some_and(|workflows| {
                workflows.iter().all(|workflow| {
                    workflow
                        .get("component_id")
                        .and_then(Value::as_str)
                        .is_some_and(is_canonical_identity)
                })
            })
        })
}

fn workflow_steps_are_typed(value: &Value) -> bool {
    value.as_sequence().is_some_and(|steps| {
        steps.iter().all(|step| {
            nonempty_text(step, "id")
                && ["agent", "skill", "rule"].iter().all(|field| {
                    step.get(*field)
                        .is_none_or(|target| target.as_str().is_some_and(is_canonical_identity))
                })
                && step.get("mode_fanout").is_none_or(mode_fanout_is_typed)
        })
    })
}

fn mode_fanout_is_typed(value: &Value) -> bool {
    value.as_mapping().is_some_and(|modes| {
        modes.iter().all(|(mode, config)| {
            mode.as_str().is_some_and(|mode| !mode.is_empty())
                && config
                    .get("agents")
                    .and_then(Value::as_sequence)
                    .is_some_and(|agents| {
                        agents
                            .iter()
                            .all(|agent| agent.as_str().is_some_and(is_canonical_identity))
                    })
        })
    })
}

fn nonempty_text(value: &Value, field: &str) -> bool {
    text_field(value, field).is_some_and(|value| !value.trim().is_empty())
}

fn is_canonical_component_id(kind: &str, component_id: &str) -> bool {
    matches!(
        kind,
        "skill" | "agent" | "hook" | "rule" | "command" | "workflow" | "composite"
    ) && component_id
        .strip_prefix(&format!("harnesskit.{kind}."))
        .is_some_and(is_slug)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum RegistryStatusDomain {
    Lifecycle,
    CatalogOnly,
    Invalid,
}

fn registry_status_domain(status: &str) -> RegistryStatusDomain {
    match status {
        "draft" | "review" | "stable" | "deprecated" | "archived" => {
            RegistryStatusDomain::Lifecycle
        }
        "draft-card-only" | "skeleton" | "semi-concrete" => RegistryStatusDomain::CatalogOnly,
        _ => RegistryStatusDomain::Invalid,
    }
}

fn registry_entry_shape_valid(
    component_id: &str,
    value: &Value,
    kind: &str,
    status: &str,
    manifest_path: &str,
) -> bool {
    const ALLOWED_FIELDS: &[&str] = &[
        "kind",
        "status",
        "path",
        "planned",
        "deprecated",
        "replaced_by",
        "absorbed_by",
        "deprecation",
        "compatibility",
    ];
    let Some(mapping) = value.as_mapping() else {
        return false;
    };
    let fields_known = mapping.keys().all(|key| {
        key.as_str()
            .is_some_and(|key| ALLOWED_FIELDS.contains(&key))
    });
    let optional_booleans_valid = ["planned", "deprecated"]
        .iter()
        .all(|field| value.get(*field).is_none_or(Value::is_bool));
    let optional_mappings_valid = ["deprecation", "compatibility"]
        .iter()
        .all(|field| value.get(*field).is_none_or(Value::is_mapping));
    let relation_targets_valid = ["replaced_by", "absorbed_by"].iter().all(|field| {
        value
            .get(*field)
            .is_none_or(|target| target.as_str().is_some_and(is_canonical_identity))
    });
    fields_known
        && is_canonical_component_id(kind, component_id)
        && !status.trim().is_empty()
        && normalized_relative_path(manifest_path).is_some()
        && optional_booleans_valid
        && optional_mappings_valid
        && relation_targets_valid
}

fn is_canonical_identity(component_id: &str) -> bool {
    [
        "skill",
        "agent",
        "hook",
        "rule",
        "command",
        "workflow",
        "composite",
    ]
    .iter()
    .any(|kind| is_canonical_component_id(kind, component_id))
}

fn is_slug(value: &str) -> bool {
    !value.is_empty()
        && value
            .bytes()
            .next()
            .is_some_and(|byte| byte.is_ascii_lowercase())
        && value
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
}

fn text_field(value: &Value, field: &str) -> Option<String> {
    value
        .get(field)
        .and_then(Value::as_str)
        .map(ToOwned::to_owned)
}

#[derive(Debug, Clone, Copy)]
enum ReadFailure {
    Escape,
    Missing,
    Inaccessible,
}

fn read_checkout_file(root: &Path, relative: &str) -> Result<Vec<u8>, ReadFailure> {
    let relative_path = normalized_relative_path(relative).ok_or(ReadFailure::Escape)?;
    let parts = relative_path.components().collect::<Vec<_>>();
    let mut candidate = root.to_path_buf();
    for (index, part) in parts.iter().enumerate() {
        let Component::Normal(part) = part else {
            return Err(ReadFailure::Escape);
        };
        candidate.push(part);
        let metadata = match std::fs::symlink_metadata(&candidate) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                return Err(ReadFailure::Missing)
            }
            Err(_) => return Err(ReadFailure::Inaccessible),
        };
        if metadata.file_type().is_symlink() {
            let resolved =
                std::fs::canonicalize(&candidate).map_err(|_| ReadFailure::Inaccessible)?;
            if !resolved.starts_with(root) {
                return Err(ReadFailure::Escape);
            }
            if index + 1 < parts.len() && !resolved.is_dir() {
                return Err(ReadFailure::Inaccessible);
            }
            candidate = resolved;
        } else if index + 1 < parts.len() && !metadata.is_dir() {
            return Err(ReadFailure::Inaccessible);
        }
    }
    let canonical = std::fs::canonicalize(&candidate).map_err(|_| ReadFailure::Inaccessible)?;
    if !canonical.starts_with(root) {
        return Err(ReadFailure::Escape);
    }
    if !canonical.is_file() {
        return Err(ReadFailure::Inaccessible);
    }
    std::fs::read(canonical).map_err(|_| ReadFailure::Inaccessible)
}

fn normalized_relative_path(value: &str) -> Option<PathBuf> {
    if value.is_empty() {
        return None;
    }
    let path = Path::new(value);
    if path.is_absolute()
        || path
            .components()
            .any(|part| !matches!(part, Component::Normal(_)))
    {
        return None;
    }
    Some(path.to_path_buf())
}

fn safe_issue_path(value: &str, component_id: &str) -> String {
    normalized_relative_path(value)
        .map(|path| path_to_slashes(&path))
        .unwrap_or_else(|| format!("components/registry.yml#{component_id}"))
}

fn path_to_slashes(path: &Path) -> String {
    path.components()
        .filter_map(|component| match component {
            Component::Normal(value) => value.to_str(),
            _ => None,
        })
        .collect::<Vec<_>>()
        .join("/")
}

pub(super) fn format_digest(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

pub(super) fn issue(code: &str, source_path: &str, safe_message: &str) -> SotIssue {
    SotIssue {
        code: code.to_string(),
        source_path: source_path.to_string(),
        safe_message: safe_message.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use std::fs;
    use std::os::unix::fs::{symlink, PermissionsExt};
    use std::os::unix::net::UnixListener;

    use tempfile::tempdir;

    use super::*;
    use crate::contexts::install::{InstallWorkspace, InstallWorkspaceError};

    fn write(path: &Path, body: &str) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, body).unwrap();
    }

    fn renderer_fixture() -> tempfile::TempDir {
        let fixture = tempdir().unwrap();
        write(
            &fixture.path().join("components/registry.yml"),
            r#"version: "1"
components:
  harnesskit.skill.fixture:
    kind: skill
    status: draft
    path: components/skills/fixture/component.yml
"#,
        );
        write(
            &fixture
                .path()
                .join("components/skills/fixture/component.yml"),
            r#"component_id: harnesskit.skill.fixture
kind: skill
status: draft
title: Old title
owned_files:
  - components/skills/fixture/SKILL.md
targets:
  codex: { output_path: skills/fixture }
"#,
        );
        write(
            &fixture.path().join("components/skills/fixture/SKILL.md"),
            "# Fixture\nbody\n",
        );
        for (relative, schema) in [
            (
                "schemas/component.schema.json",
                include_str!("../../../../schemas/component.schema.json"),
            ),
            (
                "schemas/agent.schema.json",
                include_str!("../../../../schemas/agent.schema.json"),
            ),
            (
                "schemas/workflow.schema.json",
                include_str!("../../../../schemas/workflow.schema.json"),
            ),
            (
                "schemas/composite.schema.json",
                include_str!("../../../../schemas/composite.schema.json"),
            ),
            (
                "schemas/profile.schema.json",
                include_str!("../../../../schemas/profile.schema.json"),
            ),
        ] {
            write(&fixture.path().join(relative), schema);
        }
        fixture
    }

    fn app_temp_fixture() -> (tempfile::TempDir, PathBuf) {
        let fixture = tempdir().unwrap();
        let app_temp_root = fixture.path().join("app-temp");
        fs::create_dir(&app_temp_root).unwrap();
        fs::set_permissions(&app_temp_root, fs::Permissions::from_mode(0o700)).unwrap();
        let app_temp_root = fs::canonicalize(app_temp_root).unwrap();
        (fixture, app_temp_root)
    }

    #[test]
    fn regular_only_revision_matches_strict_install_source_digest() {
        let fixture = renderer_fixture();
        let canonical_root = fs::canonicalize(fixture.path()).unwrap();
        let (_temporary, app_temp_root) = app_temp_fixture();
        let workspace = InstallWorkspace::create(&canonical_root, &app_temp_root).unwrap();
        let revision = SourceRevisionManifest::observe_root(&canonical_root).unwrap();

        assert_eq!(revision.sha256(), workspace.source_manifest().sha256());
    }

    #[test]
    fn revision_hashes_symlink_target_bytes_without_following_outside_content() {
        let fixture = renderer_fixture();
        let canonical_root = fs::canonicalize(fixture.path()).unwrap();
        let outside = tempdir().unwrap();
        let outside_file = outside.path().join("outside.txt");
        fs::write(&outside_file, "outside v1\n").unwrap();
        let link = fixture.path().join("components/outside-link");
        symlink(&outside_file, &link).unwrap();
        let before = SourceRevisionManifest::observe_root(&canonical_root).unwrap();
        let (_temporary, app_temp_root) = app_temp_fixture();

        assert_eq!(
            InstallWorkspace::create(&canonical_root, &app_temp_root).unwrap_err(),
            InstallWorkspaceError::PreviewStale
        );
        fs::write(&outside_file, "outside v2\n").unwrap();
        let outside_changed = SourceRevisionManifest::observe_root(&canonical_root).unwrap();
        assert_eq!(before, outside_changed);

        fs::remove_file(&link).unwrap();
        symlink("different-target", &link).unwrap();
        let target_changed = SourceRevisionManifest::observe_root(&canonical_root).unwrap();
        assert_ne!(before.sha256(), target_changed.sha256());
    }

    #[test]
    fn revision_represents_socket_metadata_while_strict_capture_rejects_it() {
        let fixture = renderer_fixture();
        let canonical_root = fs::canonicalize(fixture.path()).unwrap();
        let socket = fixture.path().join("components/fixture.sock");
        let listener = UnixListener::bind(&socket).unwrap();
        let with_socket = SourceRevisionManifest::observe_root(&canonical_root).unwrap();
        let canonical = with_socket.canonical_bytes();
        let (_temporary, app_temp_root) = app_temp_fixture();

        assert!(canonical
            .windows(b"components/fixture.sock".len())
            .any(|window| window == b"components/fixture.sock"));
        assert!(canonical
            .windows(b"socket".len())
            .any(|window| window == b"socket"));
        assert_eq!(
            InstallWorkspace::create(&canonical_root, &app_temp_root).unwrap_err(),
            InstallWorkspaceError::PreviewStale
        );

        drop(listener);
        fs::remove_file(&socket).unwrap();
        let without_socket = SourceRevisionManifest::observe_root(&canonical_root).unwrap();
        assert_ne!(with_socket.sha256(), without_socket.sha256());
    }

    #[test]
    fn mutation_after_parse_before_revision_observation_fails_closed() {
        let fixture = renderer_fixture();
        let manifest_path = fixture
            .path()
            .join("components/skills/fixture/component.yml");
        let canonical_root = fs::canonicalize(fixture.path()).unwrap();
        let before_revision = SourceRevisionManifest::observe_root(&canonical_root)
            .unwrap()
            .sha256();

        let result = SotSourceReader::load_with_mid_read_hook(fixture.path(), || {
            let changed = fs::read_to_string(&manifest_path)
                .unwrap()
                .replace("title: Old title", "title: New title");
            fs::write(&manifest_path, changed).unwrap();
        });

        match result {
            Err(error) => assert_eq!(error, "SoT source changed while loading"),
            Ok(source) => {
                let parsed_title = source
                    .manifests
                    .get("harnesskit.skill.fixture")
                    .and_then(|manifest| text_field(&manifest.value, "title"));
                panic!(
                    "stale source published: parsed_title={parsed_title:?}, revision_changed={}",
                    source.source_revision != before_revision
                );
            }
        }
    }
}
