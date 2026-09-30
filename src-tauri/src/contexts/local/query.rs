use std::collections::{BTreeMap, BTreeSet};

use super::adapter::{SurfaceKind, ToolId};
use super::domain::{
    CoverageRecord, ParseState, ProjectIgnoreSummary, QualifiedTool, SafeIssue, SafeSetting, Scope,
    SkippedPath,
};
use super::parser::resolve_local_display_name;
use super::snapshot::{LocalSnapshot, LocalSnapshotStatus};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LocationScope {
    All,
    User,
    Project,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocationFilter {
    pub scope: LocationScope,
    pub project_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalQueryRequest {
    pub snapshot_id: String,
    pub location_filter: LocationFilter,
    pub tool_id: Option<ToolId>,
    pub kind: Option<SurfaceKind>,
    pub query: String,
    pub correlation_projection_id: Option<String>,
    pub verified_component_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalQueryItem {
    pub instance_id: String,
    pub adapter_id: String,
    pub adapter_version: String,
    pub tool_id: ToolId,
    pub surface_id: String,
    pub scope: Scope,
    pub project_id: Option<String>,
    pub safe_locator: String,
    pub kind: SurfaceKind,
    pub display_name: String,
    pub name: Option<String>,
    pub description: Option<String>,
    pub description_source: Option<String>,
    pub parse_state: ParseState,
    pub issue_codes: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalKindCount {
    pub kind: SurfaceKind,
    pub count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalQueryCounts {
    pub total_instances: usize,
    pub matched_instances: usize,
    pub project_count: usize,
    pub tool_count: usize,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalQueryResult {
    pub snapshot_id: String,
    pub snapshot_summary: LocalSnapshotSummary,
    pub qualified_tools: Vec<QualifiedTool>,
    pub items: Vec<LocalQueryItem>,
    pub kind_counts: Vec<LocalKindCount>,
    pub counts: LocalQueryCounts,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalSnapshotSummary {
    pub snapshot_id: String,
    pub status: LocalSnapshotStatus,
    pub scan_timestamp: String,
    pub app_version: String,
    pub coverage: Vec<CoverageRecord>,
    pub skipped_paths: Vec<SkippedPath>,
    pub issues: Vec<SafeIssue>,
    pub project_ignore_summaries: Vec<ProjectIgnoreSummary>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalInstanceDetail {
    pub instance_id: String,
    pub adapter_id: String,
    pub adapter_version: String,
    pub tool_id: ToolId,
    pub surface_id: String,
    pub scope: Scope,
    pub project_id: Option<String>,
    pub safe_locator: String,
    pub kind: SurfaceKind,
    pub display_name: String,
    pub name: Option<String>,
    pub description: Option<String>,
    pub description_source: Option<String>,
    pub settings: Vec<SafeSetting>,
    pub size: Option<u64>,
    pub modified_unix_millis: Option<u64>,
    pub parse_state: ParseState,
    pub issue_codes: Vec<String>,
}

pub fn query_local_instances(
    snapshot: &LocalSnapshot,
    request: &LocalQueryRequest,
    verified_instance_ids: Option<&BTreeSet<String>>,
) -> Result<LocalQueryResult, String> {
    if snapshot.snapshot_id != request.snapshot_id {
        return Err("snapshot_expired".to_string());
    }
    validate_location_filter(&request.location_filter)?;
    if request.verified_component_id.is_some() && request.correlation_projection_id.is_none() {
        return Err("projection_stale".to_string());
    }
    if request.correlation_projection_id.is_some() && verified_instance_ids.is_none() {
        return Err("projection_stale".to_string());
    }

    let normalized_query = request.query.to_lowercase();
    let mut candidates = snapshot
        .instances
        .iter()
        .filter(|instance| {
            location_matches(
                instance.scope,
                instance.project_id.as_deref(),
                &request.location_filter,
            )
        })
        .filter(|instance| match request.tool_id {
            Some(tool_id) => tool_id == instance.tool_id,
            None => true,
        })
        .filter(|instance| safe_text_matches(instance, &normalized_query))
        .filter(|instance| {
            request.verified_component_id.is_none()
                || verified_instance_ids
                    .is_some_and(|instance_ids| instance_ids.contains(&instance.instance_id))
        })
        .collect::<Vec<_>>();

    let mut kind_counts = BTreeMap::<SurfaceKind, usize>::new();
    for instance in &candidates {
        *kind_counts.entry(instance.kind).or_default() += 1;
    }
    let project_count = candidates
        .iter()
        .filter_map(|instance| instance.project_id.as_deref())
        .collect::<BTreeSet<_>>()
        .len();
    let tool_count = candidates
        .iter()
        .map(|instance| instance.tool_id)
        .collect::<BTreeSet<_>>()
        .len();

    if let Some(kind) = request.kind {
        candidates.retain(|instance| instance.kind == kind);
    }
    candidates.sort_by(|left, right| {
        display_key(left)
            .cmp(&display_key(right))
            .then_with(|| left.instance_id.cmp(&right.instance_id))
    });
    let items = candidates.into_iter().map(query_item).collect::<Vec<_>>();

    Ok(LocalQueryResult {
        snapshot_id: snapshot.snapshot_id.clone(),
        snapshot_summary: snapshot_summary(snapshot),
        qualified_tools: snapshot.qualified_tools.clone(),
        kind_counts: kind_counts
            .into_iter()
            .map(|(kind, count)| LocalKindCount { kind, count })
            .collect(),
        counts: LocalQueryCounts {
            total_instances: snapshot.instances.len(),
            matched_instances: items.len(),
            project_count,
            tool_count,
        },
        items,
    })
}

pub fn get_local_instance_detail(
    snapshot: &LocalSnapshot,
    snapshot_id: &str,
    instance_id: &str,
) -> Result<LocalInstanceDetail, String> {
    if snapshot.snapshot_id != snapshot_id {
        return Err("snapshot_expired".to_string());
    }
    let instance = snapshot
        .instances
        .iter()
        .find(|instance| instance.instance_id == instance_id)
        .ok_or_else(|| "instance_not_found".to_string())?;
    let display_name =
        resolve_local_display_name(instance.name.as_deref(), &instance.stable_source_locator);
    Ok(LocalInstanceDetail {
        instance_id: instance.instance_id.clone(),
        adapter_id: instance.adapter_id.clone(),
        adapter_version: instance.adapter_version.clone(),
        tool_id: instance.tool_id,
        surface_id: instance.surface_id.clone(),
        scope: instance.scope,
        project_id: instance.project_id.clone(),
        safe_locator: instance.stable_source_locator.clone(),
        kind: instance.kind,
        name: instance.name.clone(),
        display_name,
        description: instance.description.clone(),
        description_source: instance.description_source.clone(),
        settings: instance.settings.clone(),
        size: instance.size,
        modified_unix_millis: instance.modified_unix_millis,
        parse_state: instance.parse_state,
        issue_codes: instance.issue_codes.clone(),
    })
}

fn snapshot_summary(snapshot: &LocalSnapshot) -> LocalSnapshotSummary {
    LocalSnapshotSummary {
        snapshot_id: snapshot.snapshot_id.clone(),
        status: snapshot.status,
        scan_timestamp: snapshot.scan_timestamp.clone(),
        app_version: snapshot.app_version.clone(),
        coverage: snapshot.coverage.clone(),
        skipped_paths: snapshot.skipped_paths.clone(),
        issues: snapshot.issues.clone(),
        project_ignore_summaries: snapshot.project_ignore_summaries.clone(),
    }
}

fn validate_location_filter(filter: &LocationFilter) -> Result<(), String> {
    if filter.scope != LocationScope::Project && filter.project_id.is_some() {
        return Err("invalid_location_filter".to_string());
    }
    Ok(())
}

fn location_matches(scope: Scope, project_id: Option<&str>, filter: &LocationFilter) -> bool {
    match filter.scope {
        LocationScope::All => true,
        LocationScope::User => scope == Scope::User,
        LocationScope::Project => {
            scope == Scope::Project
                && match filter.project_id.as_deref() {
                    Some(expected) => project_id == Some(expected),
                    None => true,
                }
        }
    }
}

fn safe_text_matches(instance: &super::domain::LocalInstance, query: &str) -> bool {
    query.is_empty()
        || resolve_local_display_name(instance.name.as_deref(), &instance.stable_source_locator)
            .to_lowercase()
            .contains(query)
        || instance
            .description
            .as_deref()
            .is_some_and(|value| value.to_lowercase().contains(query))
        || instance
            .stable_source_locator
            .to_lowercase()
            .contains(query)
}

fn display_key(instance: &super::domain::LocalInstance) -> (String, String) {
    (
        resolve_local_display_name(instance.name.as_deref(), &instance.stable_source_locator)
            .to_lowercase(),
        instance.stable_source_locator.to_lowercase(),
    )
}

fn query_item(instance: &super::domain::LocalInstance) -> LocalQueryItem {
    let display_name =
        resolve_local_display_name(instance.name.as_deref(), &instance.stable_source_locator);
    LocalQueryItem {
        instance_id: instance.instance_id.clone(),
        adapter_id: instance.adapter_id.clone(),
        adapter_version: instance.adapter_version.clone(),
        tool_id: instance.tool_id,
        surface_id: instance.surface_id.clone(),
        scope: instance.scope,
        project_id: instance.project_id.clone(),
        safe_locator: instance.stable_source_locator.clone(),
        kind: instance.kind,
        name: instance.name.clone(),
        display_name,
        description: instance.description.clone(),
        description_source: instance.description_source.clone(),
        parse_state: instance.parse_state,
        issue_codes: instance.issue_codes.clone(),
    }
}
