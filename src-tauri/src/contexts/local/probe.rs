//! Metadata-only tool version probe contracts.

use std::collections::BTreeMap;
use std::ffi::OsStr;
use std::fs::File;
use std::io::{Cursor, Read};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::MetadataExt;
use std::path::{Component, Path, PathBuf};

use rustix::fs::{openat, readlinkat, Mode, OFlags, CWD};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256, Sha512};

use super::adapter::{EvidenceDigestAlgorithm, ToolId, VersionSupportKind};
use super::catalog::{AdapterCatalog, CatalogAdapter};

const MAX_METADATA_BYTES: u64 = 1_048_576;
const MAX_CLI_BYTES: u64 = 268_435_456;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MetadataProbeFilesystem {
    home: PathBuf,
    applications: PathBuf,
    package_prefixes: Vec<PathBuf>,
    claude_config_dir: Option<PathBuf>,
}

impl MetadataProbeFilesystem {
    pub fn new(home: PathBuf, applications: PathBuf, package_prefixes: Vec<PathBuf>) -> Self {
        Self {
            home,
            applications,
            package_prefixes,
            claude_config_dir: None,
        }
    }

    pub fn for_macos(home: PathBuf) -> Self {
        let claude_config_dir = std::env::var_os("CLAUDE_CONFIG_DIR")
            .filter(|value| !value.is_empty())
            .map(PathBuf::from);
        let package_prefixes = vec![
            PathBuf::from("/opt/homebrew"),
            PathBuf::from("/usr/local"),
            home.join(".local"),
        ];
        Self {
            home,
            applications: PathBuf::from("/Applications"),
            package_prefixes,
            claude_config_dir,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum VersionProbeId {
    CodexPackageJsonV1,
    ClaudeCliMetadataV1,
    AntigravityInfoPlistV1,
    AntigravityCliManifestV1,
    HermesDistInfoV1,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MetadataProbeObservation {
    pub install_metadata_present: bool,
    pub declared_surface_present: bool,
    pub observed_tool_version: Option<String>,
    pub observed_schema_digest: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MetadataProbeOutcome {
    ToolAbsent,
    ToolPresent {
        current_tool_version_or_schema_digest: String,
    },
    Unavailable {
        reason: &'static str,
    },
}

pub fn collect_metadata_observations(
    catalog: &AdapterCatalog,
    filesystem: &MetadataProbeFilesystem,
) -> BTreeMap<ToolId, MetadataProbeObservation> {
    catalog
        .adapters()
        .iter()
        .map(|adapter| {
            (
                adapter.descriptor.tool_id,
                observe_adapter(adapter, filesystem),
            )
        })
        .collect()
}

fn observe_adapter(
    adapter: &CatalogAdapter,
    filesystem: &MetadataProbeFilesystem,
) -> MetadataProbeObservation {
    match adapter.descriptor.tool_id {
        ToolId::Codex => observe_codex(filesystem),
        ToolId::ClaudeCode => observe_claude(adapter, filesystem),
        ToolId::Antigravity => observe_antigravity(filesystem),
        ToolId::AntigravityCli => observe_antigravity_cli(adapter, filesystem),
        ToolId::Hermes => observe_hermes(filesystem),
    }
}

fn observe_codex(filesystem: &MetadataProbeFilesystem) -> MetadataProbeObservation {
    let standalone_metadata = filesystem
        .home
        .join(".codex/packages/standalone/current/codex-package.json");
    let (standalone_present, standalone_version) =
        first_json_version(std::iter::once(standalone_metadata));
    let (legacy_present, legacy_version) = if standalone_present {
        (false, None)
    } else {
        let candidates = filesystem
            .package_prefixes
            .iter()
            .map(|prefix| prefix.join("lib/node_modules/@openai/codex/package.json"));
        first_json_version(candidates)
    };
    MetadataProbeObservation {
        install_metadata_present: standalone_present || legacy_present,
        declared_surface_present: filesystem.home.join(".codex").is_dir(),
        observed_tool_version: standalone_version.or(legacy_version),
        observed_schema_digest: None,
    }
}

fn observe_claude(
    adapter: &CatalogAdapter,
    filesystem: &MetadataProbeFilesystem,
) -> MetadataProbeObservation {
    let launcher = filesystem.home.join(".local/bin/claude");
    let version_root = filesystem.home.join(".local/share/claude/versions");
    let expected_digest = adapter
        .qualification
        .probe_results
        .first()
        .filter(|probe| probe.probe_id == adapter.descriptor.version_probe_id)
        .filter(|probe| probe.artifact_digest_algorithm == Some(EvidenceDigestAlgorithm::Sha256))
        .and_then(|probe| probe.artifact_digest.as_deref());
    let (present, version) = expected_digest.map_or_else(
        || (std::fs::symlink_metadata(&launcher).is_ok(), None),
        |digest| symlink_target_version(&launcher, &version_root, &filesystem.home, digest),
    );
    let declared_surface_present = filesystem
        .claude_config_dir
        .as_ref()
        .is_some_and(|path| path.is_dir())
        || filesystem.home.join(".claude").is_dir();
    MetadataProbeObservation {
        install_metadata_present: present,
        declared_surface_present,
        observed_tool_version: version,
        observed_schema_digest: None,
    }
}

fn observe_antigravity(filesystem: &MetadataProbeFilesystem) -> MetadataProbeObservation {
    let info = filesystem
        .applications
        .join("Antigravity.app/Contents/Info.plist");
    let present =
        std::fs::symlink_metadata(&info).is_ok_and(|metadata| metadata.file_type().is_file());
    let version = read_bounded(&info, MAX_METADATA_BYTES).and_then(|bytes| {
        let value = plist::Value::from_reader(Cursor::new(bytes)).ok()?;
        value
            .as_dictionary()?
            .get("CFBundleShortVersionString")?
            .as_string()
            .map(str::to_owned)
    });
    MetadataProbeObservation {
        install_metadata_present: present,
        declared_surface_present: filesystem.home.join(".gemini/config").is_dir()
            || filesystem.home.join(".gemini/GEMINI.md").is_file(),
        observed_tool_version: version,
        observed_schema_digest: None,
    }
}

fn observe_antigravity_cli(
    adapter: &CatalogAdapter,
    filesystem: &MetadataProbeFilesystem,
) -> MetadataProbeObservation {
    let binary = filesystem.home.join(".local/bin/agy");
    let present =
        std::fs::symlink_metadata(&binary).is_ok_and(|metadata| metadata.file_type().is_file());
    let digest = present.then(|| sha512_file(&binary)).flatten();
    let version = digest.and_then(|digest| {
        adapter
            .qualification
            .probe_results
            .iter()
            .find(|result| {
                result.evidence_algorithm == Some(EvidenceDigestAlgorithm::Sha512)
                    && result.evidence_digest.as_deref() == Some(digest.as_str())
            })
            .map(|result| result.observed_tool_version.clone())
    });
    MetadataProbeObservation {
        install_metadata_present: present,
        declared_surface_present: filesystem.home.join(".gemini/antigravity-cli").is_dir(),
        observed_tool_version: version,
        observed_schema_digest: None,
    }
}

fn observe_hermes(filesystem: &MetadataProbeFilesystem) -> MetadataProbeObservation {
    let metadata = filesystem
        .home
        .join(".hermes/hermes-agent/hermes_agent.egg-info/PKG-INFO");
    let present =
        std::fs::symlink_metadata(&metadata).is_ok_and(|metadata| metadata.file_type().is_file());
    let version = read_bounded(&metadata, MAX_METADATA_BYTES)
        .and_then(|bytes| String::from_utf8(bytes).ok())
        .and_then(|text| metadata_header(&text, "Version"));
    MetadataProbeObservation {
        install_metadata_present: present,
        declared_surface_present: filesystem.home.join(".hermes").is_dir(),
        observed_tool_version: version,
        observed_schema_digest: None,
    }
}

fn first_json_version(paths: impl Iterator<Item = PathBuf>) -> (bool, Option<String>) {
    let mut present = false;
    for path in paths {
        let exists =
            std::fs::symlink_metadata(&path).is_ok_and(|metadata| metadata.file_type().is_file());
        present |= exists;
        let version = read_bounded(&path, MAX_METADATA_BYTES)
            .and_then(|bytes| serde_json::from_slice::<serde_json::Value>(&bytes).ok())
            .and_then(|value| value.get("version")?.as_str().map(str::to_owned));
        if version.is_some() {
            return (present, version);
        }
    }
    (present, None)
}

fn symlink_target_version(
    launcher: &Path,
    allowed_root: &Path,
    authority_root: &Path,
    expected_digest: &str,
) -> (bool, Option<String>) {
    let Ok(metadata) = std::fs::symlink_metadata(launcher) else {
        return (false, None);
    };
    if !metadata.file_type().is_symlink() {
        return (metadata.file_type().is_file(), None);
    }
    if launcher
        .parent()
        .and_then(|path| path.strip_prefix(authority_root).ok())
        != Some(Path::new(".local/bin"))
        || allowed_root.strip_prefix(authority_root).ok()
            != Some(Path::new(".local/share/claude/versions"))
    {
        return (true, None);
    }
    let directory_flags = OFlags::RDONLY | OFlags::DIRECTORY | OFlags::NOFOLLOW | OFlags::CLOEXEC;
    let file_flags = OFlags::RDONLY | OFlags::NOFOLLOW | OFlags::CLOEXEC;
    let Ok(home_fd) = openat(CWD, authority_root, directory_flags, Mode::empty()) else {
        return (true, None);
    };
    let Ok(local_fd) = openat(&home_fd, ".local", directory_flags, Mode::empty()) else {
        return (true, None);
    };
    let Ok(bin_fd) = openat(&local_fd, "bin", directory_flags, Mode::empty()) else {
        return (true, None);
    };
    let Ok(target) = readlinkat(&bin_fd, "claude", Vec::new()) else {
        return (true, None);
    };
    let Some(version) = claude_version_from_target(target.to_bytes(), authority_root) else {
        return (true, None);
    };
    let Ok(share_fd) = openat(&local_fd, "share", directory_flags, Mode::empty()) else {
        return (true, None);
    };
    let Ok(claude_fd) = openat(&share_fd, "claude", directory_flags, Mode::empty()) else {
        return (true, None);
    };
    let Ok(versions_fd) = openat(&claude_fd, "versions", directory_flags, Mode::empty()) else {
        return (true, None);
    };
    let Ok(artifact_fd) = openat(&versions_fd, version.as_str(), file_flags, Mode::empty()) else {
        return (true, None);
    };
    let mut artifact = File::from(artifact_fd);
    let Some(actual_digest) = trusted_macho_sha256(&mut artifact) else {
        return (true, None);
    };
    if actual_digest != expected_digest {
        return (true, None);
    }
    (true, Some(version))
}

fn claude_version_from_target(target: &[u8], authority_root: &Path) -> Option<String> {
    let target = Path::new(OsStr::from_bytes(target));
    let version = if target.is_absolute() {
        let expected_root = authority_root.join(".local/share/claude/versions");
        let relative = target.strip_prefix(expected_root).ok()?;
        let mut components = relative.components();
        let Some(Component::Normal(version)) = components.next() else {
            return None;
        };
        if components.next().is_some() {
            return None;
        }
        version
    } else {
        let mut components = target.components();
        match (
            components.next(),
            components.next(),
            components.next(),
            components.next(),
            components.next(),
            components.next(),
        ) {
            (
                Some(Component::ParentDir),
                Some(Component::Normal(share)),
                Some(Component::Normal(claude)),
                Some(Component::Normal(versions)),
                Some(Component::Normal(version)),
                None,
            ) if share == "share" && claude == "claude" && versions == "versions" => version,
            _ => return None,
        }
    };
    version
        .to_str()
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn trusted_macho_sha256(file: &mut File) -> Option<String> {
    let before = file.metadata().ok()?;
    if !before.is_file()
        || before.len() < 4
        || before.len() > MAX_CLI_BYTES
        || before.mode() & 0o111 == 0
    {
        return None;
    }
    let mut digest = Sha256::new();
    let mut magic = [0_u8; 4];
    file.read_exact(&mut magic).ok()?;
    if !matches!(
        magic,
        [0xcf, 0xfa, 0xed, 0xfe]
            | [0xfe, 0xed, 0xfa, 0xcf]
            | [0xca, 0xfe, 0xba, 0xbe]
            | [0xbe, 0xba, 0xfe, 0xca]
    ) {
        return None;
    }
    digest.update(magic);
    let mut total = 4_u64;
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = file.read(&mut buffer).ok()?;
        if read == 0 {
            break;
        }
        total = total.checked_add(read as u64)?;
        if total > MAX_CLI_BYTES {
            return None;
        }
        digest.update(&buffer[..read]);
    }
    let after = file.metadata().ok()?;
    if total != before.len() || !same_file_snapshot(&before, &after) {
        return None;
    }
    Some(
        digest
            .finalize()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect(),
    )
}

fn same_file_snapshot(before: &std::fs::Metadata, after: &std::fs::Metadata) -> bool {
    before.dev() == after.dev()
        && before.ino() == after.ino()
        && before.len() == after.len()
        && before.mode() == after.mode()
        && before.mtime() == after.mtime()
        && before.mtime_nsec() == after.mtime_nsec()
}

fn metadata_header(text: &str, key: &str) -> Option<String> {
    let prefix = format!("{key}:");
    text.lines()
        .find_map(|line| line.strip_prefix(&prefix).map(str::trim))
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn read_bounded(path: &Path, limit: u64) -> Option<Vec<u8>> {
    let metadata = std::fs::symlink_metadata(path).ok()?;
    if !metadata.file_type().is_file() || metadata.len() > limit {
        return None;
    }
    let mut bytes = Vec::with_capacity(metadata.len() as usize);
    File::open(path)
        .ok()?
        .take(limit + 1)
        .read_to_end(&mut bytes)
        .ok()?;
    (bytes.len() as u64 <= limit).then_some(bytes)
}

fn sha512_file(path: &Path) -> Option<String> {
    let metadata = std::fs::symlink_metadata(path).ok()?;
    if !metadata.file_type().is_file() || metadata.len() > MAX_CLI_BYTES {
        return None;
    }
    let mut file = File::open(path).ok()?;
    let mut digest = Sha512::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = file.read(&mut buffer).ok()?;
        if read == 0 {
            break;
        }
        digest.update(&buffer[..read]);
    }
    Some(
        digest
            .finalize()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect(),
    )
}

pub fn evaluate_metadata_probe(
    adapter: &CatalogAdapter,
    observation: &MetadataProbeObservation,
) -> MetadataProbeOutcome {
    if !observation.install_metadata_present && !observation.declared_surface_present {
        return MetadataProbeOutcome::ToolAbsent;
    }

    let supported = &adapter.descriptor.supported_tool_versions;
    match supported.kind {
        VersionSupportKind::SchemaFingerprint => observation
            .observed_schema_digest
            .as_ref()
            .filter(|digest| *digest == &supported.value)
            .map(|digest| MetadataProbeOutcome::ToolPresent {
                current_tool_version_or_schema_digest: digest.clone(),
            })
            .unwrap_or(MetadataProbeOutcome::Unavailable {
                reason: "version_unknown",
            }),
        VersionSupportKind::Exact | VersionSupportKind::SemverRange => observation
            .observed_tool_version
            .as_ref()
            .map(|version| {
                if version == &supported.value {
                    MetadataProbeOutcome::ToolPresent {
                        current_tool_version_or_schema_digest: version.clone(),
                    }
                } else {
                    MetadataProbeOutcome::Unavailable {
                        reason: "version_mismatch",
                    }
                }
            })
            .unwrap_or(MetadataProbeOutcome::Unavailable {
                reason: "version_unknown",
            }),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::{symlink, PermissionsExt};

    fn claude_adapter_with_digest(bytes: &[u8]) -> CatalogAdapter {
        let expected_digest = Sha256::digest(bytes)
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        let mut adapter = AdapterCatalog::load_embedded()
            .unwrap()
            .for_tool(ToolId::ClaudeCode)
            .unwrap()
            .clone();
        adapter.qualification.probe_results[0].artifact_digest = Some(expected_digest);
        adapter
    }

    #[test]
    fn qualified_digest_accepts_relative_and_absolute_launcher_on_same_fd_lineage() {
        let temp = tempfile::tempdir().unwrap();
        let home = temp.path().join("home");
        let launcher = home.join(".local/bin/claude");
        let artifact = home.join(".local/share/claude/versions/2.1.202");
        std::fs::create_dir_all(launcher.parent().unwrap()).unwrap();
        std::fs::create_dir_all(artifact.parent().unwrap()).unwrap();
        std::fs::create_dir_all(home.join(".claude")).unwrap();
        let bytes = b"\xcf\xfa\xed\xfe\x0c\x00\x00\x01";
        std::fs::write(&artifact, bytes).unwrap();
        std::fs::set_permissions(&artifact, std::fs::Permissions::from_mode(0o755)).unwrap();

        let adapter = claude_adapter_with_digest(bytes);
        let filesystem = MetadataProbeFilesystem::new(
            home.clone(),
            temp.path().join("Applications"),
            vec![home.join(".local")],
        );

        symlink("../share/claude/versions/2.1.202", &launcher).unwrap();
        assert_eq!(
            observe_claude(&adapter, &filesystem)
                .observed_tool_version
                .as_deref(),
            Some("2.1.202")
        );

        std::fs::remove_file(&launcher).unwrap();
        symlink(&artifact, &launcher).unwrap();
        assert_eq!(
            observe_claude(&adapter, &filesystem)
                .observed_tool_version
                .as_deref(),
            Some("2.1.202")
        );
    }

    #[test]
    fn same_fd_lineage_rejects_root_leaf_and_outside_symlinks() {
        let temp = tempfile::tempdir().unwrap();
        let home = temp.path().join("home");
        let launcher = home.join(".local/bin/claude");
        let version_root = home.join(".local/share/claude/versions");
        let external_root = temp.path().join("external-versions");
        let external_artifact = external_root.join("2.1.202");
        std::fs::create_dir_all(launcher.parent().unwrap()).unwrap();
        std::fs::create_dir_all(version_root.parent().unwrap()).unwrap();
        std::fs::create_dir_all(&external_root).unwrap();
        std::fs::create_dir_all(home.join(".claude")).unwrap();
        let bytes = b"\xcf\xfa\xed\xfe\x0c\x00\x00\x01";
        std::fs::write(&external_artifact, bytes).unwrap();
        std::fs::set_permissions(&external_artifact, std::fs::Permissions::from_mode(0o755))
            .unwrap();
        let adapter = claude_adapter_with_digest(bytes);
        let filesystem = MetadataProbeFilesystem::new(
            home.clone(),
            temp.path().join("Applications"),
            vec![home.join(".local")],
        );

        symlink(&external_root, &version_root).unwrap();
        symlink("../share/claude/versions/2.1.202", &launcher).unwrap();
        assert_eq!(
            observe_claude(&adapter, &filesystem).observed_tool_version,
            None
        );

        std::fs::remove_file(&launcher).unwrap();
        std::fs::remove_file(&version_root).unwrap();
        std::fs::create_dir_all(&version_root).unwrap();
        symlink("../../../external-versions/2.1.202", &launcher).unwrap();
        assert_eq!(
            observe_claude(&adapter, &filesystem).observed_tool_version,
            None
        );

        std::fs::remove_file(&launcher).unwrap();
        symlink(&external_artifact, version_root.join("2.1.202")).unwrap();
        symlink("../share/claude/versions/2.1.202", &launcher).unwrap();
        assert_eq!(
            observe_claude(&adapter, &filesystem).observed_tool_version,
            None
        );
    }

    #[test]
    fn macho_gate_checks_mode_magic_and_size_independently() {
        let temp = tempfile::tempdir().unwrap();
        let artifact = temp.path().join("artifact");
        let magic = b"\xcf\xfa\xed\xfe\x0c\x00\x00\x01";

        std::fs::write(&artifact, magic).unwrap();
        std::fs::set_permissions(&artifact, std::fs::Permissions::from_mode(0o755)).unwrap();
        assert!(trusted_macho_sha256(&mut File::open(&artifact).unwrap()).is_some());

        std::fs::set_permissions(&artifact, std::fs::Permissions::from_mode(0o644)).unwrap();
        assert!(trusted_macho_sha256(&mut File::open(&artifact).unwrap()).is_none());

        std::fs::write(&artifact, b"text with mode bits\n").unwrap();
        std::fs::set_permissions(&artifact, std::fs::Permissions::from_mode(0o755)).unwrap();
        assert!(trusted_macho_sha256(&mut File::open(&artifact).unwrap()).is_none());

        std::fs::write(&artifact, magic).unwrap();
        std::fs::OpenOptions::new()
            .write(true)
            .open(&artifact)
            .unwrap()
            .set_len(MAX_CLI_BYTES + 1)
            .unwrap();
        assert!(trusted_macho_sha256(&mut File::open(&artifact).unwrap()).is_none());
    }
}
