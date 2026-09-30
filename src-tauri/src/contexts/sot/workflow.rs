use std::collections::{BTreeMap, BTreeSet};

use serde_yaml::Value;

use super::domain::{
    SotIssue, SotWorkflow, SotWorkflowComponentReference, SotWorkflowStep,
    SotWorkflowStepAuthoredFields, SotWorkflowUnresolvedReference,
};
use super::source::{issue, SotSourceData};

pub(super) struct WorkflowProjector;

impl WorkflowProjector {
    pub(super) fn project(source: &SotSourceData, issues: &mut Vec<SotIssue>) -> Vec<SotWorkflow> {
        let registry_kinds = source
            .entries
            .iter()
            .map(|entry| (entry.component_id.as_str(), entry.kind.as_str()))
            .collect::<BTreeMap<_, _>>();
        let component_ids = registry_kinds
            .iter()
            .filter(|(_, kind)| **kind != "workflow")
            .map(|(component_id, _)| *component_id)
            .collect::<BTreeSet<_>>();
        let mut workflows = source
            .entries
            .iter()
            .filter(|entry| entry.kind == "workflow")
            .map(|entry| {
                let manifest = source.manifests.get(&entry.component_id);
                let value = manifest.map(|manifest| &manifest.value);
                let source_path = manifest
                    .map(|manifest| manifest.source_path.clone())
                    .unwrap_or_else(|| entry.manifest_path.clone());
                let optional_invoked_by =
                    value.and_then(|value| text(value, "optional_invoked_by"));
                if let Some(invoker) = optional_invoked_by.as_deref() {
                    if !component_ids.contains(invoker) {
                        issues.push(issue(
                            "unresolved_workflow_component_reference",
                            &format!("{source_path}#optional_invoked_by"),
                            "Workflow invoker is outside the active Component registry",
                        ));
                    }
                }
                let steps = value
                    .and_then(|value| value.get("steps"))
                    .and_then(Value::as_sequence)
                    .map(|steps| {
                        project_steps(
                            &entry.component_id,
                            &source_path,
                            steps,
                            &component_ids,
                            &registry_kinds,
                            issues,
                        )
                    })
                    .unwrap_or_default();
                SotWorkflow {
                    workflow_id: entry.component_id.clone(),
                    status: entry.status.clone(),
                    title: value
                        .and_then(|value| text(value, "title"))
                        .unwrap_or_else(|| display_title(&entry.component_id)),
                    description: value
                        .and_then(|value| text(value, "description"))
                        .unwrap_or_default(),
                    domain: value.and_then(|value| text(value, "domain")),
                    runtime_implemented: value
                        .and_then(|value| value.get("runtime_implemented"))
                        .and_then(Value::as_bool)
                        .unwrap_or(false),
                    source_path,
                    raw_yaml: manifest
                        .map(|manifest| manifest.raw_yaml.clone())
                        .unwrap_or_default(),
                    optional_invoked_by,
                    steps,
                }
            })
            .collect::<Vec<_>>();
        workflows.sort_by(|left, right| left.workflow_id.cmp(&right.workflow_id));
        workflows
    }
}

fn project_steps(
    workflow_id: &str,
    source_path: &str,
    values: &[Value],
    component_ids: &BTreeSet<&str>,
    registry_kinds: &BTreeMap<&str, &str>,
    issues: &mut Vec<SotIssue>,
) -> Vec<SotWorkflowStep> {
    let step_ids = values
        .iter()
        .filter_map(|step| text(step, "id"))
        .collect::<Vec<_>>();
    let mut seen = BTreeSet::new();
    for (index, step_id) in step_ids.iter().enumerate() {
        if !seen.insert(step_id.as_str()) {
            issues.push(issue(
                "duplicate_workflow_step_id",
                &format!("{source_path}#steps[{index}].id"),
                "Workflow step identity is declared more than once",
            ));
        }
    }
    let known_step_ids = step_ids.iter().map(String::as_str).collect::<BTreeSet<_>>();

    values
        .iter()
        .enumerate()
        .filter_map(|(index, value)| {
            let step_id = text(value, "id")?;
            let authored_fields = authored_fields(value);
            let mut component_references = Vec::new();
            let mut unresolved_references = Vec::new();

            for (field, reference) in [
                ("agent", authored_fields.agent.as_deref()),
                ("skill", authored_fields.skill.as_deref()),
                ("rule", authored_fields.rule.as_deref()),
            ] {
                if let Some(reference) = reference {
                    resolve_component_reference(
                        reference,
                        &format!("steps[{index}].{field}"),
                        None,
                        source_path,
                        component_ids,
                        registry_kinds,
                        field,
                        &mut component_references,
                        &mut unresolved_references,
                        issues,
                    );
                }
            }
            for (mode, agents) in &authored_fields.mode_fanout {
                for (agent_index, reference) in agents.iter().enumerate() {
                    resolve_component_reference(
                        reference,
                        &format!("steps[{index}].mode_fanout.{mode}.agents[{agent_index}]"),
                        Some(mode.clone()),
                        source_path,
                        component_ids,
                        registry_kinds,
                        "agent",
                        &mut component_references,
                        &mut unresolved_references,
                        issues,
                    );
                }
            }
            for (field, target) in [
                (
                    "requires_pass_from",
                    authored_fields.requires_pass_from.as_deref(),
                ),
                ("loop_back_to", authored_fields.loop_back_to.as_deref()),
            ] {
                if let Some(target) = target {
                    if !known_step_ids.contains(target) {
                        let source_field = format!("steps[{index}].{field}");
                        unresolved_references.push(SotWorkflowUnresolvedReference {
                            source_field: source_field.clone(),
                            reference: target.to_string(),
                            reference_kind: "workflow-step".to_string(),
                        });
                        issues.push(issue(
                            "unresolved_workflow_step_reference",
                            &format!("{source_path}#{source_field}"),
                            "Workflow step reference does not resolve within the authored Workflow",
                        ));
                    }
                }
            }
            let resolved_component_ids = component_references
                .iter()
                .map(|reference| reference.component_id.clone())
                .collect::<BTreeSet<_>>()
                .into_iter()
                .collect();
            Some(SotWorkflowStep {
                workflow_id: workflow_id.to_string(),
                ordinal: index + 1,
                step_id,
                title: text(value, "title"),
                description: text(value, "description"),
                authored_fields,
                resolved_component_ids,
                unresolved_references,
                source_path: format!("{source_path}#steps[{index}]"),
                component_references,
            })
        })
        .collect()
}

#[allow(clippy::too_many_arguments)]
fn resolve_component_reference(
    reference: &str,
    source_field: &str,
    mode: Option<String>,
    source_path: &str,
    component_ids: &BTreeSet<&str>,
    registry_kinds: &BTreeMap<&str, &str>,
    expected_kind: &str,
    resolved: &mut Vec<SotWorkflowComponentReference>,
    unresolved: &mut Vec<SotWorkflowUnresolvedReference>,
    issues: &mut Vec<SotIssue>,
) {
    if component_ids.contains(reference)
        && registry_kinds.get(reference).copied() == Some(expected_kind)
    {
        resolved.push(SotWorkflowComponentReference {
            component_id: reference.to_string(),
            source_field: source_field.to_string(),
            mode,
        });
        return;
    }
    if component_ids.contains(reference) {
        unresolved.push(SotWorkflowUnresolvedReference {
            source_field: source_field.to_string(),
            reference: reference.to_string(),
            reference_kind: expected_kind.to_string(),
        });
        issues.push(issue(
            "workflow_reference_kind_mismatch",
            &format!("{source_path}#{source_field}"),
            "Workflow Component reference kind does not match the authored step field",
        ));
        return;
    }
    unresolved.push(SotWorkflowUnresolvedReference {
        source_field: source_field.to_string(),
        reference: reference.to_string(),
        reference_kind: "component".to_string(),
    });
    issues.push(issue(
        "unresolved_workflow_component_reference",
        &format!("{source_path}#{source_field}"),
        "Workflow Component reference is outside the active Component registry",
    ));
}

fn authored_fields(value: &Value) -> SotWorkflowStepAuthoredFields {
    let mut mode_fanout = BTreeMap::new();
    if let Some(modes) = value.get("mode_fanout").and_then(Value::as_mapping) {
        for (mode, configuration) in modes {
            let Some(mode) = mode.as_str() else {
                continue;
            };
            let agents = configuration
                .get("agents")
                .and_then(Value::as_sequence)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect::<Vec<_>>();
            mode_fanout.insert(mode.to_string(), agents);
        }
    }
    SotWorkflowStepAuthoredFields {
        agent: text(value, "agent"),
        skill: text(value, "skill"),
        rule: text(value, "rule"),
        role: text(value, "role"),
        prompt_template: text(value, "prompt_template"),
        mode_fanout,
        output: text(value, "output"),
        gate_order: value.get("gate_order").and_then(Value::as_u64),
        requires_pass_from: text(value, "requires_pass_from"),
        loop_back_to: text(value, "loop_back_to"),
    }
}

fn text(value: &Value, field: &str) -> Option<String> {
    value.get(field).and_then(Value::as_str).map(str::to_string)
}

fn display_title(component_id: &str) -> String {
    component_id
        .rsplit('.')
        .next()
        .unwrap_or(component_id)
        .split('-')
        .filter(|part| !part.is_empty())
        .map(|part| {
            let mut characters = part.chars();
            characters
                .next()
                .map(|first| format!("{}{}", first.to_uppercase(), characters.as_str()))
                .unwrap_or_default()
        })
        .collect::<Vec<_>>()
        .join(" ")
}
