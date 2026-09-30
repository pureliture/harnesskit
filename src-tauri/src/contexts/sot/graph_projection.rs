use std::collections::{BTreeMap, BTreeSet};

use sha2::{Digest, Sha256};

use super::domain::{
    SotComponent, SotComponentCrossLink, SotComponentEntityGraphNode,
    SotComponentRelationProvenance, SotGraphDirectionality, SotGraphLink, SotGraphNode,
    SotGraphProjection, SotInvokedByLink, SotMembershipProvenance, SotProfile,
    SotProfileMembershipLink, SotRelation, SotRelationGraphNode, SotSizeScale, SotWorkflow,
    SotWorkflowIncidenceBundle, SotWorkflowOccurrence,
};
use super::source::format_digest;

const UNPROFILED_CANONICAL_ID: &str = "projection:unprofiled";
const UNPROFILED_NODE_ID: &str = "unprofiled:projection:unprofiled";

pub(super) struct SotGraphProjectionService;

impl SotGraphProjectionService {
    pub(super) fn project(
        components: &[SotComponent],
        profiles: &[SotProfile],
        workflows: &[SotWorkflow],
        unprofiled_component_ids: &[String],
        relations: &[SotRelation],
    ) -> SotGraphProjection {
        let component_ids = components
            .iter()
            .map(|component| component.component_id.as_str())
            .collect::<BTreeSet<_>>();
        let links = project_links(
            &component_ids,
            profiles,
            workflows,
            unprofiled_component_ids,
            relations,
        );
        let nodes = project_nodes(
            components,
            profiles,
            workflows,
            unprofiled_component_ids,
            &links,
        );
        let layout_seed = layout_seed(&nodes, &links);
        SotGraphProjection {
            schema_version: 2,
            snapshot_id: String::new(),
            layout_seed,
            nodes,
            links,
        }
    }
}

fn project_nodes(
    components: &[SotComponent],
    profiles: &[SotProfile],
    workflows: &[SotWorkflow],
    unprofiled_component_ids: &[String],
    links: &[SotGraphLink],
) -> Vec<SotGraphNode> {
    let maximum_relation_count = profiles
        .iter()
        .map(|profile| profile.component_ids.len())
        .chain(workflows.iter().map(|workflow| workflow.steps.len()))
        .chain(std::iter::once(unprofiled_component_ids.len()))
        .max()
        .unwrap_or_default();
    let mut relation_degree = BTreeMap::<String, usize>::new();
    let mut workflow_ids = BTreeMap::<String, BTreeSet<String>>::new();
    for link in links {
        match link {
            SotGraphLink::ProfileMembership(link) => {
                increment_degree(&mut relation_degree, &link.component_node_id);
            }
            SotGraphLink::WorkflowStep(link) => {
                increment_degree(&mut relation_degree, &link.component_node_id);
                workflow_ids
                    .entry(link.component_node_id.clone())
                    .or_default()
                    .insert(
                        link.workflow_node_id
                            .strip_prefix("workflow:")
                            .unwrap_or(&link.workflow_node_id)
                            .to_string(),
                    );
            }
            SotGraphLink::InvokedBy(link) => {
                increment_degree(&mut relation_degree, &link.component_node_id);
                workflow_ids
                    .entry(link.component_node_id.clone())
                    .or_default()
                    .insert(
                        link.workflow_node_id
                            .strip_prefix("workflow:")
                            .unwrap_or(&link.workflow_node_id)
                            .to_string(),
                    );
            }
            SotGraphLink::ComponentCrossLink(link) => {
                increment_degree(
                    &mut relation_degree,
                    &component_node_id(&link.source_component_id),
                );
                increment_degree(
                    &mut relation_degree,
                    &component_node_id(&link.target_component_id),
                );
            }
        }
    }

    let mut sorted_profiles = profiles.iter().collect::<Vec<_>>();
    sorted_profiles.sort_by(|left, right| left.profile_id.cmp(&right.profile_id));
    let mut sorted_workflows = workflows.iter().collect::<Vec<_>>();
    sorted_workflows.sort_by(|left, right| left.workflow_id.cmp(&right.workflow_id));
    let mut sorted_components = components.iter().collect::<Vec<_>>();
    sorted_components.sort_by(|left, right| left.component_id.cmp(&right.component_id));

    let mut nodes = Vec::with_capacity(
        sorted_profiles.len() + sorted_workflows.len() + sorted_components.len() + 1,
    );
    for (index, profile) in sorted_profiles.iter().enumerate() {
        nodes.push(SotGraphNode::Relation(SotRelationGraphNode {
            node_id: profile_node_id(&profile.profile_id),
            relation_kind: "profile".to_string(),
            canonical_id: profile.profile_id.clone(),
            name: profile.title.clone(),
            exact_count: profile.component_ids.len(),
            anchor_ordinal: index + 1,
            size_scale: SotSizeScale::from_ratio(
                profile.component_ids.len(),
                maximum_relation_count,
            ),
        }));
    }
    nodes.push(SotGraphNode::Relation(SotRelationGraphNode {
        node_id: UNPROFILED_NODE_ID.to_string(),
        relation_kind: "unprofiled".to_string(),
        canonical_id: UNPROFILED_CANONICAL_ID.to_string(),
        name: "Unprofiled".to_string(),
        exact_count: unprofiled_component_ids.len(),
        anchor_ordinal: sorted_profiles.len() + 1,
        size_scale: SotSizeScale::from_ratio(
            unprofiled_component_ids.len(),
            maximum_relation_count,
        ),
    }));
    for (index, workflow) in sorted_workflows.iter().enumerate() {
        nodes.push(SotGraphNode::Relation(SotRelationGraphNode {
            node_id: workflow_node_id(&workflow.workflow_id),
            relation_kind: "workflow".to_string(),
            canonical_id: workflow.workflow_id.clone(),
            name: workflow.title.clone(),
            exact_count: workflow.steps.len(),
            anchor_ordinal: index + 1,
            size_scale: SotSizeScale::from_ratio(workflow.steps.len(), maximum_relation_count),
        }));
    }
    for component in sorted_components {
        let node_id = component_node_id(&component.component_id);
        nodes.push(SotGraphNode::ComponentEntity(SotComponentEntityGraphNode {
            node_id: node_id.clone(),
            component_id: component.component_id.clone(),
            kind: component.kind.clone(),
            domain: component
                .domain
                .clone()
                .unwrap_or_else(|| "unknown".to_string()),
            relation_degree: relation_degree.get(&node_id).copied().unwrap_or_default(),
            profile_ids: component.profile_ids.clone(),
            workflow_ids: workflow_ids
                .remove(&node_id)
                .unwrap_or_default()
                .into_iter()
                .collect(),
        }));
    }
    nodes.sort_by(|left, right| left.node_id().cmp(right.node_id()));
    nodes
}

fn project_links(
    component_ids: &BTreeSet<&str>,
    profiles: &[SotProfile],
    workflows: &[SotWorkflow],
    unprofiled_component_ids: &[String],
    relations: &[SotRelation],
) -> Vec<SotGraphLink> {
    let mut links = Vec::new();
    for profile in profiles {
        for component_id in profile
            .component_ids
            .iter()
            .filter(|component_id| component_ids.contains(component_id.as_str()))
        {
            let profile_node_id = profile_node_id(&profile.profile_id);
            let component_node_id = component_node_id(component_id);
            links.push(SotGraphLink::ProfileMembership(SotProfileMembershipLink {
                link_id: format!("profile-membership:{}:{component_id}", profile.profile_id),
                source_node_id: profile_node_id.clone(),
                target_node_id: component_node_id.clone(),
                directionality: SotGraphDirectionality::Unordered,
                profile_node_id,
                component_node_id,
                provenance: SotMembershipProvenance::CanonicalProfile,
            }));
        }
    }
    for component_id in unprofiled_component_ids
        .iter()
        .filter(|component_id| component_ids.contains(component_id.as_str()))
    {
        let profile_node_id = UNPROFILED_NODE_ID.to_string();
        let component_node_id = component_node_id(component_id);
        links.push(SotGraphLink::ProfileMembership(SotProfileMembershipLink {
            link_id: format!("profile-membership:{UNPROFILED_CANONICAL_ID}:{component_id}"),
            source_node_id: profile_node_id.clone(),
            target_node_id: component_node_id.clone(),
            directionality: SotGraphDirectionality::Unordered,
            profile_node_id,
            component_node_id,
            provenance: SotMembershipProvenance::DerivedUnprofiled,
        }));
    }

    let mut workflow_bundles = BTreeMap::<(String, String), Vec<SotWorkflowOccurrence>>::new();
    for workflow in workflows {
        for step in &workflow.steps {
            for reference in &step.component_references {
                if !component_ids.contains(reference.component_id.as_str()) {
                    continue;
                }
                workflow_bundles
                    .entry((workflow.workflow_id.clone(), reference.component_id.clone()))
                    .or_default()
                    .push(SotWorkflowOccurrence {
                        ordinal: step.ordinal,
                        step_id: step.step_id.clone(),
                        source_field: reference.source_field.clone(),
                        role: step.authored_fields.role.clone(),
                        mode: reference.mode.clone(),
                        gate_order: step.authored_fields.gate_order,
                        loop_back_to: step.authored_fields.loop_back_to.clone(),
                    });
            }
        }
        if let Some(invoker) = workflow
            .optional_invoked_by
            .as_ref()
            .filter(|invoker| component_ids.contains(invoker.as_str()))
        {
            let workflow_node_id = workflow_node_id(&workflow.workflow_id);
            let component_node_id = component_node_id(invoker);
            links.push(SotGraphLink::InvokedBy(SotInvokedByLink {
                link_id: format!("invoked-by:{}:{invoker}", workflow.workflow_id),
                source_node_id: component_node_id.clone(),
                target_node_id: workflow_node_id.clone(),
                directionality: SotGraphDirectionality::Directed,
                workflow_node_id,
                component_node_id,
                source_path: workflow.source_path.clone(),
                source_field: "optional_invoked_by".to_string(),
            }));
        }
    }
    for ((workflow_id, component_id), mut occurrences) in workflow_bundles {
        occurrences.sort();
        occurrences.dedup();
        let workflow_node_id = workflow_node_id(&workflow_id);
        let component_node_id = component_node_id(&component_id);
        links.push(SotGraphLink::WorkflowStep(SotWorkflowIncidenceBundle {
            link_id: format!("workflow-step:{workflow_id}:{component_id}"),
            source_node_id: workflow_node_id.clone(),
            target_node_id: component_node_id.clone(),
            directionality: SotGraphDirectionality::Directed,
            workflow_node_id,
            component_node_id,
            occurrences,
        }));
    }
    for relation in relations {
        if !component_ids.contains(relation.source.as_str())
            || !component_ids.contains(relation.target.as_str())
        {
            continue;
        }
        let source_node_id = component_node_id(&relation.source);
        let target_node_id = component_node_id(&relation.target);
        links.push(SotGraphLink::ComponentCrossLink(SotComponentCrossLink {
            link_id: format!(
                "component-cross-link:{}:{}:{}:{}",
                relation.relation_type, relation.source, relation.target, relation.source_field
            ),
            source_node_id,
            target_node_id,
            directionality: component_cross_link_directionality(&relation.relation_type),
            source_component_id: relation.source.clone(),
            target_component_id: relation.target.clone(),
            relation_type: relation.relation_type.clone(),
            provenance: SotComponentRelationProvenance {
                source_path: relation.source_path.clone(),
                source_field: relation.source_field.clone(),
                declarative_only: relation.declarative_only,
            },
        }));
    }
    links.sort_by_key(|link| serde_json::to_string(link).unwrap_or_default());
    links
}

fn increment_degree(degrees: &mut BTreeMap<String, usize>, node_id: &str) {
    *degrees.entry(node_id.to_string()).or_default() += 1;
}

fn component_cross_link_directionality(relation_type: &str) -> SotGraphDirectionality {
    match relation_type {
        "registry_replaced_by" | "registry_absorbed_by" | "composite_member" | "policy_ref" => {
            SotGraphDirectionality::Directed
        }
        _ => SotGraphDirectionality::Unordered,
    }
}

fn layout_seed(nodes: &[SotGraphNode], links: &[SotGraphLink]) -> String {
    let mut digest = Sha256::new();
    digest.update(serde_json::to_vec(&(1_u32, nodes, links)).unwrap_or_default());
    format_digest(digest.finalize().as_slice())
}

fn profile_node_id(profile_id: &str) -> String {
    format!("profile:{profile_id}")
}

fn workflow_node_id(workflow_id: &str) -> String {
    format!("workflow:{workflow_id}")
}

fn component_node_id(component_id: &str) -> String {
    format!("component:{component_id}")
}
