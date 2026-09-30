use std::collections::BTreeSet;
use std::fs;
use std::os::unix::fs::{symlink, MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use harness_desktop_lib::contexts::install::{
    embedded_target_contract, DestinationApplyState, DestinationVerifyState, InstallClock,
    InstallOperationStatus, InstallPlan, InstallRequest, InstallTargetRoots, InstallVerifier,
    InstallVerifyHook, InstallWorkspace, InstallWriteHook, InstallWriter, PlanRuntimeSurface,
    PlanValidator, ValidatedPlan, EMBEDDED_TARGET_CONTRACT_SHA256,
};
use serde_json::json;
use sha2::{Digest, Sha256};
use tempfile::TempDir;

fn source(relative: &str) -> String {
    fs::read_to_string(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(relative)).unwrap()
}

#[test]
fn writer_requires_an_unforgeable_validated_plan_capability() {
    let plan = source("src/contexts/install/plan.rs");
    let writer = source("src/contexts/install/writer.rs");
    let module = source("src/contexts/install/mod.rs");
    let start = plan.find("pub struct ValidatedPlan").unwrap();
    let block = &plan[start..plan[start..].find("\n}\n").unwrap() + start];

    for forbidden in [
        "pub contract_hash:",
        "pub semantic_fingerprint:",
        "pub canonical_artifacts:",
        "pub request_targets:",
    ] {
        assert!(
            !block.contains(forbidden),
            "forgeable capability field: {forbidden}"
        );
    }
    assert!(block.contains("canonical_artifacts: Vec<PlanArtifact>"));
    assert!(block.contains("request_targets: BTreeSet<String>"));
    assert!(writer.contains("validated: &ValidatedPlan"));
    assert!(!writer.contains("plan: &InstallPlan"));
    assert!(module.contains("pub mod writer;"));
}

struct Fixture {
    _root: TempDir,
    workspace: InstallWorkspace,
    target: PathBuf,
}

impl Fixture {
    fn new() -> Self {
        let root = tempfile::tempdir().unwrap();
        let canonical = fs::canonicalize(root.path()).unwrap();
        let source_root = canonical.join("source");
        let app_temp = canonical.join("app-temp");
        let target = canonical.join("target");
        write_mode(
            &source_root.join("components/registry.yml"),
            b"components: []\n",
            0o644,
        );
        fs::create_dir_all(&app_temp).unwrap();
        fs::set_permissions(&app_temp, fs::Permissions::from_mode(0o700)).unwrap();
        fs::create_dir_all(&target).unwrap();
        let workspace = InstallWorkspace::create(&source_root, &app_temp).unwrap();
        Self {
            _root: root,
            workspace,
            target,
        }
    }

    fn roots(&self, targets: &BTreeSet<String>) -> InstallTargetRoots {
        let metadata = fs::symlink_metadata(&self.target).unwrap();
        let identity = (metadata.dev(), metadata.ino());
        InstallTargetRoots::new(
            targets
                .iter()
                .map(|target| (target.clone(), self.target.clone()))
                .collect(),
            targets
                .iter()
                .map(|target| (target.clone(), identity))
                .collect(),
        )
        .unwrap()
    }
}

fn write_mode(path: &Path, bytes: &[u8], mode: u32) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, bytes).unwrap();
    fs::set_permissions(path, fs::Permissions::from_mode(mode)).unwrap();
}

fn positive_plan() -> InstallPlan {
    let mut value: serde_json::Value =
        serde_json::from_str(&source("tests/fixtures/m5_install_plan_positive.json")).unwrap();
    value["install_contract_hash"] = json!(EMBEDDED_TARGET_CONTRACT_SHA256);
    serde_json::from_value(value).unwrap()
}

fn plan_for(destinations: &[&str]) -> InstallPlan {
    let mut plan = positive_plan();
    plan.mode = "apply".to_string();
    plan.artifacts
        .retain(|artifact| destinations.contains(&artifact.destination.as_str()));
    let targets = plan
        .artifacts
        .iter()
        .map(|artifact| artifact.target.clone())
        .collect::<BTreeSet<_>>();
    let components = plan
        .artifacts
        .iter()
        .flat_map(|artifact| {
            std::iter::once(artifact.component_id.clone()).chain(artifact.component_ids.clone())
        })
        .collect::<BTreeSet<_>>();
    let contract = embedded_target_contract().unwrap();
    plan.targets = targets.iter().cloned().collect();
    plan.components = components.iter().cloned().collect();
    plan.runtime_surfaces = plan
        .artifacts
        .iter()
        .map(|artifact| {
            let surface = contract
                .runtime_surface(&artifact.target, &artifact.destination)
                .unwrap();
            PlanRuntimeSurface {
                target: artifact.target.clone(),
                path: surface.prefix.clone(),
                source: if artifact.destination == surface.prefix {
                    artifact.source.clone()
                } else {
                    surface.source.clone()
                },
            }
        })
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect();
    plan
}

fn artifact_body(destination: &str) -> &'static [u8] {
    match destination {
        "AGENTS.md" => b"managed body\n",
        ".agents/skills/optimal-response/SKILL.md" => b"# canonical skill\n",
        ".codex/config.toml" => {
            b"[agents.\"fresh\"]\ndescription = \"managed agent\"\nconfig_file = \"fresh.toml\"\n"
        }
        ".codex/hooks.json" => {
            br#"{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"node new.cjs"}]}]}}"#
        }
        ".hermes/config.yaml" => b"skills:\n  enabled: true\n",
        _ => b"#!/usr/bin/env node\n",
    }
}

fn materialize_plan(fixture: &Fixture, plan: &mut InstallPlan) {
    for artifact in &mut plan.artifacts {
        let body = artifact_body(&artifact.destination);
        let mode = artifact.mode.unwrap();
        write_mode(&fixture.workspace.root().join(&artifact.source), body, mode);
        artifact.source_sha256 = Some(hex_sha256(body));
    }
}

fn validate_plan(plan: &InstallPlan) -> ValidatedPlan {
    let request = InstallRequest {
        scope: plan.scope.clone(),
        targets: plan.targets.iter().cloned().collect(),
    };
    let selected = plan.components.iter().cloned().collect();
    PlanValidator::embedded()
        .unwrap()
        .validate(plan, &request, &selected)
        .unwrap()
}

fn validate_materialized(fixture: &Fixture, mut plan: InstallPlan) -> ValidatedPlan {
    materialize_plan(fixture, &mut plan);
    validate_plan(&plan)
}

fn hex_sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

#[test]
fn exact_copy_uses_atomic_sibling_replace_source_mode_and_fixed_point_verify() {
    let fixture = Fixture::new();
    let validated = validate_materialized(
        &fixture,
        plan_for(&[".agents/skills/optimal-response/SKILL.md"]),
    );
    let roots = fixture.roots(validated.request_targets());

    let applied = InstallWriter::default()
        .apply(&validated, &fixture.workspace, &roots)
        .unwrap();
    assert_eq!(
        applied.status(),
        InstallOperationStatus::Complete,
        "{:?}",
        applied.destinations()
    );
    assert_eq!(
        applied.destinations()[0].state,
        DestinationApplyState::Applied
    );
    let destination = fixture
        .target
        .join(".agents/skills/optimal-response/SKILL.md");
    assert_eq!(fs::read(&destination).unwrap(), b"# canonical skill\n");
    assert_eq!(fs::metadata(&destination).unwrap().mode() & 0o777, 0o644);
    assert!(fs::read_dir(destination.parent().unwrap())
        .unwrap()
        .all(|entry| !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .contains("harnesskit-install")));

    let verified = InstallVerifier::default()
        .verify(&validated, &fixture.workspace, &roots, &applied)
        .unwrap();
    assert_eq!(verified.status(), InstallOperationStatus::Complete);
    assert_eq!(
        verified.destinations()[0].state,
        DestinationVerifyState::Verified
    );
    assert_eq!(
        verified.destinations()[0].content_sha256.as_deref(),
        Some(hex_sha256(b"# canonical skill\n").as_str())
    );

    let inode = fs::metadata(&destination).unwrap().ino();
    let unchanged = InstallWriter::default()
        .apply(&validated, &fixture.workspace, &roots)
        .unwrap();
    assert_eq!(unchanged.status(), InstallOperationStatus::Complete);
    assert_eq!(
        unchanged.destinations()[0].state,
        DestinationApplyState::Unchanged
    );
    assert_eq!(fs::metadata(&destination).unwrap().ino(), inode);
    let unchanged_verified = InstallVerifier::default()
        .verify(&validated, &fixture.workspace, &roots, &unchanged)
        .unwrap();
    assert_eq!(
        unchanged_verified.destinations()[0].state,
        DestinationVerifyState::Verified
    );
}

#[test]
fn validated_capability_owns_canonical_artifacts_after_raw_plan_mutation() {
    let fixture = Fixture::new();
    let mut plan = plan_for(&[".agents/skills/optimal-response/SKILL.md"]);
    materialize_plan(&fixture, &mut plan);
    let validated = validate_plan(&plan);
    let roots = fixture.roots(validated.request_targets());
    plan.artifacts[0].destination = "../../outside".to_string();
    plan.artifacts[0].source = "../../outside".to_string();

    let report = InstallWriter::default()
        .apply(&validated, &fixture.workspace, &roots)
        .unwrap();
    assert_eq!(report.status(), InstallOperationStatus::Complete);
    assert!(fixture
        .target
        .join(".agents/skills/optimal-response/SKILL.md")
        .is_file());
    assert!(!fixture.target.parent().unwrap().join("outside").exists());
}

#[test]
fn managed_json_and_toml_merges_preserve_foreign_content_and_verify_exact_hashes() {
    let cases = [
        ("AGENTS.md", "foreign prose\n"),
        (
            ".codex/hooks.json",
            "{\"foreign\":true,\"hooks\":{\"BeforeAgent\":[{\"hooks\":[{\"type\":\"command\",\"command\":\"echo foreign\"}]}]}}\n",
        ),
        (
            ".codex/config.toml",
            "model = \"gpt-5\"\n[agents.\"foreign\"]\ndescription = \"keep\"\nconfig_file = \"foreign.toml\"\n",
        ),
    ];
    for (destination, existing) in cases {
        let fixture = Fixture::new();
        let validated = validate_materialized(&fixture, plan_for(&[destination]));
        let roots = fixture.roots(validated.request_targets());
        let target = fixture.target.join(destination);
        write_mode(&target, existing.as_bytes(), 0o640);

        let applied = InstallWriter::default()
            .apply(&validated, &fixture.workspace, &roots)
            .unwrap();
        assert_eq!(applied.status(), InstallOperationStatus::Complete);
        let output = fs::read_to_string(&target).unwrap();
        assert!(output.contains("foreign"), "destination={destination}");
        assert_eq!(fs::metadata(&target).unwrap().mode() & 0o777, 0o640);
        let verified = InstallVerifier::default()
            .verify(&validated, &fixture.workspace, &roots, &applied)
            .unwrap();
        assert_eq!(
            verified.destinations()[0].state,
            DestinationVerifyState::Verified
        );
    }
}

#[test]
fn symlink_leaf_and_source_drift_fail_without_writing_outside_target() {
    let fixture = Fixture::new();
    let validated = validate_materialized(
        &fixture,
        plan_for(&[".agents/skills/optimal-response/SKILL.md"]),
    );
    let roots = fixture.roots(validated.request_targets());
    let outside = fixture.target.parent().unwrap().join("outside.txt");
    fs::write(&outside, b"outside").unwrap();
    let leaf = fixture
        .target
        .join(".agents/skills/optimal-response/SKILL.md");
    fs::create_dir_all(leaf.parent().unwrap()).unwrap();
    symlink(&outside, &leaf).unwrap();

    let report = InstallWriter::default()
        .apply(&validated, &fixture.workspace, &roots)
        .unwrap();
    assert_eq!(report.status(), InstallOperationStatus::Failed);
    assert_eq!(
        report.destinations()[0].state,
        DestinationApplyState::Failed
    );
    assert_eq!(
        report.destinations()[0].code.as_deref(),
        Some("destination_unsafe")
    );
    assert_eq!(fs::read(&outside).unwrap(), b"outside");

    fs::remove_file(&leaf).unwrap();
    fs::write(
        fixture
            .workspace
            .root()
            .join("dist/codex/.agents/skills/optimal-response/SKILL.md"),
        b"changed after validation\n",
    )
    .unwrap();
    let report = InstallWriter::default()
        .apply(&validated, &fixture.workspace, &roots)
        .unwrap();
    assert_eq!(
        report.destinations()[0].code.as_deref(),
        Some("source_changed")
    );
    assert!(!leaf.exists());
}

#[test]
fn symlink_ancestor_and_source_mode_drift_fail_closed() {
    let fixture = Fixture::new();
    let validated = validate_materialized(
        &fixture,
        plan_for(&[".agents/skills/optimal-response/SKILL.md"]),
    );
    let roots = fixture.roots(validated.request_targets());
    let outside = fixture.target.parent().unwrap().join("outside-directory");
    fs::create_dir_all(&outside).unwrap();
    symlink(&outside, fixture.target.join(".agents")).unwrap();

    let report = InstallWriter::default()
        .apply(&validated, &fixture.workspace, &roots)
        .unwrap();
    assert_eq!(
        report.destinations()[0].code.as_deref(),
        Some("destination_unsafe")
    );
    assert!(fs::read_dir(&outside).unwrap().next().is_none());

    fs::remove_file(fixture.target.join(".agents")).unwrap();
    let staged = fixture
        .workspace
        .root()
        .join("dist/codex/.agents/skills/optimal-response/SKILL.md");
    fs::set_permissions(&staged, fs::Permissions::from_mode(0o755)).unwrap();
    let report = InstallWriter::default()
        .apply(&validated, &fixture.workspace, &roots)
        .unwrap();
    assert_eq!(
        report.destinations()[0].code.as_deref(),
        Some("source_changed")
    );
}

#[test]
fn verifier_rejects_post_apply_content_and_mode_tamper() {
    let fixture = Fixture::new();
    let validated = validate_materialized(
        &fixture,
        plan_for(&[".agents/skills/optimal-response/SKILL.md"]),
    );
    let roots = fixture.roots(validated.request_targets());
    let applied = InstallWriter::default()
        .apply(&validated, &fixture.workspace, &roots)
        .unwrap();
    let destination = fixture
        .target
        .join(".agents/skills/optimal-response/SKILL.md");
    fs::write(&destination, b"tampered content\n").unwrap();
    fs::set_permissions(&destination, fs::Permissions::from_mode(0o755)).unwrap();

    let verified = InstallVerifier::default()
        .verify(&validated, &fixture.workspace, &roots, &applied)
        .unwrap();
    assert_eq!(verified.status(), InstallOperationStatus::Failed);
    assert_eq!(
        verified.destinations()[0].state,
        DestinationVerifyState::Failed
    );
    assert_eq!(
        verified.destinations()[0].code.as_deref(),
        Some("verification_hash_mismatch")
    );
    assert!(verified.destinations()[0].content_sha256.is_none());
}

#[test]
fn one_destination_failure_does_not_hide_later_success() {
    let fixture = Fixture::new();
    let validated = validate_materialized(
        &fixture,
        plan_for(&[
            ".agents/skills/optimal-response/SKILL.md",
            ".codex/config.toml",
        ]),
    );
    let roots = fixture.roots(validated.request_targets());
    fs::write(
        fixture
            .workspace
            .root()
            .join("dist/codex/.agents/skills/optimal-response/SKILL.md"),
        b"drifted\n",
    )
    .unwrap();

    let report = InstallWriter::default()
        .apply(&validated, &fixture.workspace, &roots)
        .unwrap();
    assert_eq!(report.status(), InstallOperationStatus::Partial);
    assert_eq!(
        report.destinations()[0].state,
        DestinationApplyState::Failed
    );
    assert_eq!(
        report.destinations()[0].code.as_deref(),
        Some("source_changed")
    );
    assert_eq!(
        report.destinations()[1].state,
        DestinationApplyState::Applied
    );
    assert!(fixture.target.join(".codex/config.toml").is_file());

    let verified = InstallVerifier::default()
        .verify(&validated, &fixture.workspace, &roots, &report)
        .unwrap();
    assert_eq!(verified.status(), InstallOperationStatus::Partial);
    assert_eq!(
        verified.destinations()[0].state,
        DestinationVerifyState::NotApplied
    );
    assert!(verified.destinations()[0].content_sha256.is_none());
    assert_eq!(
        verified.destinations()[1].state,
        DestinationVerifyState::Verified
    );
}

struct SequenceClock {
    values: Vec<u64>,
    index: AtomicUsize,
}

impl SequenceClock {
    fn new(values: Vec<u64>) -> Self {
        Self {
            values,
            index: AtomicUsize::new(0),
        }
    }
}

impl InstallClock for SequenceClock {
    fn now_millis(&self) -> u64 {
        let index = self.index.fetch_add(1, Ordering::Relaxed);
        *self
            .values
            .get(index)
            .unwrap_or_else(|| self.values.last().unwrap())
    }
}

struct RaceHook {
    destination: PathBuf,
}

impl InstallWriteHook for RaceHook {
    fn before_replace(&self, _target: &str, _destination: &str) {
        fs::write(&self.destination, b"external replacement").unwrap();
    }
}

struct AfterCheckRaceHook {
    destination: PathBuf,
}

impl InstallWriteHook for AfterCheckRaceHook {
    fn before_replace(&self, _target: &str, _destination: &str) {}

    fn after_destination_check(&self, _target: &str, _destination: &str) {
        fs::write(&self.destination, b"after-check replacement").unwrap();
    }
}

struct TempEntryRaceHook {
    parent: PathBuf,
}

struct CorrelationRootRaceHook {
    authorized_root: PathBuf,
    displaced_root: PathBuf,
    outside_root: PathBuf,
}

impl InstallVerifyHook for CorrelationRootRaceHook {
    fn before_correlation_keys(&self, _target: &str, _destination: &str) {
        fs::rename(&self.authorized_root, &self.displaced_root).unwrap();
        fs::create_dir_all(&self.outside_root).unwrap();
        symlink(&self.outside_root, &self.authorized_root).unwrap();
    }
}

impl InstallWriteHook for TempEntryRaceHook {
    fn before_replace(&self, _target: &str, _destination: &str) {
        let temp = fs::read_dir(&self.parent)
            .unwrap()
            .map(|entry| entry.unwrap().path())
            .find(|path| {
                path.file_name()
                    .unwrap()
                    .to_string_lossy()
                    .contains(".harnesskit-install-")
            })
            .unwrap();
        fs::remove_file(&temp).unwrap();
        fs::write(temp, b"foreign temp replacement").unwrap();
    }
}

#[test]
fn pre_rename_identity_race_is_fail_closed_and_temp_is_cleaned() {
    let fixture = Fixture::new();
    let validated = validate_materialized(
        &fixture,
        plan_for(&[".agents/skills/optimal-response/SKILL.md"]),
    );
    let roots = fixture.roots(validated.request_targets());
    let destination = fixture
        .target
        .join(".agents/skills/optimal-response/SKILL.md");
    write_mode(&destination, b"original destination\n", 0o644);
    let writer = InstallWriter::with_ports(
        Arc::new(SequenceClock::new(vec![0, 0])),
        Arc::new(RaceHook {
            destination: destination.clone(),
        }),
    );

    let report = writer
        .apply(&validated, &fixture.workspace, &roots)
        .unwrap();
    assert_eq!(
        report.destinations()[0].code.as_deref(),
        Some("destination_changed")
    );
    assert_eq!(fs::read(&destination).unwrap(), b"external replacement");
    assert!(fs::read_dir(destination.parent().unwrap())
        .unwrap()
        .all(|entry| !entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .contains("harnesskit-install")));
}

#[test]
fn exchange_cas_rejects_after_check_destination_and_temp_entry_replacement() {
    let fixture = Fixture::new();
    let validated = validate_materialized(
        &fixture,
        plan_for(&[".agents/skills/optimal-response/SKILL.md"]),
    );
    let roots = fixture.roots(validated.request_targets());
    let destination = fixture
        .target
        .join(".agents/skills/optimal-response/SKILL.md");
    write_mode(&destination, b"original destination\n", 0o644);
    let after_check = InstallWriter::with_ports(
        Arc::new(SequenceClock::new(vec![0, 0])),
        Arc::new(AfterCheckRaceHook {
            destination: destination.clone(),
        }),
    );
    let report = after_check
        .apply(&validated, &fixture.workspace, &roots)
        .unwrap();
    assert_eq!(
        report.destinations()[0].code.as_deref(),
        Some("destination_changed")
    );
    assert_eq!(fs::read(&destination).unwrap(), b"after-check replacement");

    let temp_race = InstallWriter::with_ports(
        Arc::new(SequenceClock::new(vec![0, 0])),
        Arc::new(TempEntryRaceHook {
            parent: destination.parent().unwrap().to_path_buf(),
        }),
    );
    let report = temp_race
        .apply(&validated, &fixture.workspace, &roots)
        .unwrap();
    assert_eq!(
        report.destinations()[0].code.as_deref(),
        Some("temporary_file_changed")
    );
    assert_eq!(fs::read(&destination).unwrap(), b"after-check replacement");
    assert!(fs::read_dir(destination.parent().unwrap())
        .unwrap()
        .any(|entry| fs::read(entry.unwrap().path()).unwrap() == b"foreign temp replacement"));
}

#[test]
fn verifier_rejects_correlation_root_rebinding_after_content_verification() {
    let fixture = Fixture::new();
    let mut plan = plan_for(&[".agents/skills/optimal-response/SKILL.md"]);
    plan.scope = "user".to_string();
    let validated = validate_materialized(&fixture, plan);
    let roots = fixture.roots(validated.request_targets());
    let applied = InstallWriter::default()
        .apply(&validated, &fixture.workspace, &roots)
        .unwrap();
    let authorized_root = fixture.target.join(".agents/skills");
    let verifier = InstallVerifier::with_ports(
        Arc::new(SequenceClock::new(vec![0; 16])),
        Arc::new(CorrelationRootRaceHook {
            displaced_root: fixture.target.join(".agents/skills-displaced"),
            outside_root: fixture.target.parent().unwrap().join("outside-skills"),
            authorized_root,
        }),
    );

    let report = verifier
        .verify(&validated, &fixture.workspace, &roots, &applied)
        .unwrap();

    assert_eq!(report.status(), InstallOperationStatus::Failed);
    assert_eq!(
        report.destinations()[0].state,
        DestinationVerifyState::Failed
    );
    assert_eq!(
        report.destinations()[0].code.as_deref(),
        Some("correlation_root_unsafe")
    );
    assert!(report.destinations()[0].correlation_keys.is_empty());
    assert!(!source("src/contexts/install/writer.rs").contains("canonicalize("));
}

#[test]
fn apply_report_is_bound_to_the_opened_target_root_identity() {
    let fixture = Fixture::new();
    let validated = validate_materialized(
        &fixture,
        plan_for(&[".agents/skills/optimal-response/SKILL.md"]),
    );
    let roots = fixture.roots(validated.request_targets());
    let applied = InstallWriter::default()
        .apply(&validated, &fixture.workspace, &roots)
        .unwrap();
    let other = fixture.target.parent().unwrap().join("other-target");
    write_mode(
        &other.join(".agents/skills/optimal-response/SKILL.md"),
        b"# canonical skill\n",
        0o644,
    );
    let other_roots = InstallTargetRoots::new(
        validated
            .request_targets()
            .iter()
            .map(|target| (target.clone(), other.clone()))
            .collect(),
        validated
            .request_targets()
            .iter()
            .map(|target| {
                let metadata = fs::symlink_metadata(&other).unwrap();
                (target.clone(), (metadata.dev(), metadata.ino()))
            })
            .collect(),
    )
    .unwrap();

    assert_eq!(
        InstallVerifier::default()
            .verify(&validated, &fixture.workspace, &other_roots, &applied)
            .unwrap_err()
            .code(),
        "apply_report_root_mismatch"
    );
}

#[test]
fn writer_rejects_a_root_opened_with_a_different_preview_identity() {
    let fixture = Fixture::new();
    let validated = validate_materialized(
        &fixture,
        plan_for(&[".agents/skills/optimal-response/SKILL.md"]),
    );
    let other = fixture.target.parent().unwrap().join("preview-root");
    fs::create_dir(&other).unwrap();
    let other_metadata = fs::symlink_metadata(&other).unwrap();
    let expected = (other_metadata.dev(), other_metadata.ino());
    let roots = InstallTargetRoots::new(
        validated
            .request_targets()
            .iter()
            .map(|target| (target.clone(), fixture.target.clone()))
            .collect(),
        validated
            .request_targets()
            .iter()
            .map(|target| (target.clone(), expected))
            .collect(),
    )
    .unwrap();

    assert_eq!(
        InstallWriter::default()
            .apply(&validated, &fixture.workspace, &roots)
            .unwrap_err()
            .code(),
        "target_root_identity_changed"
    );
    assert!(!fixture
        .target
        .join(".agents/skills/optimal-response/SKILL.md")
        .exists());
}

#[test]
fn writer_and_verifier_deadlines_preserve_per_destination_partial_truth() {
    let fixture = Fixture::new();
    let validated = validate_materialized(
        &fixture,
        plan_for(&[
            ".agents/skills/optimal-response/SKILL.md",
            ".codex/config.toml",
        ]),
    );
    let roots = fixture.roots(validated.request_targets());
    let writer = InstallWriter::with_clock(Arc::new(SequenceClock::new(vec![0, 0, 0, 0, 181_000])));

    let applied = writer
        .apply(&validated, &fixture.workspace, &roots)
        .unwrap();
    assert_eq!(applied.status(), InstallOperationStatus::Partial);
    assert_eq!(
        applied.destinations()[0].state,
        DestinationApplyState::Applied
    );
    assert_eq!(
        applied.destinations()[1].state,
        DestinationApplyState::NotAttempted
    );
    assert_eq!(
        applied.destinations()[1].code.as_deref(),
        Some("writer_deadline_exceeded")
    );

    let completed = InstallWriter::default()
        .apply(&validated, &fixture.workspace, &roots)
        .unwrap();
    let verifier =
        InstallVerifier::with_clock(Arc::new(SequenceClock::new(vec![0, 0, 0, 0, 61_000])));
    let verified = verifier
        .verify(&validated, &fixture.workspace, &roots, &completed)
        .unwrap();
    assert_eq!(verified.status(), InstallOperationStatus::Partial);
    assert_eq!(
        verified.destinations()[0].state,
        DestinationVerifyState::Verified
    );
    assert_eq!(
        verified.destinations()[1].state,
        DestinationVerifyState::NotAttempted
    );
    assert_eq!(
        verified.destinations()[1].code.as_deref(),
        Some("verifier_deadline_exceeded")
    );
}

#[test]
fn writer_deadline_reached_during_one_destination_prevents_commit() {
    let fixture = Fixture::new();
    let validated = validate_materialized(
        &fixture,
        plan_for(&[".agents/skills/optimal-response/SKILL.md"]),
    );
    let roots = fixture.roots(validated.request_targets());
    let writer = InstallWriter::with_clock(Arc::new(SequenceClock::new(vec![0, 0, 0, 181_000])));

    let applied = writer
        .apply(&validated, &fixture.workspace, &roots)
        .unwrap();

    assert_eq!(applied.status(), InstallOperationStatus::Failed);
    assert_eq!(
        applied.destinations()[0].state,
        DestinationApplyState::Failed
    );
    assert_eq!(
        applied.destinations()[0].code.as_deref(),
        Some("writer_deadline_exceeded")
    );
    assert!(!fixture
        .target
        .join(".agents/skills/optimal-response/SKILL.md")
        .exists());
}
