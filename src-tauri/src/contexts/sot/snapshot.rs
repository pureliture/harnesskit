use std::collections::{BTreeMap, BTreeSet};
use std::path::{Component, Path};

use serde_yaml::Value;
use sha2::{Digest, Sha256};

use super::domain::{
    SotCheckoutSummary, SotComponent, SotIssue, SotNavigationGroup, SotNavigationProjection,
    SotProfile, SotProvenance, SotSnapshot, SotTarget,
};

use super::graph_projection::SotGraphProjectionService;
use super::relations::RelationExtractor;
use super::source::{format_digest, issue, SotSourceData, SotSourceReader, SourceManifest};
use super::workflow::WorkflowProjector;

pub(super) struct SotSnapshotService;

impl SotSnapshotService {
    pub fn load(checkout: &Path) -> Result<SotSnapshot, String> {
        let source = SotSourceReader::load(checkout)?;
        let mut issues = source.issues.clone();
        let profiles = source
            .profiles
            .iter()
            .map(|profile| SotProfile {
                profile_id: profile.profile_id.clone(),
                status: profile.status.clone(),
                title: profile.title.clone(),
                summary: profile.summary.clone(),
                component_ids: profile.component_ids.clone(),
                base_component_ids: profile.base_component_ids.clone(),
                scope_component_ids: profile.scope_component_ids.clone(),
            })
            .collect::<Vec<_>>();
        let memberships = profile_memberships(&source);
        let components = source
            .entries
            .iter()
            .filter(|entry| entry.kind != "workflow")
            .map(|entry| {
                let manifest = source.manifests.get(&entry.component_id);
                SotComponent {
                    component_id: entry.component_id.clone(),
                    kind: entry.kind.clone(),
                    status: entry.status.clone(),
                    title: manifest
                        .and_then(|manifest| text_field(&manifest.value, "title"))
                        .unwrap_or_else(|| display_title(&entry.component_id)),
                    summary: manifest.and_then(manifest_summary),
                    domain: manifest.and_then(|manifest| text_field(&manifest.value, "domain")),
                    targets: manifest.map(extract_targets).unwrap_or_default(),
                    provenance: manifest.map(extract_provenance).unwrap_or_default(),
                    owned_files: manifest
                        .map(|manifest| extract_owned_files(manifest, &mut issues))
                        .unwrap_or_default(),
                    profile_ids: memberships
                        .get(&entry.component_id)
                        .map(|ids| ids.iter().cloned().collect())
                        .unwrap_or_default(),
                    install_scopes: manifest
                        .map(extract_install_scopes)
                        .unwrap_or_else(|| vec!["project".to_string()]),
                }
            })
            .collect::<Vec<_>>();
        let profiled = memberships.keys().cloned().collect::<BTreeSet<_>>();
        let unprofiled_component_ids = components
            .iter()
            .map(|component| component.component_id.clone())
            .filter(|component_id| !profiled.contains(component_id))
            .collect::<Vec<_>>();
        let workflows = WorkflowProjector::project(&source, &mut issues);
        let relations = RelationExtractor::extract(&source, &workflows, &mut issues);
        let graph_projection = SotGraphProjectionService::project(
            &components,
            &profiles,
            &workflows,
            &unprofiled_component_ids,
            &relations,
        );
        let navigation_projection = navigation_projection(&components);
        issues.sort();
        issues.dedup();

        let mut snapshot = SotSnapshot {
            snapshot_id: String::new(),
            checkout_summary: SotCheckoutSummary {
                source_revision: source.source_revision.clone(),
                canonical_path: std::fs::canonicalize(checkout)
                    .unwrap_or_else(|_| checkout.to_path_buf())
                    .to_string_lossy()
                    .to_string(),
                branch: None,
                detached: None,
                dirty: None,
                recent_commits: Vec::new(),
            },
            components,
            profiles,
            workflows,
            unprofiled_component_ids,
            relations,
            graph_projection,
            navigation_projection,
            issues,
        };
        finalize_snapshot_id(&mut snapshot)?;
        Ok(snapshot)
    }
}

fn extract_install_scopes(manifest: &SourceManifest) -> Vec<String> {
    let Some(values) = manifest.value.get("scopes") else {
        return vec!["project".to_string(), "user".to_string()];
    };
    let Some(values) = values.as_sequence() else {
        return Vec::new();
    };
    let mut scopes = values
        .iter()
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect::<Vec<_>>();
    scopes.sort();
    scopes
}

fn profile_memberships(source: &SotSourceData) -> BTreeMap<String, BTreeSet<String>> {
    let mut memberships = BTreeMap::<String, BTreeSet<String>>::new();
    for profile in &source.profiles {
        for component_id in &profile.component_ids {
            memberships
                .entry(component_id.clone())
                .or_default()
                .insert(profile.profile_id.clone());
        }
    }
    memberships
}

fn extract_targets(manifest: &SourceManifest) -> Vec<SotTarget> {
    let mut targets = BTreeMap::<String, Option<String>>::new();
    match manifest.value.get("targets") {
        Some(Value::Mapping(values)) => {
            for (target, _) in values {
                if let Some(target) = target.as_str() {
                    targets.entry(target.to_string()).or_default();
                }
            }
        }
        Some(Value::Sequence(values)) => {
            for target in values.iter().filter_map(Value::as_str) {
                targets.entry(target.to_string()).or_default();
            }
        }
        _ => {}
    }
    if let Some(Value::Mapping(values)) = manifest.value.get("target_support") {
        for (target, support) in values {
            let Some(target) = target.as_str() else {
                continue;
            };
            let status = text_field(support, "status");
            targets.insert(target.to_string(), status);
        }
    }
    targets
        .into_iter()
        .map(|(target_id, support_status)| SotTarget {
            target_id,
            support_status,
        })
        .collect()
}

fn extract_provenance(manifest: &SourceManifest) -> SotProvenance {
    SotProvenance {
        mode: text_field(&manifest.value, "provenance_mode"),
        source_classification: text_field(&manifest.value, "source_classification"),
    }
}

fn extract_owned_files(manifest: &SourceManifest, issues: &mut Vec<SotIssue>) -> Vec<String> {
    let mut files = BTreeSet::new();
    let Some(values) = manifest
        .value
        .get("owned_files")
        .and_then(Value::as_sequence)
    else {
        return Vec::new();
    };
    for value in values.iter().filter_map(Value::as_str) {
        let normalized = value.replace('\\', "/");
        let path = Path::new(&normalized);
        let is_relative = !path.is_absolute()
            && !normalized.is_empty()
            && path
                .components()
                .all(|part| matches!(part, Component::Normal(_)));
        if is_relative {
            files.insert(normalized);
        } else {
            issues.push(issue(
                "invalid_owned_file",
                &manifest.source_path,
                "Owned file must be a checkout-relative path",
            ));
        }
    }
    files.into_iter().collect()
}

fn manifest_summary(manifest: &SourceManifest) -> Option<String> {
    text_field(&manifest.value, "summary").or_else(|| text_field(&manifest.value, "description"))
}

fn navigation_projection(components: &[SotComponent]) -> SotNavigationProjection {
    let mut all_component_ids = components
        .iter()
        .map(|component| component.component_id.clone())
        .collect::<Vec<_>>();
    all_component_ids.sort();
    let mut groups = BTreeMap::<String, Vec<String>>::new();
    for component in components {
        groups
            .entry(component.kind.clone())
            .or_default()
            .push(component.component_id.clone());
    }
    let groups = groups
        .into_iter()
        .map(|(kind, mut component_ids)| {
            component_ids.sort();
            SotNavigationGroup {
                kind,
                component_ids,
            }
        })
        .collect();
    SotNavigationProjection {
        all_component_ids,
        groups,
    }
}

pub(super) fn finalize_snapshot_id(snapshot: &mut SotSnapshot) -> Result<(), String> {
    snapshot.graph_projection.snapshot_id.clear();
    snapshot.snapshot_id = snapshot_id(snapshot)?;
    snapshot.graph_projection.snapshot_id = snapshot.snapshot_id.clone();
    Ok(())
}

fn snapshot_id(snapshot: &SotSnapshot) -> Result<String, String> {
    let canonical = serde_json::to_vec(&(
        &snapshot.checkout_summary,
        &snapshot.components,
        &snapshot.profiles,
        &snapshot.workflows,
        &snapshot.unprofiled_component_ids,
        &snapshot.relations,
        &snapshot.graph_projection,
        &snapshot.navigation_projection,
        &snapshot.issues,
    ))
    .map_err(|_| "SoT snapshot could not be serialized".to_string())?;
    let mut digest = Sha256::new();
    digest.update(canonical);
    Ok(format_digest(digest.finalize().as_slice()))
}

fn display_title(component_id: &str) -> String {
    let slug = component_id.rsplit('.').next().unwrap_or(component_id);
    let title = slug
        .split('-')
        .filter(|part| !part.is_empty())
        .map(|part| {
            let mut characters = part.chars();
            match characters.next() {
                Some(first) => format!("{}{}", first.to_uppercase(), characters.as_str()),
                None => String::new(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ");
    if title.is_empty() {
        component_id.to_string()
    } else {
        title
    }
}

fn text_field(value: &Value, field: &str) -> Option<String> {
    value
        .get(field)
        .and_then(Value::as_str)
        .map(ToOwned::to_owned)
}
