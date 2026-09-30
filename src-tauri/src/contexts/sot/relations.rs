use std::collections::{BTreeSet, HashSet};

use serde_yaml::Value;

use super::domain::{SotIssue, SotRelation, SotWorkflow};
use super::source::{issue, SotSourceData, SourceManifest};

pub(super) struct RelationExtractor;

impl RelationExtractor {
    pub(super) fn extract(
        source: &SotSourceData,
        workflows: &[SotWorkflow],
        issues: &mut Vec<SotIssue>,
    ) -> Vec<SotRelation> {
        let active_ids = source
            .entries
            .iter()
            .filter(|entry| entry.canonical)
            .map(|entry| entry.component_id.as_str())
            .collect::<HashSet<_>>();
        let mut relations = BTreeSet::new();

        for entry in &source.entries {
            for (relation_type, source_field, target) in [
                (
                    "registry_replaced_by",
                    "replaced_by",
                    entry.replaced_by.as_deref(),
                ),
                (
                    "registry_absorbed_by",
                    "absorbed_by",
                    entry.absorbed_by.as_deref(),
                ),
            ] {
                if let Some(target) = target {
                    add_relation(
                        &mut relations,
                        issues,
                        &active_ids,
                        &entry.component_id,
                        target,
                        relation_type,
                        "components/registry.yml",
                        source_field,
                    );
                }
            }
        }

        for entry in &source.entries {
            let Some(manifest) = source.manifests.get(&entry.component_id) else {
                continue;
            };
            if !manifest.relation_schema_valid {
                continue;
            }
            match entry.kind.as_str() {
                "composite" => extract_composite_relations(
                    &entry.component_id,
                    manifest,
                    &active_ids,
                    &mut relations,
                    issues,
                ),
                "workflow" => {}
                _ => extract_policy_and_router_relations(
                    &entry.component_id,
                    &entry.kind,
                    manifest,
                    &active_ids,
                    &mut relations,
                    issues,
                ),
            }
        }

        extract_typed_workflow_relations(workflows, &active_ids, &mut relations, issues);

        relations.into_iter().collect()
    }
}

fn extract_policy_and_router_relations(
    source_id: &str,
    source_kind: &str,
    manifest: &SourceManifest,
    active_ids: &HashSet<&str>,
    output: &mut BTreeSet<SotRelation>,
    issues: &mut Vec<SotIssue>,
) {
    extract_optional_text_relation(
        manifest.value.get("policy_ref"),
        source_id,
        "policy_ref",
        "policy_ref",
        manifest,
        active_ids,
        output,
        issues,
    );

    let router_agent = source_kind == "agent"
        && manifest
            .value
            .get("role")
            .and_then(|role| role.get("type"))
            .and_then(Value::as_str)
            == Some("router");

    let Some(routes_value) = manifest.value.get("routes") else {
        return;
    };
    let Some(routes) = routes_value.as_mapping() else {
        issues.push(issue(
            "invalid_relation_field",
            &manifest.source_path,
            "Relation field does not match its canonical shape",
        ));
        return;
    };
    extract_optional_text_relation(
        routes.get(Value::String("policy_ref".to_string())),
        source_id,
        "routes.policy_ref",
        "policy_ref",
        manifest,
        active_ids,
        output,
        issues,
    );
    if !router_agent {
        return;
    }
    let Some(workflows) = routes
        .get(Value::String("workflows".to_string()))
        .and_then(Value::as_sequence)
    else {
        return;
    };
    for (index, workflow) in workflows.iter().enumerate() {
        let field = format!("routes.workflows[{index}].component_id");
        extract_optional_text_relation(
            workflow.get("component_id"),
            source_id,
            &field,
            "router_workflow",
            manifest,
            active_ids,
            output,
            issues,
        );
    }
}

fn extract_composite_relations(
    composite_id: &str,
    manifest: &SourceManifest,
    active_ids: &HashSet<&str>,
    output: &mut BTreeSet<SotRelation>,
    issues: &mut Vec<SotIssue>,
) {
    let Some(members_value) = manifest.value.get("members") else {
        return;
    };
    let Some(members) = members_value.as_sequence() else {
        issues.push(issue(
            "invalid_relation_field",
            &manifest.source_path,
            "Relation field does not match its canonical shape",
        ));
        return;
    };
    for (index, member) in members.iter().enumerate() {
        let field = format!("members[{index}]");
        extract_optional_text_relation(
            Some(member),
            composite_id,
            &field,
            "composite_member",
            manifest,
            active_ids,
            output,
            issues,
        );
    }
}

fn extract_typed_workflow_relations(
    workflows: &[SotWorkflow],
    active_ids: &HashSet<&str>,
    output: &mut BTreeSet<SotRelation>,
    issues: &mut Vec<SotIssue>,
) {
    for workflow in workflows {
        if let Some(invoker) = workflow.optional_invoked_by.as_deref() {
            add_relation(
                output,
                issues,
                active_ids,
                invoker,
                &workflow.workflow_id,
                "optional_invokes_workflow",
                &workflow.source_path,
                "optional_invoked_by",
            );
        }
        for step in &workflow.steps {
            for reference in &step.component_references {
                let relation_type = if reference.source_field.contains(".mode_fanout.") {
                    "workflow_mode_agent"
                } else if reference.source_field.ends_with(".agent") {
                    "workflow_agent"
                } else if reference.source_field.ends_with(".skill") {
                    "workflow_skill"
                } else if reference.source_field.ends_with(".rule") {
                    "workflow_rule"
                } else {
                    continue;
                };
                add_relation(
                    output,
                    issues,
                    active_ids,
                    &workflow.workflow_id,
                    &reference.component_id,
                    relation_type,
                    &workflow.source_path,
                    &reference.source_field,
                );
            }
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn extract_optional_text_relation(
    value: Option<&Value>,
    source: &str,
    source_field: &str,
    relation_type: &str,
    manifest: &SourceManifest,
    active_ids: &HashSet<&str>,
    output: &mut BTreeSet<SotRelation>,
    issues: &mut Vec<SotIssue>,
) {
    let Some(value) = value else {
        return;
    };
    let Some(target) = value.as_str() else {
        issues.push(issue(
            "invalid_relation_field",
            &manifest.source_path,
            "Relation field does not match its canonical shape",
        ));
        return;
    };
    add_relation(
        output,
        issues,
        active_ids,
        source,
        target,
        relation_type,
        &manifest.source_path,
        source_field,
    );
}

#[allow(clippy::too_many_arguments)]
fn add_relation(
    output: &mut BTreeSet<SotRelation>,
    issues: &mut Vec<SotIssue>,
    active_ids: &HashSet<&str>,
    source: &str,
    target: &str,
    relation_type: &str,
    source_path: &str,
    source_field: &str,
) {
    if !active_ids.contains(source) || !active_ids.contains(target) {
        issues.push(issue(
            "unresolved_relation",
            source_path,
            "Relation endpoint is outside the active registry",
        ));
        return;
    }
    output.insert(SotRelation {
        source: source.to_string(),
        target: target.to_string(),
        relation_type: relation_type.to_string(),
        source_path: source_path.to_string(),
        source_field: source_field.to_string(),
        declarative_only: true,
    });
}
