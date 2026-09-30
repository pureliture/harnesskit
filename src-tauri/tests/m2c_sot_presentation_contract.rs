use std::path::Path;

use harness_desktop_lib::api::dto::sot::{
    SotComponentCrossLinkDto, SotComponentEntityGraphNodeDto, SotComponentRelationProvenanceDto,
    SotGraphDirectionalityDto, SotGraphLinkDto, SotGraphNodeDto, SotGraphProjectionDto,
    SotInvokedByLinkDto, SotMembershipProvenanceDto, SotProfileMembershipLinkDto,
    SotRelationGraphNodeDto, SotWorkflowIncidenceBundleDto, SotWorkflowOccurrenceDto,
};
use serde_json::{json, Value};

const DOMAIN: &str = include_str!("../src/contexts/sot/domain.rs");
const DTO: &str = include_str!("../src/api/dto/sot.rs");
const GRAPH_PROJECTION: &str = include_str!("../src/contexts/sot/graph_projection.rs");
const SOT_MODULE: &str = include_str!("../src/contexts/sot/mod.rs");
const SNAPSHOT: &str = include_str!("../src/contexts/sot/snapshot.rs");

fn component_node() -> SotGraphNodeDto {
    SotGraphNodeDto::ComponentEntity(SotComponentEntityGraphNodeDto {
        node_id: "component:harnesskit.skill.alpha".to_string(),
        component_id: "harnesskit.skill.alpha".to_string(),
        kind: "skill".to_string(),
        domain: "core".to_string(),
        relation_degree: 3,
        profile_ids: vec!["harnesskit.profile.engineering".to_string()],
        workflow_ids: vec!["harnesskit.workflow.delivery".to_string()],
    })
}

fn relation_node(
    relation_kind: &str,
    canonical_id: &str,
    name: &str,
    size_scale: f64,
) -> SotGraphNodeDto {
    SotGraphNodeDto::Relation(SotRelationGraphNodeDto {
        node_id: format!("{relation_kind}:{canonical_id}"),
        relation_kind: relation_kind.to_string(),
        canonical_id: canonical_id.to_string(),
        name: name.to_string(),
        exact_count: 3,
        anchor_ordinal: 1,
        size_scale,
    })
}

fn assert_no_legacy_geometry(value: &Value) {
    match value {
        Value::Object(map) => {
            for field in [
                "x",
                "y",
                "width",
                "height",
                "hit_width",
                "hit_height",
                "logical_width",
                "logical_height",
                "view_box",
                "content_extent",
                "edges",
            ] {
                assert!(
                    !map.contains_key(field),
                    "legacy 2D field must not leak into the final graph wire contract: {field}"
                );
            }
            for child in map.values() {
                assert_no_legacy_geometry(child);
            }
        }
        Value::Array(values) => {
            for child in values {
                assert_no_legacy_geometry(child);
            }
        }
        _ => {}
    }
}

#[test]
fn relation_atlas_owns_component_entity_and_relation_node_discriminators() {
    assert!(DOMAIN.contains("pub enum SotGraphNode"));
    assert!(DOMAIN.contains("ComponentEntity(SotComponentEntityGraphNode)"));
    assert!(DOMAIN.contains("Relation(SotRelationGraphNode)"));
    assert!(DTO.contains("ComponentEntity(SotComponentEntityGraphNodeDto)"));
    assert!(DTO.contains("Relation(SotRelationGraphNodeDto)"));

    let nodes = [
        component_node(),
        relation_node(
            "profile",
            "harnesskit.profile.engineering",
            "Engineering",
            1.0,
        ),
        relation_node("workflow", "harnesskit.workflow.delivery", "Delivery", 1.1),
        relation_node("unprofiled", "projection:unprofiled", "Unprofiled", 1.2),
    ];
    let wire = nodes
        .into_iter()
        .map(|node| serde_json::to_value(node).unwrap())
        .collect::<Vec<_>>();

    assert_eq!(wire[0]["node_type"], "component");
    assert_eq!(wire[0]["component_id"], "harnesskit.skill.alpha");
    for node in &wire {
        assert!(
            node.get("shell").is_none(),
            "the canonical graph wire must not carry shell or hemisphere presentation authority"
        );
    }
    assert_eq!(wire[1]["node_type"], "relation");
    assert_eq!(wire[1]["relation_kind"], "profile");
    assert_eq!(wire[2]["relation_kind"], "workflow");
    assert_eq!(wire[3]["relation_kind"], "unprofiled");
    assert_eq!(wire[1]["size_scale"], 1.0);
    assert_eq!(wire[2]["size_scale"], 1.1);
    assert_eq!(wire[3]["size_scale"], 1.2);
}

#[test]
fn relation_node_size_scale_is_bounded_at_the_domain_authority() {
    assert!(
        DOMAIN.contains("pub struct SotSizeScale(u16)"),
        "relation size must use a bounded domain value rather than a free f64"
    );
    assert!(DOMAIN.contains("1.0..=1.2"));
    assert!(DOMAIN.contains("Self(bounded.clamp(1_000, 1_200))"));
    assert!(GRAPH_PROJECTION.contains("SotSizeScale::from_ratio"));
    assert!(DTO.contains("size_scale: node.size_scale.as_f64()"));
}

#[test]
fn final_graph_links_serialize_exact_relation_semantics_and_occurrences() {
    let links = [
        SotGraphLinkDto::ProfileMembership(SotProfileMembershipLinkDto {
            link_id: "profile-membership:engineering:alpha".to_string(),
            source_node_id: "profile:harnesskit.profile.engineering".to_string(),
            target_node_id: "component:harnesskit.skill.alpha".to_string(),
            directionality: SotGraphDirectionalityDto::Unordered,
            profile_node_id: "profile:harnesskit.profile.engineering".to_string(),
            component_node_id: "component:harnesskit.skill.alpha".to_string(),
            provenance: SotMembershipProvenanceDto::CanonicalProfile,
        }),
        SotGraphLinkDto::WorkflowStep(SotWorkflowIncidenceBundleDto {
            link_id: "workflow-step:delivery:alpha".to_string(),
            source_node_id: "workflow:harnesskit.workflow.delivery".to_string(),
            target_node_id: "component:harnesskit.skill.alpha".to_string(),
            directionality: SotGraphDirectionalityDto::Directed,
            workflow_node_id: "workflow:harnesskit.workflow.delivery".to_string(),
            component_node_id: "component:harnesskit.skill.alpha".to_string(),
            occurrences: vec![
                SotWorkflowOccurrenceDto {
                    ordinal: 1,
                    step_id: "prepare".to_string(),
                    source_field: "steps[0].skill".to_string(),
                    role: Some("author".to_string()),
                    mode: None,
                    gate_order: Some(1),
                    loop_back_to: None,
                },
                SotWorkflowOccurrenceDto {
                    ordinal: 3,
                    step_id: "revise".to_string(),
                    source_field: "steps[2].skill".to_string(),
                    role: Some("author".to_string()),
                    mode: Some("revision".to_string()),
                    gate_order: Some(3),
                    loop_back_to: Some("prepare".to_string()),
                },
            ],
        }),
        SotGraphLinkDto::InvokedBy(SotInvokedByLinkDto {
            link_id: "invoked-by:delivery:router".to_string(),
            source_node_id: "component:harnesskit.agent.router".to_string(),
            target_node_id: "workflow:harnesskit.workflow.delivery".to_string(),
            directionality: SotGraphDirectionalityDto::Directed,
            workflow_node_id: "workflow:harnesskit.workflow.delivery".to_string(),
            component_node_id: "component:harnesskit.agent.router".to_string(),
            source_path: "components/workflows/delivery/workflow.yml".to_string(),
            source_field: "optional_invoked_by".to_string(),
        }),
        SotGraphLinkDto::ComponentCrossLink(SotComponentCrossLinkDto {
            link_id: "component-cross-link:router:alpha".to_string(),
            source_node_id: "component:harnesskit.agent.router".to_string(),
            target_node_id: "component:harnesskit.skill.alpha".to_string(),
            directionality: SotGraphDirectionalityDto::Directed,
            source_component_id: "harnesskit.agent.router".to_string(),
            target_component_id: "harnesskit.skill.alpha".to_string(),
            relation_type: "agent_skill".to_string(),
            provenance: SotComponentRelationProvenanceDto {
                source_path: "components/agents/router/agent.yml".to_string(),
                source_field: "skills[0]".to_string(),
                declarative_only: true,
            },
        }),
    ];
    let wire = links
        .into_iter()
        .map(|link| serde_json::to_value(link).unwrap())
        .collect::<Vec<_>>();

    assert_eq!(wire[0]["semantic"], "profile-membership");
    assert_eq!(wire[1]["semantic"], "workflow-step");
    assert_eq!(wire[2]["semantic"], "invoked-by");
    assert_eq!(wire[3]["semantic"], "component-cross-link");
    assert_eq!(wire[1]["occurrences"][0]["ordinal"], 1);
    assert_eq!(wire[1]["occurrences"][1]["ordinal"], 3);
    assert_eq!(wire[1]["occurrences"][1]["loop_back_to"], "prepare");
    assert_eq!(wire[3]["provenance"]["declarative_only"], true);

    let expected = [
        (
            "unordered",
            "profile:harnesskit.profile.engineering",
            "component:harnesskit.skill.alpha",
        ),
        (
            "directed",
            "workflow:harnesskit.workflow.delivery",
            "component:harnesskit.skill.alpha",
        ),
        (
            "directed",
            "component:harnesskit.agent.router",
            "workflow:harnesskit.workflow.delivery",
        ),
        (
            "directed",
            "component:harnesskit.agent.router",
            "component:harnesskit.skill.alpha",
        ),
    ];
    for (link, (directionality, source, target)) in wire.iter().zip(expected) {
        assert_eq!(link["directionality"], directionality);
        assert_eq!(link["source_node_id"], source);
        assert_eq!(link["target_node_id"], target);
    }
}

#[test]
fn final_projection_wire_has_nodes_and_links_without_legacy_2d_authority() {
    let projection = SotGraphProjectionDto {
        schema_version: 2,
        snapshot_id: "snapshot-1".to_string(),
        layout_seed: "stable-layout-seed".to_string(),
        nodes: vec![component_node()],
        links: vec![SotGraphLinkDto::ProfileMembership(
            SotProfileMembershipLinkDto {
                link_id: "profile-membership:engineering:alpha".to_string(),
                source_node_id: "profile:harnesskit.profile.engineering".to_string(),
                target_node_id: "component:harnesskit.skill.alpha".to_string(),
                directionality: SotGraphDirectionalityDto::Unordered,
                profile_node_id: "profile:harnesskit.profile.engineering".to_string(),
                component_node_id: "component:harnesskit.skill.alpha".to_string(),
                provenance: SotMembershipProvenanceDto::CanonicalProfile,
            },
        )],
    };
    let wire = serde_json::to_value(projection).unwrap();

    assert_eq!(wire["schema_version"], 2);
    assert_eq!(wire["snapshot_id"], "snapshot-1");
    assert_eq!(wire["layout_seed"], "stable-layout-seed");
    assert_eq!(wire["nodes"].as_array().unwrap().len(), 1);
    assert_eq!(wire["links"].as_array().unwrap().len(), 1);
    assert_no_legacy_geometry(&wire);

    let layout_path = Path::new(env!("CARGO_MANIFEST_DIR")).join("src/contexts/sot/layout.rs");
    assert!(
        !layout_path.exists(),
        "legacy layout.rs must not remain a projection authority"
    );
    assert!(!SOT_MODULE.contains("mod layout"));
    assert!(!SNAPSHOT.contains("LayoutProjector"));
    for legacy_type in [
        "SotProfileGraphNodeDto",
        "SotUnprofiledGraphNodeDto",
        "SotGraphEdgeDto",
        "SotMembershipGraphEdgeDto",
        "SotComponentRelationGraphEdgeDto",
    ] {
        assert!(
            !DTO.contains(legacy_type),
            "legacy DTO remains public: {legacy_type}"
        );
    }
    assert!(!wire.as_object().unwrap().contains_key("edges"));
    assert_ne!(wire, json!({}));
}
