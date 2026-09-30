use std::path::{Path, PathBuf};

use harness_desktop_lib::contexts::local::domain::Scope;
use harness_desktop_lib::contexts::local::roots::RootEnumeratorId;
use harness_desktop_lib::contexts::local::{
    collect_metadata_observations, evaluate_metadata_probe, fixture_manifest_sha256,
    AdapterCatalog, EvidenceDigestAlgorithm, IgnorePolicyId, MetadataProbeFilesystem,
    MetadataProbeObservation, MetadataProbeOutcome, ParserId, ProbeStatus, RedactionPolicyId,
    SurfaceKind, ToolId, VersionProbeId,
};

fn manifest_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn assert_file(path: &Path) {
    assert!(
        path.is_file(),
        "missing M3 catalog file: {}",
        path.display()
    );
}

#[test]
fn m3_catalog_public_surface_and_embedded_pairs_are_present() {
    let root = manifest_dir();
    let local = root.join("src/contexts/local");

    for relative in ["adapter.rs", "catalog.rs", "probe.rs", "adapters/mod.rs"] {
        assert_file(&local.join(relative));
    }

    let module = std::fs::read_to_string(local.join("mod.rs")).unwrap();
    for declaration in [
        "mod adapters;",
        "mod adapter;",
        "mod catalog;",
        "mod probe;",
    ] {
        assert!(
            module.contains(declaration),
            "LocalContext must declare {declaration}"
        );
    }

    let catalog = std::fs::read_to_string(local.join("catalog.rs")).unwrap();
    assert!(catalog.contains("pub struct AdapterCatalog"));
    assert!(catalog.contains("pub fn load_embedded"));
    assert!(catalog.contains("pub fn adapters"));

    for stem in [
        "codex",
        "claude-code",
        "antigravity",
        "antigravity-cli",
        "hermes",
    ] {
        assert_file(&local.join(format!("adapters/{stem}.yml")));
        assert_file(&local.join(format!("adapters/{stem}.qualification.json")));
    }
}

#[test]
fn embedded_catalog_load_is_available() {
    assert!(
        AdapterCatalog::load_embedded().is_ok(),
        "the approved embedded catalog must load without runtime discovery"
    );
}

#[test]
fn embedded_catalog_contains_exactly_the_five_approved_adapters() {
    let catalog = AdapterCatalog::load_embedded().unwrap();
    let adapter_ids = catalog
        .adapters()
        .iter()
        .map(|adapter| adapter.adapter_id.as_str())
        .collect::<Vec<_>>();

    assert_eq!(
        adapter_ids,
        [
            "antigravity",
            "antigravity-cli",
            "claude-code",
            "codex",
            "hermes",
        ]
    );
}

fn is_lower_hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

#[test]
fn descriptors_qualifications_and_fixture_manifests_are_cryptographically_bound() {
    let catalog = AdapterCatalog::load_embedded().unwrap();
    let expected = [
        (ToolId::Antigravity, "antigravity", "2.8.1"),
        (ToolId::AntigravityCli, "antigravity-cli", "1.1.22"),
        (ToolId::ClaudeCode, "claude-code", "2.1.202"),
        (ToolId::Codex, "codex", "0.150.1"),
        (ToolId::Hermes, "hermes", "0.20.6"),
    ];

    for (tool_id, fixture_stem, observed_version) in expected {
        let adapter = catalog.for_tool(tool_id).unwrap();
        assert_eq!(adapter.descriptor.tool_id, tool_id);
        assert_eq!(
            adapter.qualification.observed_runtime.tool_version,
            observed_version
        );
        assert_eq!(
            adapter.qualification.descriptor_canonical_sha256,
            adapter.descriptor_canonical_sha256
        );
        assert!(is_lower_hex(&adapter.descriptor_canonical_sha256, 64));
        assert!(is_lower_hex(&adapter.qualification_record_sha256, 64));
        assert!(is_lower_hex(
            &adapter.qualification.fixture_manifest_sha256,
            64
        ));
        let fixture_root = manifest_dir()
            .join("tests/fixtures/discovery/catalog")
            .join(fixture_stem);
        assert_eq!(
            fixture_manifest_sha256(&fixture_root).unwrap(),
            adapter.qualification.fixture_manifest_sha256
        );
        assert!(
            adapter
                .qualification
                .evidence_sources
                .iter()
                .any(|source| source.source_id.starts_with("https://")),
            "{fixture_stem} must retain an official primary source"
        );
    }

    let cli = catalog.for_tool(ToolId::AntigravityCli).unwrap();
    assert_eq!(
        cli.qualification.probe_results[0].status,
        ProbeStatus::Passed
    );
    assert_eq!(
        cli.qualification.probe_results[0].evidence_algorithm,
        Some(EvidenceDigestAlgorithm::Sha512)
    );
    assert_eq!(
        cli.qualification.probe_results[0]
            .evidence_digest
            .as_deref(),
        Some("03b36320ce79d5f80a360c4fc129512b0e0568acd2e510f5e968ef794ae4ca2e8b3acc54f3613ce1862e47c4180a4bd20c565bca879e151b2ee94faa2a928a51")
    );
}

#[test]
fn compiled_descriptor_matrix_matches_the_approved_design() {
    let catalog = AdapterCatalog::load_embedded().unwrap();
    let matrix = [
        (
            ToolId::Codex,
            RootEnumeratorId::CodexHomeMarkersV1,
            VersionProbeId::CodexPackageJsonV1,
            vec![
                ParserId::SkillFrontmatterV1,
                ParserId::CodexTomlV1,
                ParserId::HookJsonV1,
                ParserId::MarkdownRuleV1,
            ],
            vec![
                SurfaceKind::Skill,
                SurfaceKind::Agent,
                SurfaceKind::Hook,
                SurfaceKind::Rule,
                SurfaceKind::Command,
                SurfaceKind::Unclassified,
            ],
        ),
        (
            ToolId::ClaudeCode,
            RootEnumeratorId::ClaudeHomeMarkersV2,
            VersionProbeId::ClaudeCliMetadataV1,
            vec![
                ParserId::SkillFrontmatterV1,
                ParserId::ClaudeSettingsV1,
                ParserId::ClaudeWorkflowJsMetadataV1,
                ParserId::MarkdownRuleV1,
            ],
            vec![
                SurfaceKind::Skill,
                SurfaceKind::Agent,
                SurfaceKind::Hook,
                SurfaceKind::Rule,
                SurfaceKind::Command,
                SurfaceKind::Workflow,
                SurfaceKind::Unclassified,
            ],
        ),
        (
            ToolId::Antigravity,
            RootEnumeratorId::AntigravityHomeMarkersV2,
            VersionProbeId::AntigravityInfoPlistV1,
            vec![
                ParserId::SkillFrontmatterV1,
                ParserId::AntigravityManifestV1,
                ParserId::HookJsonV1,
                ParserId::MarkdownRuleV1,
            ],
            vec![
                SurfaceKind::Skill,
                SurfaceKind::Agent,
                SurfaceKind::Hook,
                SurfaceKind::Rule,
                SurfaceKind::Workflow,
                SurfaceKind::Unclassified,
            ],
        ),
        (
            ToolId::AntigravityCli,
            RootEnumeratorId::AntigravityCliHomeMarkersV2,
            VersionProbeId::AntigravityCliManifestV1,
            vec![
                ParserId::AntigravityCliJsonV1,
                ParserId::SkillFrontmatterV1,
                ParserId::HookJsonV1,
                ParserId::MarkdownRuleV1,
            ],
            vec![
                SurfaceKind::Skill,
                SurfaceKind::Agent,
                SurfaceKind::Hook,
                SurfaceKind::Rule,
                SurfaceKind::Command,
                SurfaceKind::Workflow,
                SurfaceKind::Unclassified,
            ],
        ),
        (
            ToolId::Hermes,
            RootEnumeratorId::HermesExternalDirsV1,
            VersionProbeId::HermesDistInfoV1,
            vec![
                ParserId::SkillFrontmatterV1,
                ParserId::HermesYamlV1,
                ParserId::MarkdownRuleV1,
            ],
            vec![
                SurfaceKind::Skill,
                SurfaceKind::Hook,
                SurfaceKind::Rule,
                SurfaceKind::Unclassified,
            ],
        ),
    ];

    for (tool_id, enumerator, probe, parsers, kinds) in matrix {
        let descriptor = &catalog.for_tool(tool_id).unwrap().descriptor;
        assert_eq!(descriptor.version_probe_id, probe);
        assert_eq!(descriptor.surfaces.len(), 2);
        assert_eq!(
            descriptor
                .surfaces
                .iter()
                .map(|surface| surface.scope)
                .collect::<Vec<_>>(),
            [Scope::Project, Scope::User]
        );
        for surface in &descriptor.surfaces {
            assert_eq!(surface.root_enumerator_id, enumerator);
            assert_eq!(surface.parser_ids, parsers);
            assert_eq!(surface.supported_kinds, kinds);
            assert_eq!(
                surface.ignore_policy_id,
                IgnorePolicyId::LocalDiscoveryIgnoreV1
            );
            assert_eq!(
                surface.redaction_policy_id,
                RedactionPolicyId::LocalSecretRedactionV1
            );
        }
    }
}

#[test]
fn metadata_probe_never_executes_a_tool_and_tool_absent_keeps_static_qualification() {
    let catalog = AdapterCatalog::load_embedded().unwrap();
    for adapter in catalog.adapters() {
        let absent = evaluate_metadata_probe(
            adapter,
            &MetadataProbeObservation {
                install_metadata_present: false,
                declared_surface_present: false,
                observed_tool_version: None,
                observed_schema_digest: None,
            },
        );
        assert_eq!(absent, MetadataProbeOutcome::ToolAbsent);
    }

    let codex = catalog.for_tool(ToolId::Codex).unwrap();
    assert!(matches!(
        evaluate_metadata_probe(
            codex,
            &MetadataProbeObservation {
                install_metadata_present: true,
                declared_surface_present: true,
                observed_tool_version: Some("0.150.1".to_string()),
                observed_schema_digest: None,
            }
        ),
        MetadataProbeOutcome::ToolPresent { .. }
    ));
    assert_eq!(
        evaluate_metadata_probe(
            codex,
            &MetadataProbeObservation {
                install_metadata_present: true,
                declared_surface_present: true,
                observed_tool_version: Some("0.150.2".to_string()),
                observed_schema_digest: None,
            }
        ),
        MetadataProbeOutcome::Unavailable {
            reason: "version_mismatch"
        }
    );

    let probe_source =
        std::fs::read_to_string(manifest_dir().join("src/contexts/local/probe.rs")).unwrap();
    for forbidden in ["std::process", "Command::new", ".output()", "executable"] {
        assert!(
            !probe_source.contains(forbidden),
            "metadata probe must not contain {forbidden}"
        );
    }
}

#[test]
fn claude_probe_contract_binds_same_fd_lineage_and_qualified_binary_digest() {
    let source =
        std::fs::read_to_string(manifest_dir().join("src/contexts/local/probe.rs")).unwrap();
    let start = source.find("fn symlink_target_version").unwrap();
    let end = source[start..]
        .find("fn metadata_header")
        .map(|offset| start + offset)
        .unwrap();
    let claude_probe = &source[start..end];
    for required in ["openat", "readlinkat", "OFlags::NOFOLLOW", "Sha256"] {
        assert!(
            claude_probe.contains(required),
            "Claude probe must bind {required} in its same-FD metadata lineage"
        );
    }
    for forbidden in ["canonicalize", "File::open"] {
        assert!(
            !claude_probe.contains(forbidden),
            "Claude probe must not re-resolve validated paths with {forbidden}"
        );
    }

    let qualification: serde_json::Value = serde_json::from_str(include_str!(
        "../src/contexts/local/adapters/claude-code.qualification.json"
    ))
    .unwrap();
    let probe = &qualification["probe_results"][0];
    assert_eq!(probe["artifact_digest_algorithm"], "sha256");
    assert_eq!(
        probe["artifact_digest"],
        "7414f707861e2fe5afef33a466f888a8d2170e5028f5e9d2858f1d3ef45ffca5"
    );
}

#[test]
fn claude_metadata_probe_requires_qualified_digest_and_rejects_outside_target() {
    use std::os::unix::fs::{symlink, PermissionsExt};

    let temp = tempfile::tempdir().unwrap();
    let home = temp.path().join("home");
    let launcher = home.join(".local/bin/claude");
    let version_root = home.join(".local/share/claude/versions");
    std::fs::create_dir_all(launcher.parent().unwrap()).unwrap();
    std::fs::create_dir_all(&version_root).unwrap();
    std::fs::create_dir_all(home.join(".claude")).unwrap();
    let version_binary = version_root.join("2.1.202");
    std::fs::write(&version_binary, b"\xcf\xfa\xed\xfe\x0c\x00\x00\x01").unwrap();
    std::fs::set_permissions(&version_binary, std::fs::Permissions::from_mode(0o755)).unwrap();
    symlink("../share/claude/versions/2.1.202", &launcher).unwrap();

    let catalog = AdapterCatalog::load_embedded().unwrap();
    let filesystem = MetadataProbeFilesystem::new(
        home.clone(),
        temp.path().join("Applications"),
        vec![home.join(".local")],
    );
    let observation = collect_metadata_observations(&catalog, &filesystem)
        .remove(&ToolId::ClaudeCode)
        .unwrap();
    assert_eq!(observation.observed_tool_version, None);
    assert_eq!(
        evaluate_metadata_probe(catalog.for_tool(ToolId::ClaudeCode).unwrap(), &observation),
        MetadataProbeOutcome::Unavailable {
            reason: "version_unknown"
        }
    );

    std::fs::remove_file(&launcher).unwrap();
    let outside = temp.path().join("outside/2.1.202");
    std::fs::create_dir_all(outside.parent().unwrap()).unwrap();
    std::fs::write(&outside, b"\xcf\xfa\xed\xfe\x0c\x00\x00\x01").unwrap();
    std::fs::set_permissions(&outside, std::fs::Permissions::from_mode(0o755)).unwrap();
    symlink("../../../outside/2.1.202", &launcher).unwrap();
    let escaped = collect_metadata_observations(&catalog, &filesystem)
        .remove(&ToolId::ClaudeCode)
        .unwrap();
    assert_eq!(escaped.observed_tool_version, None);
    assert_eq!(
        evaluate_metadata_probe(catalog.for_tool(ToolId::ClaudeCode).unwrap(), &escaped),
        MetadataProbeOutcome::Unavailable {
            reason: "version_unknown"
        }
    );
}

#[test]
fn claude_metadata_probe_rejects_symlinked_version_authority_and_non_executable_target() {
    use std::os::unix::fs::{symlink, PermissionsExt};

    let temp = tempfile::tempdir().unwrap();
    let home = temp.path().join("home");
    let launcher = home.join(".local/bin/claude");
    let version_root = home.join(".local/share/claude/versions");
    let external_root = temp.path().join("external-versions");
    std::fs::create_dir_all(launcher.parent().unwrap()).unwrap();
    std::fs::create_dir_all(version_root.parent().unwrap()).unwrap();
    std::fs::create_dir_all(&external_root).unwrap();
    let external_binary = external_root.join("2.1.202");
    std::fs::write(&external_binary, b"\xcf\xfa\xed\xfe\x0c\x00\x00\x01").unwrap();
    std::fs::set_permissions(&external_binary, std::fs::Permissions::from_mode(0o755)).unwrap();
    symlink(&external_root, &version_root).unwrap();
    symlink("../share/claude/versions/2.1.202", &launcher).unwrap();

    let catalog = AdapterCatalog::load_embedded().unwrap();
    let filesystem = MetadataProbeFilesystem::new(
        home.clone(),
        temp.path().join("Applications"),
        vec![home.join(".local")],
    );
    let symlinked_root = collect_metadata_observations(&catalog, &filesystem)
        .remove(&ToolId::ClaudeCode)
        .unwrap();
    assert_eq!(symlinked_root.observed_tool_version, None);

    std::fs::remove_file(&launcher).unwrap();
    std::fs::remove_file(&version_root).unwrap();
    std::fs::create_dir_all(&version_root).unwrap();
    let version_binary = version_root.join("2.1.202");
    std::fs::write(&version_binary, b"\xcf\xfa\xed\xfe\x0c\x00\x00\x01").unwrap();
    symlink("../share/claude/versions/2.1.202", &launcher).unwrap();
    let non_executable = collect_metadata_observations(&catalog, &filesystem)
        .remove(&ToolId::ClaudeCode)
        .unwrap();
    assert_eq!(non_executable.observed_tool_version, None);

    std::fs::write(&version_binary, b"executable text is not a Mach-O binary\n").unwrap();
    std::fs::set_permissions(&version_binary, std::fs::Permissions::from_mode(0o755)).unwrap();
    let non_macho = collect_metadata_observations(&catalog, &filesystem)
        .remove(&ToolId::ClaudeCode)
        .unwrap();
    assert_eq!(non_macho.observed_tool_version, None);

    std::fs::write(&version_binary, b"\xcf\xfa\xed\xfe\x0c\x00\x00\x01").unwrap();
    std::fs::OpenOptions::new()
        .write(true)
        .open(&version_binary)
        .unwrap()
        .set_len(268_435_457)
        .unwrap();
    let oversized = collect_metadata_observations(&catalog, &filesystem)
        .remove(&ToolId::ClaudeCode)
        .unwrap();
    assert_eq!(oversized.observed_tool_version, None);
}
