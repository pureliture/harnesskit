use std::collections::BTreeMap;

use serde::{Deserialize, Deserializer, Serialize, Serializer};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotSnapshot {
    pub snapshot_id: String,
    pub checkout_summary: SotCheckoutSummary,
    pub components: Vec<SotComponent>,
    pub profiles: Vec<SotProfile>,
    #[serde(default)]
    pub workflows: Vec<SotWorkflow>,
    pub unprofiled_component_ids: Vec<String>,
    pub relations: Vec<SotRelation>,
    pub graph_projection: SotGraphProjection,
    pub navigation_projection: SotNavigationProjection,
    pub issues: Vec<SotIssue>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotWorkflow {
    pub workflow_id: String,
    pub status: String,
    pub title: String,
    pub description: String,
    pub domain: Option<String>,
    pub runtime_implemented: bool,
    pub source_path: String,
    pub raw_yaml: String,
    pub optional_invoked_by: Option<String>,
    pub steps: Vec<SotWorkflowStep>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotWorkflowStep {
    pub workflow_id: String,
    pub ordinal: usize,
    pub step_id: String,
    pub title: Option<String>,
    pub description: Option<String>,
    pub authored_fields: SotWorkflowStepAuthoredFields,
    pub resolved_component_ids: Vec<String>,
    pub unresolved_references: Vec<SotWorkflowUnresolvedReference>,
    pub source_path: String,
    #[serde(skip)]
    pub(crate) component_references: Vec<SotWorkflowComponentReference>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotWorkflowStepAuthoredFields {
    pub agent: Option<String>,
    pub skill: Option<String>,
    pub rule: Option<String>,
    pub role: Option<String>,
    pub prompt_template: Option<String>,
    pub mode_fanout: BTreeMap<String, Vec<String>>,
    pub output: Option<String>,
    pub gate_order: Option<u64>,
    pub requires_pass_from: Option<String>,
    pub loop_back_to: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotWorkflowUnresolvedReference {
    pub source_field: String,
    pub reference: String,
    pub reference_kind: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct SotWorkflowComponentReference {
    pub component_id: String,
    pub source_field: String,
    pub mode: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotCheckoutSummary {
    pub source_revision: String,
    pub canonical_path: String,
    pub branch: Option<String>,
    pub detached: Option<bool>,
    pub dirty: Option<bool>,
    pub recent_commits: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotComponent {
    pub component_id: String,
    pub kind: String,
    pub status: String,
    pub title: String,
    pub summary: Option<String>,
    pub domain: Option<String>,
    pub targets: Vec<SotTarget>,
    pub provenance: SotProvenance,
    pub owned_files: Vec<String>,
    pub profile_ids: Vec<String>,
    #[serde(default)]
    pub install_scopes: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct SotTarget {
    pub target_id: String,
    pub support_status: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotProvenance {
    pub mode: Option<String>,
    pub source_classification: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotProfile {
    pub profile_id: String,
    pub status: Option<String>,
    pub title: String,
    pub summary: Option<String>,
    pub component_ids: Vec<String>,
    #[serde(default)]
    pub base_component_ids: Vec<String>,
    #[serde(default)]
    pub scope_component_ids: BTreeMap<String, Vec<String>>,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct SotRelation {
    pub source: String,
    pub target: String,
    pub relation_type: String,
    pub source_path: String,
    pub source_field: String,
    pub declarative_only: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotGraphProjection {
    #[serde(default = "default_graph_schema_version")]
    pub schema_version: u32,
    #[serde(default)]
    pub snapshot_id: String,
    #[serde(default)]
    pub layout_seed: String,
    #[serde(default)]
    pub nodes: Vec<SotGraphNode>,
    #[serde(default)]
    pub links: Vec<SotGraphLink>,
}

const fn default_graph_schema_version() -> u32 {
    2
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "node_type")]
pub enum SotGraphNode {
    #[serde(rename = "component")]
    ComponentEntity(SotComponentEntityGraphNode),
    #[serde(rename = "relation")]
    Relation(SotRelationGraphNode),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotRelationGraphNode {
    pub node_id: String,
    pub relation_kind: String,
    pub canonical_id: String,
    pub name: String,
    pub exact_count: usize,
    pub anchor_ordinal: usize,
    pub size_scale: SotSizeScale,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SotSizeScale(u16);

impl SotSizeScale {
    pub fn from_ratio(exact_count: usize, maximum_count: usize) -> Self {
        let bounded = if maximum_count == 0 {
            1_000
        } else {
            1_000 + ((exact_count.min(maximum_count) * 200) / maximum_count) as u16
        };
        Self(bounded.clamp(1_000, 1_200))
    }

    pub fn as_f64(self) -> f64 {
        f64::from(self.0) / 1_000.0
    }
}

impl Serialize for SotSizeScale {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        serializer.serialize_f64(f64::from(self.0) / 1_000.0)
    }
}

impl<'de> Deserialize<'de> for SotSizeScale {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let value = f64::deserialize(deserializer)?;
        if !(1.0..=1.2).contains(&value) {
            return Err(serde::de::Error::custom(
                "relation size scale must be between 1.0 and 1.2",
            ));
        }
        Ok(Self((value * 1_000.0).round() as u16))
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotComponentEntityGraphNode {
    pub node_id: String,
    pub component_id: String,
    pub kind: String,
    pub domain: String,
    pub relation_degree: usize,
    pub profile_ids: Vec<String>,
    pub workflow_ids: Vec<String>,
}

impl SotGraphNode {
    pub fn node_id(&self) -> &str {
        match self {
            Self::ComponentEntity(node) => &node.node_id,
            Self::Relation(node) => &node.node_id,
        }
    }

    pub fn component_id(&self) -> Option<&str> {
        match self {
            Self::ComponentEntity(node) => Some(&node.component_id),
            Self::Relation(_) => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "semantic")]
pub enum SotGraphLink {
    #[serde(rename = "profile-membership")]
    ProfileMembership(SotProfileMembershipLink),
    #[serde(rename = "workflow-step")]
    WorkflowStep(SotWorkflowIncidenceBundle),
    #[serde(rename = "invoked-by")]
    InvokedBy(SotInvokedByLink),
    #[serde(rename = "component-cross-link")]
    ComponentCrossLink(SotComponentCrossLink),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SotGraphDirectionality {
    Directed,
    Unordered,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotProfileMembershipLink {
    pub link_id: String,
    pub source_node_id: String,
    pub target_node_id: String,
    pub directionality: SotGraphDirectionality,
    pub profile_node_id: String,
    pub component_node_id: String,
    pub provenance: SotMembershipProvenance,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotWorkflowIncidenceBundle {
    pub link_id: String,
    pub source_node_id: String,
    pub target_node_id: String,
    pub directionality: SotGraphDirectionality,
    pub workflow_node_id: String,
    pub component_node_id: String,
    pub occurrences: Vec<SotWorkflowOccurrence>,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct SotWorkflowOccurrence {
    pub ordinal: usize,
    pub step_id: String,
    pub source_field: String,
    pub role: Option<String>,
    pub mode: Option<String>,
    pub gate_order: Option<u64>,
    pub loop_back_to: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotInvokedByLink {
    pub link_id: String,
    pub source_node_id: String,
    pub target_node_id: String,
    pub directionality: SotGraphDirectionality,
    pub workflow_node_id: String,
    pub component_node_id: String,
    pub source_path: String,
    pub source_field: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotComponentCrossLink {
    pub link_id: String,
    pub source_node_id: String,
    pub target_node_id: String,
    pub directionality: SotGraphDirectionality,
    pub source_component_id: String,
    pub target_component_id: String,
    pub relation_type: String,
    pub provenance: SotComponentRelationProvenance,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum SotMembershipProvenance {
    CanonicalProfile,
    DerivedUnprofiled,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotComponentRelationProvenance {
    pub source_path: String,
    pub source_field: String,
    pub declarative_only: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotNavigationProjection {
    pub all_component_ids: Vec<String>,
    pub groups: Vec<SotNavigationGroup>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotNavigationGroup {
    pub kind: String,
    pub component_ids: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct SotIssue {
    pub code: String,
    pub source_path: String,
    pub safe_message: String,
}
