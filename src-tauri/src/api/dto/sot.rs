//! SoT dashboard command DTOs.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};

#[derive(Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "outcome", rename_all = "snake_case")]
pub enum CheckoutDirectoryPickerOutcomeDto {
    Selected { path: String },
    Cancelled,
    Failed { reason: String },
}

impl From<crate::contexts::sot::checkout_picker::PickerOutcome>
    for CheckoutDirectoryPickerOutcomeDto
{
    fn from(value: crate::contexts::sot::checkout_picker::PickerOutcome) -> Self {
        use crate::contexts::sot::checkout_picker::{PickerOutcome, PickerSafeReason};

        match value {
            PickerOutcome::Selected(path) => match path.into_os_string().into_string() {
                Ok(path) if !path.contains('\r') && !path.contains('\n') => Self::Selected { path },
                Ok(_) | Err(_) => Self::Failed {
                    reason: PickerSafeReason::SelectionUnavailable
                        .safe_message()
                        .to_owned(),
                },
            },
            PickerOutcome::Cancelled => Self::Cancelled,
            PickerOutcome::Failed(reason) => Self::Failed {
                reason: reason.safe_message().to_owned(),
            },
        }
    }
}

impl std::fmt::Debug for CheckoutDirectoryPickerOutcomeDto {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Selected { .. } => formatter.write_str("Selected(<redacted-path>)"),
            Self::Cancelled => formatter.write_str("Cancelled"),
            Self::Failed { reason } => formatter
                .debug_struct("Failed")
                .field("reason", reason)
                .finish(),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CheckoutRegistrationDto {
    pub checkout_id: String,
    pub repo_status: CheckoutRepoStatusDto,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CheckoutRepoStatusDto {
    pub branch: Option<String>,
    pub detached: bool,
    pub dirty: bool,
    pub recent_commits: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotSessionStateDto {
    pub checkout_id: Option<String>,
    pub snapshot_id: Option<String>,
    pub install_evidence_id: Option<String>,
}

impl From<crate::controller::RegisterCheckoutResponse> for CheckoutRegistrationDto {
    fn from(value: crate::controller::RegisterCheckoutResponse) -> Self {
        Self {
            checkout_id: value.checkout_id,
            repo_status: CheckoutRepoStatusDto {
                branch: value.repo_status.branch,
                detached: value.repo_status.detached,
                dirty: value.repo_status.dirty,
                recent_commits: value.repo_status.recent_commits,
            },
        }
    }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SotSnapshotDto {
    pub snapshot_id: String,
    pub checkout_summary: SotCheckoutSummaryDto,
    pub components: Vec<SotComponentDto>,
    pub profiles: Vec<SotProfileDto>,
    #[serde(default)]
    pub workflows: Vec<SotWorkflowDto>,
    pub unprofiled_component_ids: Vec<String>,
    pub relations: Vec<SotRelationDto>,
    pub graph_projection: SotGraphProjectionDto,
    pub navigation_projection: SotNavigationProjectionDto,
    pub issues: Vec<SotIssueDto>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotCheckoutSummaryDto {
    pub source_revision: String,
    pub branch: Option<String>,
    pub detached: Option<bool>,
    pub dirty: Option<bool>,
    pub recent_commits: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotComponentDto {
    pub component_id: String,
    pub kind: String,
    pub status: String,
    pub title: String,
    pub summary: Option<String>,
    pub domain: Option<String>,
    pub targets: Vec<SotTargetDto>,
    pub provenance: SotProvenanceDto,
    pub owned_files: Vec<String>,
    pub profile_ids: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotWorkflowDto {
    pub workflow_id: String,
    pub status: String,
    pub title: String,
    pub description: String,
    pub domain: Option<String>,
    pub runtime_implemented: bool,
    pub source_path: String,
    pub raw_yaml: String,
    pub optional_invoked_by: Option<String>,
    pub steps: Vec<SotWorkflowStepDto>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotWorkflowStepDto {
    pub workflow_id: String,
    pub ordinal: usize,
    pub step_id: String,
    pub title: Option<String>,
    pub description: Option<String>,
    pub authored_fields: SotWorkflowStepAuthoredFieldsDto,
    pub resolved_component_ids: Vec<String>,
    pub unresolved_references: Vec<SotWorkflowUnresolvedReferenceDto>,
    pub source_path: String,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotWorkflowStepAuthoredFieldsDto {
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
pub struct SotWorkflowUnresolvedReferenceDto {
    pub source_field: String,
    pub reference: String,
    pub reference_kind: String,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct SotTargetDto {
    pub target_id: String,
    pub support_status: Option<String>,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotProvenanceDto {
    pub mode: Option<String>,
    pub source_classification: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotProfileDto {
    pub profile_id: String,
    pub status: Option<String>,
    pub title: String,
    pub summary: Option<String>,
    pub component_ids: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct SotRelationDto {
    pub source: String,
    pub target: String,
    pub relation_type: String,
    pub source_path: String,
    pub source_field: String,
    pub declarative_only: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SotGraphProjectionDto {
    #[serde(default = "default_graph_schema_version")]
    pub schema_version: u32,
    #[serde(default)]
    pub snapshot_id: String,
    #[serde(default)]
    pub layout_seed: String,
    #[serde(default)]
    pub nodes: Vec<SotGraphNodeDto>,
    #[serde(default)]
    pub links: Vec<SotGraphLinkDto>,
}

const fn default_graph_schema_version() -> u32 {
    2
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "node_type")]
pub enum SotGraphNodeDto {
    #[serde(rename = "component")]
    ComponentEntity(SotComponentEntityGraphNodeDto),
    #[serde(rename = "relation")]
    Relation(SotRelationGraphNodeDto),
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct SotRelationGraphNodeDto {
    pub node_id: String,
    pub relation_kind: String,
    pub canonical_id: String,
    pub name: String,
    pub exact_count: usize,
    pub anchor_ordinal: usize,
    pub size_scale: f64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotComponentEntityGraphNodeDto {
    pub node_id: String,
    pub component_id: String,
    pub kind: String,
    pub domain: String,
    pub relation_degree: usize,
    pub profile_ids: Vec<String>,
    pub workflow_ids: Vec<String>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SotGraphDirectionalityDto {
    Directed,
    Unordered,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "semantic")]
pub enum SotGraphLinkDto {
    #[serde(rename = "profile-membership")]
    ProfileMembership(SotProfileMembershipLinkDto),
    #[serde(rename = "workflow-step")]
    WorkflowStep(SotWorkflowIncidenceBundleDto),
    #[serde(rename = "invoked-by")]
    InvokedBy(SotInvokedByLinkDto),
    #[serde(rename = "component-cross-link")]
    ComponentCrossLink(SotComponentCrossLinkDto),
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotProfileMembershipLinkDto {
    pub link_id: String,
    pub source_node_id: String,
    pub target_node_id: String,
    pub directionality: SotGraphDirectionalityDto,
    pub profile_node_id: String,
    pub component_node_id: String,
    pub provenance: SotMembershipProvenanceDto,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotWorkflowIncidenceBundleDto {
    pub link_id: String,
    pub source_node_id: String,
    pub target_node_id: String,
    pub directionality: SotGraphDirectionalityDto,
    pub workflow_node_id: String,
    pub component_node_id: String,
    pub occurrences: Vec<SotWorkflowOccurrenceDto>,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct SotWorkflowOccurrenceDto {
    pub ordinal: usize,
    pub step_id: String,
    pub source_field: String,
    pub role: Option<String>,
    pub mode: Option<String>,
    pub gate_order: Option<u64>,
    pub loop_back_to: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotInvokedByLinkDto {
    pub link_id: String,
    pub source_node_id: String,
    pub target_node_id: String,
    pub directionality: SotGraphDirectionalityDto,
    pub workflow_node_id: String,
    pub component_node_id: String,
    pub source_path: String,
    pub source_field: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotComponentCrossLinkDto {
    pub link_id: String,
    pub source_node_id: String,
    pub target_node_id: String,
    pub directionality: SotGraphDirectionalityDto,
    pub source_component_id: String,
    pub target_component_id: String,
    pub relation_type: String,
    pub provenance: SotComponentRelationProvenanceDto,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum SotMembershipProvenanceDto {
    CanonicalProfile,
    DerivedUnprofiled,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotComponentRelationProvenanceDto {
    pub source_path: String,
    pub source_field: String,
    pub declarative_only: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotNavigationProjectionDto {
    pub all_component_ids: Vec<String>,
    pub groups: Vec<SotNavigationGroupDto>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SotNavigationGroupDto {
    pub kind: String,
    pub component_ids: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct SotIssueDto {
    pub code: String,
    pub source_path: String,
    pub safe_message: String,
}

impl From<crate::contexts::sot::domain::SotSnapshot> for SotSnapshotDto {
    fn from(value: crate::contexts::sot::domain::SotSnapshot) -> Self {
        Self {
            snapshot_id: value.snapshot_id,
            checkout_summary: value.checkout_summary.into(),
            components: value.components.into_iter().map(Into::into).collect(),
            profiles: value.profiles.into_iter().map(Into::into).collect(),
            workflows: value.workflows.into_iter().map(Into::into).collect(),
            unprofiled_component_ids: value.unprofiled_component_ids,
            relations: value.relations.into_iter().map(Into::into).collect(),
            graph_projection: value.graph_projection.into(),
            navigation_projection: value.navigation_projection.into(),
            issues: value.issues.into_iter().map(Into::into).collect(),
        }
    }
}

impl From<crate::contexts::sot::domain::SotWorkflow> for SotWorkflowDto {
    fn from(value: crate::contexts::sot::domain::SotWorkflow) -> Self {
        Self {
            workflow_id: value.workflow_id,
            status: value.status,
            title: value.title,
            description: value.description,
            domain: value.domain,
            runtime_implemented: value.runtime_implemented,
            source_path: value.source_path,
            raw_yaml: value.raw_yaml,
            optional_invoked_by: value.optional_invoked_by,
            steps: value.steps.into_iter().map(Into::into).collect(),
        }
    }
}

impl From<crate::contexts::sot::domain::SotWorkflowStep> for SotWorkflowStepDto {
    fn from(value: crate::contexts::sot::domain::SotWorkflowStep) -> Self {
        Self {
            workflow_id: value.workflow_id,
            ordinal: value.ordinal,
            step_id: value.step_id,
            title: value.title,
            description: value.description,
            authored_fields: value.authored_fields.into(),
            resolved_component_ids: value.resolved_component_ids,
            unresolved_references: value
                .unresolved_references
                .into_iter()
                .map(Into::into)
                .collect(),
            source_path: value.source_path,
        }
    }
}

impl From<crate::contexts::sot::domain::SotWorkflowStepAuthoredFields>
    for SotWorkflowStepAuthoredFieldsDto
{
    fn from(value: crate::contexts::sot::domain::SotWorkflowStepAuthoredFields) -> Self {
        Self {
            agent: value.agent,
            skill: value.skill,
            rule: value.rule,
            role: value.role,
            prompt_template: value.prompt_template,
            mode_fanout: value.mode_fanout,
            output: value.output,
            gate_order: value.gate_order,
            requires_pass_from: value.requires_pass_from,
            loop_back_to: value.loop_back_to,
        }
    }
}

impl From<crate::contexts::sot::domain::SotWorkflowUnresolvedReference>
    for SotWorkflowUnresolvedReferenceDto
{
    fn from(value: crate::contexts::sot::domain::SotWorkflowUnresolvedReference) -> Self {
        Self {
            source_field: value.source_field,
            reference: value.reference,
            reference_kind: value.reference_kind,
        }
    }
}

impl From<crate::contexts::sot::domain::SotCheckoutSummary> for SotCheckoutSummaryDto {
    fn from(value: crate::contexts::sot::domain::SotCheckoutSummary) -> Self {
        Self {
            source_revision: value.source_revision,
            branch: value.branch,
            detached: value.detached,
            dirty: value.dirty,
            recent_commits: value.recent_commits,
        }
    }
}

impl From<crate::contexts::sot::domain::SotComponent> for SotComponentDto {
    fn from(value: crate::contexts::sot::domain::SotComponent) -> Self {
        Self {
            component_id: value.component_id,
            kind: value.kind,
            status: value.status,
            title: value.title,
            summary: value.summary,
            domain: value.domain,
            targets: value.targets.into_iter().map(Into::into).collect(),
            provenance: value.provenance.into(),
            owned_files: value.owned_files,
            profile_ids: value.profile_ids,
        }
    }
}

impl From<crate::contexts::sot::domain::SotTarget> for SotTargetDto {
    fn from(value: crate::contexts::sot::domain::SotTarget) -> Self {
        Self {
            target_id: value.target_id,
            support_status: value.support_status,
        }
    }
}

impl From<crate::contexts::sot::domain::SotProvenance> for SotProvenanceDto {
    fn from(value: crate::contexts::sot::domain::SotProvenance) -> Self {
        Self {
            mode: value.mode,
            source_classification: value.source_classification,
        }
    }
}

impl From<crate::contexts::sot::domain::SotProfile> for SotProfileDto {
    fn from(value: crate::contexts::sot::domain::SotProfile) -> Self {
        Self {
            profile_id: value.profile_id,
            status: value.status,
            title: value.title,
            summary: value.summary,
            component_ids: value.component_ids,
        }
    }
}

impl From<crate::contexts::sot::domain::SotRelation> for SotRelationDto {
    fn from(value: crate::contexts::sot::domain::SotRelation) -> Self {
        Self {
            source: value.source,
            target: value.target,
            relation_type: value.relation_type,
            source_path: value.source_path,
            source_field: value.source_field,
            declarative_only: value.declarative_only,
        }
    }
}

impl From<crate::contexts::sot::domain::SotGraphProjection> for SotGraphProjectionDto {
    fn from(value: crate::contexts::sot::domain::SotGraphProjection) -> Self {
        Self {
            schema_version: value.schema_version,
            snapshot_id: value.snapshot_id,
            layout_seed: value.layout_seed,
            nodes: value.nodes.into_iter().map(Into::into).collect(),
            links: value.links.into_iter().map(Into::into).collect(),
        }
    }
}

impl From<crate::contexts::sot::domain::SotGraphNode> for SotGraphNodeDto {
    fn from(value: crate::contexts::sot::domain::SotGraphNode) -> Self {
        use crate::contexts::sot::domain::SotGraphNode;

        match value {
            SotGraphNode::ComponentEntity(node) => {
                Self::ComponentEntity(SotComponentEntityGraphNodeDto {
                    node_id: node.node_id,
                    component_id: node.component_id,
                    kind: node.kind,
                    domain: node.domain,
                    relation_degree: node.relation_degree,
                    profile_ids: node.profile_ids,
                    workflow_ids: node.workflow_ids,
                })
            }
            SotGraphNode::Relation(node) => Self::Relation(SotRelationGraphNodeDto {
                node_id: node.node_id,
                relation_kind: node.relation_kind,
                canonical_id: node.canonical_id,
                name: node.name,
                exact_count: node.exact_count,
                anchor_ordinal: node.anchor_ordinal,
                size_scale: node.size_scale.as_f64(),
            }),
        }
    }
}

impl From<crate::contexts::sot::domain::SotGraphLink> for SotGraphLinkDto {
    fn from(value: crate::contexts::sot::domain::SotGraphLink) -> Self {
        use crate::contexts::sot::domain::SotGraphLink;

        match value {
            SotGraphLink::ProfileMembership(link) => {
                Self::ProfileMembership(SotProfileMembershipLinkDto {
                    link_id: link.link_id,
                    source_node_id: link.source_node_id,
                    target_node_id: link.target_node_id,
                    directionality: link.directionality.into(),
                    profile_node_id: link.profile_node_id,
                    component_node_id: link.component_node_id,
                    provenance: link.provenance.into(),
                })
            }
            SotGraphLink::WorkflowStep(link) => Self::WorkflowStep(SotWorkflowIncidenceBundleDto {
                link_id: link.link_id,
                source_node_id: link.source_node_id,
                target_node_id: link.target_node_id,
                directionality: link.directionality.into(),
                workflow_node_id: link.workflow_node_id,
                component_node_id: link.component_node_id,
                occurrences: link.occurrences.into_iter().map(Into::into).collect(),
            }),
            SotGraphLink::InvokedBy(link) => Self::InvokedBy(SotInvokedByLinkDto {
                link_id: link.link_id,
                source_node_id: link.source_node_id,
                target_node_id: link.target_node_id,
                directionality: link.directionality.into(),
                workflow_node_id: link.workflow_node_id,
                component_node_id: link.component_node_id,
                source_path: link.source_path,
                source_field: link.source_field,
            }),
            SotGraphLink::ComponentCrossLink(link) => {
                Self::ComponentCrossLink(SotComponentCrossLinkDto {
                    link_id: link.link_id,
                    source_node_id: link.source_node_id,
                    target_node_id: link.target_node_id,
                    directionality: link.directionality.into(),
                    source_component_id: link.source_component_id,
                    target_component_id: link.target_component_id,
                    relation_type: link.relation_type,
                    provenance: SotComponentRelationProvenanceDto {
                        source_path: link.provenance.source_path,
                        source_field: link.provenance.source_field,
                        declarative_only: link.provenance.declarative_only,
                    },
                })
            }
        }
    }
}

impl From<crate::contexts::sot::domain::SotGraphDirectionality> for SotGraphDirectionalityDto {
    fn from(value: crate::contexts::sot::domain::SotGraphDirectionality) -> Self {
        use crate::contexts::sot::domain::SotGraphDirectionality;

        match value {
            SotGraphDirectionality::Directed => Self::Directed,
            SotGraphDirectionality::Unordered => Self::Unordered,
        }
    }
}

impl From<crate::contexts::sot::domain::SotWorkflowOccurrence> for SotWorkflowOccurrenceDto {
    fn from(value: crate::contexts::sot::domain::SotWorkflowOccurrence) -> Self {
        Self {
            ordinal: value.ordinal,
            step_id: value.step_id,
            source_field: value.source_field,
            role: value.role,
            mode: value.mode,
            gate_order: value.gate_order,
            loop_back_to: value.loop_back_to,
        }
    }
}

impl From<crate::contexts::sot::domain::SotMembershipProvenance> for SotMembershipProvenanceDto {
    fn from(value: crate::contexts::sot::domain::SotMembershipProvenance) -> Self {
        use crate::contexts::sot::domain::SotMembershipProvenance;

        match value {
            SotMembershipProvenance::CanonicalProfile => Self::CanonicalProfile,
            SotMembershipProvenance::DerivedUnprofiled => Self::DerivedUnprofiled,
        }
    }
}

impl From<crate::contexts::sot::domain::SotNavigationProjection> for SotNavigationProjectionDto {
    fn from(value: crate::contexts::sot::domain::SotNavigationProjection) -> Self {
        Self {
            all_component_ids: value.all_component_ids,
            groups: value.groups.into_iter().map(Into::into).collect(),
        }
    }
}

impl From<crate::contexts::sot::domain::SotNavigationGroup> for SotNavigationGroupDto {
    fn from(value: crate::contexts::sot::domain::SotNavigationGroup) -> Self {
        Self {
            kind: value.kind,
            component_ids: value.component_ids,
        }
    }
}

impl From<crate::contexts::sot::domain::SotIssue> for SotIssueDto {
    fn from(value: crate::contexts::sot::domain::SotIssue) -> Self {
        Self {
            code: value.code,
            source_path: value.source_path,
            safe_message: value.safe_message,
        }
    }
}
