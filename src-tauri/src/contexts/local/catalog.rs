//! Embedded discovery adapter catalog and qualification verification.

use std::collections::BTreeSet;
use std::fmt;
use std::path::Path;

use serde::Serialize;
use sha2::{Digest, Sha256};
use walkdir::WalkDir;

use super::adapter::{
    AdapterDescriptor, AdapterImplementation, EvidenceDigestAlgorithm, ProbeStatus,
    QualificationRecord, ToolId, VersionSupportKind,
};
use super::adapters::{EmbeddedAdapterSource, EMBEDDED_ADAPTERS};
use super::probe::VersionProbeId;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CatalogAdapter {
    pub adapter_id: String,
    pub implementation: &'static AdapterImplementation,
    pub descriptor: AdapterDescriptor,
    pub qualification: QualificationRecord,
    pub descriptor_canonical_sha256: String,
    pub qualification_record_sha256: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CatalogError {
    pub code: &'static str,
    pub safe_message: &'static str,
}

impl fmt::Display for CatalogError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.safe_message)
    }
}

impl std::error::Error for CatalogError {}

#[derive(Debug, Default)]
pub struct AdapterCatalog {
    adapters: Vec<CatalogAdapter>,
}

impl AdapterCatalog {
    pub fn load_embedded() -> Result<Self, CatalogError> {
        validate_sources(EMBEDDED_ADAPTERS)
    }

    pub fn adapters(&self) -> &[CatalogAdapter] {
        &self.adapters
    }

    pub fn for_tool(&self, tool_id: ToolId) -> Option<&CatalogAdapter> {
        self.adapters
            .iter()
            .find(|adapter| adapter.descriptor.tool_id == tool_id)
    }
}

fn validate_sources(sources: &[EmbeddedAdapterSource]) -> Result<AdapterCatalog, CatalogError> {
    let mut adapters = sources
        .iter()
        .map(validate_pair)
        .collect::<Result<Vec<_>, _>>()?;
    adapters.sort_by(|left, right| left.adapter_id.cmp(&right.adapter_id));

    let ids = adapters
        .iter()
        .map(|adapter| adapter.adapter_id.as_str())
        .collect::<BTreeSet<_>>();
    let tools = adapters
        .iter()
        .map(|adapter| adapter.descriptor.tool_id)
        .collect::<BTreeSet<_>>();
    if adapters.len() != 5 || ids.len() != 5 || tools.len() != 5 {
        return Err(error(
            "adapter_catalog_set_mismatch",
            "The embedded discovery adapter set is invalid",
        ));
    }
    Ok(AdapterCatalog { adapters })
}

fn validate_pair(source: &EmbeddedAdapterSource) -> Result<CatalogAdapter, CatalogError> {
    let descriptor: AdapterDescriptor =
        serde_yaml::from_str(source.descriptor_yaml).map_err(|_| {
            error(
                "adapter_descriptor_invalid",
                "An embedded discovery adapter descriptor is invalid",
            )
        })?;
    let qualification: QualificationRecord = serde_json::from_str(source.qualification_json)
        .map_err(|_| {
            error(
                "adapter_qualification_invalid",
                "An embedded discovery adapter qualification is invalid",
            )
        })?;
    validate_compiled_binding(&descriptor, source.implementation)?;

    let descriptor_canonical_sha256 = canonical_descriptor_sha256(&descriptor)?;
    if qualification.qualification_id != descriptor.qualification_id
        || qualification.adapter_id != descriptor.adapter_id
        || qualification.adapter_version != descriptor.adapter_version
        || qualification.descriptor_canonical_sha256 != descriptor_canonical_sha256
    {
        return Err(error(
            "adapter_qualification_binding_mismatch",
            "An embedded discovery adapter qualification does not match its descriptor",
        ));
    }
    validate_qualification(&descriptor, &qualification)?;
    let qualification_record_sha256 = canonical_sha256(&qualification)?;

    Ok(CatalogAdapter {
        adapter_id: descriptor.adapter_id.clone(),
        implementation: source.implementation,
        descriptor,
        qualification,
        descriptor_canonical_sha256,
        qualification_record_sha256,
    })
}

fn validate_compiled_binding(
    descriptor: &AdapterDescriptor,
    implementation: &AdapterImplementation,
) -> Result<(), CatalogError> {
    let surfaces_match =
        descriptor.surfaces.len() == implementation.surfaces.len()
            && descriptor.surfaces.iter().zip(implementation.surfaces).all(
                |(declared, compiled)| {
                    declared.surface_id == compiled.surface_id
                        && declared.scope == compiled.scope
                        && declared.root_enumerator_id == compiled.root_enumerator_id
                        && declared.parser_ids == compiled.parser_ids
                        && declared.supported_kinds == compiled.supported_kinds
                        && declared.ignore_policy_id == compiled.ignore_policy_id
                        && declared.redaction_policy_id == compiled.redaction_policy_id
                },
            );
    if descriptor.schema_version != 1
        || descriptor.adapter_id != implementation.adapter_id
        || descriptor.implementation_version != implementation.implementation_version
        || descriptor.tool_id != implementation.tool_id
        || descriptor.version_probe_id != implementation.version_probe_id
        || !surfaces_match
    {
        return Err(error(
            "adapter_compiled_binding_mismatch",
            "An embedded descriptor does not match its compiled adapter binding",
        ));
    }
    Ok(())
}

fn validate_qualification(
    descriptor: &AdapterDescriptor,
    qualification: &QualificationRecord,
) -> Result<(), CatalogError> {
    if !is_lower_hex(&qualification.fixture_manifest_sha256, 64)
        || qualification.evidence_sources.is_empty()
        || !qualification
            .evidence_sources
            .iter()
            .any(|source| source.source_id.starts_with("https://"))
        || qualification.probe_results.len() != 1
        || qualification.probe_results.iter().any(|probe| {
            probe.probe_id != descriptor.version_probe_id
                || probe.surface_id.is_some()
                || probe.status != ProbeStatus::Passed
                || probe.observed_tool_version != qualification.observed_runtime.tool_version
        })
    {
        return Err(error(
            "adapter_qualification_evidence_invalid",
            "An embedded discovery adapter qualification has invalid evidence",
        ));
    }
    for probe in &qualification.probe_results {
        for (algorithm, digest) in [
            (probe.evidence_algorithm, probe.evidence_digest.as_deref()),
            (
                probe.artifact_digest_algorithm,
                probe.artifact_digest.as_deref(),
            ),
        ] {
            let valid = match (algorithm, digest) {
                (None, None) => true,
                (Some(EvidenceDigestAlgorithm::Sha256), Some(digest)) => is_lower_hex(digest, 64),
                (Some(EvidenceDigestAlgorithm::Sha512), Some(digest)) => is_lower_hex(digest, 128),
                _ => false,
            };
            if !valid {
                return Err(error(
                    "adapter_qualification_evidence_invalid",
                    "An embedded discovery adapter qualification has invalid evidence",
                ));
            }
        }
        if descriptor.version_probe_id == VersionProbeId::ClaudeCliMetadataV1
            && (probe.artifact_digest_algorithm != Some(EvidenceDigestAlgorithm::Sha256)
                || probe.artifact_digest.is_none())
        {
            return Err(error(
                "adapter_qualification_evidence_invalid",
                "An embedded discovery adapter qualification has invalid evidence",
            ));
        }
    }
    if descriptor.supported_tool_versions.kind == VersionSupportKind::Exact
        && qualification.observed_runtime.tool_version != descriptor.supported_tool_versions.value
    {
        return Err(error(
            "adapter_qualification_version_mismatch",
            "An embedded discovery adapter qualification has incompatible version evidence",
        ));
    }
    Ok(())
}

pub fn canonical_descriptor_sha256(descriptor: &AdapterDescriptor) -> Result<String, CatalogError> {
    let mut normalized = descriptor.clone();
    normalized.supported_os.sort();
    normalized
        .surfaces
        .sort_by(|left, right| left.surface_id.cmp(&right.surface_id));
    for surface in &mut normalized.surfaces {
        surface.parser_ids.sort();
        surface.supported_kinds.sort();
    }
    canonical_sha256(&normalized)
}

pub fn fixture_manifest_sha256(root: &Path) -> Result<String, CatalogError> {
    #[derive(Serialize)]
    struct Entry {
        path: String,
        sha256: String,
    }

    if !root.is_dir() {
        return Err(error(
            "fixture_manifest_invalid",
            "A catalog fixture root is unavailable",
        ));
    }
    let mut entries = Vec::new();
    for entry in WalkDir::new(root).follow_links(false) {
        let entry = entry.map_err(|_| {
            error(
                "fixture_manifest_invalid",
                "A catalog fixture could not be read",
            )
        })?;
        if entry.file_type().is_symlink() {
            return Err(error(
                "fixture_manifest_invalid",
                "A catalog fixture contains a symlink",
            ));
        }
        if !entry.file_type().is_file() {
            continue;
        }
        let relative = entry.path().strip_prefix(root).map_err(|_| {
            error(
                "fixture_manifest_invalid",
                "A catalog fixture escaped its root",
            )
        })?;
        let bytes = std::fs::read(entry.path()).map_err(|_| {
            error(
                "fixture_manifest_invalid",
                "A catalog fixture could not be read",
            )
        })?;
        entries.push(Entry {
            path: relative.to_string_lossy().replace('\\', "/"),
            sha256: sha256(&bytes),
        });
    }
    entries.sort_by(|left, right| left.path.cmp(&right.path));
    canonical_sha256(&entries)
}

fn canonical_sha256<T: Serialize>(value: &T) -> Result<String, CatalogError> {
    let bytes = serde_json::to_vec(value).map_err(|_| {
        error(
            "adapter_canonicalization_failed",
            "Embedded adapter evidence could not be canonicalized",
        )
    })?;
    Ok(sha256(&bytes))
}

fn sha256(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn is_lower_hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

fn error(code: &'static str, safe_message: &'static str) -> CatalogError {
    CatalogError { code, safe_message }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tampered_source(
        descriptor_yaml: String,
        qualification_json: String,
    ) -> EmbeddedAdapterSource {
        EmbeddedAdapterSource {
            implementation: EMBEDDED_ADAPTERS[3].implementation,
            descriptor_yaml: Box::leak(descriptor_yaml.into_boxed_str()),
            qualification_json: Box::leak(qualification_json.into_boxed_str()),
        }
    }

    #[test]
    fn claude_qualification_rejects_stale_version_and_duplicate_digest_authority() {
        let catalog = AdapterCatalog::load_embedded().unwrap();
        let claude = catalog.for_tool(ToolId::ClaudeCode).unwrap();

        let mut stale = claude.qualification.clone();
        stale.probe_results[0].observed_tool_version = "2.1.203".to_string();
        assert!(validate_qualification(&claude.descriptor, &stale).is_err());

        let mut duplicate = claude.qualification.clone();
        let mut conflicting = duplicate.probe_results[0].clone();
        conflicting.artifact_digest = Some("0".repeat(64));
        duplicate.probe_results.push(conflicting);
        assert!(validate_qualification(&claude.descriptor, &duplicate).is_err());
    }

    #[test]
    fn duplicate_catalog_entries_fail_closed() {
        let duplicated = [EMBEDDED_ADAPTERS[0]; 5];
        assert_eq!(
            validate_sources(&duplicated).unwrap_err().code,
            "adapter_catalog_set_mismatch"
        );
    }

    #[test]
    fn descriptor_hash_mismatch_fails_closed() {
        let source = &EMBEDDED_ADAPTERS[3];
        let qualification = source.qualification_json.replace(
            "0b97e5a9f8a26c30b4e86f45c84fc20fd0bd4fa2d217665c677054ed01b68053",
            "0000000000000000000000000000000000000000000000000000000000000000",
        );
        let tampered = tampered_source(source.descriptor_yaml.to_owned(), qualification);
        assert_eq!(
            validate_pair(&tampered).unwrap_err().code,
            "adapter_qualification_binding_mismatch"
        );
    }

    #[test]
    fn unknown_tool_and_parser_enums_fail_closed() {
        let source = &EMBEDDED_ADAPTERS[3];
        for descriptor in [
            source
                .descriptor_yaml
                .replace("tool_id: codex", "tool_id: unknown"),
            source
                .descriptor_yaml
                .replace("codex_toml_v1", "unknown_parser_v9"),
        ] {
            let tampered = tampered_source(descriptor, source.qualification_json.to_owned());
            assert_eq!(
                validate_pair(&tampered).unwrap_err().code,
                "adapter_descriptor_invalid"
            );
        }
    }
}
