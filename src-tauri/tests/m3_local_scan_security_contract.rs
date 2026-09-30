use std::fs;
use std::path::Path;

use harness_desktop_lib::contexts::local::domain::{
    CoverageStatus, ParseState, Scope, SurfacePresence,
};
use harness_desktop_lib::contexts::local::scanner::LocalScanner;
use harness_desktop_lib::contexts::local::{AdapterCatalog, SurfaceKind, ToolId};
use tempfile::tempdir;

fn write(path: &Path, body: &str) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, body).unwrap();
}

fn skill(name: &str) -> String {
    format!("---\nname: {name}\ndescription: fixture\n---\nprivate body\n")
}

#[test]
fn antigravity_reads_only_declared_user_paths_not_container_app_data() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    write(
        &home.join(".gemini/config/skills/allowed/SKILL.md"),
        &skill("allowed-skill"),
    );
    write(
        &home.join(".gemini/history/skills/forbidden/SKILL.md"),
        &skill("history-secret"),
    );
    write(
        &home.join(".gemini/auth/hooks.json"),
        r#"{"hooks":{"BeforeAgent":[{"command":"must-not-read"}]}}"#,
    );

    let catalog = AdapterCatalog::load_embedded().unwrap();
    let adapter = catalog.for_tool(ToolId::Antigravity).unwrap().clone();
    let result = LocalScanner::scan(&home, &[adapter]).unwrap();

    assert!(result
        .instances
        .iter()
        .any(|instance| instance.name.as_deref() == Some("allowed-skill")));
    assert!(result.instances.iter().all(|instance| {
        instance.name.as_deref() != Some("history-secret")
            && !instance.stable_source_locator.contains("history")
            && !instance.stable_source_locator.contains("auth")
    }));
}

#[cfg(unix)]
#[test]
fn hermes_external_root_requires_no_follow_config_provenance() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    let outside = fixture.path().join("outside");
    let external = outside.join("external-skills");
    write(&external.join("secret/SKILL.md"), &skill("external-secret"));
    write(
        &outside.join("config.yaml"),
        &format!("skills:\n  external_dirs:\n    - {}\n", external.display()),
    );
    fs::create_dir_all(home.join(".hermes")).unwrap();
    std::os::unix::fs::symlink(
        outside.join("config.yaml"),
        home.join(".hermes/config.yaml"),
    )
    .unwrap();

    let catalog = AdapterCatalog::load_embedded().unwrap();
    let adapter = catalog.for_tool(ToolId::Hermes).unwrap().clone();
    let result = LocalScanner::scan(&home, &[adapter]).unwrap();

    assert!(result
        .instances
        .iter()
        .all(|instance| instance.name.as_deref() != Some("external-secret")));
    assert!(result
        .issues
        .iter()
        .any(|issue| issue.code == "config_provenance_symlink_rejected"));
}

#[test]
fn depth_bound_is_attributed_to_partial_root_coverage() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    let mut deep = home.join(".codex/skills");
    for index in 0..12 {
        deep.push(format!("level-{index}"));
    }
    write(&deep.join("SKILL.md"), &skill("too-deep"));

    let catalog = AdapterCatalog::load_embedded().unwrap();
    let adapter = catalog.for_tool(ToolId::Codex).unwrap().clone();
    let result = LocalScanner::scan(&home, &[adapter]).unwrap();

    assert!(result
        .instances
        .iter()
        .all(|instance| instance.name.as_deref() != Some("too-deep")));
    assert!(result
        .issues
        .iter()
        .any(|issue| issue.code == "scan_depth_bound_reached"));
    assert!(result
        .skipped_paths
        .iter()
        .any(|skipped| skipped.reason_code == "scan_depth_bound_reached"));
    assert!(result.coverage.iter().any(|coverage| {
        coverage.scope == Scope::User && coverage.status == CoverageStatus::Partial
    }));
}

#[test]
fn oversized_file_is_not_read_and_is_attributed_to_partial_coverage() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    let path = home.join(".codex/skills/huge/SKILL.md");
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(&path, vec![b'x'; 4 * 1024 * 1024 + 1]).unwrap();

    let catalog = AdapterCatalog::load_embedded().unwrap();
    let adapter = catalog.for_tool(ToolId::Codex).unwrap().clone();
    let result = LocalScanner::scan(&home, &[adapter]).unwrap();

    assert!(result.instances.iter().any(|instance| {
        instance
            .stable_source_locator
            .ends_with("skills/huge/SKILL.md")
            && instance.parse_state == ParseState::Unreadable
            && instance.content_hash.is_none()
    }));
    assert!(result
        .issues
        .iter()
        .any(|issue| issue.code == "file_size_limit_exceeded"));
    assert!(result
        .skipped_paths
        .iter()
        .any(|skipped| skipped.reason_code == "file_size_limit_exceeded"));
    assert!(result.coverage.iter().any(|coverage| {
        coverage.scope == Scope::User && coverage.status == CoverageStatus::Partial
    }));
}

#[test]
fn secret_like_locator_segments_are_redacted_with_stable_opaque_tokens() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    let secret_like_path = [".codex/skills/api_", "key=SUPER_SECRET/SKILL.md"].concat();
    write(
        &home.join(secret_like_path),
        &skill("secret-path-skill"),
    );

    let catalog = AdapterCatalog::load_embedded().unwrap();
    let adapter = catalog.for_tool(ToolId::Codex).unwrap().clone();
    let result = LocalScanner::scan(&home, &[adapter]).unwrap();

    let instance = result
        .instances
        .iter()
        .find(|instance| instance.name.as_deref() == Some("secret-path-skill"))
        .unwrap();
    assert!(instance.stable_source_locator.contains("[redacted-"));
    let serialized = serde_json::to_string(&result).unwrap();
    assert!(!serialized.contains("SUPER_SECRET"));
    assert!(!serialized.to_ascii_lowercase().contains("api_key="));

    let second =
        LocalScanner::scan(&home, &[catalog.for_tool(ToolId::Codex).unwrap().clone()]).unwrap();
    assert_eq!(result, second);
}

#[test]
fn parser_output_kind_must_be_declared_by_the_surface() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    write(
        &home.join(".codex/skills/undeclared/SKILL.md"),
        &skill("undeclared-kind"),
    );

    let catalog = AdapterCatalog::load_embedded().unwrap();
    let mut adapter = catalog.for_tool(ToolId::Codex).unwrap().clone();
    adapter
        .descriptor
        .surfaces
        .iter_mut()
        .find(|surface| surface.scope == Scope::User)
        .unwrap()
        .supported_kinds
        .retain(|kind| *kind != SurfaceKind::Skill);
    let result = LocalScanner::scan(&home, &[adapter]).unwrap();

    assert!(result
        .instances
        .iter()
        .all(|instance| instance.name.as_deref() != Some("undeclared-kind")));
    assert!(result
        .issues
        .iter()
        .any(|issue| issue.code == "unsupported_parser_kind"));
    assert!(result.coverage.iter().any(|coverage| {
        coverage.scope == Scope::User && coverage.status == CoverageStatus::Partial
    }));
}

#[test]
fn scanner_uses_handle_relative_no_follow_lineage_for_traversal_and_reads() {
    let source = fs::read_to_string(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("src/contexts/local/scanner.rs"),
    )
    .unwrap();

    for required in [
        "rustix::fs",
        "Dir::read_from",
        "openat(",
        "OFlags::NOFOLLOW",
        "OFlags::DIRECTORY",
        "fstat(",
        "source_identity_changed",
    ] {
        assert!(
            source.contains(required),
            "missing secure traversal primitive: {required}"
        );
    }
    for forbidden in [
        "WalkDir::new",
        "std::fs::read(entry.path())",
        "std::fs::metadata(entry.path())",
    ] {
        assert!(
            !source.contains(forbidden),
            "path-based traversal remains: {forbidden}"
        );
    }
}

#[test]
fn hermes_profiles_open_only_skills_and_config_branches() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    write(
        &home.join(".hermes/profiles/team/skills/allowed/SKILL.md"),
        &skill("profile-skill"),
    );
    write(
        &home.join(".hermes/profiles/team/hooks/private.yaml"),
        "event: must-not-read\ncommand: private\n",
    );

    let catalog = AdapterCatalog::load_embedded().unwrap();
    let adapter = catalog.for_tool(ToolId::Hermes).unwrap().clone();
    let result = LocalScanner::scan(&home, &[adapter]).unwrap();

    assert!(result
        .instances
        .iter()
        .any(|instance| instance.name.as_deref() == Some("profile-skill")));
    assert!(result.instances.iter().all(|instance| {
        instance.name.as_deref() != Some("must-not-read")
            && !instance
                .stable_source_locator
                .contains("profiles/team/hooks")
    }));
}

#[test]
fn hermes_config_authority_is_read_through_no_follow_handles() {
    let source = fs::read_to_string(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("src/contexts/local/roots.rs"),
    )
    .unwrap();

    for required in [
        "Dir::read_from",
        "openat(",
        "OFlags::NOFOLLOW",
        "fstat(",
        "config_provenance_changed",
    ] {
        assert!(
            source.contains(required),
            "missing config authority primitive: {required}"
        );
    }
    for forbidden in ["std::fs::read(&config)", "std::fs::read_dir(&profiles)"] {
        assert!(
            !source.contains(forbidden),
            "path-based config authority remains: {forbidden}"
        );
    }
}

#[test]
fn project_marker_walk_uses_no_follow_directory_handles() {
    let source = fs::read_to_string(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("src/contexts/local/roots.rs"),
    )
    .unwrap();

    assert!(source.contains("walk_marker_directory"));
    assert!(source.contains("AtFlags::SYMLINK_NOFOLLOW"));
    for forbidden in [
        "std::fs::read_dir(directory)",
        "std::fs::symlink_metadata(&path)",
    ] {
        assert!(
            !source.contains(forbidden),
            "path-based marker walk remains: {forbidden}"
        );
    }
}

#[test]
fn all_project_marker_enumerators_share_one_home_walk_index() {
    let source = fs::read_to_string(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("src/contexts/local/roots.rs"),
    )
    .unwrap();

    assert!(source.contains("struct ProjectMarkerIndex"));
    assert!(source.contains("ProjectMarkerIndex::discover_once"));
    assert!(source.contains("candidates_for(spec.enumerator_id)"));
    assert!(!source.contains("fn discover_project_candidates("));
}

#[cfg(unix)]
#[test]
fn single_walk_preserves_per_enumerator_marker_issue_ownership() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    let outside = fixture.path().join("outside");
    fs::create_dir_all(home.join("workspace/project")).unwrap();
    fs::create_dir_all(&outside).unwrap();
    std::os::unix::fs::symlink(&outside, home.join("workspace/project/.codex")).unwrap();

    let catalog = AdapterCatalog::load_embedded().unwrap();
    let codex = catalog.for_tool(ToolId::Codex).unwrap().clone();
    let claude = catalog.for_tool(ToolId::ClaudeCode).unwrap().clone();
    let codex_adapter_id = codex.adapter_id.clone();
    let claude_adapter_id = claude.adapter_id.clone();
    let result = LocalScanner::scan(&home, &[codex, claude]).unwrap();

    let codex_project = result
        .coverage
        .iter()
        .find(|coverage| {
            coverage.adapter_id == codex_adapter_id && coverage.scope == Scope::Project
        })
        .unwrap();
    assert_eq!(codex_project.status, CoverageStatus::Failed);
    assert_eq!(codex_project.presence, SurfacePresence::Unknown);
    assert!(codex_project
        .issue_codes
        .iter()
        .any(|code| code == "symlink_marker_rejected"));

    let claude_project = result
        .coverage
        .iter()
        .find(|coverage| {
            coverage.adapter_id == claude_adapter_id && coverage.scope == Scope::Project
        })
        .unwrap();
    assert_eq!(claude_project.status, CoverageStatus::Complete);
    assert_eq!(claude_project.presence, SurfacePresence::NotPresent);
    assert!(!claude_project
        .issue_codes
        .iter()
        .any(|code| code == "symlink_marker_rejected"));
}

#[cfg(unix)]
#[test]
fn rejected_project_marker_is_failed_unknown_coverage_not_not_present() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    let outside = fixture.path().join("outside");
    fs::create_dir_all(home.join("workspace/project")).unwrap();
    fs::create_dir_all(&outside).unwrap();
    std::os::unix::fs::symlink(&outside, home.join("workspace/project/.codex")).unwrap();

    let catalog = AdapterCatalog::load_embedded().unwrap();
    let adapter = catalog.for_tool(ToolId::Codex).unwrap().clone();
    let result = LocalScanner::scan(&home, &[adapter]).unwrap();

    let project = result
        .coverage
        .iter()
        .find(|coverage| coverage.scope == Scope::Project)
        .unwrap();
    assert_eq!(project.status, CoverageStatus::Failed);
    assert_eq!(project.presence, SurfacePresence::Unknown);
    assert!(project
        .issue_codes
        .iter()
        .any(|code| code == "symlink_marker_rejected"));
}

#[cfg(unix)]
#[test]
fn denied_declared_user_root_is_failed_unknown_coverage() {
    use std::os::unix::fs::PermissionsExt;

    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    let root = home.join(".codex");
    fs::create_dir_all(&root).unwrap();
    fs::set_permissions(&root, fs::Permissions::from_mode(0o000)).unwrap();

    let catalog = AdapterCatalog::load_embedded().unwrap();
    let adapter = catalog.for_tool(ToolId::Codex).unwrap().clone();
    let result = LocalScanner::scan(&home, &[adapter]).unwrap();
    fs::set_permissions(&root, fs::Permissions::from_mode(0o700)).unwrap();

    let user = result
        .coverage
        .iter()
        .find(|coverage| coverage.scope == Scope::User)
        .unwrap();
    assert_eq!(user.status, CoverageStatus::Failed);
    assert_eq!(user.presence, SurfacePresence::Unknown);
    assert!(user
        .issue_codes
        .iter()
        .any(|code| code == "root_permission_denied"));
}

#[cfg(unix)]
#[test]
fn config_derived_root_rejects_intermediate_symlink_components() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    let outside = fixture.path().join("outside");
    let actual = fixture.path().join("actual/skills");
    write(
        &actual.join("secret/SKILL.md"),
        &skill("symlink-root-secret"),
    );
    fs::create_dir_all(&outside).unwrap();
    std::os::unix::fs::symlink(actual.parent().unwrap(), outside.join("link")).unwrap();
    write(
        &home.join(".hermes/config.yaml"),
        &format!(
            "skills:\n  external_dirs:\n    - {}\n",
            outside.join("link/skills").display()
        ),
    );

    let catalog = AdapterCatalog::load_embedded().unwrap();
    let adapter = catalog.for_tool(ToolId::Hermes).unwrap().clone();
    let result = LocalScanner::scan(&home, &[adapter]).unwrap();

    assert!(result
        .instances
        .iter()
        .all(|instance| instance.name.as_deref() != Some("symlink-root-secret")));
    assert!(result
        .issues
        .iter()
        .any(|issue| issue.code == "symlink_root_component_rejected"));
    assert!(result.coverage.iter().any(|coverage| {
        coverage.scope == Scope::Project
            && coverage.status == CoverageStatus::Failed
            && coverage.presence == SurfacePresence::Unknown
    }));
}
