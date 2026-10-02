use crate::contexts::install::InstallCoordinator;
use crate::contexts::install::{
    CanonicalArtifactRequest, GeneratedArtifactSet, InstallCoordinatorError, InstallPlan,
    InstallPlanGenerator, InstallWorkspace, PlanRunRequest,
};
use crate::contexts::local::scanner::LocalScanner;
use crate::contexts::local::{
    AdapterCatalog, LocalContext, LocalScanExecutorResult, LocalScanPublication, LocalSnapshot,
    LocalSnapshotStatus, OpenSourceRequest, ToolId,
};
use crate::contexts::sot::SotContext;
use crate::controller::AppController;
use std::{
    fs,
    path::{Path, PathBuf},
    process::Command,
    sync::Arc,
};
use tempfile::TempDir;

struct RealFixtureGenerator;
impl InstallPlanGenerator for RealFixtureGenerator {
    fn runtime_manifest_sha256(&self) -> &str {
        "fixture"
    }
    fn generate(
        &self,
        workspace: &InstallWorkspace,
        request: &PlanRunRequest,
    ) -> Result<InstallPlan, InstallCoordinatorError> {
        let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
        let mut cmd = Command::new(repo.join(".venv/bin/python"));
        cmd.arg("-B")
            .arg(repo.join("src-tauri/resources/install-runtime-entry/install_entry.py"))
            .args(["--script-id", "install-plan", "--workspace"])
            .arg(workspace.root())
            .args([
                "--mode",
                if request.mode == crate::contexts::install::PlanMode::Apply {
                    "apply"
                } else {
                    "dry-run"
                },
                "--profile",
                &request.profile,
                "--scope",
                &request.scope,
            ]);
        for id in &request.target_ids {
            cmd.args(["--target-id", id]);
        }
        for id in &request.component_ids {
            cmd.args(["--component-id", id]);
        }
        let out = cmd.output().unwrap();
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
        serde_json::from_slice(&out.stdout)
            .map_err(|_| InstallCoordinatorError::new("fixture_plan_invalid"))
    }
    fn generate_artifacts_in_workspace(
        &self,
        workspace: &InstallWorkspace,
        request: &CanonicalArtifactRequest,
    ) -> Result<GeneratedArtifactSet, InstallCoordinatorError> {
        let python = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../.venv/bin/python");
        let out = Command::new(python).current_dir(workspace.root()).args(["-B", "-c", "import json,sys; from scripts.install.plan import build_artifact_projection; print(json.dumps(build_artifact_projection([sys.argv[1]])))", request.component_ids.first().unwrap()]).output().unwrap();
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
        let result: serde_json::Value = serde_json::from_slice(&out.stdout).unwrap();
        assert!(!result["records"].as_array().unwrap().is_empty());
        Ok(GeneratedArtifactSet {
            generator_version: "canonical-artifact-generator-v1".into(),
            source_revision: request.source_revision.clone(),
            adapter_set_revision: "a".repeat(64),
            target_contract_revision: crate::contexts::install::EMBEDDED_TARGET_CONTRACT_SHA256
                .into(),
            issues: vec![],
            records: result["records"]
                .as_array()
                .unwrap()
                .iter()
                .map(|r| crate::contexts::install::GeneratedArtifact {
                    component_id: r["component_id"].as_str().unwrap().into(),
                    adapter_id: r["adapter_id"].as_str().unwrap().into(),
                    adapter_version: r["adapter_version"].as_str().unwrap().into(),
                    target: r["target"].as_str().unwrap().into(),
                    scope: r["scope"].as_str().unwrap().into(),
                    destination: r["destination"].as_str().unwrap().into(),
                    source: r["source"].as_str().unwrap().into(),
                    merge_strategy: r["merge_strategy"].as_str().map(str::to_owned),
                    config_entry_locator: r["config_entry_locator"].as_str().map(str::to_owned),
                    content_sha256: r["content_sha256"].as_str().unwrap().into(),
                    exact_bytes: r["exact_text"].as_str().unwrap().as_bytes().to_vec(),
                })
                .collect(),
        })
    }
}
fn copy_tree(from: &Path, to: &Path) {
    fs::create_dir_all(to).unwrap();
    for entry in fs::read_dir(from).unwrap() {
        let entry = entry.unwrap();
        if entry.file_type().unwrap().is_dir() {
            copy_tree(&entry.path(), &to.join(entry.file_name()));
        } else {
            fs::copy(entry.path(), to.join(entry.file_name())).unwrap();
        }
    }
}
struct Fixture {
    _temp: TempDir,
    sot: SotContext,
    local: LocalContext,
    checkout_id: String,
    sot_id: String,
    source_revision: String,
    instance_id: String,
    source: PathBuf,
    root: PathBuf,
    private: PathBuf,
}
fn fixture() -> Fixture {
    fixture_with_writer(None)
}
fn fixture_with_writer(writer: Option<crate::contexts::install::InstallWriter>) -> Fixture {
    fixture_source(writer, false)
}
fn fixture_source(
    writer: Option<crate::contexts::install::InstallWriter>,
    shared: bool,
) -> Fixture {
    fixture_tool_source(writer, shared, ToolId::Codex, false)
}
fn fixture_tool_source(
    writer: Option<crate::contexts::install::InstallWriter>,
    shared: bool,
    tool: ToolId,
    project: bool,
) -> Fixture {
    fixture_kind_source(writer, shared, tool, project, false)
}
fn fixture_kind_source(
    writer: Option<crate::contexts::install::InstallWriter>,
    shared: bool,
    tool: ToolId,
    project: bool,
    agent: bool,
) -> Fixture {
    fixture_surface_source(writer, shared, tool, project, agent, false)
}
fn fixture_surface_source(
    writer: Option<crate::contexts::install::InstallWriter>,
    shared: bool,
    tool: ToolId,
    project: bool,
    agent: bool,
    rule: bool,
) -> Fixture {
    let temp = TempDir::new().unwrap();
    let root = temp.path().join("checkout");
    fs::create_dir(&root).unwrap();
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
    for dir in ["components", "profiles", "schemas", "adapters", "scripts"] {
        copy_tree(&repo.join(dir), &root.join(dir));
    }
    assert!(Command::new("git")
        .args(["init", "-q"])
        .arg(&root)
        .status()
        .unwrap()
        .success());
    assert!(Command::new("git")
        .current_dir(&root)
        .args([
            "-c",
            "user.name=Fixture",
            "-c",
            concat!("user.email=fixture@", "example.invalid"),
            "commit",
            "--allow-empty",
            "-qm",
            "fixture"
        ])
        .status()
        .unwrap()
        .success());
    let private = temp.path().join("private");
    fs::create_dir(&private).unwrap();
    let private = fs::canonicalize(private).unwrap();
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(&private, fs::Permissions::from_mode(0o700)).unwrap();
    let checkout = AppController::with_state_file(private.join("state.json"));
    let install = InstallCoordinator::new(private.clone(), Arc::new(RealFixtureGenerator)).unwrap();
    let install = match writer {
        Some(writer) => install.with_test_writer(writer),
        None => install,
    };
    let sot = SotContext::with_services(checkout, Some(install)).unwrap();
    let checkout_id = sot.register_checkout(&root).unwrap().checkout_id;
    let sot_id = sot.load_active(&checkout_id).unwrap().snapshot_id;
    crate::contexts::install::writer::private_registration_directory(
        &private,
        "component-import-state",
    )
    .unwrap();
    let home = temp.path().join("home");
    let source_root = if project {
        home.join("workspace")
    } else {
        home.clone()
    };
    if rule {
        fs::create_dir_all(source_root.join(if tool == ToolId::ClaudeCode {
            ".claude"
        } else if matches!(tool, ToolId::Antigravity | ToolId::AntigravityCli) {
            ".agents"
        } else {
            ".codex"
        }))
        .unwrap();
    }
    let source = source_root.join(if rule {
        if tool == ToolId::ClaudeCode {
            "CLAUDE.md"
        } else {
            "AGENTS.md"
        }
    } else if agent {
        match tool {
            ToolId::Codex => ".codex/agents/fixture-import.toml",
            ToolId::ClaudeCode => ".claude/agents/fixture-import.md",
            ToolId::Antigravity if project => ".agents/agents/fixture-import.md",
            ToolId::Antigravity => ".gemini/config/agents/fixture-import.md",
            ToolId::AntigravityCli if project => ".agents/agents/fixture-import.md",
            ToolId::AntigravityCli => ".gemini/antigravity-cli/agents/fixture-import.md",
            _ => panic!("unqualified fixture agent tool"),
        }
    } else if shared {
        ".claude/settings.json"
    } else {
        match (tool, project) {
            (ToolId::Codex, false) => ".codex/skills/fixture-import/SKILL.md",
            (ToolId::ClaudeCode, _) => ".claude/skills/fixture-import/SKILL.md",
            (ToolId::Hermes, _) => ".hermes/skills/fixture-import/SKILL.md",
            (ToolId::Antigravity, false) => ".gemini/config/skills/fixture-import/SKILL.md",
            (ToolId::AntigravityCli, false) => {
                ".gemini/antigravity-cli/skills/fixture-import/SKILL.md"
            }
            _ => ".agents/skills/fixture-import/SKILL.md",
        }
    });
    fs::create_dir_all(source.parent().unwrap()).unwrap();
    fs::write(&source, if shared { r#"{"theme":"foreign","hooks":{"Stop":[{"unknown":"keep","hooks":[{"type":"command","command":"printf fixture","timeout":10,"foreign":"keep"}]}],"SessionStart":[{"hooks":[{"type":"command","command":"printf foreign"}]}]}}"# } else { "---\nname: fixture-import\ndescription: Fixture skill import\n---\n# Fixture\nUse the fixture.\n" }).unwrap();
    if agent {
        fs::write(&source, "---\nname: fixture-import\ndescription: Fixture agent import\ntools: Read, Grep\nmodel: sonnet\ncolor: blue\n---\n# Fixture\nUse the fixture.\n").unwrap();
        if tool != ToolId::ClaudeCode {
            fs::write(&source, "---\nname: fixture-import\ndescription: |\n  Fixture agent import\n  preserve  spacing.\ntools:\n  enable_write_tools: false\n  enable_mcp_tools: true\n  enable_subagent_tools: false\n---\n# Fixture\nUse the fixture.\n").unwrap();
        }
        if tool == ToolId::Codex {
            fs::write(&source, "name = \"fixture-import\"\ndescription = \"Fixture agent import\"\nmodel = \"gpt-test\"\nmodel_reasoning_effort = \"high\"\ndeveloper_instructions = \"Use the fixture.\\n\"\n").unwrap();
            fs::write(source_root.join(".codex/config.toml"), "# keep global comment\nmodel = \"foreign-model\"\n[agents.\"fixture-import\"]\n# keep registration comment\ndescription = \"Fixture agent import\"\nconfig_file = \"agents/fixture-import.toml\"\nnickname_candidates = [\"Reviewer\", \"Second\"]\n[agents.neighbor]\n# keep neighbor comment\ndescription = \"Foreign\"\nconfig_file = \"agents/neighbor.toml\"\n").unwrap();
        }
        // Neighboring standalone agents are not this agent's supporting files.
        fs::write(
            source.parent().unwrap().join("neighbor.md"),
            "---\nname: neighbor\ndescription: Neighbor\n---\nNeighbor prompt.\n",
        )
        .unwrap();
    }
    if rule {
        fs::write(&source, "Foreign before\n<!-- BEGIN HARNESSKIT GENERATED CONTEXT -->\nUse the fixture.\n<!-- END HARNESSKIT GENERATED CONTEXT -->\nForeign after\n").unwrap();
    }
    let adapter = AdapterCatalog::load_embedded()
        .unwrap()
        .for_tool(if shared { ToolId::ClaudeCode } else { tool })
        .unwrap()
        .clone();
    let output = LocalScanner::scan_with_handles(&home, &[adapter]).unwrap();
    let instance_id = output
        .result
        .instances
        .iter()
        .find(|i| {
            if rule {
                i.kind == crate::contexts::local::SurfaceKind::Rule
            } else {
                i.name.as_deref() == Some(if shared { "Stop" } else { "fixture-import" })
            }
        })
        .unwrap()
        .instance_id
        .clone();
    let local = LocalContext::default();
    local
        .publish_scan_result_for_test(
            "scan",
            LocalScanExecutorResult::Complete(LocalScanPublication {
                snapshot: LocalSnapshot {
                    snapshot_id: "local".into(),
                    attempt_id: "scan".into(),
                    status: LocalSnapshotStatus::Complete,
                    scan_timestamp: "fixture".into(),
                    app_version: "fixture".into(),
                    adapter_set_fingerprint: "a".repeat(64),
                    content_fingerprint: "b".repeat(64),
                    qualified_tools: output.result.qualified_tools,
                    instances: output.result.instances,
                    coverage: output.result.coverage,
                    skipped_paths: output.result.skipped_paths,
                    issues: output.result.issues,
                    project_ignore_summaries: output.result.project_ignore_summaries,
                },
                instance_handle_ids: vec![instance_id.clone()],
                instance_handles: output.instance_handles,
                project_locations: output.project_locations,
            }),
        )
        .unwrap();
    let header = local
        .prepare_open_source_preview(OpenSourceRequest {
            snapshot_id: "local".into(),
            instance_id: instance_id.clone(),
            view_generation: 1,
        })
        .unwrap()
        .run()
        .unwrap();
    Fixture {
        _temp: temp,
        sot,
        local,
        checkout_id,
        sot_id,
        source_revision: header.source_revision,
        instance_id,
        source,
        root,
        private,
    }
}

fn support_fixture() -> Fixture {
    let mut f = fixture();
    fs::create_dir(f.source.parent().unwrap().join("references")).unwrap();
    fs::write(
        f.source.parent().unwrap().join("references/guide.md"),
        "Guide\n",
    )
    .unwrap();
    let skill = fs::read_to_string(&f.source).unwrap() + "[Guide](references/guide.md)\n";
    fs::write(&f.source, skill).unwrap();
    f.source_revision = f
        .local
        .prepare_open_source_preview(OpenSourceRequest {
            snapshot_id: "local".into(),
            instance_id: f.instance_id.clone(),
            view_generation: 2,
        })
        .unwrap()
        .run()
        .unwrap()
        .source_revision;
    f
}
fn support_preview(f: &Fixture) -> Result<crate::contexts::sot::import::ImportPreview, String> {
    f.sot.preview_component_import(
        &f.local,
        &f.checkout_id,
        &f.sot_id,
        "local",
        &f.instance_id,
        &f.source_revision,
    )
}

fn whole_file_fixture() -> Fixture {
    let mut f = fixture_surface_source(None, false, ToolId::Codex, true, false, true);
    fs::write(&f.source, "Original whole file.").unwrap();
    f.source_revision = f
        .local
        .prepare_open_source_preview(OpenSourceRequest {
            snapshot_id: "local".into(),
            instance_id: f.instance_id.clone(),
            view_generation: 2,
        })
        .unwrap()
        .run()
        .unwrap()
        .source_revision;
    f
}

#[test]
fn project_rule_whole_file_private_authority_and_unsafe_targets_fail_closed() {
    use std::os::unix::fs::{symlink, PermissionsExt};
    let f = whole_file_fixture();
    let p = support_preview(&f).unwrap();
    let id = p.component_id.clone();
    let s = f
        .sot
        .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
        .unwrap();
    let state = managed_state_path(&f);
    let before = fs::read(&state).unwrap();
    let original = fs::read(&f.source).unwrap();
    let preview = || {
        f.sot.preview_install(
            &f.checkout_id,
            &s.snapshot_id,
            &format!("component:{id}"),
            "project",
            "import-source".into(),
            std::collections::BTreeSet::from(["project".into()]),
        )
    };
    let links: Vec<serde_json::Value> = serde_json::from_slice(&before).unwrap();
    for field in [
        "tool",
        "scope",
        "source_locator",
        "source_path",
        "project_id",
        "selected_project_whole_file",
    ] {
        let mut altered = links.clone();
        altered[0][field] = if field == "project_id" { "" } else { "forged" }.into();
        fs::write(&state, serde_json::to_vec(&altered).unwrap()).unwrap();
        assert!(preview().is_err(), "invalid authority {field}");
        assert_eq!(fs::read(&f.source).unwrap(), original);
    }
    for bad in [b"corrupt".as_slice(), b"[]"] {
        fs::write(&state, bad).unwrap();
        assert!(preview().is_err());
    }
    fs::remove_file(&state).unwrap();
    assert!(preview().is_err());
    fs::write(&state, &before).unwrap();
    fs::set_permissions(&state, fs::Permissions::from_mode(0o600)).unwrap();
    fs::write(
        &state,
        serde_json::to_vec(&vec![links[0].clone(), links[0].clone()]).unwrap(),
    )
    .unwrap();
    assert!(preview().is_err());
    fs::write(&state, &before).unwrap();
    fs::remove_file(&f.source).unwrap();
    assert!(preview().is_err());
    assert!(!f.source.exists());
    let outside = f._temp.path().join("outside.md");
    fs::write(&outside, &original).unwrap();
    symlink(&outside, &f.source).unwrap();
    assert!(preview().is_err());
    fs::remove_file(&f.source).unwrap();
    fs::hard_link(&outside, &f.source).unwrap();
    assert!(preview().is_err());
    fs::remove_file(&f.source).unwrap();
    fs::write(&f.source, &original).unwrap();
    let mut aliased = links.clone();
    let mut neighbor = links[0].clone();
    neighbor["component_id"] = "harnesskit.rule.foreign".into();
    neighbor["selected_project_rule"] = true.into();
    aliased.push(neighbor);
    fs::write(&state, serde_json::to_vec(&aliased).unwrap()).unwrap();
    assert_eq!(preview().unwrap_err(), "management_destination_collision");
    fs::write(&state, &before).unwrap();
    assert_eq!(
        f.sot
            .preview_install(
                &f.checkout_id,
                &s.snapshot_id,
                "harnesskit.profile.engineering",
                "project",
                f.source.parent().unwrap().into(),
                std::collections::BTreeSet::from(["project".into()])
            )
            .unwrap_err(),
        "management_destination_collision"
    );
    assert_eq!(fs::read(&f.source).unwrap(), original);
}

#[test]
fn project_rule_whole_file_writer_race_and_private_failure_do_not_record_success() {
    for mode in ["source", "state"] {
        let f = whole_file_fixture();
        let p = support_preview(&f).unwrap();
        let id = p.component_id.clone();
        f.sot
            .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
            .unwrap();
        let baseline = fs::read(managed_state_path(&f)).unwrap();
        let writer = crate::contexts::install::InstallWriter::with_ports(
            Arc::new(FrozenClock),
            Arc::new(ManagedFailureHook {
                source: f.source.clone(),
                state: managed_state_path(&f),
                outside: f.private.join("state-hardlink"),
                mode,
            }),
        );
        let service = SotContext::with_services(
            AppController::with_state_file(f.private.join("state.json")),
            Some(
                InstallCoordinator::new(f.private.clone(), Arc::new(RealFixtureGenerator))
                    .unwrap()
                    .with_test_writer(writer),
            ),
        )
        .unwrap();
        let s = service.load_active(&f.checkout_id).unwrap();
        let s = service
            .save_imported_skill(
                &f.checkout_id,
                &s.snapshot_id,
                &id,
                "Replacement whole file.",
            )
            .unwrap();
        let p = service
            .preview_install(
                &f.checkout_id,
                &s.snapshot_id,
                &format!("component:{id}"),
                "project",
                "import-source".into(),
                std::collections::BTreeSet::from(["project".into()]),
            )
            .unwrap();
        let result = service.apply_install(&p.preview_id, managed_approvals(&p));
        if mode == "state" {
            assert_eq!(result.unwrap_err(), "management_state_write_failed");
        } else {
            assert_ne!(
                result.unwrap().status,
                crate::contexts::install::InstallOperationStatus::Complete
            );
        }
        assert_eq!(fs::read(managed_state_path(&f)).unwrap(), baseline);
        assert_eq!(service.session_headers().unwrap().install_evidence_id, None);
    }
}

#[test]
fn project_rule_whole_file_import_edit_adopt_and_protected_reapply() {
    let mut f = fixture_surface_source(None, false, ToolId::Codex, true, false, true);
    let original = "# Original\r\nKeep trailing spaces.  \r\nNo final newline";
    fs::write(&f.source, original).unwrap();
    f.source_revision = f
        .local
        .prepare_open_source_preview(OpenSourceRequest {
            snapshot_id: "local".into(),
            instance_id: f.instance_id.clone(),
            view_generation: 2,
        })
        .unwrap()
        .run()
        .unwrap()
        .source_revision;
    let p = support_preview(&f).unwrap();
    assert_eq!(p.content, original);
    assert_eq!(p.generated_artifact_count, 1);
    let id = p.component_id.clone();
    let mut s = f
        .sot
        .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
        .unwrap();
    assert_eq!(fs::read(&f.source).unwrap(), original.as_bytes());
    assert_eq!(
        f.sot
            .read_imported_skill(&f.checkout_id, &s.snapshot_id, &id)
            .unwrap()
            .content,
        original
    );
    s = f
        .sot
        .save_imported_skill(
            &f.checkout_id,
            &s.snapshot_id,
            &id,
            "Edited whole file.  \r\n",
        )
        .unwrap();
    assert_eq!(fs::read(&f.source).unwrap(), original.as_bytes());
    let preview = |service: &SotContext, snapshot: &crate::contexts::sot::SotSnapshot| {
        service.preview_install(
            &f.checkout_id,
            &snapshot.snapshot_id,
            &format!("component:{id}"),
            "project",
            "import-source".into(),
            std::collections::BTreeSet::from(["project".into()]),
        )
    };
    let p = preview(&f.sot, &s).unwrap();
    assert!(p.required_approvals.management_adoption);
    assert!(p.required_approvals.overwrite);
    assert!(p.warnings.iter().any(|w| w.contains("파일 전체 관리 전환")));
    assert!(p.warnings.iter().any(|w| w.contains("-# Original")
        && w.contains("-Keep trailing spaces.  ")
        && w.contains("+Edited whole file.  ")));
    let mut denied = managed_approvals(&p);
    denied.adopt_management = false;
    assert!(f.sot.apply_install(&p.preview_id, denied).is_err());
    let mut denied = managed_approvals(&p);
    denied.overwrite = false;
    assert!(f.sot.apply_install(&p.preview_id, denied).is_err());
    let out = f
        .sot
        .apply_install(&p.preview_id, managed_approvals(&p))
        .unwrap();
    assert_eq!(
        out.status,
        crate::contexts::install::InstallOperationStatus::Complete
    );
    assert_eq!(fs::read(&f.source).unwrap(), b"Edited whole file.  \r\n");
    let restarted = SotContext::with_services(
        AppController::with_state_file(f.private.join("state.json")),
        Some(InstallCoordinator::new(f.private.clone(), Arc::new(RealFixtureGenerator)).unwrap()),
    )
    .unwrap();
    s = restarted.load_active(&f.checkout_id).unwrap();
    assert!(
        restarted
            .read_imported_skill(&f.checkout_id, &s.snapshot_id, &id)
            .unwrap()
            .managed
    );
    s = restarted
        .save_imported_skill(&f.checkout_id, &s.snapshot_id, &id, "Updated whole file.")
        .unwrap();
    let p = preview(&restarted, &s).unwrap();
    assert!(!p.required_approvals.management_adoption && !p.required_approvals.managed_replacement);
    restarted
        .apply_install(&p.preview_id, managed_approvals(&p))
        .unwrap();
    assert_eq!(fs::read(&f.source).unwrap(), b"Updated whole file.");
    fs::write(&f.source, "External whole file.").unwrap();
    let p = preview(&restarted, &s).unwrap();
    assert!(p.required_approvals.managed_replacement);
    assert!(restarted
        .apply_install(&p.preview_id, managed_approvals(&p))
        .is_err());
    let mut replace = managed_approvals(&p);
    replace.replace_managed = true;
    fs::write(&f.source, "Later external.").unwrap();
    assert!(restarted.apply_install(&p.preview_id, replace).is_err());
    let p = preview(&restarted, &s).unwrap();
    let mut replace = managed_approvals(&p);
    replace.replace_managed = true;
    restarted.apply_install(&p.preview_id, replace).unwrap();
    assert_eq!(fs::read(&f.source).unwrap(), b"Updated whole file.");
}

#[test]
fn project_rule_whole_file_does_not_expand_other_tools_or_marker_sources() {
    for tool in [
        ToolId::ClaudeCode,
        ToolId::Antigravity,
        ToolId::AntigravityCli,
    ] {
        let mut f = fixture_surface_source(None, false, tool, true, false, true);
        fs::write(&f.source, "Unmarked instructions").unwrap();
        f.source_revision = f
            .local
            .prepare_open_source_preview(OpenSourceRequest {
                snapshot_id: "local".into(),
                instance_id: f.instance_id.clone(),
                view_generation: 2,
            })
            .unwrap()
            .run()
            .unwrap()
            .source_revision;
        assert_eq!(
            support_preview(&f).unwrap_err(),
            "import_rule_block_ambiguous"
        );
        assert_eq!(fs::read(&f.source).unwrap(), b"Unmarked instructions");
    }
}

#[test]
fn project_rule_block_refuses_ambiguous_unsafe_sources_without_writes() {
    use std::os::unix::fs::symlink;
    let mut f = fixture_surface_source(None, false, ToolId::Codex, true, false, true);
    let original = fs::read_to_string(&f.source).unwrap();
    let registry = fs::read(f.root.join("components/registry.yml")).unwrap();
    let begin = "<!-- BEGIN HARNESSKIT GENERATED CONTEXT -->";
    let end = "<!-- END HARNESSKIT GENERATED CONTEXT -->";
    for bad in [format!("{begin}\nno end"),
        format!("{end}\nbody\n{begin}\n"), format!("{original}{original}"),
        format!("{original}<!-- BEGIN ROUTINE-HARNESS GENERATED CONTEXT -->\nlegacy\n<!-- END ROUTINE-HARNESS GENERATED CONTEXT -->"),
        original.replace("Use the fixture.", "password: forbidden"),
        original.replace("Use the fixture.", "[dependency](../private.md)") ] {
        fs::write(&f.source, &bad).unwrap();
        f.source_revision = f.local.prepare_open_source_preview(OpenSourceRequest { snapshot_id:"local".into(), instance_id:f.instance_id.clone(), view_generation:2 }).unwrap().run().unwrap().source_revision;
        assert!(support_preview(&f).is_err());
        assert_eq!(fs::read_to_string(&f.source).unwrap(), bad);
        assert_eq!(fs::read(f.root.join("components/registry.yml")).unwrap(), registry);
    }
    let outside = f._temp.path().join("outside.md");
    fs::write(&outside, &original).unwrap();
    fs::remove_file(&f.source).unwrap();
    symlink(&outside, &f.source).unwrap();
    assert!(support_preview(&f).is_err());
    fs::remove_file(&f.source).unwrap();
    fs::hard_link(&outside, &f.source).unwrap();
    assert!(support_preview(&f).is_err());
    assert_eq!(fs::read_to_string(&outside).unwrap(), original);
    assert_eq!(
        fs::read(f.root.join("components/registry.yml")).unwrap(),
        registry
    );
}
#[test]
fn project_rule_block_lost_private_state_and_malformed_destination_fail_closed() {
    let f = fixture_surface_source(None, false, ToolId::Codex, true, false, true);
    let p = support_preview(&f).unwrap();
    let id = p.component_id.clone();
    let s = f
        .sot
        .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
        .unwrap();
    let state = managed_state_path(&f);
    let baseline = fs::read(&state).unwrap();
    let source = fs::read_to_string(&f.source).unwrap();
    let preview = || {
        f.sot.preview_install(
            &f.checkout_id,
            &s.snapshot_id,
            &format!("component:{id}"),
            "project",
            "import-source".into(),
            std::collections::BTreeSet::from(["project".into()]),
        )
    };
    for bytes in [b"corrupt".as_slice(), b"[]"] {
        fs::write(&state, bytes).unwrap();
        assert!(preview().is_err());
        assert_eq!(fs::read_to_string(&f.source).unwrap(), source);
    }
    fs::remove_file(&state).unwrap();
    assert!(preview().is_err());
    fs::write(&state, &baseline).unwrap();
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(&state, fs::Permissions::from_mode(0o600)).unwrap();
    for bad in [
        "Unmarked".to_string(),
        source.clone() + &source,
        source.replace("<!-- END HARNESSKIT GENERATED CONTEXT -->", ""),
        source.replace(
            "<!-- BEGIN HARNESSKIT GENERATED CONTEXT -->",
            "<!-- BEGIN ROUTINE-HARNESS GENERATED CONTEXT -->",
        ),
    ] {
        fs::write(&f.source, &bad).unwrap();
        assert!(preview().is_err());
        assert_eq!(fs::read_to_string(&f.source).unwrap(), bad);
        assert_eq!(fs::read(&state).unwrap(), baseline);
    }
    fs::write(&f.source, &source).unwrap();
    let links: Vec<serde_json::Value> = serde_json::from_slice(&baseline).unwrap();
    fs::write(
        &state,
        serde_json::to_vec(&vec![links[0].clone(), links[0].clone()]).unwrap(),
    )
    .unwrap();
    assert!(preview().is_err());
    fs::write(&state, &baseline).unwrap();
    fs::set_permissions(&state, fs::Permissions::from_mode(0o644)).unwrap();
    assert!(preview().is_err());
    fs::set_permissions(&state, fs::Permissions::from_mode(0o600)).unwrap();
    fs::hard_link(&state, f.private.join("state-alias.json")).unwrap();
    assert!(preview().is_err());
}

#[test]
fn project_rule_block_writer_race_and_private_failure_do_not_record_success() {
    for mode in ["source", "state"] {
        let f = fixture_surface_source(None, false, ToolId::Codex, true, false, true);
        let p = support_preview(&f).unwrap();
        let id = p.component_id.clone();
        f.sot
            .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
            .unwrap();
        let baseline = fs::read(managed_state_path(&f)).unwrap();
        let writer = crate::contexts::install::InstallWriter::with_ports(
            Arc::new(FrozenClock),
            Arc::new(ManagedFailureHook {
                source: f.source.clone(),
                state: managed_state_path(&f),
                outside: f.private.join("state-hardlink"),
                mode,
            }),
        );
        let service = SotContext::with_services(
            AppController::with_state_file(f.private.join("state.json")),
            Some(
                InstallCoordinator::new(f.private.clone(), Arc::new(RealFixtureGenerator))
                    .unwrap()
                    .with_test_writer(writer),
            ),
        )
        .unwrap();
        let s = service.load_active(&f.checkout_id).unwrap();
        let s = service
            .save_imported_skill(&f.checkout_id, &s.snapshot_id, &id, "Replacement rule.\n")
            .unwrap();
        let p = service
            .preview_install(
                &f.checkout_id,
                &s.snapshot_id,
                &format!("component:{id}"),
                "project",
                "import-source".into(),
                std::collections::BTreeSet::from(["project".into()]),
            )
            .unwrap();
        let result = service.apply_install(&p.preview_id, managed_approvals(&p));
        if mode == "state" {
            assert_eq!(result.unwrap_err(), "management_state_write_failed");
        } else {
            assert_ne!(
                result.unwrap().status,
                crate::contexts::install::InstallOperationStatus::Complete
            );
        }
        assert_eq!(fs::read(managed_state_path(&f)).unwrap(), baseline);
        assert_eq!(service.session_headers().unwrap().install_evidence_id, None);
    }
}
#[test]
fn project_rule_block_existing_context_destination_ownership_collision_is_rejected() {
    let f = fixture_surface_source(None, false, ToolId::Codex, true, false, true);
    let p = support_preview(&f).unwrap();
    let s = f
        .sot
        .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
        .unwrap();
    let original = fs::read(&f.source).unwrap();
    let result = f.sot.preview_install(
        &f.checkout_id,
        &s.snapshot_id,
        "harnesskit.profile.engineering",
        "project",
        f.source.parent().unwrap().into(),
        std::collections::BTreeSet::from(["project".into()]),
    );
    assert_eq!(result.unwrap_err(), "management_destination_collision");
    assert_eq!(fs::read(&f.source).unwrap(), original);
}

#[test]
fn project_rule_block_stale_import_and_canonical_marker_edit_are_rejected() {
    let f = fixture_surface_source(None, false, ToolId::Codex, true, false, true);
    let p = support_preview(&f).unwrap();
    let registry = fs::read(f.root.join("components/registry.yml")).unwrap();
    let original = fs::read_to_string(&f.source).unwrap();
    fs::write(
        &f.source,
        original.replace("Use the fixture.", "Changed before confirmation."),
    )
    .unwrap();
    assert!(f
        .sot
        .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
        .is_err());
    assert_eq!(
        fs::read(f.root.join("components/registry.yml")).unwrap(),
        registry
    );
    let mut f = f;
    fs::write(&f.source, &original).unwrap();
    f.source_revision = f
        .local
        .prepare_open_source_preview(OpenSourceRequest {
            snapshot_id: "local".into(),
            instance_id: f.instance_id.clone(),
            view_generation: 2,
        })
        .unwrap()
        .run()
        .unwrap()
        .source_revision;
    let p = support_preview(&f).unwrap();
    let id = p.component_id.clone();
    let s = f
        .sot
        .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
        .unwrap();
    let before = f
        .sot
        .read_imported_skill(&f.checkout_id, &s.snapshot_id, &id)
        .unwrap()
        .content;
    for bad in [
        "<!-- BEGIN HARNESSKIT GENERATED CONTEXT -->",
        "password: forbidden",
        "[link](../private.md)",
    ] {
        assert!(f
            .sot
            .save_imported_skill(&f.checkout_id, &s.snapshot_id, &id, bad)
            .is_err());
        assert_eq!(
            f.sot
                .read_imported_skill(&f.checkout_id, &s.snapshot_id, &id)
                .unwrap()
                .content,
            before
        );
        assert_eq!(fs::read_to_string(&f.source).unwrap(), original);
    }
}

#[test]
fn project_rule_block_import_edit_adopt_and_protected_reapply() {
    for tool in [
        ToolId::AntigravityCli,
        ToolId::Codex,
        ToolId::ClaudeCode,
        ToolId::Antigravity,
    ] {
        let f = fixture_surface_source(None, false, tool, true, false, true);
        let original = fs::read(&f.source).unwrap();
        let p = support_preview(&f).unwrap();
        assert_eq!(p.kind, "rule");
        assert_eq!(p.content, "Use the fixture.\n");
        assert_eq!(p.generated_artifact_count, 1);
        if tool == ToolId::AntigravityCli {
            assert_eq!(
                p.component_id,
                "harnesskit.rule.imported-agents-antigravity-cli"
            );
            let manifest: serde_yaml::Value = serde_yaml::from_str(&p.manifest).unwrap();
            assert_eq!(
                manifest["targets"]["project"]["output_path"],
                "dist/project/AGENTS.md"
            );
            assert_eq!(manifest["merge_artifacts"][0]["destination"], "AGENTS.md");
        }
        let id = p.component_id.clone();
        let mut s = f
            .sot
            .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
            .unwrap();
        assert_eq!(fs::read(&f.source).unwrap(), original);
        let detail = f
            .sot
            .read_imported_skill(&f.checkout_id, &s.snapshot_id, &id)
            .unwrap();
        assert!(!detail.managed);
        assert_eq!(detail.content, "Use the fixture.\n");
        s = f
            .sot
            .save_imported_skill(&f.checkout_id, &s.snapshot_id, &id, "Edited rule.\n")
            .unwrap();
        assert_eq!(fs::read(&f.source).unwrap(), original);
        let preview = |service: &SotContext, snapshot: &crate::contexts::sot::SotSnapshot| {
            service.preview_install(
                &f.checkout_id,
                &snapshot.snapshot_id,
                &format!("component:{id}"),
                "project",
                "import-source".into(),
                std::collections::BTreeSet::from(["project".into()]),
            )
        };
        let p = preview(&f.sot, &s).unwrap();
        assert!(p.required_approvals.management_adoption);
        let mut denied = managed_approvals(&p);
        denied.adopt_management = false;
        assert!(f.sot.apply_install(&p.preview_id, denied).is_err());
        // Outside edits after preview are not ownership conflicts and must survive.
        fs::write(
            &f.source,
            String::from_utf8(original.clone())
                .unwrap()
                .replace("Foreign before", "Changed before"),
        )
        .unwrap();
        let outcome = f
            .sot
            .apply_install(&p.preview_id, managed_approvals(&p))
            .unwrap();
        assert!(outcome
            .destinations
            .iter()
            .all(|d| d.verify_state == crate::contexts::install::DestinationVerifyState::Verified));
        assert_eq!(fs::read_to_string(&f.source).unwrap(), "Changed before\n<!-- BEGIN HARNESSKIT GENERATED CONTEXT -->\nEdited rule.\n<!-- END HARNESSKIT GENERATED CONTEXT -->\nForeign after\n");
        let restarted = SotContext::with_services(
            AppController::with_state_file(f.private.join("state.json")),
            Some(
                InstallCoordinator::new(f.private.clone(), Arc::new(RealFixtureGenerator)).unwrap(),
            ),
        )
        .unwrap();
        s = restarted.load_active(&f.checkout_id).unwrap();
        assert!(
            restarted
                .read_imported_skill(&f.checkout_id, &s.snapshot_id, &id)
                .unwrap()
                .managed
        );
        s = restarted
            .save_imported_skill(&f.checkout_id, &s.snapshot_id, &id, "Updated rule.\n")
            .unwrap();
        let p = preview(&restarted, &s).unwrap();
        assert!(!p.required_approvals.managed_replacement);
        restarted
            .apply_install(&p.preview_id, managed_approvals(&p))
            .unwrap();
        assert!(fs::read_to_string(&f.source)
            .unwrap()
            .contains("Updated rule."));
        fs::write(
            &f.source,
            fs::read_to_string(&f.source)
                .unwrap()
                .replace("Updated rule.", "External rule."),
        )
        .unwrap();
        let p = preview(&restarted, &s).unwrap();
        assert!(p.required_approvals.managed_replacement);
        assert!(restarted
            .apply_install(&p.preview_id, managed_approvals(&p))
            .is_err());
        let mut replace = managed_approvals(&p);
        replace.replace_managed = true;
        fs::write(
            &f.source,
            fs::read_to_string(&f.source)
                .unwrap()
                .replace("External rule.", "Later external rule."),
        )
        .unwrap();
        assert!(restarted.apply_install(&p.preview_id, replace).is_err());
        let p = preview(&restarted, &s).unwrap();
        let mut replace = managed_approvals(&p);
        replace.replace_managed = true;
        restarted.apply_install(&p.preview_id, replace).unwrap();
        assert!(fs::read_to_string(&f.source)
            .unwrap()
            .contains("Updated rule."));
        assert!(fs::read_to_string(&f.source)
            .unwrap()
            .contains("Changed before"));
    }
}

#[test]
fn support_documents_stale_preview_preserves_source_and_registry() {
    let f = support_fixture();
    let p = support_preview(&f).unwrap();
    let registry = fs::read(f.root.join("components/registry.yml")).unwrap();
    fs::write(
        f.source.parent().unwrap().join("references/guide.md"),
        "changed after review",
    )
    .unwrap();
    assert_eq!(
        f.sot
            .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
            .unwrap_err(),
        "import_preview_stale"
    );
    assert_eq!(
        fs::read(f.root.join("components/registry.yml")).unwrap(),
        registry
    );
    assert!(!f.root.join("components/skills/fixture-import").exists());
}
#[test]
fn support_documents_refuse_symlinks_hardlinks_executable_binary_secret_and_missing() {
    use std::os::unix::fs::{symlink, PermissionsExt};
    let f = support_fixture();
    let path = f.source.parent().unwrap().join("references/guide.md");
    let registry = fs::read(f.root.join("components/registry.yml")).unwrap();
    for bytes in [
        b"password: forbidden".as_slice(),
        b"\xff",
        b"embedded\0binary",
        b"[escape](../../outside.md)",
        b"[script](run.sh)",
        concat!("[absolute](/home/", "private.md)").as_bytes(),
        b"references/unresolved.md",
    ] {
        fs::write(&path, bytes).unwrap();
        assert!(support_preview(&f).is_err(), "must refuse unsafe document");
    }
    fs::write(&path, "Guide").unwrap();
    fs::set_permissions(&path, fs::Permissions::from_mode(0o755)).unwrap();
    assert!(support_preview(&f).is_err());
    fs::set_permissions(&path, fs::Permissions::from_mode(0o644)).unwrap();
    let outside = f._temp.path().join("outside.md");
    fs::write(&outside, "Outside").unwrap();
    fs::remove_file(&path).unwrap();
    symlink(&outside, &path).unwrap();
    assert!(support_preview(&f).is_err());
    fs::remove_file(&path).unwrap();
    fs::hard_link(&outside, &path).unwrap();
    assert!(support_preview(&f).is_err());
    fs::remove_file(&path).unwrap();
    assert!(support_preview(&f).is_err());
    assert_eq!(
        fs::read(f.root.join("components/registry.yml")).unwrap(),
        registry
    );
    assert_eq!(fs::read(outside).unwrap(), b"Outside");
}
#[test]
fn support_documents_restart_baselines_and_deleted_target_do_not_recreate() {
    let f = support_fixture();
    let p = support_preview(&f).unwrap();
    let id = p.component_id.clone();
    let s = f
        .sot
        .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
        .unwrap();
    let p = f
        .sot
        .preview_install(
            &f.checkout_id,
            &s.snapshot_id,
            &format!("component:{id}"),
            "user",
            "import-source".into(),
            std::collections::BTreeSet::from(["codex".into()]),
        )
        .unwrap();
    f.sot
        .apply_install(&p.preview_id, managed_approvals(&p))
        .unwrap();
    let restarted = SotContext::with_services(
        AppController::with_state_file(f.private.join("state.json")),
        Some(InstallCoordinator::new(f.private.clone(), Arc::new(RealFixtureGenerator)).unwrap()),
    )
    .unwrap();
    let s = restarted.load_active(&f.checkout_id).unwrap();
    let detail = restarted
        .read_imported_skill(&f.checkout_id, &s.snapshot_id, &id)
        .unwrap();
    assert!(detail.documents[0].managed);
    let path = f.source.parent().unwrap().join("references/guide.md");
    fs::remove_file(&path).unwrap();
    assert!(restarted
        .preview_install(
            &f.checkout_id,
            &s.snapshot_id,
            &format!("component:{id}"),
            "user",
            "import-source".into(),
            std::collections::BTreeSet::from(["codex".into()])
        )
        .is_err());
    assert!(!path.exists());
}

#[test]
fn support_documents_refuse_unresolved_reference_style_and_autolinks() {
    let f = support_fixture();
    let path = f.source.parent().unwrap().join("references/guide.md");
    let registry = fs::read(f.root.join("components/registry.yml")).unwrap();
    for text in [
        "[External][remote]\n\n[remote]: https://example.invalid/private.md\n",
        "[Missing][dependency]\n\n[dependency]: missing.md\n",
        "<https://example.invalid/private.md>\n",
    ] {
        fs::write(&path, text).unwrap();
        assert!(
            support_preview(&f).is_err(),
            "unsupported reference syntax must not hide a dependency: {text}"
        );
        assert_eq!(fs::read(&path).unwrap(), text.as_bytes());
        assert_eq!(
            fs::read(f.root.join("components/registry.yml")).unwrap(),
            registry
        );
    }
}

#[test]
fn support_management_records_require_one_to_one_file_links() {
    let f = support_fixture();
    let p = support_preview(&f).unwrap();
    let id = p.component_id.clone();
    let s = f
        .sot
        .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
        .unwrap();
    let path = managed_state_path(&f);
    let links: Vec<serde_json::Value> = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    let main = links
        .iter()
        .find(|l| l.get("canonical_document").is_none())
        .unwrap()
        .clone();
    let mut support = links
        .iter()
        .find(|l| l["canonical_document"] == "references/guide.md")
        .unwrap()
        .clone();
    // Keep the main link and artifact/link counts, but alias the support target.
    support["source_path"] = main["source_path"].clone();
    support["source_locator"] = main["source_locator"].clone();
    fs::write(&path, serde_json::to_vec(&vec![main, support]).unwrap()).unwrap();
    assert!(
        f.sot
            .preview_install(
                &f.checkout_id,
                &s.snapshot_id,
                &format!("component:{id}"),
                "user",
                "import-source".into(),
                std::collections::BTreeSet::from(["codex".into()]),
            )
            .is_err(),
        "aliased support links must not leave a generated file unprotected"
    );
}

#[test]
fn support_documents_do_not_hide_unselected_dependencies() {
    let f = support_fixture();
    fs::write(f.source.parent().unwrap().join("helper.sh"), "exit 0").unwrap();
    assert!(
        support_preview(&f).is_err(),
        "a selected guide cannot silently discard unknown dependencies"
    );
}

struct SupportRaceHook {
    support: PathBuf,
}
impl crate::contexts::install::InstallWriteHook for SupportRaceHook {
    fn before_replace(&self, _: &str, destination: &str) {
        if destination.ends_with("references/guide.md") {
            fs::write(&self.support, "foreign writer race").unwrap();
        }
    }
}
#[test]
fn support_partial_apply_records_only_verified_files_and_stale_review_stops() {
    let f = support_fixture();
    let p = support_preview(&f).unwrap();
    let id = p.component_id.clone();
    let s = f
        .sot
        .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
        .unwrap();
    let source = f.source.parent().unwrap().join("references/guide.md");
    let preview = |service: &SotContext, s: &crate::contexts::sot::SotSnapshot| {
        service.preview_install(
            &f.checkout_id,
            &s.snapshot_id,
            &format!("component:{id}"),
            "user",
            "import-source".into(),
            std::collections::BTreeSet::from(["codex".into()]),
        )
    };
    let p = preview(&f.sot, &s).unwrap();
    fs::write(&source, "changed after preview").unwrap();
    assert!(f
        .sot
        .apply_install(&p.preview_id, managed_approvals(&p))
        .is_err());
    fs::write(&source, "Guide\n").unwrap();
    let s = f
        .sot
        .save_imported_document(
            &f.checkout_id,
            &s.snapshot_id,
            &id,
            "Updated document",
            Some("references/guide.md"),
        )
        .unwrap();
    let writer = crate::contexts::install::InstallWriter::with_ports(
        Arc::new(FrozenClock),
        Arc::new(SupportRaceHook {
            support: source.clone(),
        }),
    );
    let service = SotContext::with_services(
        AppController::with_state_file(f.private.join("state.json")),
        Some(
            InstallCoordinator::new(f.private.clone(), Arc::new(RealFixtureGenerator))
                .unwrap()
                .with_test_writer(writer),
        ),
    )
    .unwrap();
    let s = service.load_active(&f.checkout_id).unwrap();
    let p = preview(&service, &s).unwrap();
    let result = service
        .apply_install(&p.preview_id, managed_approvals(&p))
        .unwrap();
    assert_eq!(
        result.status,
        crate::contexts::install::InstallOperationStatus::Partial
    );
    assert_eq!(fs::read(&source).unwrap(), b"foreign writer race");
    let state: Vec<serde_json::Value> =
        serde_json::from_slice(&fs::read(managed_state_path(&f)).unwrap()).unwrap();
    let main = state
        .iter()
        .find(|l| l["component_id"] == id && l.get("canonical_document").is_none())
        .unwrap();
    let support = state
        .iter()
        .find(|l| l["canonical_document"] == "references/guide.md")
        .unwrap();
    assert_eq!(main["managed"], true);
    assert_eq!(support["managed"], false);
    assert!(support.get("last_applied_sha256").is_none());
}

#[test]
fn support_document_revision_binds_parent_lineage() {
    let temp = TempDir::new().unwrap();
    // macOS may expose the temporary root through /var -> /private/var.
    // Give the strict reader a real directory path, not that system alias.
    let root = temp.path().canonicalize().unwrap().join("skill");
    fs::create_dir_all(root.join("references/nested")).unwrap();
    fs::write(root.join("references/nested/guide.md"), "Guide").unwrap();
    let first = crate::contexts::install::writer::capture_import_document(
        &root,
        "references/nested/guide.md",
    )
    .unwrap();
    fs::rename(root.join("references"), root.join("old")).unwrap();
    fs::create_dir(root.join("references")).unwrap();
    fs::rename(root.join("old/nested"), root.join("references/nested")).unwrap();
    let second = crate::contexts::install::writer::capture_import_document(
        &root,
        "references/nested/guide.md",
    )
    .unwrap();
    assert_ne!(
        first.1, second.1,
        "same inode and bytes cannot authorize changed parent lineage"
    );
}

#[test]
fn support_document_edit_command_accepts_only_registered_selection() {
    let request = serde_json::json!({"checkoutId":"fixture", "sotSnapshotId":"snapshot", "componentId":"harnesskit.skill.fixture-import", "content":"changed", "document":"references/guide.md"});
    assert!(
        serde_json::from_value::<crate::contexts::sot::import::ImportedSkillEdit>(request).is_ok(),
        "existing editor command needs explicit document selection"
    );
}

#[test]
fn support_documents_import_generate_adopt_and_protect_each_file() {
    for (tool, project, target) in [
        (ToolId::Codex, false, "codex"),
        (ToolId::Codex, true, "codex"),
        (ToolId::ClaudeCode, false, "claude"),
        (ToolId::ClaudeCode, true, "claude"),
        (ToolId::Antigravity, false, "antigravity"),
        (ToolId::Antigravity, true, "antigravity"),
        (ToolId::AntigravityCli, true, "antigravity-cli"),
    ] {
        check_support_documents(tool, project, target, false);
    }
}

#[test]
fn skill_metadata_invalid_unknown_sensitive_and_unresolved_fail_without_writes() {
    let f = fixture_tool_source(None, false, ToolId::ClaudeCode, false);
    let registry = fs::read(f.root.join("components/registry.yml")).unwrap();
    for (extra, expected) in [
        ("user-invocable: 'false'", "import_skill_option_invalid"),
        ("metadata: {version: 1}", "import_skill_option_invalid"),
        ("allowed-tools: [Read, 3]", "import_skill_option_invalid"),
        ("effort: extreme", "import_skill_option_invalid"),
        (
            "context: fork\nagent: missing-agent",
            "import_skill_dependency_unresolved",
        ),
        ("agent: Explore", "import_skill_option_invalid"),
        ("background: false", "import_skill_option_invalid"),
        ("hooks: {}", "import_frontmatter_unsupported"),
        ("unknown-operation: true", "import_frontmatter_unsupported"),
        (
            "metadata: {password: secret-value}",
            "import_sensitive_or_dependency_content",
        ),
        (
            "paths: ['../outside/**']",
            "import_sensitive_or_dependency_content",
        ),
        (
            "license: references/missing.md",
            "import_sensitive_or_dependency_content",
        ),
    ] {
        let content =
            format!("---\nname: fixture-import\ndescription: Fixture\n{extra}\n---\nBody\n");
        fs::write(&f.source, &content).unwrap();
        let revision = f
            .local
            .prepare_open_source_preview(OpenSourceRequest {
                snapshot_id: "local".into(),
                instance_id: f.instance_id.clone(),
                view_generation: 2,
            })
            .unwrap()
            .run()
            .unwrap()
            .source_revision;
        assert_eq!(
            f.sot
                .preview_component_import(
                    &f.local,
                    &f.checkout_id,
                    &f.sot_id,
                    "local",
                    &f.instance_id,
                    &revision
                )
                .unwrap_err(),
            expected,
            "{extra}"
        );
        assert_eq!(fs::read_to_string(&f.source).unwrap(), content);
        assert_eq!(
            fs::read(f.root.join("components/registry.yml")).unwrap(),
            registry
        );
        assert!(!f.root.join("components/skills/fixture-import").exists());
    }
}

#[test]
fn skill_metadata_import_generate_edit_adopt_and_protect_each_file() {
    for (tool, project, target) in [
        (ToolId::Codex, false, "codex"),
        (ToolId::Codex, true, "codex"),
        (ToolId::ClaudeCode, false, "claude"),
        (ToolId::ClaudeCode, true, "claude"),
        (ToolId::Antigravity, false, "antigravity"),
        (ToolId::Antigravity, true, "antigravity"),
        (ToolId::AntigravityCli, true, "antigravity-cli"),
    ] {
        check_support_documents(tool, project, target, true);
    }
}
fn check_support_documents(tool: ToolId, project: bool, target: &str, metadata: bool) {
    let f = fixture_tool_source(None, false, tool, project);
    let docs = f.source.parent().unwrap().join("references");
    fs::create_dir(&docs).unwrap();
    let original = b"# Guide\r\nUTF-8: \xe2\x9c\x93  \r\nlast  ";
    fs::write(docs.join("guide.md"), original).unwrap();
    let mut skill = fs::read_to_string(&f.source).unwrap() + "[Guide](references/guide.md)\n";
    let extras = if metadata {
        let mut extra = "license: MIT\ncompatibility: Requires git\nmetadata:\n  author: fixture-org\n  version: \"1.0\"\nallowed-tools: Read Grep\n".to_string();
        if tool == ToolId::ClaudeCode {
            extra.push_str("argument-hint: '[issue-number]'\narguments: [issue, format]\ndisable-model-invocation: true\nuser-invocable: false\nmodel: sonnet\neffort: high\ncontext: fork\nagent: Explore\nbackground: false\nwhen_to_use: Review fixture changes\ndisallowed-tools: [AskUserQuestion]\npaths: ['src/**', '*.rs']\nshell: bash\n");
        }
        skill = skill.replacen("\n---\n", &format!("\n{extra}---\n"), 1);
        Some(serde_yaml::from_str::<serde_yaml::Value>(&extra).unwrap())
    } else {
        None
    };
    let assert_metadata = || {
        if let Some(expected) = &extras {
            let generated = fs::read_to_string(&f.source).unwrap();
            let front: serde_yaml::Value =
                serde_yaml::from_str(generated.split("---").nth(1).unwrap()).unwrap();
            for (key, value) in expected.as_mapping().unwrap() {
                assert_eq!(&front[key], value, "{target}/{project}: {key:?}");
            }
        }
    };
    fs::write(&f.source, &skill).unwrap();
    let revision = f
        .local
        .prepare_open_source_preview(OpenSourceRequest {
            snapshot_id: "local".into(),
            instance_id: f.instance_id.clone(),
            view_generation: 2,
        })
        .unwrap()
        .run()
        .unwrap()
        .source_revision;
    let p = f.sot.preview_component_import(
        &f.local,
        &f.checkout_id,
        &f.sot_id,
        "local",
        &f.instance_id,
        &revision,
    );
    assert!(
        p.is_ok(),
        "referenced non-executable documents must import: {:?}",
        p.err()
    );
    let p = p.unwrap();
    assert_eq!(p.generated_artifact_count, 4);
    let id = p.component_id.clone();
    let s = f
        .sot
        .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
        .unwrap();
    assert_eq!(fs::read(&f.source).unwrap(), skill.as_bytes());
    assert_eq!(fs::read(docs.join("guide.md")).unwrap(), original);
    let canonical = f
        .root
        .join("components/skills/fixture-import/references/guide.md");
    assert_eq!(fs::read(&canonical).unwrap(), original);
    let preview = |s: &crate::contexts::sot::SotSnapshot| {
        f.sot.preview_install(
            &f.checkout_id,
            &s.snapshot_id,
            &format!("component:{id}"),
            if project { "project" } else { "user" },
            "import-source".into(),
            std::collections::BTreeSet::from([target.into()]),
        )
    };
    let p = preview(&s).unwrap();
    assert!(p.required_approvals.management_adoption);
    assert!(p.warnings.iter().any(|w| w.contains("references/guide.md")));
    let result = f
        .sot
        .apply_install(&p.preview_id, managed_approvals(&p))
        .unwrap();
    assert_eq!(
        result.status,
        crate::contexts::install::InstallOperationStatus::Complete
    );
    assert_eq!(fs::read(docs.join("guide.md")).unwrap(), original);
    assert_metadata();
    let detail = f
        .sot
        .read_imported_skill(&f.checkout_id, &s.snapshot_id, &id)
        .unwrap();
    assert!(
        serde_json::to_value(&detail).unwrap()["documents"].is_array(),
        "editor must expose selected registered documents"
    );
    if metadata {
        let before = fs::read(
            f.root
                .join("components/skills/fixture-import/component.yml"),
        )
        .unwrap();
        assert_eq!(
            f.sot
                .save_imported_skill(
                    &f.checkout_id,
                    &s.snapshot_id,
                    &id,
                    &detail
                        .content
                        .replace("license: MIT", "license: Apache-2.0")
                )
                .unwrap_err(),
            "import_skill_metadata_edit_unsupported"
        );
        assert_eq!(
            fs::read(
                f.root
                    .join("components/skills/fixture-import/component.yml")
            )
            .unwrap(),
            before
        );
    }
    let s = f
        .sot
        .save_imported_skill(
            &f.checkout_id,
            &s.snapshot_id,
            &id,
            &detail
                .content
                .replace("Use the fixture.", "Updated fixture."),
        )
        .expect("main editor retains approved references");
    assert!(f
        .sot
        .save_imported_document(
            &f.checkout_id,
            &s.snapshot_id,
            &id,
            "password: forbidden",
            Some("references/guide.md")
        )
        .is_err());
    assert!(f
        .sot
        .save_imported_document(
            &f.checkout_id,
            &s.snapshot_id,
            &id,
            "foreign",
            Some("../foreign.md")
        )
        .is_err());
    let s = f
        .sot
        .save_imported_document(
            &f.checkout_id,
            &s.snapshot_id,
            &id,
            "# Updated\r\nNo final newline  ",
            Some("references/guide.md"),
        )
        .unwrap();
    assert_eq!(
        fs::read(&canonical).unwrap(),
        b"# Updated\r\nNo final newline  "
    );
    assert_eq!(fs::read(docs.join("guide.md")).unwrap(), original);
    let p = preview(&s).unwrap();
    assert!(!p.required_approvals.managed_replacement);
    f.sot
        .apply_install(&p.preview_id, managed_approvals(&p))
        .unwrap();
    assert_eq!(
        fs::read(docs.join("guide.md")).unwrap(),
        b"# Updated\r\nNo final newline  "
    );
    assert_metadata();
    fs::write(docs.join("guide.md"), b"external edit").unwrap();
    let p = preview(&s).unwrap();
    assert!(p.required_approvals.managed_replacement);
    assert!(f
        .sot
        .apply_install(&p.preview_id, managed_approvals(&p))
        .is_err());
    assert_eq!(fs::read(docs.join("guide.md")).unwrap(), b"external edit");
    let p = preview(&s).unwrap();
    let mut approvals = managed_approvals(&p);
    approvals.replace_managed = true;
    f.sot.apply_install(&p.preview_id, approvals).unwrap();
    assert_eq!(
        fs::read(docs.join("guide.md")).unwrap(),
        b"# Updated\r\nNo final newline  "
    );
    assert_metadata();
    if metadata {
        let restarted = SotContext::with_services(
            AppController::with_state_file(f.private.join("state.json")),
            Some(
                InstallCoordinator::new(f.private.clone(), Arc::new(RealFixtureGenerator)).unwrap(),
            ),
        )
        .unwrap();
        let preview = |service: &SotContext| {
            let s = service.load_active(&f.checkout_id).unwrap();
            service
                .preview_install(
                    &f.checkout_id,
                    &s.snapshot_id,
                    &format!("component:{id}"),
                    if project { "project" } else { "user" },
                    "import-source".into(),
                    std::collections::BTreeSet::from([target.into()]),
                )
                .unwrap()
        };
        assert!(!preview(&restarted).required_approvals.managed_replacement);
        let generated = fs::read_to_string(&f.source).unwrap();
        fs::write(
            &f.source,
            generated.replace("license: MIT", "license: Apache-2.0"),
        )
        .unwrap();
        let p = preview(&restarted);
        assert!(p.required_approvals.managed_replacement);
        assert!(restarted
            .apply_install(&p.preview_id, managed_approvals(&p))
            .is_err());
        fs::write(&f.source, "changed after metadata review\n").unwrap();
        let mut a = managed_approvals(&p);
        a.replace_managed = true;
        assert!(restarted.apply_install(&p.preview_id, a).is_err());
        let p = preview(&restarted);
        let mut a = managed_approvals(&p);
        a.replace_managed = true;
        restarted.apply_install(&p.preview_id, a).unwrap();
        assert_metadata();
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            fs::metadata(managed_state_path(&f))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
        assert_eq!(
            fs::metadata(managed_state_path(&f).parent().unwrap())
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o700
        );
    }
}

#[test]
fn shared_hook_import_adopt_update_preserves_unowned_settings() {
    check_shared_hook_import(false);
}
#[test]
fn project_shared_hook_import_adopt_update_preserves_unowned_settings() {
    check_shared_hook_import(true);
}
fn check_shared_hook_import(project: bool) {
    let f = fixture_tool_source(None, true, ToolId::ClaudeCode, project);
    let scope = if project { "project" } else { "user" };
    let before = fs::read(&f.source).unwrap();
    let p = f
        .sot
        .preview_component_import(
            &f.local,
            &f.checkout_id,
            &f.sot_id,
            "local",
            &f.instance_id,
            &f.source_revision,
        )
        .expect("shared-setting hook import");
    assert_eq!(p.kind, "hook");
    assert_eq!(p.generated_artifact_count, 1);
    let id = p.component_id.clone();
    let snapshot = f
        .sot
        .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
        .unwrap();
    assert_eq!(fs::read(&f.source).unwrap(), before);
    assert!(snapshot
        .components
        .iter()
        .any(|c| c.component_id == id && c.kind == "hook"));
    let preview = f
        .sot
        .preview_install(
            &f.checkout_id,
            &snapshot.snapshot_id,
            &format!("component:{id}"),
            scope,
            "import-source".into(),
            std::collections::BTreeSet::from(["claude".into()]),
        )
        .unwrap();
    assert!(preview.required_approvals.management_adoption);
    let mut approvals = managed_approvals(&preview);
    approvals.allow_runtime_hooks = true;
    f.sot.apply_install(&preview.preview_id, approvals).unwrap();
    let detail = f
        .sot
        .read_imported_skill(&f.checkout_id, &snapshot.snapshot_id, &id)
        .unwrap();
    let changed = detail.content.replace("printf fixture", "printf updated");
    let snapshot = f
        .sot
        .save_imported_skill(&f.checkout_id, &snapshot.snapshot_id, &id, &changed)
        .unwrap();
    let mut external: serde_json::Value =
        serde_json::from_slice(&fs::read(&f.source).unwrap()).unwrap();
    external["theme"] = "changed outside".into();
    fs::write(&f.source, serde_json::to_vec(&external).unwrap()).unwrap();
    let preview = f
        .sot
        .preview_install(
            &f.checkout_id,
            &snapshot.snapshot_id,
            &format!("component:{id}"),
            scope,
            "import-source".into(),
            std::collections::BTreeSet::from(["claude".into()]),
        )
        .unwrap();
    assert!(!preview.required_approvals.managed_replacement);
    let mut approvals = managed_approvals(&preview);
    approvals.allow_runtime_hooks = true;
    let outcome = f.sot.apply_install(&preview.preview_id, approvals).unwrap();
    assert_eq!(
        outcome.status,
        crate::contexts::install::InstallOperationStatus::Complete
    );
    let after: serde_json::Value = serde_json::from_slice(&fs::read(&f.source).unwrap()).unwrap();
    assert_eq!(after["theme"], "changed outside");
    assert_eq!(
        after["hooks"]["SessionStart"],
        external["hooks"]["SessionStart"]
    );
    assert_eq!(after["hooks"]["Stop"][0]["unknown"], "keep");
    assert_eq!(after["hooks"]["Stop"][0]["hooks"][0]["foreign"], "keep");
    assert_eq!(
        after["hooks"]["Stop"][0]["hooks"][0]["command"],
        "printf updated"
    );
}

#[test]
fn shared_hook_external_edits_replacement_restart_and_ambiguity_fail_closed() {
    let f = fixture_source(None, true);
    let p = f
        .sot
        .preview_component_import(
            &f.local,
            &f.checkout_id,
            &f.sot_id,
            "local",
            &f.instance_id,
            &f.source_revision,
        )
        .unwrap();
    let id = p.component_id.clone();
    let snapshot = f
        .sot
        .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
        .unwrap();
    let preview = |service: &SotContext| {
        let s = service.load_active(&f.checkout_id).unwrap();
        service.preview_install(
            &f.checkout_id,
            &s.snapshot_id,
            &format!("component:{id}"),
            "user",
            "import-source".into(),
            std::collections::BTreeSet::from(["claude".into()]),
        )
    };
    let p = preview(&f.sot).unwrap();
    let mut outside: serde_json::Value =
        serde_json::from_slice(&fs::read(&f.source).unwrap()).unwrap();
    outside["theme"] = "outside after preview".into();
    outside["hooks"]["Stop"][0]["unknown"] = "outside after preview".into();
    fs::write(&f.source, serde_json::to_vec(&outside).unwrap()).unwrap();
    assert!(p.required_approvals.runtime_hooks);
    assert_eq!(
        f.sot
            .apply_install(&p.preview_id, managed_approvals(&p))
            .unwrap_err(),
        "runtime_hook_approval_required"
    );
    let mut a = managed_approvals(&p);
    a.allow_runtime_hooks = true;
    f.sot.apply_install(&p.preview_id, a).unwrap();
    let restarted = SotContext::with_services(
        AppController::with_state_file(f.private.join("state.json")),
        Some(InstallCoordinator::new(f.private.clone(), Arc::new(RealFixtureGenerator)).unwrap()),
    )
    .unwrap();
    assert!(
        !preview(&restarted)
            .unwrap()
            .required_approvals
            .managed_replacement
    );
    let original_baseline: serde_json::Value =
        serde_json::from_slice(&fs::read(managed_state_path(&f)).unwrap()).unwrap();
    let original: serde_json::Value =
        serde_json::from_slice(&fs::read(&f.source).unwrap()).unwrap();
    let mut external = original.clone();
    external["hooks"]["Stop"][0]["hooks"][0]["command"] = "printf external".into();
    fs::write(&f.source, serde_json::to_vec(&external).unwrap()).unwrap();
    let p = preview(&restarted).unwrap();
    assert!(p.required_approvals.managed_replacement);
    let mut a = managed_approvals(&p);
    a.allow_runtime_hooks = true;
    assert_eq!(
        restarted
            .apply_install(&p.preview_id, a.clone())
            .unwrap_err(),
        "managed_replacement_approval_required"
    );
    assert_eq!(
        serde_json::from_slice::<serde_json::Value>(&fs::read(managed_state_path(&f)).unwrap())
            .unwrap(),
        original_baseline
    );
    a.replace_managed = true;
    external["hooks"]["Stop"][0]["hooks"][0]["command"] = "printf later".into();
    fs::write(&f.source, serde_json::to_vec(&external).unwrap()).unwrap();
    assert_eq!(
        restarted.apply_install(&p.preview_id, a).unwrap_err(),
        "preview_stale"
    );
    let p = preview(&restarted).unwrap();
    let mut a = managed_approvals(&p);
    a.allow_runtime_hooks = true;
    a.replace_managed = true;
    restarted.apply_install(&p.preview_id, a).unwrap();
    let after: serde_json::Value = serde_json::from_slice(&fs::read(&f.source).unwrap()).unwrap();
    assert_eq!(after, original);
    let baseline = fs::read(managed_state_path(&f)).unwrap();
    for case in [
        "duplicate-group",
        "duplicate-handler",
        "missing-event",
        "missing-handler",
        "reordered",
    ] {
        let mut v = original.clone();
        match case {
            "duplicate-group" => {
                let group = v["hooks"]["Stop"][0].clone();
                v["hooks"]["Stop"].as_array_mut().unwrap().push(group);
            }
            "duplicate-handler" => {
                let h = v["hooks"]["Stop"][0]["hooks"][0].clone();
                v["hooks"]["Stop"][0]["hooks"]
                    .as_array_mut()
                    .unwrap()
                    .push(h);
            }
            "missing-event" => {
                v["hooks"].as_object_mut().unwrap().remove("Stop");
            }
            "missing-handler" => v["hooks"]["Stop"][0]["hooks"] = serde_json::json!([]),
            _ => {
                let foreign = serde_json::json!({"hooks":[{"type":"command","command":"printf foreign","timeout":10}]});
                v["hooks"]["Stop"]
                    .as_array_mut()
                    .unwrap()
                    .insert(0, foreign);
            }
        }
        fs::write(&f.source, serde_json::to_vec(&v).unwrap()).unwrap();
        assert_eq!(
            preview(&restarted).unwrap_err(),
            "management_item_ambiguous",
            "{case}"
        );
        assert_eq!(
            fs::read(managed_state_path(&f)).unwrap(),
            baseline,
            "{case}"
        );
    }
    let _ = snapshot;
}

#[test]
fn shared_hook_canonical_blocks_unresolved_script_dependencies() {
    let f = fixture_source(None, true);
    let p = f
        .sot
        .preview_component_import(
            &f.local,
            &f.checkout_id,
            &f.sot_id,
            "local",
            &f.instance_id,
            &f.source_revision,
        )
        .unwrap();
    let s = f
        .sot
        .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
        .unwrap();
    assert!(f
        .sot
        .save_imported_skill(
            &f.checkout_id,
            &s.snapshot_id,
            &p.component_id,
            r#"{"type":"command","command":"node scripts/missing.js","timeout":10}"#
        )
        .is_err());
}

#[test]
fn shared_hook_import_rejects_ambiguous_missing_and_registration_conflicts() {
    let f = fixture_source(None, true);
    let original = fs::read(&f.source).unwrap();
    let source_revision = f.source_revision.clone();
    for value in [
        serde_json::json!({"hooks":{"Stop":[{"hooks":[{"type":"command","command":"printf fixture","timeout":10},{"type":"command","command":"printf other","timeout":10}]}]}}),
        serde_json::json!({"hooks":{"Stop":[]}}),
    ] {
        fs::write(&f.source, serde_json::to_vec(&value).unwrap()).unwrap();
        assert!(f
            .sot
            .preview_component_import(
                &f.local,
                &f.checkout_id,
                &f.sot_id,
                "local",
                &f.instance_id,
                &source_revision
            )
            .is_err());
    }
    fs::write(&f.source, &original).unwrap();
    // The original preview revision includes file identity, not only restored bytes.
    let fresh = f
        .local
        .prepare_open_source_preview(OpenSourceRequest {
            snapshot_id: "local".into(),
            instance_id: f.instance_id.clone(),
            view_generation: 2,
        })
        .unwrap()
        .run()
        .unwrap();
    let source_revision = fresh.source_revision;
    let p = f
        .sot
        .preview_component_import(
            &f.local,
            &f.checkout_id,
            &f.sot_id,
            "local",
            &f.instance_id,
            &source_revision,
        )
        .unwrap();
    let s = f
        .sot
        .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
        .unwrap();
    assert_eq!(
        f.sot
            .preview_component_import(
                &f.local,
                &f.checkout_id,
                &s.snapshot_id,
                "local",
                &f.instance_id,
                &source_revision
            )
            .unwrap_err(),
        "import_collision"
    );
    let manifest =
        fs::read_to_string(f.root.join("components/hooks/imported-stop/component.yml")).unwrap();
    assert!(!manifest.contains(f.source.to_str().unwrap()));
    assert!(!manifest.contains("config_entry_locator"));
    assert_eq!(fs::read(&f.source).unwrap(), original);
}

#[test]
fn imported_standalone_skill_can_preview_without_changing_profiles() {
    let f = fixture();
    let before = fs::read(f.root.join("profiles/engineering.yml")).unwrap();
    let candidate = f
        .sot
        .preview_component_import(
            &f.local,
            &f.checkout_id,
            &f.sot_id,
            "local",
            &f.instance_id,
            &f.source_revision,
        )
        .unwrap();
    f.sot
        .confirm_component_import(
            &f.local,
            &candidate.preview_id,
            &candidate.fingerprint,
            true,
        )
        .unwrap();
    let current = f.sot.load_active(&f.checkout_id).unwrap();
    let detail = f
        .sot
        .read_imported_skill(
            &f.checkout_id,
            &current.snapshot_id,
            "harnesskit.skill.fixture-import",
        )
        .unwrap();
    assert!(detail.content.contains("Use the fixture."));
    assert!(!detail.managed);
    let changed = "---\nname: fixture-import\ndescription: Fixture skill import\n---\n# Changed canonical\nExplicit apply only.\n";
    let edited = f
        .sot
        .save_imported_skill(
            &f.checkout_id,
            &current.snapshot_id,
            "harnesskit.skill.fixture-import",
            changed,
        )
        .unwrap();
    assert!(edited
        .components
        .iter()
        .any(|c| c.component_id == "harnesskit.skill.fixture-import"));
    assert!(fs::read_to_string(&f.source)
        .unwrap()
        .contains("Use the fixture."));
    let snapshot = f.sot.load_active(&f.checkout_id).unwrap();
    let bad_path = f
        .private
        .join("component-import-state")
        .join(format!("component-imports-{}.json", f.checkout_id));
    let valid = fs::read(&bad_path).unwrap();
    let outside = f.private.join("hardlinked-record.json");
    fs::hard_link(&bad_path, &outside).unwrap();
    assert!(
        f.sot
            .preview_install(
                &f.checkout_id,
                &snapshot.snapshot_id,
                "component:harnesskit.skill.fixture-import",
                "user",
                PathBuf::from("import-source"),
                std::collections::BTreeSet::from(["codex".into()])
            )
            .is_err(),
        "hardlinked private records must fail closed"
    );
    fs::remove_file(&outside).unwrap();
    fs::write(&bad_path, &valid).unwrap();
    let preview = f
        .sot
        .preview_install(
            &f.checkout_id,
            &snapshot.snapshot_id,
            "component:harnesskit.skill.fixture-import",
            "user",
            PathBuf::from("import-source"),
            std::collections::BTreeSet::from(["codex".into()]),
        )
        .expect("standalone imported component selection");
    assert_eq!(preview.components, vec!["harnesskit.skill.fixture-import"]);
    assert_eq!(preview.artifacts.len(), 1);
    assert_eq!(
        preview.target_root, "import-source",
        "frontend approval binds the handle, not a disclosed absolute path"
    );
    assert!(preview.required_approvals.management_adoption);
    assert!(preview
        .warnings
        .iter()
        .any(|w| w.contains("Use the fixture.") && w.contains("Changed canonical")));
    let approvals = crate::contexts::install::InstallApprovals {
        confirmed: true,
        semantic_fingerprint: preview.fingerprint.clone(),
        overwrite: true,
        allow_runtime_hooks: false,
        replace_managed: false,
        adopt_management: false,
    };
    assert_eq!(
        f.sot
            .apply_install(&preview.preview_id, approvals.clone())
            .unwrap_err(),
        "management_adoption_approval_required"
    );
    let outcome = f
        .sot
        .apply_install(
            &preview.preview_id,
            crate::contexts::install::InstallApprovals {
                replace_managed: false,
                adopt_management: true,
                ..approvals
            },
        )
        .unwrap();
    assert_eq!(
        outcome.status,
        crate::contexts::install::InstallOperationStatus::Complete
    );
    assert!(fs::read_to_string(&f.source)
        .unwrap()
        .contains("Changed canonical"));
    let path = f
        .private
        .join("component-import-state")
        .join(format!("component-imports-{}.json", f.checkout_id));
    let links: serde_json::Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    assert_eq!(links[0]["managed"], true);
    assert_eq!(
        links[0]["last_applied_sha256"],
        format!(
            "{:x}",
            <sha2::Sha256 as sha2::Digest>::digest(fs::read(&f.source).unwrap())
        )
    );
    let restarted = SotContext::with_services(
        AppController::with_state_file(f.private.join("state.json")),
        Some(InstallCoordinator::new(f.private.clone(), Arc::new(RealFixtureGenerator)).unwrap()),
    )
    .unwrap();
    let snapshot = restarted.load_active(&f.checkout_id).unwrap();
    let normal = restarted
        .preview_install(
            &f.checkout_id,
            &snapshot.snapshot_id,
            "component:harnesskit.skill.fixture-import",
            "user",
            PathBuf::from("import-source"),
            std::collections::BTreeSet::from(["codex".into()]),
        )
        .expect("managed source matching the verified baseline can reapply after restart");
    assert!(!normal.required_approvals.management_adoption);
    assert_eq!(
        fs::read(f.root.join("profiles/engineering.yml")).unwrap(),
        before
    );
    let detail = restarted
        .read_imported_skill(
            &f.checkout_id,
            &snapshot.snapshot_id,
            "harnesskit.skill.fixture-import",
        )
        .unwrap();
    assert!(detail.managed);
    assert_eq!(
        detail.source_locator,
        ".codex/skills/fixture-import/SKILL.md"
    );
    assert!(detail.content.contains("Changed canonical"));
    use std::os::unix::fs::PermissionsExt;
    assert_eq!(
        fs::metadata(path.parent().unwrap())
            .unwrap()
            .permissions()
            .mode()
            & 0o777,
        0o700
    );
    assert_eq!(
        fs::metadata(&path).unwrap().permissions().mode() & 0o777,
        0o600
    );
    let baseline = fs::read(&path).unwrap();
    let next = changed.replace("Changed canonical", "Next canonical, never applied");
    restarted
        .save_imported_skill(
            &f.checkout_id,
            &snapshot.snapshot_id,
            "harnesskit.skill.fixture-import",
            &next,
        )
        .unwrap();
    assert_eq!(
        fs::read(&path).unwrap(),
        baseline,
        "canonical edit is not applied evidence"
    );
    assert!(!fs::read_to_string(&f.source)
        .unwrap()
        .contains("Next canonical"));
}

fn managed_fixture() -> Fixture {
    let f = fixture();
    let candidate = f
        .sot
        .preview_component_import(
            &f.local,
            &f.checkout_id,
            &f.sot_id,
            "local",
            &f.instance_id,
            &f.source_revision,
        )
        .unwrap();
    f.sot
        .confirm_component_import(
            &f.local,
            &candidate.preview_id,
            &candidate.fingerprint,
            true,
        )
        .unwrap();
    let preview = managed_preview(&f, &f.sot);
    f.sot
        .apply_install(&preview.preview_id, managed_approvals(&preview))
        .unwrap();
    f
}
fn managed_preview(f: &Fixture, service: &SotContext) -> crate::contexts::install::InstallPreview {
    let snapshot = service.load_active(&f.checkout_id).unwrap();
    service
        .preview_install(
            &f.checkout_id,
            &snapshot.snapshot_id,
            "component:harnesskit.skill.fixture-import",
            "user",
            "import-source".into(),
            std::collections::BTreeSet::from(["codex".into()]),
        )
        .unwrap()
}
fn managed_approvals(
    p: &crate::contexts::install::InstallPreview,
) -> crate::contexts::install::InstallApprovals {
    crate::contexts::install::InstallApprovals {
        confirmed: true,
        semantic_fingerprint: p.fingerprint.clone(),
        overwrite: true,
        replace_managed: false,
        adopt_management: true,
        allow_runtime_hooks: false,
    }
}
fn managed_state_path(f: &Fixture) -> PathBuf {
    f.private
        .join("component-import-state")
        .join(format!("component-imports-{}.json", f.checkout_id))
}
#[test]
fn managed_external_edit_stops_with_diff_without_replacement_approval() {
    let f = managed_fixture();
    let baseline = fs::read(managed_state_path(&f)).unwrap();
    fs::write(
        &f.source,
        "<script>external()</script>\nExternal source changed\n",
    )
    .unwrap();
    let preview = managed_preview(&f, &f.sot);
    assert!(
        preview.warnings.iter().any(|w| w.contains("외부 수정")
            && w.contains("-<script>external()</script>")
            && w.contains("+")),
        "external review must display a diff, not treat this as first adoption"
    );
    assert_eq!(
        f.sot
            .apply_install(&preview.preview_id, managed_approvals(&preview))
            .unwrap_err(),
        "managed_replacement_approval_required"
    );
    assert_eq!(fs::read(managed_state_path(&f)).unwrap(), baseline);
    assert!(fs::read_to_string(&f.source)
        .unwrap()
        .contains("external()"));
}

#[test]
fn imported_adoption_fails_closed_for_missing_corrupt_records_and_stale_source() {
    let f = fixture();
    let candidate = f
        .sot
        .preview_component_import(
            &f.local,
            &f.checkout_id,
            &f.sot_id,
            "local",
            &f.instance_id,
            &f.source_revision,
        )
        .unwrap();
    f.sot
        .confirm_component_import(
            &f.local,
            &candidate.preview_id,
            &candidate.fingerprint,
            true,
        )
        .unwrap();
    let snapshot = f.sot.load_active(&f.checkout_id).unwrap();
    let path = f
        .private
        .join("component-import-state")
        .join(format!("component-imports-{}.json", f.checkout_id));
    let before = fs::read(&path).unwrap();
    let mut malformed: serde_json::Value = serde_json::from_slice(&before).unwrap();
    malformed[0]["content_sha256"] = "invalid".into();
    fs::write(&path, serde_json::to_vec(&malformed).unwrap()).unwrap();
    assert!(
        f.sot
            .preview_install(
                &f.checkout_id,
                &snapshot.snapshot_id,
                "component:harnesskit.skill.fixture-import",
                "user",
                "import-source".into(),
                std::collections::BTreeSet::from(["codex".into()])
            )
            .is_err(),
        "invalid import evidence must fail closed"
    );
    for broken in [
        b"not json".as_slice(),
        b"[]".as_slice(),
        b"[{\"component_id\":\"harnesskit.skill.fixture-import\"}]".as_slice(),
    ] {
        fs::write(&path, broken).unwrap();
        assert!(f
            .sot
            .preview_install(
                &f.checkout_id,
                &snapshot.snapshot_id,
                "component:harnesskit.skill.fixture-import",
                "user",
                "import-source".into(),
                std::collections::BTreeSet::from(["codex".into()])
            )
            .is_err());
    }
    fs::remove_file(&path).unwrap();
    assert!(f
        .sot
        .preview_install(
            &f.checkout_id,
            &snapshot.snapshot_id,
            "component:harnesskit.skill.fixture-import",
            "user",
            "import-source".into(),
            std::collections::BTreeSet::from(["codex".into()])
        )
        .is_err());
    fs::write(&path, &before).unwrap();
    use std::os::unix::fs::PermissionsExt;
    fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
    let preview = f
        .sot
        .preview_install(
            &f.checkout_id,
            &snapshot.snapshot_id,
            "component:harnesskit.skill.fixture-import",
            "user",
            "import-source".into(),
            std::collections::BTreeSet::from(["codex".into()]),
        )
        .unwrap();
    fs::write(&f.source, b"external change after review").unwrap();
    assert_eq!(
        f.sot
            .apply_install(
                &preview.preview_id,
                crate::contexts::install::InstallApprovals {
                    confirmed: true,
                    semantic_fingerprint: preview.fingerprint,
                    overwrite: true,
                    allow_runtime_hooks: false,
                    replace_managed: false,
                    adopt_management: true
                }
            )
            .unwrap_err(),
        "preview_stale"
    );
    assert_eq!(fs::read(&path).unwrap(), before);
    assert_eq!(
        fs::read(&f.source).unwrap(),
        b"external change after review"
    );
}

#[test]
fn managed_canonical_only_update_then_reviewed_external_replacement_survives_restart() {
    let f = managed_fixture();
    let path = managed_state_path(&f);
    let baseline = fs::read(&path).unwrap();
    let original = fs::read(&f.source).unwrap();
    let snapshot = f.sot.load_active(&f.checkout_id).unwrap();
    let content = "---\nname: fixture-import\ndescription: Fixture skill import\n---\n# New canonical\nExplicit update.\n";
    f.sot
        .save_imported_skill(
            &f.checkout_id,
            &snapshot.snapshot_id,
            "harnesskit.skill.fixture-import",
            content,
        )
        .unwrap();
    assert_eq!(fs::read(&f.source).unwrap(), original);
    assert_eq!(fs::read(&path).unwrap(), baseline);
    let normal = managed_preview(&f, &f.sot);
    assert!(!normal.required_approvals.managed_replacement);
    assert!(!normal.required_approvals.management_adoption);
    let mut approvals = managed_approvals(&normal);
    approvals.adopt_management = false;
    let result = f.sot.apply_install(&normal.preview_id, approvals).unwrap();
    assert_eq!(
        result.status,
        crate::contexts::install::InstallOperationStatus::Complete
    );
    assert!(fs::read_to_string(&f.source)
        .unwrap()
        .contains("New canonical"));
    let updated = fs::read(&path).unwrap();
    assert_ne!(updated, baseline);
    fs::write(&f.source, "External managed state\n").unwrap();
    let external = managed_preview(&f, &f.sot);
    assert!(external.required_approvals.managed_replacement);
    let mut approvals = managed_approvals(&external);
    approvals.replace_managed = true;
    let result = f
        .sot
        .apply_install(&external.preview_id, approvals)
        .unwrap();
    assert_eq!(
        result.status,
        crate::contexts::install::InstallOperationStatus::Complete
    );
    let state: serde_json::Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
    assert_eq!(
        state[0]["last_applied_sha256"],
        format!(
            "{:x}",
            <sha2::Sha256 as sha2::Digest>::digest(fs::read(&f.source).unwrap())
        )
    );
    let restarted = SotContext::with_services(
        AppController::with_state_file(f.private.join("state.json")),
        Some(InstallCoordinator::new(f.private.clone(), Arc::new(RealFixtureGenerator)).unwrap()),
    )
    .unwrap();
    assert!(
        !managed_preview(&f, &restarted)
            .required_approvals
            .managed_replacement
    );
    let dto = crate::api::dto::install::PreviewInstallResponseDto::from(external);
    assert!(
        serde_json::to_value(dto).unwrap()["requiredApprovals"]["managedReplacement"]
            .as_bool()
            .unwrap()
    );
}
#[test]
fn managed_review_rejects_changed_deleted_symlinked_hardlinked_and_canonical_states() {
    let f = managed_fixture();
    let baseline = fs::read(managed_state_path(&f)).unwrap();
    for mutation in [
        "changed",
        "deleted",
        "symlink",
        "hardlink",
        "canonical",
        "record",
    ] {
        fs::write(&f.source, "External reviewed state\n").unwrap();
        let reviewed = managed_preview(&f, &f.sot);
        let mut approvals = managed_approvals(&reviewed);
        approvals.replace_managed = true;
        let outside = f._temp.path().join("outside");
        fs::write(&outside, "untouched outside").unwrap();
        match mutation {
            "changed" => fs::write(&f.source, "External newer state").unwrap(),
            "deleted" => fs::remove_file(&f.source).unwrap(),
            "symlink" => {
                fs::remove_file(&f.source).unwrap();
                std::os::unix::fs::symlink(&outside, &f.source).unwrap();
            }
            "hardlink" => {
                fs::remove_file(&f.source).unwrap();
                fs::hard_link(&outside, &f.source).unwrap();
            }
            "record" => {
                let mut state: serde_json::Value = serde_json::from_slice(&baseline).unwrap();
                state[0]["last_applied_sha256"] = "a".repeat(64).into();
                fs::write(managed_state_path(&f), serde_json::to_vec(&state).unwrap()).unwrap();
            }
            _ => {
                let snapshot = f.sot.load_active(&f.checkout_id).unwrap();
                f.sot.save_imported_skill(&f.checkout_id, &snapshot.snapshot_id, "harnesskit.skill.fixture-import", "---\nname: fixture-import\ndescription: Fixture skill import\n---\n# Changed after review\n").unwrap();
            }
        }
        assert!(
            f.sot
                .apply_install(&reviewed.preview_id, approvals)
                .is_err(),
            "{mutation} must stop"
        );
        assert_eq!(fs::read(&outside).unwrap(), b"untouched outside");
        if mutation != "record" {
            assert_eq!(fs::read(managed_state_path(&f)).unwrap(), baseline);
        }
        if mutation == "deleted" {
            assert!(!f.source.exists());
        }
        if matches!(mutation, "deleted" | "symlink" | "hardlink") {
            let snapshot = f.sot.load_active(&f.checkout_id).unwrap();
            assert!(f
                .sot
                .preview_install(
                    &f.checkout_id,
                    &snapshot.snapshot_id,
                    "component:harnesskit.skill.fixture-import",
                    "user",
                    "import-source".into(),
                    std::collections::BTreeSet::from(["codex".into()])
                )
                .is_err());
        }
        let _ = fs::remove_file(&f.source);
        fs::write(managed_state_path(&f), &baseline).unwrap();
    }
}
#[test]
fn managed_missing_corrupt_baseline_and_ambiguous_link_fail_closed() {
    let f = managed_fixture();
    let path = managed_state_path(&f);
    let baseline = fs::read(&path).unwrap();
    let original = fs::read(&f.source).unwrap();
    for broken in [
        "missing",
        "hash",
        "operation",
        "duplicate",
        "target",
        "empty",
        "corrupt",
        "deleted",
    ] {
        let mut state: serde_json::Value = serde_json::from_slice(&baseline).unwrap();
        match broken {
            "missing" => {
                state[0]
                    .as_object_mut()
                    .unwrap()
                    .remove("last_applied_sha256");
            }
            "hash" => state[0]["last_applied_sha256"] = "invalid".into(),
            "operation" => state[0]["verified_operation_id"] = "".into(),
            "duplicate" => {
                let link = state[0].clone();
                state.as_array_mut().unwrap().push(link);
            }
            "target" => {
                state[0]["source_path"] = f
                    ._temp
                    .path()
                    .join("other")
                    .to_string_lossy()
                    .as_ref()
                    .into()
            }
            "empty" => state = serde_json::json!([]),
            _ => {}
        }
        if broken == "deleted" {
            fs::remove_file(&path).unwrap();
        } else if broken == "corrupt" {
            fs::write(&path, "bad json").unwrap();
        } else {
            fs::write(&path, serde_json::to_vec(&state).unwrap()).unwrap();
        }
        let snapshot = f.sot.load_active(&f.checkout_id).unwrap();
        assert!(
            f.sot
                .preview_install(
                    &f.checkout_id,
                    &snapshot.snapshot_id,
                    "component:harnesskit.skill.fixture-import",
                    "user",
                    "import-source".into(),
                    std::collections::BTreeSet::from(["codex".into()])
                )
                .is_err(),
            "{broken}"
        );
        assert_eq!(fs::read(&f.source).unwrap(), original);
        fs::write(&path, &baseline).unwrap();
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
    }
}

struct ManagedFailureHook {
    source: PathBuf,
    state: PathBuf,
    outside: PathBuf,
    mode: &'static str,
}
impl crate::contexts::install::InstallWriteHook for ManagedFailureHook {
    fn before_replace(&self, _: &str, _: &str) {
        match self.mode {
            "source" => fs::write(&self.source, "Changed during actual writer").unwrap(),
            "deleted" => fs::remove_file(&self.source).unwrap(),
            "hardlink" => {
                fs::hard_link(&self.source, &self.outside).unwrap();
            }
            _ => {
                fs::hard_link(&self.state, &self.outside).unwrap();
            }
        }
    }
}
#[test]
fn managed_writer_races_and_baseline_write_failure_never_advance_verified_state() {
    for mode in ["source", "deleted", "hardlink", "state"] {
        let f = managed_fixture();
        let state_path = managed_state_path(&f);
        let baseline = fs::read(&state_path).unwrap();
        let outside = f._temp.path().join("hardlink");
        let writer = crate::contexts::install::InstallWriter::with_ports(
            Arc::new(FrozenClock),
            Arc::new(ManagedFailureHook {
                source: f.source.clone(),
                state: state_path.clone(),
                outside: outside.clone(),
                mode,
            }),
        );
        let service = SotContext::with_services(
            AppController::with_state_file(f.private.join("state.json")),
            Some(
                InstallCoordinator::new(f.private.clone(), Arc::new(RealFixtureGenerator))
                    .unwrap()
                    .with_test_writer(writer),
            ),
        )
        .unwrap();
        let snapshot = service.load_active(&f.checkout_id).unwrap();
        service.save_imported_skill(&f.checkout_id, &snapshot.snapshot_id, "harnesskit.skill.fixture-import", "---\nname: fixture-import\ndescription: Fixture skill import\n---\n# New managed update\n").unwrap();
        fs::write(&f.source, "Reviewed external change").unwrap();
        let preview = managed_preview(&f, &service);
        let mut approvals = managed_approvals(&preview);
        approvals.replace_managed = true;
        let result = service.apply_install(&preview.preview_id, approvals);
        if mode == "state" {
            assert_eq!(result.unwrap_err(), "management_state_write_failed");
            assert!(fs::read_to_string(&f.source)
                .unwrap()
                .contains("New managed update"));
            fs::remove_file(&outside).unwrap();
        } else {
            assert_ne!(
                result.unwrap().status,
                crate::contexts::install::InstallOperationStatus::Complete
            );
        }
        assert_eq!(
            fs::read(&state_path).unwrap(),
            baseline,
            "{mode}: baseline only advances after verified durable write"
        );
        assert_eq!(service.session_headers().unwrap().install_evidence_id, None);
        if mode == "source" {
            assert_eq!(
                fs::read(&f.source).unwrap(),
                b"Changed during actual writer"
            );
        }
        if mode == "deleted" {
            assert!(!f.source.exists());
        }
        if mode == "hardlink" {
            assert_eq!(fs::read(outside).unwrap(), b"Reviewed external change");
        }
        let restarted = SotContext::with_services(
            AppController::with_state_file(f.private.join("state.json")),
            Some(
                InstallCoordinator::new(f.private.clone(), Arc::new(RealFixtureGenerator)).unwrap(),
            ),
        )
        .unwrap();
        if matches!(mode, "source" | "state") {
            assert!(
                managed_preview(&f, &restarted)
                    .required_approvals
                    .managed_replacement,
                "restart must not silently reset baseline"
            );
        }
    }
}

struct AdoptionFailureHook {
    state_path: PathBuf,
    corrupt_state: bool,
}
impl crate::contexts::install::InstallWriteHook for AdoptionFailureHook {
    fn before_replace(&self, _: &str, _: &str) {
        if self.corrupt_state {
            fs::write(&self.state_path, b"[]").unwrap();
        }
    }
}
struct FrozenClock;
impl crate::contexts::install::InstallClock for FrozenClock {
    fn now_millis(&self) -> u64 {
        0
    }
}

#[test]
fn adoption_state_write_failure_never_reports_overall_success() {
    let f = fixture();
    let candidate = f
        .sot
        .preview_component_import(
            &f.local,
            &f.checkout_id,
            &f.sot_id,
            "local",
            &f.instance_id,
            &f.source_revision,
        )
        .unwrap();
    f.sot
        .confirm_component_import(
            &f.local,
            &candidate.preview_id,
            &candidate.fingerprint,
            true,
        )
        .unwrap();
    let state_path = f
        .private
        .join("component-import-state")
        .join(format!("component-imports-{}.json", f.checkout_id));
    let writer = crate::contexts::install::InstallWriter::with_ports(
        Arc::new(FrozenClock),
        Arc::new(AdoptionFailureHook {
            state_path: state_path.clone(),
            corrupt_state: true,
        }),
    );
    let service = SotContext::with_services(
        AppController::with_state_file(f.private.join("state.json")),
        Some(
            InstallCoordinator::new(f.private.clone(), Arc::new(RealFixtureGenerator))
                .unwrap()
                .with_test_writer(writer),
        ),
    )
    .unwrap();
    let snapshot = service.load_active(&f.checkout_id).unwrap();
    service.save_imported_skill(&f.checkout_id, &snapshot.snapshot_id, "harnesskit.skill.fixture-import", "---\nname: fixture-import\ndescription: Fixture skill import\n---\n# Applied but state failed\n").unwrap();
    let snapshot = service.load_active(&f.checkout_id).unwrap();
    let preview = service
        .preview_install(
            &f.checkout_id,
            &snapshot.snapshot_id,
            "component:harnesskit.skill.fixture-import",
            "user",
            "import-source".into(),
            std::collections::BTreeSet::from(["codex".into()]),
        )
        .unwrap();
    assert_eq!(
        service
            .apply_install(
                &preview.preview_id,
                crate::contexts::install::InstallApprovals {
                    confirmed: true,
                    semantic_fingerprint: preview.fingerprint,
                    overwrite: true,
                    allow_runtime_hooks: false,
                    replace_managed: false,
                    adopt_management: true
                }
            )
            .unwrap_err(),
        "management_state_write_failed"
    );
    assert!(fs::read_to_string(&f.source)
        .unwrap()
        .contains("Applied but state failed"));
    assert_eq!(fs::read(&state_path).unwrap(), b"[]");
    assert_eq!(service.session_headers().unwrap().install_evidence_id, None);
    let restarted = SotContext::with_services(
        AppController::with_state_file(f.private.join("state.json")),
        Some(InstallCoordinator::new(f.private.clone(), Arc::new(RealFixtureGenerator)).unwrap()),
    )
    .unwrap();
    let snapshot = restarted.load_active(&f.checkout_id).unwrap();
    assert!(restarted
        .preview_install(
            &f.checkout_id,
            &snapshot.snapshot_id,
            "component:harnesskit.skill.fixture-import",
            "user",
            "import-source".into(),
            std::collections::BTreeSet::from(["codex".into()])
        )
        .is_err());
}

#[test]
fn import_preserves_frontmatter_scalar_containing_delimiter_text() {
    let f = fixture();
    let text = "---\nname: fixture-import\ndescription: A --- B comparison\n---\n# Fixture\nUse the fixture.\n";
    fs::write(&f.source, text).unwrap();
    let header = f
        .local
        .prepare_open_source_preview(OpenSourceRequest {
            snapshot_id: "local".into(),
            instance_id: f.instance_id.clone(),
            view_generation: 2,
        })
        .unwrap()
        .run()
        .unwrap();
    let preview = f
        .sot
        .preview_component_import(
            &f.local,
            &f.checkout_id,
            &f.sot_id,
            "local",
            &f.instance_id,
            &header.source_revision,
        )
        .expect("delimiter text within a YAML scalar is not a frontmatter boundary");
    assert_eq!(preview.content, text);
    let manifest: serde_yaml::Value = serde_yaml::from_str(&preview.manifest).unwrap();
    assert_eq!(manifest["summary"].as_str(), Some("A --- B comparison"));
    f.sot
        .confirm_component_import(&f.local, &preview.preview_id, &preview.fingerprint, true)
        .unwrap();
    assert_eq!(fs::read_to_string(&f.source).unwrap(), text);
}

#[test]
fn import_registration_preserves_existing_registry_bytes() {
    let f = fixture();
    let before = fs::read(f.root.join("components/registry.yml")).unwrap();
    let preview = f
        .sot
        .preview_component_import(
            &f.local,
            &f.checkout_id,
            &f.sot_id,
            "local",
            &f.instance_id,
            &f.source_revision,
        )
        .unwrap();
    f.sot
        .confirm_component_import(&f.local, &preview.preview_id, &preview.fingerprint, true)
        .unwrap();
    assert!(fs::read(f.root.join("components/registry.yml"))
        .unwrap()
        .starts_with(&before));
}

#[test]
fn registration_writer_never_overwrites_collision_and_rolls_back_only_its_own_bytes() {
    use crate::contexts::install::writer::{rollback_registration_file, write_registration_file};
    let temp = TempDir::new().unwrap();
    let root = fs::canonicalize(temp.path()).unwrap();
    write_registration_file(&root, "component.yml", None, b"candidate", 0o644).unwrap();
    assert_eq!(
        write_registration_file(&root, "component.yml", None, b"other", 0o644).unwrap_err(),
        "import_destination_changed"
    );
    assert_eq!(fs::read(root.join("component.yml")).unwrap(), b"candidate");
    rollback_registration_file(&root, "component.yml", b"candidate", None, 0o644).unwrap();
    assert!(!root.join("component.yml").exists());
    write_registration_file(&root, "registry.yml", None, b"before", 0o644).unwrap();
    write_registration_file(&root, "registry.yml", Some(b"before"), b"after", 0o644).unwrap();
    rollback_registration_file(&root, "registry.yml", b"after", Some(b"before"), 0o644).unwrap();
    assert_eq!(fs::read(root.join("registry.yml")).unwrap(), b"before");
    fs::write(root.join("registry.yml"), b"external").unwrap();
    assert!(
        rollback_registration_file(&root, "registry.yml", b"after", Some(b"before"), 0o644)
            .is_err()
    );
    assert_eq!(fs::read(root.join("registry.yml")).unwrap(), b"external");
}

#[test]
fn import_rejects_symlinked_private_state_without_reading_it() {
    let f = fixture();
    let outside = f.private.join("outside.json");
    fs::write(&outside, "[]").unwrap();
    let links = f
        .private
        .join("component-import-state")
        .join(format!("component-imports-{}.json", f.checkout_id));
    std::os::unix::fs::symlink(&outside, links).unwrap();
    assert_eq!(
        f.sot
            .preview_component_import(
                &f.local,
                &f.checkout_id,
                &f.sot_id,
                "local",
                &f.instance_id,
                &f.source_revision
            )
            .unwrap_err(),
        "import_private_state_unsafe"
    );
}

#[test]
fn import_rechecks_source_after_slow_generation_before_writing() {
    // A real converter may take time. Simulate a source mutation during the second generation.
    struct ChangingGenerator {
        source: PathBuf,
        calls: std::sync::atomic::AtomicUsize,
    }
    impl InstallPlanGenerator for ChangingGenerator {
        fn runtime_manifest_sha256(&self) -> &str {
            "fixture"
        }
        fn generate(
            &self,
            _: &InstallWorkspace,
            _: &PlanRunRequest,
        ) -> Result<InstallPlan, InstallCoordinatorError> {
            unreachable!()
        }
        fn generate_artifacts_in_workspace(
            &self,
            workspace: &InstallWorkspace,
            request: &CanonicalArtifactRequest,
        ) -> Result<GeneratedArtifactSet, InstallCoordinatorError> {
            let generated =
                RealFixtureGenerator.generate_artifacts_in_workspace(workspace, request)?;
            if self.calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst) == 1 {
                fs::write(&self.source, "external edit").unwrap();
            }
            Ok(generated)
        }
    }
    let f = fixture();
    let checkout = AppController::with_state_file(f.private.join("state.json"));
    let install = InstallCoordinator::new(
        f.private.clone(),
        Arc::new(ChangingGenerator {
            source: f.source.clone(),
            calls: std::sync::atomic::AtomicUsize::new(0),
        }),
    )
    .unwrap();
    let sot = SotContext::with_services(checkout, Some(install)).unwrap();
    let sot_id = sot.load_active(&f.checkout_id).unwrap().snapshot_id;
    let preview = sot
        .preview_component_import(
            &f.local,
            &f.checkout_id,
            &sot_id,
            "local",
            &f.instance_id,
            &f.source_revision,
        )
        .unwrap();
    assert!(sot
        .confirm_component_import(&f.local, &preview.preview_id, &preview.fingerprint, true)
        .is_err());
    assert!(!f.root.join("components/skills/fixture-import").exists());
    assert_eq!(fs::read_to_string(&f.source).unwrap(), "external edit");
}

#[test]
fn import_rejects_non_private_provenance_directory() {
    use std::os::unix::fs::PermissionsExt;
    let f = fixture();
    fs::set_permissions(
        f.private.join("component-import-state"),
        fs::Permissions::from_mode(0o755),
    )
    .unwrap();
    assert_eq!(
        f.sot
            .preview_component_import(
                &f.local,
                &f.checkout_id,
                &f.sot_id,
                "local",
                &f.instance_id,
                &f.source_revision
            )
            .unwrap_err(),
        "import_private_state_unsafe"
    );
}

#[test]
fn import_rejects_stale_source_checkout_collision_and_replayed_approval() {
    let f = fixture();
    let preview = f
        .sot
        .preview_component_import(
            &f.local,
            &f.checkout_id,
            &f.sot_id,
            "local",
            &f.instance_id,
            &f.source_revision,
        )
        .unwrap();
    fs::write(&f.source, "changed").unwrap();
    assert!(f
        .sot
        .confirm_component_import(&f.local, &preview.preview_id, &preview.fingerprint, true)
        .is_err());
    assert!(!f.root.join("components/skills/fixture-import").exists());
    let f = fixture();
    let preview = f
        .sot
        .preview_component_import(
            &f.local,
            &f.checkout_id,
            &f.sot_id,
            "local",
            &f.instance_id,
            &f.source_revision,
        )
        .unwrap();
    fs::write(f.root.join("schemas/component.schema.json"), "{}").unwrap();
    assert_eq!(
        f.sot
            .confirm_component_import(&f.local, &preview.preview_id, &preview.fingerprint, true)
            .unwrap_err(),
        "import_preview_stale"
    );
    let f = fixture();
    fs::create_dir(f.root.join("components/skills/fixture-import")).unwrap();
    assert_eq!(
        f.sot
            .preview_component_import(
                &f.local,
                &f.checkout_id,
                &f.sot_id,
                "local",
                &f.instance_id,
                &f.source_revision
            )
            .unwrap_err(),
        "import_collision"
    );
    let f = fixture();
    let preview = f
        .sot
        .preview_component_import(
            &f.local,
            &f.checkout_id,
            &f.sot_id,
            "local",
            &f.instance_id,
            &f.source_revision,
        )
        .unwrap();
    f.sot
        .confirm_component_import(&f.local, &preview.preview_id, &preview.fingerprint, true)
        .unwrap();
    assert_eq!(
        f.sot
            .confirm_component_import(&f.local, &preview.preview_id, &preview.fingerprint, true)
            .unwrap_err(),
        "import_preview_expired"
    );
}

#[test]
fn import_rejects_schema_invalid_sensitive_and_dependent_candidates() {
    for (text, expected) in [
        (
            "---\nname: ../escape\ndescription: fixture\n---\nbody",
            "import_name_invalid",
        ),
        (
            "---\nname: fixture-import\ndescription: fixture\n---\npassword: actual-secret",
            "import_sensitive_or_dependency_content",
        ),
        (
            "---\nname: fixture-import\ndescription: fixture\n---\nRead references/guide.md",
            "import_sensitive_or_dependency_content",
        ),
        (
            "---\nname: fixture-import\ndescription: fixture\nunknown-operation: Bash\n---\nbody",
            "import_frontmatter_unsupported",
        ),
    ] {
        let f = fixture();
        fs::write(&f.source, text).unwrap();
        let header = f
            .local
            .prepare_open_source_preview(OpenSourceRequest {
                snapshot_id: "local".into(),
                instance_id: f.instance_id.clone(),
                view_generation: 2,
            })
            .unwrap()
            .run()
            .unwrap();
        assert_eq!(
            f.sot
                .preview_component_import(
                    &f.local,
                    &f.checkout_id,
                    &f.sot_id,
                    "local",
                    &f.instance_id,
                    &header.source_revision
                )
                .unwrap_err(),
            expected
        );
        assert!(!f.root.join("components/skills/fixture-import").exists());
    }
    let f = fixture();
    let schema = f.root.join("schemas/component.schema.json");
    let mut value: serde_json::Value = serde_json::from_slice(&fs::read(&schema).unwrap()).unwrap();
    value["required"]
        .as_array_mut()
        .unwrap()
        .push(serde_json::json!("missing_required"));
    fs::write(schema, serde_json::to_vec(&value).unwrap()).unwrap();
    let sot_id = f.sot.load_active(&f.checkout_id).unwrap().snapshot_id;
    assert_eq!(
        f.sot
            .preview_component_import(
                &f.local,
                &f.checkout_id,
                &sot_id,
                "local",
                &f.instance_id,
                &f.source_revision
            )
            .unwrap_err(),
        "import_manifest_schema_invalid"
    );
}

#[test]
fn import_commands_are_registered_and_controller_owns_local_to_sot_handoff() {
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let lib = fs::read_to_string(root.join("src/lib.rs")).unwrap();
    let commands = fs::read_to_string(root.join("src/api/commands.rs")).unwrap();
    let controller = fs::read_to_string(root.join("src/app_controller.rs")).unwrap();
    for name in ["preview_component_import", "confirm_component_import"] {
        assert!(lib.contains(&format!("api::commands::{name}")));
        assert!(commands.contains(&format!("controller.{name}")));
        assert!(controller.contains(&format!("fn {name}")));
        let body = controller
            .split(&format!("fn {name}"))
            .nth(1)
            .unwrap()
            .split("\n    pub(crate) fn ")
            .next()
            .unwrap();
        assert!(
            body.contains("begin_local_read"),
            "import must block concurrent install/removal"
        );
    }
}

#[test]
fn import_rejects_unapproved_support_files_in_the_source_directory() {
    let f = fixture();
    fs::write(f.source.parent().unwrap().join("helper.sh"), "exit 0\n").unwrap();
    assert_eq!(
        f.sot
            .preview_component_import(
                &f.local,
                &f.checkout_id,
                &f.sot_id,
                "local",
                &f.instance_id,
                &f.source_revision
            )
            .unwrap_err(),
        "import_support_files_unsupported"
    );
}

#[test]
fn local_skill_import_requires_confirmation_registers_and_refreshes_without_source_changes() {
    let f = fixture();
    let before = fs::read(&f.source).unwrap();
    let preview = f
        .sot
        .preview_component_import(
            &f.local,
            &f.checkout_id,
            &f.sot_id,
            "local",
            &f.instance_id,
            &f.source_revision,
        )
        .unwrap();
    assert_eq!(preview.component_id, "harnesskit.skill.fixture-import");
    assert!(!f.root.join("components/skills/fixture-import").exists());
    assert_eq!(
        f.sot
            .confirm_component_import(&f.local, &preview.preview_id, &preview.fingerprint, false)
            .unwrap_err(),
        "import_confirmation_required"
    );
    let result = f
        .sot
        .confirm_component_import(&f.local, &preview.preview_id, &preview.fingerprint, true)
        .unwrap();
    assert!(result
        .components
        .iter()
        .any(|c| c.component_id == preview.component_id));
    assert_ne!(result.snapshot_id, f.sot_id);
    assert_eq!(fs::read(&f.source).unwrap(), before);
    assert_eq!(
        fs::read(f.root.join("components/skills/fixture-import/SKILL.md")).unwrap(),
        before
    );
    let registry = fs::read_to_string(f.root.join("components/registry.yml")).unwrap();
    assert!(registry.contains("harnesskit.skill.fixture-import:"));
    let manifest = fs::read_to_string(
        f.root
            .join("components/skills/fixture-import/component.yml"),
    )
    .unwrap();
    assert!(!manifest.contains(f.source.to_str().unwrap()));
    let links = fs::read_to_string(
        f.private
            .join("component-import-state")
            .join(format!("component-imports-{}.json", f.checkout_id)),
    )
    .unwrap();
    assert!(links.contains(f.source.to_str().unwrap()));
    assert!(links.contains("\"managed\":false"));
}

#[test]
fn unqualified_skill_path_and_package_policy_never_register_as_codex() {
    for (tool, expected) in [
        (ToolId::AntigravityCli, "import_skill_install_path_conflict"),
        (ToolId::Hermes, "import_hermes_package_policy_unqualified"),
    ] {
        let f = fixture_tool_source(None, false, tool, false);
        let before = fs::read(&f.source).unwrap();
        let registry = fs::read(f.root.join("components/registry.yml")).unwrap();
        let error = f
            .sot
            .preview_component_import(
                &f.local,
                &f.checkout_id,
                &f.sot_id,
                "local",
                &f.instance_id,
                &f.source_revision,
            )
            .unwrap_err();
        assert_eq!(error, expected);
        assert_eq!(fs::read(&f.source).unwrap(), before);
        assert_eq!(
            fs::read(f.root.join("components/registry.yml")).unwrap(),
            registry
        );
    }
}

#[test]
fn claude_standalone_agent_import_preserves_options_and_source_in_both_scopes() {
    for project in [false, true] {
        let f = fixture_kind_source(None, false, ToolId::ClaudeCode, project, true);
        let before = fs::read(&f.source).unwrap();
        let p = f
            .sot
            .preview_component_import(
                &f.local,
                &f.checkout_id,
                &f.sot_id,
                "local",
                &f.instance_id,
                &f.source_revision,
            )
            .expect("Claude standalone agent candidate");
        assert_eq!(p.kind, "agent");
        assert_eq!(p.component_id, "harnesskit.agent.fixture-import");
        assert!(p.generated_artifact_count > 0);
        let manifest: serde_yaml::Value = serde_yaml::from_str(&p.manifest).unwrap();
        assert_eq!(manifest["adapter"]["claude"]["tools"], "Read, Grep");
        assert_eq!(manifest["adapter"]["claude"]["model"], "sonnet");
        assert_eq!(manifest["adapter"]["claude"]["color"], "blue");
        assert!(!p.content.starts_with("---"));
        assert_eq!(
            f.sot
                .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, false)
                .unwrap_err(),
            "import_confirmation_required"
        );
        let s = f
            .sot
            .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
            .unwrap();
        assert!(s
            .components
            .iter()
            .any(|c| c.component_id == p.component_id && c.kind == "agent"));
        assert_eq!(fs::read(&f.source).unwrap(), before);
        assert_eq!(
            fs::read_to_string(f.root.join("components/agents/fixture-import/prompt.md")).unwrap(),
            p.content
        );
        assert!(!p.manifest.contains(f.source.to_str().unwrap()));
    }
}

#[test]
fn claude_standalone_agent_edit_adopt_protected_update_and_restart() {
    for project in [false, true] {
        let f = fixture_kind_source(None, false, ToolId::ClaudeCode, project, true);
        let before = fs::read(&f.source).unwrap();
        let p = f
            .sot
            .preview_component_import(
                &f.local,
                &f.checkout_id,
                &f.sot_id,
                "local",
                &f.instance_id,
                &f.source_revision,
            )
            .unwrap();
        let id = p.component_id.clone();
        let snapshot = f
            .sot
            .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
            .unwrap();
        let detail = f
            .sot
            .read_imported_skill(&f.checkout_id, &snapshot.snapshot_id, &id)
            .unwrap();
        assert!(!detail.managed);
        let content = detail
            .content
            .replace("Use the fixture.", "Use the edited prompt.");
        let snapshot = f
            .sot
            .save_imported_skill(&f.checkout_id, &snapshot.snapshot_id, &id, &content)
            .expect("agent prompt editor");
        assert_eq!(fs::read(&f.source).unwrap(), before);
        let scope = if project { "project" } else { "user" };
        let preview = |service: &SotContext| {
            let s = service.load_active(&f.checkout_id).unwrap();
            service
                .preview_install(
                    &f.checkout_id,
                    &s.snapshot_id,
                    &format!("component:{id}"),
                    scope,
                    "import-source".into(),
                    std::collections::BTreeSet::from(["claude".into()]),
                )
                .unwrap()
        };
        let p = preview(&f.sot);
        assert!(p.required_approvals.management_adoption);
        let mut a = managed_approvals(&p);
        a.adopt_management = false;
        assert_eq!(
            f.sot.apply_install(&p.preview_id, a).unwrap_err(),
            "management_adoption_approval_required"
        );
        assert_eq!(fs::read(&f.source).unwrap(), before);
        let outcome = f
            .sot
            .apply_install(&p.preview_id, managed_approvals(&p))
            .unwrap();
        assert_eq!(
            outcome.status,
            crate::contexts::install::InstallOperationStatus::Complete
        );
        let generated = fs::read_to_string(&f.source).unwrap();
        assert!(generated.contains("Use the edited prompt."));
        let front: serde_yaml::Value =
            serde_yaml::from_str(generated.split("---").nth(1).unwrap()).unwrap();
        for (key, value) in [
            ("tools", "Read, Grep"),
            ("model", "sonnet"),
            ("color", "blue"),
        ] {
            assert_eq!(front[key], value);
        }
        let restarted = SotContext::with_services(
            AppController::with_state_file(f.private.join("state.json")),
            Some(
                InstallCoordinator::new(f.private.clone(), Arc::new(RealFixtureGenerator)).unwrap(),
            ),
        )
        .unwrap();
        let s = restarted.load_active(&f.checkout_id).unwrap();
        assert!(
            restarted
                .read_imported_skill(&f.checkout_id, &s.snapshot_id, &id)
                .unwrap()
                .managed
        );
        let baseline = fs::read(managed_state_path(&f)).unwrap();
        let changed = content.replace("edited prompt", "normal update");
        restarted
            .save_imported_skill(&f.checkout_id, &s.snapshot_id, &id, &changed)
            .unwrap();
        assert_eq!(fs::read(managed_state_path(&f)).unwrap(), baseline);
        let p = preview(&restarted);
        assert!(!p.required_approvals.management_adoption);
        assert!(!p.required_approvals.managed_replacement);
        restarted
            .apply_install(&p.preview_id, managed_approvals(&p))
            .unwrap();
        assert!(fs::read_to_string(&f.source)
            .unwrap()
            .contains("normal update"));
        fs::write(&f.source, "external agent edit\n").unwrap();
        let p = preview(&restarted);
        assert!(p.required_approvals.managed_replacement);
        assert_eq!(
            restarted
                .apply_install(&p.preview_id, managed_approvals(&p))
                .unwrap_err(),
            "managed_replacement_approval_required"
        );
        assert_eq!(
            fs::read_to_string(&f.source).unwrap(),
            "external agent edit\n"
        );
        // Replacement approval applies only to the reviewed external bytes.
        fs::write(&f.source, "changed after review\n").unwrap();
        let mut a = managed_approvals(&p);
        a.replace_managed = true;
        assert!(restarted.apply_install(&p.preview_id, a).is_err());
        let p = preview(&restarted);
        let mut a = managed_approvals(&p);
        a.replace_managed = true;
        restarted.apply_install(&p.preview_id, a).unwrap();
        assert!(fs::read_to_string(&f.source)
            .unwrap()
            .contains("normal update"));
        let restarted_again = SotContext::with_services(
            AppController::with_state_file(f.private.join("state.json")),
            Some(
                InstallCoordinator::new(f.private.clone(), Arc::new(RealFixtureGenerator)).unwrap(),
            ),
        )
        .unwrap();
        assert!(
            !preview(&restarted_again)
                .required_approvals
                .managed_replacement
        );
        let _ = snapshot;
    }
}

#[test]
fn claude_agent_unknown_fields_dependencies_and_invalid_options_fail_without_writes() {
    let f = fixture_kind_source(None, false, ToolId::ClaudeCode, false, true);
    let original = fs::read_to_string(&f.source).unwrap();
    let registry = fs::read(f.root.join("components/registry.yml")).unwrap();
    for (content, expected) in [
        (
            original.replace(
                "color: blue",
                "color: blue\npermissionMode: bypassPermissions",
            ),
            "import_frontmatter_unsupported",
        ),
        (
            original.replace("color: blue", "color: blue\nskills: [missing-skill]"),
            "import_frontmatter_unsupported",
        ),
        (
            original.replace("tools: Read, Grep", "tools: [Read, Grep]"),
            "import_agent_option_unsupported",
        ),
        (
            original.replace("model: sonnet", "model: true"),
            "import_agent_option_unsupported",
        ),
        (
            original.replace("tools: Read, Grep", "tools: 'Read: injected'"),
            "import_agent_option_unsupported",
        ),
        (
            original.replace("model: sonnet", "model: 'null'"),
            "import_agent_option_unsupported",
        ),
        (
            original.replace("color: blue", "color: 'blue: injected'"),
            "import_agent_option_unsupported",
        ),
        (
            original.replace("Use the fixture.", "Read scripts/missing.sh"),
            "import_sensitive_or_dependency_content",
        ),
    ] {
        fs::write(&f.source, &content).unwrap();
        let header = f
            .local
            .prepare_open_source_preview(OpenSourceRequest {
                snapshot_id: "local".into(),
                instance_id: f.instance_id.clone(),
                view_generation: 2,
            })
            .unwrap()
            .run()
            .unwrap();
        assert_eq!(
            f.sot
                .preview_component_import(
                    &f.local,
                    &f.checkout_id,
                    &f.sot_id,
                    "local",
                    &f.instance_id,
                    &header.source_revision
                )
                .unwrap_err(),
            expected
        );
        assert_eq!(fs::read_to_string(&f.source).unwrap(), content);
        assert_eq!(
            fs::read(f.root.join("components/registry.yml")).unwrap(),
            registry
        );
        assert!(!f.root.join("components/agents/fixture-import").exists());
    }
}

#[test]
fn claude_agent_stale_confirmation_collision_and_missing_private_record_fail_closed() {
    let f = fixture_kind_source(None, false, ToolId::ClaudeCode, false, true);
    let p = f
        .sot
        .preview_component_import(
            &f.local,
            &f.checkout_id,
            &f.sot_id,
            "local",
            &f.instance_id,
            &f.source_revision,
        )
        .unwrap();
    fs::write(&f.source, "changed after preview\n").unwrap();
    assert!(f
        .sot
        .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
        .is_err());
    assert!(!f.root.join("components/agents/fixture-import").exists());
    let f = fixture_kind_source(None, false, ToolId::ClaudeCode, false, true);
    let p = f
        .sot
        .preview_component_import(
            &f.local,
            &f.checkout_id,
            &f.sot_id,
            "local",
            &f.instance_id,
            &f.source_revision,
        )
        .unwrap();
    let s = f
        .sot
        .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
        .unwrap();
    assert_eq!(
        f.sot
            .preview_component_import(
                &f.local,
                &f.checkout_id,
                &s.snapshot_id,
                "local",
                &f.instance_id,
                &f.source_revision
            )
            .unwrap_err(),
        "import_collision"
    );
    let before = fs::read(&f.source).unwrap();
    let canonical = fs::read(f.root.join("components/agents/fixture-import/prompt.md")).unwrap();
    for content in [
        "",
        "---\nname: changed\n---\nPrompt",
        "Read scripts/missing.sh",
        "api_key: example",
    ] {
        assert!(f
            .sot
            .save_imported_skill(&f.checkout_id, &s.snapshot_id, &p.component_id, content)
            .is_err());
        assert_eq!(
            fs::read(f.root.join("components/agents/fixture-import/prompt.md")).unwrap(),
            canonical
        );
    }
    fs::remove_file(managed_state_path(&f)).unwrap();
    assert!(f
        .sot
        .preview_install(
            &f.checkout_id,
            &s.snapshot_id,
            &format!("component:{}", p.component_id),
            "user",
            "import-source".into(),
            std::collections::BTreeSet::from(["claude".into()])
        )
        .is_err());
    assert_eq!(fs::read(&f.source).unwrap(), before);
}

#[test]
fn qualified_skill_tools_generate_adopt_update_at_their_actual_paths() {
    for (tool, target, project) in [
        (ToolId::Codex, "codex", false),
        (ToolId::ClaudeCode, "claude", false),
        (ToolId::Antigravity, "antigravity", false),
        (ToolId::Codex, "codex", true),
        (ToolId::ClaudeCode, "claude", true),
        (ToolId::Antigravity, "antigravity", true),
        (ToolId::AntigravityCli, "antigravity-cli", true),
    ] {
        let f = fixture_tool_source(None, false, tool, project);
        let before = fs::read(&f.source).unwrap();
        let p = f
            .sot
            .preview_component_import(
                &f.local,
                &f.checkout_id,
                &f.sot_id,
                "local",
                &f.instance_id,
                &f.source_revision,
            )
            .unwrap();
        let manifest: serde_yaml::Value = serde_yaml::from_str(&p.manifest).unwrap();
        assert!(
            manifest["targets"][target].is_mapping(),
            "{target} must not be imported as Codex"
        );
        assert_eq!(manifest["targets"].as_mapping().unwrap().len(), 1);
        assert!(p.generated_artifact_count > 0);
        let snapshot = f
            .sot
            .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
            .unwrap();
        assert_eq!(fs::read(&f.source).unwrap(), before);
        let scope = if project { "project" } else { "user" };
        let preview = f
            .sot
            .preview_install(
                &f.checkout_id,
                &snapshot.snapshot_id,
                &format!("component:{}", p.component_id),
                scope,
                "import-source".into(),
                std::collections::BTreeSet::from([target.into()]),
            )
            .unwrap();
        assert!(preview.required_approvals.management_adoption);
        let outcome = f
            .sot
            .apply_install(&preview.preview_id, managed_approvals(&preview))
            .unwrap();
        assert_eq!(
            outcome.status,
            crate::contexts::install::InstallOperationStatus::Complete
        );
        let detail = f
            .sot
            .read_imported_skill(&f.checkout_id, &snapshot.snapshot_id, &p.component_id)
            .unwrap();
        assert!(detail.managed);
        let changed = detail
            .content
            .replace("Use the fixture.", "Use the qualified update.");
        let snapshot = f
            .sot
            .save_imported_skill(
                &f.checkout_id,
                &snapshot.snapshot_id,
                &p.component_id,
                &changed,
            )
            .unwrap();
        let preview = f
            .sot
            .preview_install(
                &f.checkout_id,
                &snapshot.snapshot_id,
                &format!("component:{}", p.component_id),
                scope,
                "import-source".into(),
                std::collections::BTreeSet::from([target.into()]),
            )
            .unwrap();
        assert!(!preview.required_approvals.management_adoption);
        assert!(!preview.required_approvals.managed_replacement);
        let outcome = f
            .sot
            .apply_install(&preview.preview_id, managed_approvals(&preview))
            .unwrap();
        assert_eq!(
            outcome.status,
            crate::contexts::install::InstallOperationStatus::Complete
        );
        assert!(fs::read_to_string(&f.source)
            .unwrap()
            .contains("Use the qualified update."));
    }
}

struct CodexConfigRaceHook {
    config: PathBuf,
}
impl crate::contexts::install::InstallWriteHook for CodexConfigRaceHook {
    fn before_replace(&self, _: &str, destination: &str) {
        if destination == ".codex/config.toml" {
            fs::write(&self.config, "# foreign writer race\n").unwrap();
        }
    }
}
#[test]
fn codex_agent_registration_stale_security_partial_and_missing_state() {
    for case in [
        "selected-stale",
        "symlink",
        "hardlink",
        "partial",
        "missing-state",
    ] {
        let f = fixture_kind_source(None, false, ToolId::Codex, false, true);
        let config = f
            .source
            .parent()
            .unwrap()
            .parent()
            .unwrap()
            .join("config.toml");
        if case == "partial" {
            fs::write(
                &config,
                fs::read_to_string(&config)
                    .unwrap()
                    .replace("[\"Reviewer\", \"Second\"]", "['Reviewer', 'Second']"),
            )
            .unwrap();
        }
        let p = support_preview(&f).unwrap();
        let id = p.component_id.clone();
        if case == "selected-stale" {
            fs::write(
                &config,
                fs::read_to_string(&config)
                    .unwrap()
                    .replace("Reviewer", "Changed"),
            )
            .unwrap();
            assert!(f
                .sot
                .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
                .is_err());
            continue;
        }
        if matches!(case, "symlink" | "hardlink") {
            let foreign = config.with_file_name("foreign.toml");
            fs::rename(&config, &foreign).unwrap();
            if case == "symlink" {
                std::os::unix::fs::symlink(&foreign, &config).unwrap();
            } else {
                fs::hard_link(&foreign, &config).unwrap();
            }
            assert!(f
                .sot
                .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
                .is_err());
            continue;
        }
        let s = f
            .sot
            .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
            .unwrap();
        let svc = if case == "partial" {
            let writer = crate::contexts::install::InstallWriter::with_ports(
                Arc::new(FrozenClock),
                Arc::new(CodexConfigRaceHook {
                    config: config.clone(),
                }),
            );
            SotContext::with_services(
                AppController::with_state_file(f.private.join("state.json")),
                Some(
                    InstallCoordinator::new(f.private.clone(), Arc::new(RealFixtureGenerator))
                        .unwrap()
                        .with_test_writer(writer),
                ),
            )
            .unwrap()
        } else {
            fs::remove_file(managed_state_path(&f)).unwrap();
            SotContext::with_services(
                AppController::with_state_file(f.private.join("state.json")),
                Some(
                    InstallCoordinator::new(f.private.clone(), Arc::new(RealFixtureGenerator))
                        .unwrap(),
                ),
            )
            .unwrap()
        };
        let s = svc.load_active(&f.checkout_id).unwrap_or(s);
        let p = svc.preview_install(
            &f.checkout_id,
            &s.snapshot_id,
            &format!("component:{id}"),
            "user",
            "import-source".into(),
            std::collections::BTreeSet::from(["codex".into()]),
        );
        if case == "missing-state" {
            assert!(p.is_err());
            continue;
        }
        let p = p.unwrap();
        let out = svc
            .apply_install(&p.preview_id, managed_approvals(&p))
            .unwrap();
        assert_eq!(
            out.status,
            crate::contexts::install::InstallOperationStatus::Partial
        );
        let links: Vec<serde_json::Value> =
            serde_json::from_slice(&fs::read(managed_state_path(&f)).unwrap()).unwrap();
        let registration = links
            .iter()
            .find(|l| l["source_role"] == "registration")
            .unwrap();
        assert_eq!(registration["managed"], false);
        assert!(registration.get("last_applied_sha256").is_none());
        use std::os::unix::fs::MetadataExt;
        assert_eq!(
            fs::metadata(managed_state_path(&f)).unwrap().mode() & 0o777,
            0o600
        );
        assert_eq!(
            fs::metadata(managed_state_path(&f).parent().unwrap())
                .unwrap()
                .mode()
                & 0o777,
            0o700
        );
    }
}

#[test]
fn codex_agent_registration_absent_defaults_and_source_revision_selection() {
    let mut f = fixture_kind_source(None, false, ToolId::Codex, false, true);
    let config = f
        .source
        .parent()
        .unwrap()
        .parent()
        .unwrap()
        .join("config.toml");
    let text = fs::read_to_string(&f.source)
        .unwrap()
        .replace("model = \"gpt-test\"\n", "")
        .replace("model_reasoning_effort = \"high\"\n", "");
    fs::write(&f.source, text).unwrap();
    fs::write(
        &config,
        fs::read_to_string(&config)
            .unwrap()
            .replace("nickname_candidates = [\"Reviewer\", \"Second\"]\n", ""),
    )
    .unwrap();
    f.source_revision = f
        .local
        .prepare_open_source_preview(OpenSourceRequest {
            snapshot_id: "local".into(),
            instance_id: f.instance_id.clone(),
            view_generation: 2,
        })
        .unwrap()
        .run()
        .unwrap()
        .source_revision;
    let p = support_preview(&f).unwrap();
    fs::write(
        &config,
        fs::read_to_string(&config)
            .unwrap()
            .replace("foreign-model", "unowned-change"),
    )
    .unwrap();
    let id = p.component_id.clone();
    let s = f
        .sot
        .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
        .expect("unowned config edit cannot stale selected registration");
    let p = f
        .sot
        .preview_install(
            &f.checkout_id,
            &s.snapshot_id,
            &format!("component:{id}"),
            "user",
            "import-source".into(),
            std::collections::BTreeSet::from(["codex".into()]),
        )
        .unwrap();
    f.sot
        .apply_install(&p.preview_id, managed_approvals(&p))
        .unwrap();
    let agent = fs::read_to_string(&f.source).unwrap();
    assert!(!agent.contains("model ="));
    assert!(!agent.contains("model_reasoning_effort"));
    let config = fs::read_to_string(&config).unwrap();
    assert!(!config.contains("nickname_candidates"));
    assert!(config.contains("unowned-change"));
}

#[test]
fn codex_agent_registration_unknown_and_unsafe_inputs_fail_closed() {
    for case in [
        "agent-unknown",
        "registration-unknown",
        "nickname-type",
        "description",
        "traversal",
        "absolute",
        "missing",
        "duplicate",
        "alias",
        "secret",
        "missing-name",
        "missing-body",
    ] {
        let mut f = fixture_kind_source(None, false, ToolId::Codex, false, true);
        let config = f
            .source
            .parent()
            .unwrap()
            .parent()
            .unwrap()
            .join("config.toml");
        let text = fs::read_to_string(&config).unwrap();
        match case {
            "agent-unknown" => fs::write(
                &f.source,
                fs::read_to_string(&f.source).unwrap() + "unknown = true\n",
            )
            .unwrap(),
            "registration-unknown" => fs::write(
                &config,
                text.replace("# keep registration comment", "unknown = true"),
            )
            .unwrap(),
            "nickname-type" => {
                fs::write(&config, text.replace("[\"Reviewer\", \"Second\"]", "true")).unwrap()
            }
            "description" => {
                fs::write(&config, text.replace("Fixture agent import", "mismatch")).unwrap()
            }
            "traversal" => fs::write(
                &config,
                text.replace(
                    "agents/fixture-import.toml",
                    "agents/../agents/fixture-import.toml",
                ),
            )
            .unwrap(),
            "absolute" => fs::write(
                &config,
                text.replace("agents/fixture-import.toml", "/private/agent.toml"),
            )
            .unwrap(),
            "missing" => fs::remove_file(&config).unwrap(),
            "duplicate" => fs::write(
                &config,
                text
                    + "[agents.\"fixture-import\"]\nconfig_file = \"agents/fixture-import.toml\"\n",
            )
            .unwrap(),
            "alias" => fs::write(
                &config,
                text.replace("agents/neighbor.toml", "agents/fixture-import.toml"),
            )
            .unwrap(),
            "secret" => fs::write(
                &f.source,
                fs::read_to_string(&f.source)
                    .unwrap()
                    .replace("Use the fixture.", concat!("api_key=", "private-value")),
            )
            .unwrap(),
            "missing-name" => fs::write(
                &f.source,
                fs::read_to_string(&f.source)
                    .unwrap()
                    .replace("name = \"fixture-import\"\n", ""),
            )
            .unwrap(),
            "missing-body" => fs::write(
                &f.source,
                "name = \"fixture-import\"\ndescription = \"Fixture agent import\"\n",
            )
            .unwrap(),
            _ => unreachable!(),
        }
        f.source_revision = f
            .local
            .prepare_open_source_preview(OpenSourceRequest {
                snapshot_id: "local".into(),
                instance_id: f.instance_id.clone(),
                view_generation: 2,
            })
            .unwrap()
            .run()
            .unwrap()
            .source_revision;
        let before = fs::read(&f.source).unwrap();
        assert!(
            support_preview(&f).is_err(),
            "lossy registration accepted: {case}"
        );
        assert_eq!(fs::read(&f.source).unwrap(), before);
        assert!(!f.root.join("components/agents/fixture-import").exists());
    }
}

#[test]
fn codex_agent_registration_edit_adopt_protected_update_restart() {
    for project in [false, true] {
        let f = fixture_kind_source(None, false, ToolId::Codex, project, true);
        let config = f
            .source
            .parent()
            .unwrap()
            .parent()
            .unwrap()
            .join("config.toml");
        let before = fs::read(&f.source).unwrap();
        let config_before = fs::read(&config).unwrap();
        let p = support_preview(&f).expect("Codex agent plus selected registration import");
        assert_eq!(p.kind, "agent");
        assert_eq!(p.generated_artifact_count, 4); // two destinations, two scopes
        let id = p.component_id.clone();
        let s = f
            .sot
            .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
            .unwrap();
        assert_eq!(fs::read(&f.source).unwrap(), before);
        assert_eq!(fs::read(&config).unwrap(), config_before);
        let d = f
            .sot
            .read_imported_skill(&f.checkout_id, &s.snapshot_id, &id)
            .unwrap();
        let edited = d.content.replace("fixture", "edited prompt");
        f.sot
            .save_imported_skill(&f.checkout_id, &s.snapshot_id, &id, &edited)
            .unwrap();
        assert_eq!(fs::read(&f.source).unwrap(), before);
        let preview = |svc: &SotContext| {
            let s = svc.load_active(&f.checkout_id).unwrap();
            svc.preview_install(
                &f.checkout_id,
                &s.snapshot_id,
                &format!("component:{id}"),
                if project { "project" } else { "user" },
                "import-source".into(),
                std::collections::BTreeSet::from(["codex".into()]),
            )
            .unwrap()
        };
        let p = preview(&f.sot);
        assert!(p.required_approvals.management_adoption);
        let mut a = managed_approvals(&p);
        a.adopt_management = false;
        assert_eq!(
            f.sot.apply_install(&p.preview_id, a).unwrap_err(),
            "management_adoption_approval_required"
        );
        let out = f
            .sot
            .apply_install(&p.preview_id, managed_approvals(&p))
            .unwrap();
        assert_eq!(
            out.status,
            crate::contexts::install::InstallOperationStatus::Complete
        );
        assert!(fs::read_to_string(&f.source)
            .unwrap()
            .contains("edited prompt"));
        let assert_metadata = || {
            let text = fs::read_to_string(&f.source).unwrap();
            assert!(text.contains("model = \"gpt-test\""));
            assert!(text.contains("model_reasoning_effort = \"high\""));
            let text = fs::read_to_string(&config).unwrap();
            for preserved in [
                "# keep global comment",
                "# keep registration comment",
                "# keep neighbor comment",
                "[agents.neighbor]",
                "Reviewer",
                "Second",
            ] {
                assert!(text.contains(preserved), "missing {preserved}");
            }
        };
        assert_metadata();
        let restart = || {
            SotContext::with_services(
                AppController::with_state_file(f.private.join("state.json")),
                Some(
                    InstallCoordinator::new(f.private.clone(), Arc::new(RealFixtureGenerator))
                        .unwrap(),
                ),
            )
            .unwrap()
        };
        let svc = restart();
        let s = svc.load_active(&f.checkout_id).unwrap();
        assert!(
            svc.read_imported_skill(&f.checkout_id, &s.snapshot_id, &id)
                .unwrap()
                .managed
        );
        let links: serde_json::Value =
            serde_json::from_slice(&fs::read(managed_state_path(&f)).unwrap()).unwrap();
        assert_eq!(links.as_array().unwrap().len(), 2);
        assert!(links
            .as_array()
            .unwrap()
            .iter()
            .all(|l| l["managed"] == true));
        svc.save_imported_skill(
            &f.checkout_id,
            &s.snapshot_id,
            &id,
            &edited.replace("edited", "normal"),
        )
        .unwrap();
        fs::write(
            &config,
            fs::read_to_string(&config)
                .unwrap()
                .replace("foreign-model", "changed-global"),
        )
        .unwrap();
        let p = preview(&svc);
        assert!(!p.required_approvals.managed_replacement);
        // Latest unowned settings after review must survive the real writer.
        fs::write(
            &config,
            fs::read_to_string(&config)
                .unwrap()
                .replace("changed-global", "after-review"),
        )
        .unwrap();
        svc.apply_install(&p.preview_id, managed_approvals(&p))
            .unwrap();
        assert!(fs::read_to_string(&config)
            .unwrap()
            .contains("after-review"));
        assert_metadata();
        fs::write(
            &config,
            fs::read_to_string(&config)
                .unwrap()
                .replace("Reviewer", "External"),
        )
        .unwrap();
        let p = preview(&svc);
        assert!(p.required_approvals.managed_replacement);
        assert_eq!(
            svc.apply_install(&p.preview_id, managed_approvals(&p))
                .unwrap_err(),
            "managed_replacement_approval_required"
        );
        fs::write(
            &config,
            fs::read_to_string(&config)
                .unwrap()
                .replace("External", "Race"),
        )
        .unwrap();
        let mut a = managed_approvals(&p);
        a.replace_managed = true;
        assert!(svc.apply_install(&p.preview_id, a).is_err());
        let p = preview(&svc);
        let mut a = managed_approvals(&p);
        a.replace_managed = true;
        svc.apply_install(&p.preview_id, a).unwrap();
        assert_metadata();
        assert!(!preview(&restart()).required_approvals.managed_replacement);
        let manifest = fs::read_to_string(
            f.root
                .join("components/agents/fixture-import/component.yml"),
        )
        .unwrap();
        assert!(!manifest.contains(f.source.to_str().unwrap()));
    }
}

#[test]
fn antigravity_project_agent_edit_adopt_protected_update_and_restart() {
    for project in [true] {
        let f = fixture_kind_source(None, false, ToolId::Antigravity, project, true);
        let before = fs::read(&f.source).unwrap();
        let p = f
            .sot
            .preview_component_import(
                &f.local,
                &f.checkout_id,
                &f.sot_id,
                "local",
                &f.instance_id,
                &f.source_revision,
            )
            .unwrap();
        let id = p.component_id.clone();
        let snapshot = f
            .sot
            .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
            .unwrap();
        let detail = f
            .sot
            .read_imported_skill(&f.checkout_id, &snapshot.snapshot_id, &id)
            .unwrap();
        assert!(!detail.managed);
        let content = detail
            .content
            .replace("Use the fixture.", "Use the edited prompt.");
        let snapshot = f
            .sot
            .save_imported_skill(&f.checkout_id, &snapshot.snapshot_id, &id, &content)
            .expect("agent prompt editor");
        assert_eq!(fs::read(&f.source).unwrap(), before);
        let scope = if project { "project" } else { "user" };
        let preview = |service: &SotContext| {
            let s = service.load_active(&f.checkout_id).unwrap();
            service
                .preview_install(
                    &f.checkout_id,
                    &s.snapshot_id,
                    &format!("component:{id}"),
                    scope,
                    "import-source".into(),
                    std::collections::BTreeSet::from(["antigravity".into()]),
                )
                .unwrap()
        };
        let p = preview(&f.sot);
        assert!(p.required_approvals.management_adoption);
        let mut a = managed_approvals(&p);
        a.adopt_management = false;
        assert_eq!(
            f.sot.apply_install(&p.preview_id, a).unwrap_err(),
            "management_adoption_approval_required"
        );
        assert_eq!(fs::read(&f.source).unwrap(), before);
        let outcome = f
            .sot
            .apply_install(&p.preview_id, managed_approvals(&p))
            .unwrap();
        assert_eq!(
            outcome.status,
            crate::contexts::install::InstallOperationStatus::Complete
        );
        let generated = fs::read_to_string(&f.source).unwrap();
        assert!(generated.contains("Use the edited prompt."));
        let front: serde_yaml::Value =
            serde_yaml::from_str(generated.split("---").nth(1).unwrap()).unwrap();
        let original: serde_yaml::Value = serde_yaml::from_str(
            std::str::from_utf8(&before)
                .unwrap()
                .split("---")
                .nth(1)
                .unwrap(),
        )
        .unwrap();
        assert_eq!(front["name"], original["name"]);
        assert_eq!(front["description"], original["description"]);
        assert_eq!(front["tools"], original["tools"]);
        let assert_metadata = || {
            let generated = fs::read_to_string(&f.source).unwrap();
            let front: serde_yaml::Value =
                serde_yaml::from_str(generated.split("---").nth(1).unwrap()).unwrap();
            assert_eq!(front["tools"], original["tools"]);
            assert_eq!(front["description"], original["description"]);
        };
        let restarted = SotContext::with_services(
            AppController::with_state_file(f.private.join("state.json")),
            Some(
                InstallCoordinator::new(f.private.clone(), Arc::new(RealFixtureGenerator)).unwrap(),
            ),
        )
        .unwrap();
        let s = restarted.load_active(&f.checkout_id).unwrap();
        assert!(
            restarted
                .read_imported_skill(&f.checkout_id, &s.snapshot_id, &id)
                .unwrap()
                .managed
        );
        let baseline = fs::read(managed_state_path(&f)).unwrap();
        let changed = content.replace("edited prompt", "normal update");
        restarted
            .save_imported_skill(&f.checkout_id, &s.snapshot_id, &id, &changed)
            .unwrap();
        assert_eq!(fs::read(managed_state_path(&f)).unwrap(), baseline);
        let p = preview(&restarted);
        assert!(!p.required_approvals.management_adoption);
        assert!(!p.required_approvals.managed_replacement);
        restarted
            .apply_install(&p.preview_id, managed_approvals(&p))
            .unwrap();
        assert!(fs::read_to_string(&f.source)
            .unwrap()
            .contains("normal update"));
        fs::write(&f.source, "external agent edit\n").unwrap();
        let p = preview(&restarted);
        assert!(p.required_approvals.managed_replacement);
        assert_eq!(
            restarted
                .apply_install(&p.preview_id, managed_approvals(&p))
                .unwrap_err(),
            "managed_replacement_approval_required"
        );
        assert_eq!(
            fs::read_to_string(&f.source).unwrap(),
            "external agent edit\n"
        );
        // Replacement approval applies only to the reviewed external bytes.
        fs::write(&f.source, "changed after review\n").unwrap();
        let mut a = managed_approvals(&p);
        a.replace_managed = true;
        assert!(restarted.apply_install(&p.preview_id, a).is_err());
        let p = preview(&restarted);
        let mut a = managed_approvals(&p);
        a.replace_managed = true;
        restarted.apply_install(&p.preview_id, a).unwrap();
        assert!(fs::read_to_string(&f.source)
            .unwrap()
            .contains("normal update"));
        let restarted_again = SotContext::with_services(
            AppController::with_state_file(f.private.join("state.json")),
            Some(
                InstallCoordinator::new(f.private.clone(), Arc::new(RealFixtureGenerator)).unwrap(),
            ),
        )
        .unwrap();
        assert!(
            !preview(&restarted_again)
                .required_approvals
                .managed_replacement
        );
        assert_metadata();
        use std::os::unix::fs::MetadataExt;
        assert_eq!(
            fs::metadata(managed_state_path(&f)).unwrap().mode() & 0o777,
            0o600
        );
        assert_eq!(
            fs::metadata(managed_state_path(&f).parent().unwrap())
                .unwrap()
                .mode()
                & 0o777,
            0o700
        );
        let baseline = fs::read(managed_state_path(&f)).unwrap();
        let canonical =
            fs::read(f.root.join("components/agents/fixture-import/prompt.md")).unwrap();
        assert!(fs::read_to_string(managed_state_path(&f))
            .unwrap()
            .contains("antigravity"));
        assert!(fs::read_to_string(managed_state_path(&f))
            .unwrap()
            .contains(".agents/agents/fixture-import.md"));
        fs::remove_file(managed_state_path(&f)).unwrap();
        let s = restarted_again.load_active(&f.checkout_id).unwrap();
        assert!(restarted_again
            .preview_install(
                &f.checkout_id,
                &s.snapshot_id,
                &format!("component:{id}"),
                "project",
                "import-source".into(),
                std::collections::BTreeSet::from(["antigravity".into()])
            )
            .is_err());
        assert_eq!(
            fs::read(f.root.join("components/agents/fixture-import/prompt.md")).unwrap(),
            canonical
        );
        assert_metadata();
        assert!(!baseline.is_empty());
        let _ = snapshot;
    }
}

#[test]
fn antigravity_agent_stale_confirmation_collision_and_missing_private_record_fail_closed() {
    let f = fixture_kind_source(None, false, ToolId::Antigravity, true, true);
    let p = f
        .sot
        .preview_component_import(
            &f.local,
            &f.checkout_id,
            &f.sot_id,
            "local",
            &f.instance_id,
            &f.source_revision,
        )
        .unwrap();
    fs::write(&f.source, "changed after preview\n").unwrap();
    assert!(f
        .sot
        .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
        .is_err());
    assert!(!f.root.join("components/agents/fixture-import").exists());
    let f = fixture_kind_source(None, false, ToolId::Antigravity, true, true);
    let p = f
        .sot
        .preview_component_import(
            &f.local,
            &f.checkout_id,
            &f.sot_id,
            "local",
            &f.instance_id,
            &f.source_revision,
        )
        .unwrap();
    let s = f
        .sot
        .confirm_component_import(&f.local, &p.preview_id, &p.fingerprint, true)
        .unwrap();
    assert_eq!(
        f.sot
            .preview_component_import(
                &f.local,
                &f.checkout_id,
                &s.snapshot_id,
                "local",
                &f.instance_id,
                &f.source_revision
            )
            .unwrap_err(),
        "import_collision"
    );
    let before = fs::read(&f.source).unwrap();
    let canonical = fs::read(f.root.join("components/agents/fixture-import/prompt.md")).unwrap();
    for content in [
        "",
        "---\nname: changed\n---\nPrompt",
        "Read scripts/missing.sh",
        "api_key: example",
    ] {
        assert!(f
            .sot
            .save_imported_skill(&f.checkout_id, &s.snapshot_id, &p.component_id, content)
            .is_err());
        assert_eq!(
            fs::read(f.root.join("components/agents/fixture-import/prompt.md")).unwrap(),
            canonical
        );
    }
    fs::remove_file(managed_state_path(&f)).unwrap();
    assert!(f
        .sot
        .preview_install(
            &f.checkout_id,
            &s.snapshot_id,
            &format!("component:{}", p.component_id),
            "project",
            "import-source".into(),
            std::collections::BTreeSet::from(["antigravity".into()])
        )
        .is_err());
    assert_eq!(fs::read(&f.source).unwrap(), before);
}

#[test]
fn antigravity_agent_strict_tools_metadata_dependencies_and_name_fail_without_writes() {
    let f = fixture_kind_source(None, false, ToolId::Antigravity, true, true);
    let original = fs::read_to_string(&f.source).unwrap();
    let registry = fs::read(f.root.join("components/registry.yml")).unwrap();
    let mut cases = vec![
        (
            original.replace("name: fixture-import", "name: other-agent"),
            "import_agent_name_path_mismatch",
        ),
        (
            original.replace("tools:\n", "model: sonnet\ntools:\n"),
            "import_frontmatter_unsupported",
        ),
        (
            original.replace("tools:\n", "skills: [missing]\ntools:\n"),
            "import_frontmatter_unsupported",
        ),
        (
            original.replace("tools:\n", "unknown: true\ntools:\n"),
            "import_frontmatter_unsupported",
        ),
        (
            original.replace("tools:\n", "tools:\n  extra: true\n"),
            "import_agent_option_unsupported",
        ),
        (
            original.replace("Use the fixture.", "Read scripts/missing.sh"),
            "import_sensitive_or_dependency_content",
        ),
        (
            original.replace("Use the fixture.", "api_key: example"),
            "import_sensitive_or_dependency_content",
        ),
        (
            original.replace("Use the fixture.", "[Guide](references/guide.md)"),
            "import_sensitive_or_dependency_content",
        ),
    ];
    for (key, value) in [
        ("enable_write_tools", "false"),
        ("enable_mcp_tools", "true"),
        ("enable_subagent_tools", "false"),
    ] {
        cases.push((
            original.replace(&format!("  {key}: {value}\n"), ""),
            "import_agent_option_unsupported",
        ));
        for invalid in ["'true'", "1", "null", "[true]", "{value: true}"] {
            cases.push((
                original.replace(&format!("{key}: {value}"), &format!("{key}: {invalid}")),
                "import_agent_option_unsupported",
            ));
        }
    }
    for (content, expected) in cases {
        fs::write(&f.source, &content).unwrap();
        let h = f
            .local
            .prepare_open_source_preview(OpenSourceRequest {
                snapshot_id: "local".into(),
                instance_id: f.instance_id.clone(),
                view_generation: 2,
            })
            .unwrap()
            .run()
            .unwrap();
        assert_eq!(
            f.sot
                .preview_component_import(
                    &f.local,
                    &f.checkout_id,
                    &f.sot_id,
                    "local",
                    &f.instance_id,
                    &h.source_revision
                )
                .unwrap_err(),
            expected
        );
        assert_eq!(fs::read_to_string(&f.source).unwrap(), content);
        assert_eq!(
            fs::read(f.root.join("components/registry.yml")).unwrap(),
            registry
        );
        assert!(!f.root.join("components/agents/fixture-import").exists());
        assert!(!managed_state_path(&f).exists());
    }
}

#[test]
fn antigravity_agent_user_and_cli_handles_remain_unqualified() {
    for (tool, project) in [
        (ToolId::Antigravity, false),
        (ToolId::AntigravityCli, false),
        (ToolId::AntigravityCli, true),
    ] {
        let f = fixture_kind_source(None, false, tool, true, true);
        let before = fs::read(&f.source).unwrap();
        let registry = fs::read(f.root.join("components/registry.yml")).unwrap();
        // User agents are not discovered in current policy. Qualify refusal at the
        // published handle boundary without adding a new production discovery root.
        if !project {
            let mut publication = f.local.publication("local").unwrap();
            for instance in &mut publication.snapshot.instances {
                instance.scope = crate::contexts::local::domain::Scope::User;
            }
            publication.snapshot.snapshot_id = "user-local".into();
            f.local
                .publish_scan_result_for_test(
                    "user-scan",
                    LocalScanExecutorResult::Complete(publication),
                )
                .unwrap();
            assert_eq!(
                f.sot
                    .preview_component_import(
                        &f.local,
                        &f.checkout_id,
                        &f.sot_id,
                        "user-local",
                        &f.instance_id,
                        &f.source_revision
                    )
                    .unwrap_err(),
                "import_standalone_skill_required"
            );
        } else {
            assert_eq!(
                support_preview(&f).unwrap_err(),
                "import_standalone_skill_required"
            );
        }
        assert_eq!(fs::read(&f.source).unwrap(), before);
        assert_eq!(
            fs::read(f.root.join("components/registry.yml")).unwrap(),
            registry
        );
        assert!(!f.root.join("components/agents/fixture-import").exists());
    }
}
