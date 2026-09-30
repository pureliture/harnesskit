use std::collections::{BTreeMap, BTreeSet};
use std::path::{Component, Path};

use sha2::{Digest, Sha256};

use crate::contexts::install::{
    embedded_target_contract, GeneratedArtifact, GeneratedArtifactSet, InstallEvidenceSnapshot,
};
use crate::contexts::local::{AdapterCatalog, CatalogAdapter, LocalSnapshot, ToolId};

use super::{
    normalized_relative_locator, ArtifactJoinKey, CorrelationAvailability, CorrelationProjection,
    CorrelationState, DestinationScope, InstanceCorrelation, QualifiedArtifactIssue,
    QualifiedArtifactProjection, QualifiedArtifactRecord,
};

pub const ARTIFACT_PROJECTOR_VERSION: &str = "qualified-artifact-projector-v1";
pub const PROJECTOR_VERSION: &str = "local-sot-correlation-v2";

#[derive(Debug, Clone, Copy, Default)]
pub struct QualifiedArtifactProjector;

impl QualifiedArtifactProjector {
    pub fn project(
        &self,
        sot_snapshot_id: &str,
        generated: GeneratedArtifactSet,
    ) -> QualifiedArtifactProjection {
        let mut records = Vec::new();
        let mut issues = generated
            .issues
            .into_iter()
            .map(|issue| QualifiedArtifactIssue {
                code: issue.code,
                safe_reason: issue.safe_reason,
            })
            .collect::<Vec<_>>();
        let catalog = match AdapterCatalog::load_embedded() {
            Ok(catalog) => catalog,
            Err(_) => {
                issues.push(QualifiedArtifactIssue {
                    code: "qualified_adapter_set_unavailable".to_string(),
                    safe_reason: "Qualified discovery adapter set was unavailable".to_string(),
                });
                return finalize_artifact_projection(
                    sot_snapshot_id,
                    generated.source_revision,
                    generated.adapter_set_revision,
                    records,
                    issues,
                );
            }
        };
        let adapter_set_revision =
            combined_adapter_set_revision(&generated.adapter_set_revision, &catalog);
        let contract = match embedded_target_contract() {
            Ok(contract) => contract,
            Err(_) => {
                issues.push(QualifiedArtifactIssue {
                    code: "target_contract_unavailable".to_string(),
                    safe_reason: "Qualified target contract was unavailable".to_string(),
                });
                return finalize_artifact_projection(
                    sot_snapshot_id,
                    generated.source_revision,
                    adapter_set_revision,
                    records,
                    issues,
                );
            }
        };

        for artifact in generated.records {
            let bindings = contract.correlation_bindings(
                &artifact.target,
                &artifact.scope,
                &artifact.destination,
            );
            if bindings.is_empty() {
                continue;
            }
            for binding in bindings {
                let adapter = qualified_adapter(&catalog, &binding.tool_id);
                match adapter.and_then(|adapter| qualified_record(&artifact, binding, adapter)) {
                    Some(record) => records.push(record),
                    None => issues.push(QualifiedArtifactIssue {
                        code: "artifact_binding_unavailable".to_string(),
                        safe_reason: "Canonical artifact binding was unavailable".to_string(),
                    }),
                }
            }
        }
        finalize_artifact_projection(
            sot_snapshot_id,
            generated.source_revision,
            adapter_set_revision,
            records,
            issues,
        )
    }
}

fn qualified_record(
    artifact: &GeneratedArtifact,
    binding: &crate::contexts::install::CorrelationBindingContract,
    adapter: &CatalogAdapter,
) -> Option<QualifiedArtifactRecord> {
    let relative = locator_relative_to_root(
        &artifact.destination,
        binding.authorized_root_prefix.as_deref(),
    )?;
    let normalized_target_locator = normalized_relative_locator(&relative)?;
    let scope = match binding.scope.as_str() {
        "user" => DestinationScope::User,
        "project" => DestinationScope::Project,
        _ => return None,
    };
    let local_scope = match scope {
        DestinationScope::User => crate::contexts::local::domain::Scope::User,
        DestinationScope::Project => crate::contexts::local::domain::Scope::Project,
    };
    let config_entry_locator = match binding.content_match.as_str() {
        "whole_file_sha256" => None,
        _ => artifact.config_entry_locator.clone(),
    };
    if !adapter
        .descriptor
        .surfaces
        .iter()
        .any(|surface| surface.surface_id == binding.surface_id && surface.scope == local_scope)
    {
        return None;
    }
    let adapter_id = adapter.adapter_id.clone();
    let adapter_version = adapter.descriptor.adapter_version.clone();
    let canonical = serde_json::to_vec(&(
        ARTIFACT_PROJECTOR_VERSION,
        artifact.component_id.as_str(),
        adapter_id.as_str(),
        adapter_version.as_str(),
        binding.tool_id.as_str(),
        binding.surface_id.as_str(),
        scope,
        normalized_target_locator.as_str(),
        config_entry_locator.as_deref(),
        artifact.content_sha256.as_str(),
    ))
    .ok()?;
    Some(QualifiedArtifactRecord {
        artifact_record_ref: hex_sha256(&canonical),
        component_id: artifact.component_id.clone(),
        adapter_id,
        adapter_version,
        tool_id: binding.tool_id.clone(),
        surface_id: binding.surface_id.clone(),
        scope,
        normalized_target_locator,
        config_entry_locator,
        content_sha256: artifact.content_sha256.clone(),
    })
}

fn locator_relative_to_root(destination: &str, root: Option<&str>) -> Option<std::path::PathBuf> {
    let destination = safe_relative_path(destination)?;
    match root {
        Some(root) => destination
            .strip_prefix(safe_relative_path(root)?)
            .ok()
            .map(Path::to_path_buf),
        None => Some(destination),
    }
}

fn safe_relative_path(value: &str) -> Option<std::path::PathBuf> {
    let path = Path::new(value);
    (!value.is_empty()
        && !path.is_absolute()
        && path
            .components()
            .all(|component| matches!(component, Component::Normal(_))))
    .then(|| path.to_path_buf())
}

fn qualified_adapter<'a>(catalog: &'a AdapterCatalog, tool_id: &str) -> Option<&'a CatalogAdapter> {
    let tool_id = match tool_id {
        "codex" => ToolId::Codex,
        "claude_code" => ToolId::ClaudeCode,
        "antigravity" => ToolId::Antigravity,
        "antigravity_cli" => ToolId::AntigravityCli,
        "hermes" => ToolId::Hermes,
        _ => return None,
    };
    catalog.for_tool(tool_id)
}

fn discovery_adapter_id(tool_id: &str) -> String {
    tool_id.replace('_', "-")
}

fn combined_adapter_set_revision(generation_revision: &str, catalog: &AdapterCatalog) -> String {
    let qualified = catalog
        .adapters()
        .iter()
        .map(|adapter| {
            (
                adapter.adapter_id.as_str(),
                adapter.descriptor.adapter_version.as_str(),
                adapter.descriptor_canonical_sha256.as_str(),
                adapter.qualification_record_sha256.as_str(),
            )
        })
        .collect::<Vec<_>>();
    let canonical = serde_json::to_vec(&(generation_revision, qualified)).unwrap_or_default();
    hex_sha256(&canonical)
}

fn finalize_artifact_projection(
    sot_snapshot_id: &str,
    source_revision: String,
    adapter_set_revision: String,
    mut records: Vec<QualifiedArtifactRecord>,
    mut issues: Vec<QualifiedArtifactIssue>,
) -> QualifiedArtifactProjection {
    records.sort_by(|left, right| {
        left.component_id
            .cmp(&right.component_id)
            .then_with(|| left.join_key().cmp(&right.join_key()))
            .then_with(|| left.content_sha256.cmp(&right.content_sha256))
            .then_with(|| left.artifact_record_ref.cmp(&right.artifact_record_ref))
    });
    records.dedup();
    issues.sort_by(|left, right| {
        left.code
            .cmp(&right.code)
            .then_with(|| left.safe_reason.cmp(&right.safe_reason))
    });
    issues.dedup();
    let availability = if issues.is_empty() && !records.is_empty() {
        CorrelationAvailability::Available
    } else {
        CorrelationAvailability::Unavailable {
            safe_reason: issues
                .first()
                .map(|issue| issue.safe_reason.clone())
                .unwrap_or_else(|| "No qualified artifacts were available".to_string()),
        }
    };
    let canonical = serde_json::to_vec(&(
        ARTIFACT_PROJECTOR_VERSION,
        sot_snapshot_id,
        source_revision.as_str(),
        adapter_set_revision.as_str(),
        &availability,
        &records,
        &issues,
    ))
    .unwrap_or_default();
    QualifiedArtifactProjection {
        artifact_projection_id: hex_sha256(&canonical),
        projector_version: ARTIFACT_PROJECTOR_VERSION.to_string(),
        sot_snapshot_id: sot_snapshot_id.to_string(),
        source_revision,
        adapter_set_revision,
        availability,
        records,
        issues,
    }
}

pub fn project(
    local: &LocalSnapshot,
    artifacts: &QualifiedArtifactProjection,
    evidence: Option<&InstallEvidenceSnapshot>,
) -> Result<CorrelationProjection, String> {
    let canonical = serde_json::to_vec(&(
        PROJECTOR_VERSION,
        local.snapshot_id.as_str(),
        artifacts.artifact_projection_id.as_str(),
    ))
    .map_err(|_| "correlation_projection_canonicalization_failed".to_string())?;
    let projection_id = hex_sha256(&canonical);
    let install_evidence_id = evidence
        .filter(|evidence| evidence.sot_snapshot_id == artifacts.sot_snapshot_id)
        .map(|evidence| evidence.evidence_id.clone());

    if !matches!(artifacts.availability, CorrelationAvailability::Available) {
        return Ok(CorrelationProjection {
            projection_id,
            projector_version: PROJECTOR_VERSION.to_string(),
            local_snapshot_id: local.snapshot_id.clone(),
            sot_snapshot_id: Some(artifacts.sot_snapshot_id.clone()),
            artifact_projection_id: Some(artifacts.artifact_projection_id.clone()),
            install_evidence_id,
            availability: artifacts.availability.clone(),
            correlations: local
                .instances
                .iter()
                .map(|instance| InstanceCorrelation {
                    instance_id: instance.instance_id.clone(),
                    correlation: CorrelationState::Uncorrelated,
                })
                .collect(),
        });
    }

    let mut records_by_key = BTreeMap::<ArtifactJoinKey, Vec<&QualifiedArtifactRecord>>::new();
    for record in &artifacts.records {
        records_by_key
            .entry(record.join_key())
            .or_default()
            .push(record);
    }
    let mut correlations = local
        .instances
        .iter()
        .map(|instance| {
            let correlation = match (&instance.correlation_key, &instance.content_hash) {
                (Some(destination), Some(current_sha256)) => {
                    let key = ArtifactJoinKey::from_local(&instance.adapter_id, destination);
                    correlate_one(
                        records_by_key.get(&key).map(Vec::as_slice).unwrap_or(&[]),
                        current_sha256,
                        evidence,
                        &key,
                    )
                }
                _ => CorrelationState::Uncorrelated,
            };
            InstanceCorrelation {
                instance_id: instance.instance_id.clone(),
                correlation,
            }
        })
        .collect::<Vec<_>>();
    correlations.sort_by(|left, right| left.instance_id.cmp(&right.instance_id));
    Ok(CorrelationProjection {
        projection_id,
        projector_version: PROJECTOR_VERSION.to_string(),
        local_snapshot_id: local.snapshot_id.clone(),
        sot_snapshot_id: Some(artifacts.sot_snapshot_id.clone()),
        artifact_projection_id: Some(artifacts.artifact_projection_id.clone()),
        install_evidence_id,
        availability: artifacts.availability.clone(),
        correlations,
    })
}

fn correlate_one(
    candidates: &[&QualifiedArtifactRecord],
    current_sha256: &str,
    evidence: Option<&InstallEvidenceSnapshot>,
    join_key: &ArtifactJoinKey,
) -> CorrelationState {
    if candidates.is_empty() {
        return CorrelationState::Uncorrelated;
    }
    let component_ids = candidates
        .iter()
        .map(|record| record.component_id.clone())
        .collect::<BTreeSet<_>>();
    if component_ids.len() > 1 {
        return CorrelationState::Ambiguous {
            candidate_component_ids: component_ids.into_iter().collect(),
            safe_reason: "multiple_qualified_components".to_string(),
        };
    }
    let expected = candidates.iter().copied().collect::<BTreeSet<_>>();
    if expected.len() != 1 {
        return CorrelationState::Ambiguous {
            candidate_component_ids: component_ids.into_iter().collect(),
            safe_reason: "multiple_expected_artifacts".to_string(),
        };
    }
    let record = expected.into_iter().next().expect("one expected record");
    if record.content_sha256 == current_sha256 {
        CorrelationState::Verified {
            component_id: record.component_id.clone(),
            method: "exact_artifact".to_string(),
            artifact_record_ref: record.artifact_record_ref.clone(),
            install_evidence_ref: matching_evidence_ref(
                evidence,
                join_key,
                &record.component_id,
                current_sha256,
            ),
        }
    } else {
        CorrelationState::Drift {
            component_id: record.component_id.clone(),
            artifact_record_ref: record.artifact_record_ref.clone(),
            expected_sha256: record.content_sha256.clone(),
            current_sha256: current_sha256.to_string(),
        }
    }
}

fn matching_evidence_ref(
    evidence: Option<&InstallEvidenceSnapshot>,
    join_key: &ArtifactJoinKey,
    component_id: &str,
    content_sha256: &str,
) -> Option<String> {
    let evidence = evidence?;
    evidence
        .verified_records
        .iter()
        .any(|record| {
            record.component_id == component_id
                && record.content_sha256 == content_sha256
                && ArtifactJoinKey::from_local(
                    &discovery_adapter_id(&record.destination_key.tool_id),
                    &record.destination_key,
                ) == *join_key
        })
        .then(|| evidence.evidence_id.clone())
}

fn hex_sha256(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}
