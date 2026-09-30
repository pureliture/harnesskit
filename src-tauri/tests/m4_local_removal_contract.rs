use std::fs;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use harness_desktop_lib::contexts::local::domain::ParseState;
use harness_desktop_lib::contexts::local::scanner::LocalScanner;
use harness_desktop_lib::contexts::local::{
    AdapterCatalog, CatalogAdapter, LocalContext, LocalRemovalReconciliationState,
    LocalRemovalRescanState, LocalRemovalSourceGroupState, LocalScanExecutor,
    LocalScanExecutorResult, LocalScanProgressPort, LocalScanPublication, LocalSnapshot,
    LocalSnapshotStatus, ToolId,
};
use serde_json::Value;
use tempfile::tempdir;

const HOOKS: &str = include_str!("fixtures/local-removal/home/.codex/hooks.json");
const REMOVE_SKILL: &str =
    include_str!("fixtures/local-removal/home/.codex/skills/remove/SKILL.md");
const KEEP_SKILL: &str = include_str!("fixtures/local-removal/home/.codex/skills/keep/SKILL.md");

struct FixtureScanExecutor {
    home: PathBuf,
    codex: CatalogAdapter,
    sequence: AtomicU64,
    mutation: FixtureScanMutation,
}

#[derive(Clone, Copy)]
enum FixtureScanMutation {
    None,
    RemoveParseState(ParseState),
    RemoveDigest,
    DuplicateRemoveOwner,
}

impl FixtureScanExecutor {
    fn new(home: PathBuf) -> Self {
        Self::with_mutation(home, FixtureScanMutation::None)
    }

    fn with_mutation(home: PathBuf, mutation: FixtureScanMutation) -> Self {
        let catalog = AdapterCatalog::load_embedded().expect("embedded catalog");
        Self {
            home,
            codex: catalog
                .for_tool(ToolId::Codex)
                .expect("Codex adapter")
                .clone(),
            sequence: AtomicU64::new(0),
            mutation,
        }
    }
}

impl LocalScanExecutor for FixtureScanExecutor {
    fn execute(
        &self,
        attempt_id: &str,
        _progress: &dyn LocalScanProgressPort,
    ) -> LocalScanExecutorResult {
        let mut output = LocalScanner::scan_with_handles(&self.home, &[self.codex.clone()])
            .expect("fixture scan");
        let remove_id = output
            .result
            .instances
            .iter()
            .find(|instance| instance.name.as_deref() == Some("remove"))
            .map(|instance| instance.instance_id.clone());
        match self.mutation {
            FixtureScanMutation::None => {}
            FixtureScanMutation::RemoveParseState(parse_state) => {
                if let Some(instance) = output
                    .result
                    .instances
                    .iter_mut()
                    .find(|instance| instance.name.as_deref() == Some("remove"))
                {
                    instance.parse_state = parse_state;
                }
            }
            FixtureScanMutation::RemoveDigest => {
                if let Some(instance_id) = remove_id {
                    output
                        .instance_handles
                        .get_mut(&instance_id)
                        .expect("remove handle")
                        .scan_content_sha256 = None;
                }
            }
            FixtureScanMutation::DuplicateRemoveOwner => {
                if let Some(instance_id) = remove_id {
                    let mut duplicate = output
                        .result
                        .instances
                        .iter()
                        .find(|instance| instance.instance_id == instance_id)
                        .expect("remove instance")
                        .clone();
                    duplicate.instance_id = "fixture-duplicate-remove-owner".to_string();
                    duplicate.name = Some("remove duplicate".to_string());
                    output.result.instances.push(duplicate.clone());
                    output.instance_handles.insert(
                        duplicate.instance_id,
                        output
                            .instance_handles
                            .get(&instance_id)
                            .expect("remove handle")
                            .clone(),
                    );
                }
            }
        }
        let result = output.result;
        let sequence = self.sequence.fetch_add(1, Ordering::SeqCst) + 1;
        LocalScanExecutorResult::Complete(LocalScanPublication {
            snapshot: LocalSnapshot {
                snapshot_id: format!("removal-snapshot-{sequence}"),
                attempt_id: attempt_id.to_string(),
                status: LocalSnapshotStatus::Complete,
                scan_timestamp: "2026-08-02T00:00:00Z".to_string(),
                app_version: "test".to_string(),
                adapter_set_fingerprint: "a".repeat(64),
                content_fingerprint: format!("{sequence:064x}"),
                qualified_tools: result.qualified_tools,
                instances: result.instances,
                coverage: result.coverage,
                skipped_paths: result.skipped_paths,
                issues: result.issues,
                project_ignore_summaries: result.project_ignore_summaries,
            },
            instance_handle_ids: output.instance_handles.keys().cloned().collect(),
            instance_handles: output.instance_handles,
            project_locations: output.project_locations,
        })
    }
}

fn write(path: &Path, contents: &str) {
    fs::create_dir_all(path.parent().expect("fixture parent")).expect("fixture directory");
    fs::write(path, contents).expect("fixture file");
}

fn materialize_fixture(home: &Path) {
    write(&home.join(".codex/hooks.json"), HOOKS);
    write(&home.join(".codex/skills/remove/SKILL.md"), REMOVE_SKILL);
    write(&home.join(".codex/skills/keep/SKILL.md"), KEEP_SKILL);
}

fn wait_for_new_snapshot(context: &LocalContext, previous: Option<&str>) -> String {
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        if let Some(snapshot) = context.state().latest_complete {
            if previous != Some(snapshot.snapshot_id.as_str()) {
                return snapshot.snapshot_id;
            }
        }
        assert!(Instant::now() < deadline, "fixture scan timed out");
        std::thread::yield_now();
    }
}

fn instance_id(publication: &LocalScanPublication, locator_suffix: &str) -> String {
    publication
        .snapshot
        .instances
        .iter()
        .find(|instance| instance.stable_source_locator.ends_with(locator_suffix))
        .unwrap_or_else(|| panic!("fixture instance missing: {locator_suffix}"))
        .instance_id
        .clone()
}

#[test]
fn confirmed_batch_removal_rewrites_all_selected_source_owners_deletes_selected_file_and_rescans() {
    let fixture = tempdir().expect("temporary fixture");
    let home = fixture.path().join("home");
    materialize_fixture(&home);
    let context = LocalContext::with_executor(Arc::new(FixtureScanExecutor::new(home.clone())));
    context.start_scan().expect("initial scan accepted");
    let snapshot_id = wait_for_new_snapshot(&context, None);
    let publication = context
        .publication(&snapshot_id)
        .expect("initial publication");
    let first_handler = instance_id(&publication, "#/hooks/PreToolUse/0/hooks/0");
    let second_handler = instance_id(&publication, "#/hooks/PreToolUse/1/hooks/0");
    let third_handler = instance_id(&publication, "#/hooks/PreToolUse/2/hooks/0");
    let remove_skill = instance_id(&publication, "/skills/remove/SKILL.md");

    let plan = context
        .prepare_local_removal(
            &snapshot_id,
            &[
                first_handler.clone(),
                second_handler.clone(),
                third_handler.clone(),
                remove_skill.clone(),
            ],
        )
        .expect("safe removal plan");
    assert_eq!(plan.eligible_members.len(), 4);
    assert!(plan.blocked_members.is_empty());
    assert_eq!(plan.shared_config_entry_removal_count, 3);
    assert_eq!(plan.dedicated_file_deletion_count, 1);
    assert_eq!(plan.parent_folder_deletion_count, 0);

    let outcome = context
        .apply_local_removal(&plan.plan_id, &plan.plan_digest, true)
        .expect("confirmed removal");
    assert_eq!(outcome.rescan_state, LocalRemovalRescanState::Accepted);
    assert_eq!(
        outcome
            .source_group_outcomes
            .iter()
            .filter(|group| group.state == LocalRemovalSourceGroupState::Success)
            .count(),
        2
    );

    let replacement_snapshot = wait_for_new_snapshot(&context, Some(&snapshot_id));
    let rewritten_bytes = fs::read(home.join(".codex/hooks.json")).expect("rewritten JSON");
    let rewritten: Value = serde_json::from_slice(&rewritten_bytes).expect("valid rewritten JSON");
    assert_eq!(
        rewritten
            .pointer("/hooks/PreToolUse")
            .and_then(Value::as_array)
            .unwrap()
            .len(),
        3
    );
    assert_eq!(
        rewritten
            .pointer("/hooks/PreToolUse/0/hooks")
            .and_then(Value::as_array)
            .unwrap()
            .len(),
        0
    );
    assert_eq!(
        rewritten
            .pointer("/hooks/PreToolUse/1/hooks")
            .and_then(Value::as_array)
            .unwrap()
            .len(),
        0
    );
    assert_eq!(
        rewritten
            .pointer("/hooks/PreToolUse/2/hooks")
            .and_then(Value::as_array)
            .unwrap()
            .len(),
        0
    );
    assert_eq!(
        rewritten
            .pointer("/unrelated/preserved")
            .and_then(Value::as_bool),
        Some(true)
    );
    assert!(!home.join(".codex/skills/remove/SKILL.md").exists());
    assert!(home.join(".codex/skills/remove").is_dir());
    assert!(home.join(".codex/skills/keep/SKILL.md").is_file());
    let replacement = context
        .publication(&replacement_snapshot)
        .expect("replacement publication");
    let reconciliation = context
        .reconcile_local_removal(
            &replacement_snapshot,
            "local-scan-2",
            &[
                first_handler.clone(),
                second_handler.clone(),
                third_handler.clone(),
                remove_skill.clone(),
            ],
        )
        .expect("complete replacement must reconcile selected IDs");
    assert_eq!(
        reconciliation.state,
        LocalRemovalReconciliationState::Confirmed
    );
    assert_eq!(
        reconciliation.confirmed_absent_instance_ids,
        vec![
            first_handler.clone(),
            second_handler.clone(),
            third_handler.clone(),
            remove_skill.clone(),
        ]
    );
    assert!(reconciliation.unresolved_instance_ids.is_empty());
    assert_eq!(
        replacement
            .snapshot
            .instances
            .iter()
            .filter(|instance| instance.stable_source_locator.contains("PreToolUse"))
            .count(),
        3
    );
    assert!(replacement
        .snapshot
        .instances
        .iter()
        .all(|instance| instance.instance_id != first_handler
            && instance.instance_id != second_handler
            && instance.instance_id != third_handler));
    assert!(replacement
        .snapshot
        .instances
        .iter()
        .all(|instance| instance.name.as_deref() != Some("remove")));
}

#[test]
fn stale_source_group_remains_unchanged_while_an_independent_group_succeeds_and_rescans() {
    let fixture = tempdir().expect("temporary fixture");
    let home = fixture.path().join("home");
    materialize_fixture(&home);
    let context = LocalContext::with_executor(Arc::new(FixtureScanExecutor::new(home.clone())));
    context.start_scan().expect("initial scan accepted");
    let snapshot_id = wait_for_new_snapshot(&context, None);
    let publication = context
        .publication(&snapshot_id)
        .expect("initial publication");
    let first_handler = instance_id(&publication, "#/hooks/PreToolUse/0/hooks/0");
    let second_handler = instance_id(&publication, "#/hooks/PreToolUse/1/hooks/0");
    let third_handler = instance_id(&publication, "#/hooks/PreToolUse/2/hooks/0");
    let remove_skill = instance_id(&publication, "/skills/remove/SKILL.md");
    let plan = context
        .prepare_local_removal(
            &snapshot_id,
            &[
                first_handler,
                second_handler,
                third_handler,
                remove_skill.clone(),
            ],
        )
        .expect("safe removal plan");

    write(
        &home.join(".codex/skills/remove/SKILL.md"),
        "---\nname: remove\ndescription: deliberately changed after prepare\n---\n\nchanged\n",
    );
    let outcome = context
        .apply_local_removal(&plan.plan_id, &plan.plan_digest, true)
        .expect("partial batch outcome");
    assert_eq!(outcome.rescan_state, LocalRemovalRescanState::Accepted);
    assert_eq!(
        outcome
            .source_group_outcomes
            .iter()
            .filter(|group| group.state == LocalRemovalSourceGroupState::Success)
            .count(),
        1
    );
    let failed = outcome
        .source_group_outcomes
        .iter()
        .find(|group| group.state == LocalRemovalSourceGroupState::FailedUnchanged)
        .expect("stale dedicated file must remain a failed group");
    assert_eq!(failed.member_ids, vec![remove_skill]);
    assert!(failed.code.is_some());

    let replacement_snapshot = wait_for_new_snapshot(&context, Some(&snapshot_id));
    let rewritten: Value =
        serde_json::from_slice(&fs::read(home.join(".codex/hooks.json")).expect("rewritten JSON"))
            .expect("valid rewritten JSON");
    assert_eq!(
        rewritten
            .pointer("/hooks/PreToolUse")
            .and_then(Value::as_array)
            .unwrap()
            .len(),
        3
    );
    assert!(rewritten
        .pointer("/hooks/PreToolUse")
        .and_then(Value::as_array)
        .unwrap()
        .iter()
        .all(|entry| {
            entry
                .pointer("/hooks")
                .and_then(Value::as_array)
                .is_some_and(Vec::is_empty)
        }));
    assert!(home.join(".codex/skills/remove/SKILL.md").is_file());
    assert!(home.join(".codex/skills/remove").is_dir());
    let replacement = context
        .publication(&replacement_snapshot)
        .expect("replacement publication");
    assert!(replacement
        .snapshot
        .instances
        .iter()
        .any(|instance| instance.name.as_deref() == Some("remove")));
}

#[test]
fn all_unchanged_groups_do_not_start_a_rescan() {
    let fixture = tempdir().expect("temporary fixture");
    let home = fixture.path().join("home");
    materialize_fixture(&home);
    let context = LocalContext::with_executor(Arc::new(FixtureScanExecutor::new(home.clone())));
    context.start_scan().expect("initial scan accepted");
    let snapshot_id = wait_for_new_snapshot(&context, None);
    let publication = context
        .publication(&snapshot_id)
        .expect("initial publication");
    let remove_skill = instance_id(&publication, "/skills/remove/SKILL.md");
    let plan = context
        .prepare_local_removal(&snapshot_id, &[remove_skill.clone()])
        .expect("safe removal plan");

    write(
        &home.join(".codex/skills/remove/SKILL.md"),
        "---\nname: remove\ndescription: changed after prepare\n---\n\nchanged\n",
    );
    let outcome = context
        .apply_local_removal(&plan.plan_id, &plan.plan_digest, true)
        .expect("unchanged failure outcome");

    assert_eq!(outcome.rescan_state, LocalRemovalRescanState::NotStarted);
    assert_eq!(outcome.source_group_outcomes.len(), 1);
    let group = &outcome.source_group_outcomes[0];
    assert_eq!(group.state, LocalRemovalSourceGroupState::FailedUnchanged);
    assert_eq!(group.member_ids, vec![remove_skill]);
    assert!(group.code.is_some());
    assert_eq!(
        context
            .state()
            .latest_complete
            .as_ref()
            .map(|snapshot| snapshot.snapshot_id.as_str()),
        Some(snapshot_id.as_str())
    );
}

#[test]
fn unconfirmed_apply_consumes_the_plan_without_mutating_any_source() {
    let fixture = tempdir().expect("temporary fixture");
    let home = fixture.path().join("home");
    materialize_fixture(&home);
    let context = LocalContext::with_executor(Arc::new(FixtureScanExecutor::new(home.clone())));
    context.start_scan().expect("initial scan accepted");
    let snapshot_id = wait_for_new_snapshot(&context, None);
    let publication = context
        .publication(&snapshot_id)
        .expect("initial publication");
    let remove_skill = instance_id(&publication, "/skills/remove/SKILL.md");
    let plan = context
        .prepare_local_removal(&snapshot_id, &[remove_skill])
        .expect("safe removal plan");

    let error = context
        .apply_local_removal(&plan.plan_id, &plan.plan_digest, false)
        .expect_err("unconfirmed request must fail closed");
    assert_eq!(error.code, "removal_confirmation_required");
    assert_eq!(
        fs::read_to_string(home.join(".codex/hooks.json")).unwrap(),
        HOOKS
    );
    assert_eq!(
        fs::read_to_string(home.join(".codex/skills/remove/SKILL.md")).unwrap(),
        REMOVE_SKILL
    );
    assert_eq!(
        context
            .apply_local_removal(&plan.plan_id, &plan.plan_digest, true)
            .expect_err("unconfirmed plan must not be reusable")
            .code,
        "removal_plan_expired"
    );
}

#[test]
fn shared_source_with_an_unselected_owner_is_blocked_without_disclosing_source_details() {
    let fixture = tempdir().expect("temporary fixture");
    let home = fixture.path().join("home");
    materialize_fixture(&home);
    let context = LocalContext::with_executor(Arc::new(FixtureScanExecutor::new(home)));
    context.start_scan().expect("initial scan accepted");
    let snapshot_id = wait_for_new_snapshot(&context, None);
    let publication = context
        .publication(&snapshot_id)
        .expect("initial publication");
    let first_handler = instance_id(&publication, "#/hooks/PreToolUse/0/hooks/0");

    let plan = context
        .prepare_local_removal(&snapshot_id, &[first_handler.clone()])
        .expect("blocked preview is still safe to render");

    assert!(plan.eligible_members.is_empty());
    assert_eq!(plan.blocked_members.len(), 1);
    let blocked = &plan.blocked_members[0];
    assert_eq!(blocked.instance_id, first_handler);
    assert_eq!(
        blocked.code.as_deref(),
        Some("removal_source_unselected_owner")
    );
    assert!(!blocked.display_name.contains('#'));
    assert!(!blocked.display_name.contains('/'));
}

#[test]
fn dedicated_source_requires_exactly_one_selected_owner() {
    let fixture = tempdir().expect("temporary fixture");
    let home = fixture.path().join("home");
    materialize_fixture(&home);
    let context = LocalContext::with_executor(Arc::new(FixtureScanExecutor::with_mutation(
        home,
        FixtureScanMutation::DuplicateRemoveOwner,
    )));
    context.start_scan().expect("initial scan accepted");
    let snapshot_id = wait_for_new_snapshot(&context, None);
    let publication = context
        .publication(&snapshot_id)
        .expect("initial publication");
    let remove_skill = instance_id(&publication, "/skills/remove/SKILL.md");
    let duplicate = "fixture-duplicate-remove-owner".to_string();

    let unselected_plan = context
        .prepare_local_removal(&snapshot_id, std::slice::from_ref(&remove_skill))
        .expect("unselected-owner preview");
    assert!(unselected_plan.eligible_members.is_empty());
    assert_eq!(
        unselected_plan.blocked_members[0].code.as_deref(),
        Some("removal_source_unselected_owner")
    );

    let fully_selected_plan = context
        .prepare_local_removal(&snapshot_id, &[remove_skill, duplicate])
        .expect("non-standalone dedicated preview");
    assert!(fully_selected_plan.eligible_members.is_empty());
    assert_eq!(fully_selected_plan.blocked_members.len(), 2);
    assert!(fully_selected_plan
        .blocked_members
        .iter()
        .all(|member| member.code.as_deref() == Some("removal_dedicated_source_not_standalone")));
}

#[test]
fn malformed_unreadable_and_digest_missing_standalone_sources_are_blocked_at_prepare() {
    let malformed_fixture = tempdir().expect("malformed fixture");
    let malformed_home = malformed_fixture.path().join("home");
    materialize_fixture(&malformed_home);
    write(&malformed_home.join(".codex/hooks.json"), "{ malformed");
    let malformed_context =
        LocalContext::with_executor(Arc::new(FixtureScanExecutor::new(malformed_home)));
    malformed_context
        .start_scan()
        .expect("malformed scan accepted");
    let malformed_snapshot = wait_for_new_snapshot(&malformed_context, None);
    let malformed_publication = malformed_context
        .publication(&malformed_snapshot)
        .expect("malformed publication");
    let malformed_hook = instance_id(&malformed_publication, "/hooks.json");
    let malformed_plan = malformed_context
        .prepare_local_removal(&malformed_snapshot, &[malformed_hook])
        .expect("malformed preview");
    assert!(malformed_plan.eligible_members.is_empty());
    assert_eq!(
        malformed_plan.blocked_members[0].code.as_deref(),
        Some("removal_requires_parsed_source")
    );

    let unreadable_fixture = tempdir().expect("unreadable fixture");
    let unreadable_home = unreadable_fixture.path().join("home");
    materialize_fixture(&unreadable_home);
    let unreadable_context =
        LocalContext::with_executor(Arc::new(FixtureScanExecutor::with_mutation(
            unreadable_home,
            FixtureScanMutation::RemoveParseState(ParseState::Unreadable),
        )));
    unreadable_context
        .start_scan()
        .expect("unreadable scan accepted");
    let unreadable_snapshot = wait_for_new_snapshot(&unreadable_context, None);
    let unreadable_publication = unreadable_context
        .publication(&unreadable_snapshot)
        .expect("unreadable publication");
    let unreadable_skill = instance_id(&unreadable_publication, "/skills/remove/SKILL.md");
    let unreadable_plan = unreadable_context
        .prepare_local_removal(&unreadable_snapshot, &[unreadable_skill])
        .expect("unreadable preview");
    assert!(unreadable_plan.eligible_members.is_empty());
    assert_eq!(
        unreadable_plan.blocked_members[0].code.as_deref(),
        Some("removal_requires_parsed_source")
    );

    let digest_fixture = tempdir().expect("digest fixture");
    let digest_home = digest_fixture.path().join("home");
    materialize_fixture(&digest_home);
    let digest_context = LocalContext::with_executor(Arc::new(FixtureScanExecutor::with_mutation(
        digest_home,
        FixtureScanMutation::RemoveDigest,
    )));
    digest_context.start_scan().expect("digest scan accepted");
    let digest_snapshot = wait_for_new_snapshot(&digest_context, None);
    let digest_publication = digest_context
        .publication(&digest_snapshot)
        .expect("digest publication");
    let digest_skill = instance_id(&digest_publication, "/skills/remove/SKILL.md");
    let digest_plan = digest_context
        .prepare_local_removal(&digest_snapshot, &[digest_skill])
        .expect("digest preview");
    assert!(digest_plan.eligible_members.is_empty());
    assert_eq!(
        digest_plan.blocked_members[0].code.as_deref(),
        Some("removal_requires_source_digest")
    );
}
