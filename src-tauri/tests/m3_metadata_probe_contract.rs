use std::fs;
use std::path::PathBuf;

use harness_desktop_lib::contexts::local::{
    collect_metadata_observations, evaluate_metadata_probe, AdapterCatalog,
    MetadataProbeFilesystem, MetadataProbeOutcome, ToolId,
};
use tempfile::tempdir;

#[cfg(unix)]
use std::os::unix::fs::{symlink, PermissionsExt};

fn write(path: PathBuf, body: &str) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, body).unwrap();
}

#[test]
#[cfg(unix)]
fn compiled_probes_read_exact_local_metadata_without_executing_tools() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    let applications = fixture.path().join("Applications");
    let codex_release =
        home.join(".codex/packages/standalone/releases/0.150.1-aarch64-apple-darwin");
    write(
        codex_release.join("codex-package.json"),
        r#"{
  "layoutVersion": 1,
  "version": "0.150.1",
  "target": "aarch64-apple-darwin",
  "variant": "codex",
  "entrypoint": "bin/codex",
  "resourcesDir": "codex-resources",
  "pathDir": "codex-path"
}
"#,
    );
    symlink(
        "releases/0.150.1-aarch64-apple-darwin",
        home.join(".codex/packages/standalone/current"),
    )
    .unwrap();
    let claude_target = home.join(".local/share/claude/versions/2.1.202");
    fs::create_dir_all(claude_target.parent().unwrap()).unwrap();
    fs::write(&claude_target, b"\xcf\xfa\xed\xfe\x0c\x00\x00\x01").unwrap();
    fs::set_permissions(&claude_target, fs::Permissions::from_mode(0o755)).unwrap();
    fs::create_dir_all(home.join(".local/bin")).unwrap();
    symlink(&claude_target, home.join(".local/bin/claude")).unwrap();
    write(
        applications.join("Antigravity.app/Contents/Info.plist"),
        r#"<?xml version="1.0" encoding="UTF-8"?>
        <plist version="1.0"><dict>
          <key>CFBundleShortVersionString</key><string>2.8.1</string>
        </dict></plist>"#,
    );
    write(
        home.join(".hermes/hermes-agent/hermes_agent.egg-info/PKG-INFO"),
        "Metadata-Version: 2.4\nName: hermes-agent\nVersion: 0.20.6\n",
    );
    for relative in [".codex", ".claude", ".gemini/config", ".hermes"] {
        fs::create_dir_all(home.join(relative)).unwrap();
    }

    let catalog = AdapterCatalog::load_embedded().unwrap();
    let observations = collect_metadata_observations(
        &catalog,
        &MetadataProbeFilesystem::new(home, applications, Vec::new()),
    );

    for (tool, expected) in [
        (ToolId::Codex, "0.150.1"),
        (ToolId::Antigravity, "2.8.1"),
        (ToolId::Hermes, "0.20.6"),
    ] {
        let adapter = catalog
            .adapters()
            .iter()
            .find(|adapter| adapter.descriptor.tool_id == tool)
            .unwrap();
        assert_eq!(
            evaluate_metadata_probe(adapter, observations.get(&tool).unwrap()),
            MetadataProbeOutcome::ToolPresent {
                current_tool_version_or_schema_digest: expected.to_string(),
            }
        );
    }
    let claude = catalog.for_tool(ToolId::ClaudeCode).unwrap();
    assert_eq!(
        evaluate_metadata_probe(claude, observations.get(&ToolId::ClaudeCode).unwrap()),
        MetadataProbeOutcome::Unavailable {
            reason: "version_unknown"
        }
    );
    let cli = catalog
        .adapters()
        .iter()
        .find(|adapter| adapter.descriptor.tool_id == ToolId::AntigravityCli)
        .unwrap();
    assert_eq!(
        evaluate_metadata_probe(cli, observations.get(&ToolId::AntigravityCli).unwrap()),
        MetadataProbeOutcome::ToolAbsent
    );
}

#[test]
fn a_declared_cli_surface_without_verified_binary_is_not_called_absent_or_supported() {
    let fixture = tempdir().unwrap();
    let home = fixture.path().join("home");
    write(home.join(".gemini/antigravity-cli/settings.json"), "{}");
    let catalog = AdapterCatalog::load_embedded().unwrap();
    let observations = collect_metadata_observations(
        &catalog,
        &MetadataProbeFilesystem::new(home, fixture.path().join("Applications"), Vec::new()),
    );
    let cli = catalog
        .adapters()
        .iter()
        .find(|adapter| adapter.descriptor.tool_id == ToolId::AntigravityCli)
        .unwrap();

    assert_eq!(
        evaluate_metadata_probe(cli, observations.get(&ToolId::AntigravityCli).unwrap()),
        MetadataProbeOutcome::Unavailable {
            reason: "version_unknown"
        }
    );
}

#[test]
fn metadata_probe_source_has_no_subprocess_execution_boundary() {
    let source = fs::read_to_string("src/contexts/local/probe.rs").unwrap();
    for forbidden in ["std::process", "Command::new", ".output()", ".status()"] {
        assert!(
            !source.contains(forbidden),
            "forbidden probe token: {forbidden}"
        );
    }
}
