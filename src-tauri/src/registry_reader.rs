use serde::{Deserialize, Serialize};
use serde_yaml;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use crate::models::{ComponentManifest, ProfileMembership, RegistryEntry, TargetConfig};

pub struct RegistryReader;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RegistryReadError {
    pub category: String,
    pub path: String,
    pub message: String,
}

pub struct RegistryData {
    pub entries: Vec<RegistryEntry>,
    pub manifests: BTreeMap<String, ComponentManifest>,
    pub profile_memberships: Vec<ProfileMembership>,
    pub errors: Vec<RegistryReadError>,
}

impl RegistryReader {
    /// registry.yml 로드
    pub fn load_registry(checkout_path: &Path) -> Result<Vec<RegistryEntry>, String> {
        let registry_path = checkout_path.join("components/registry.yml");
        if !registry_path.exists() {
            return Err(format!(
                "registry.yml not found: {}",
                registry_path.display()
            ));
        }

        let text = std::fs::read_to_string(&registry_path)
            .map_err(|e| format!("Failed to read registry.yml: {}", e))?;

        let parsed: serde_yaml::Value = serde_yaml::from_str(&text)
            .map_err(|e| format!("Failed to parse registry.yml: {}", e))?;

        let components = parsed
            .get("components")
            .and_then(|c| c.as_mapping())
            .ok_or("registry.yml missing components mapping")?;

        let mut entries = Vec::new();
        for (key, value) in components {
            let component_id = key
                .as_str()
                .ok_or("component key is not a string")?
                .to_string();

            let kind = value
                .get("kind")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let status = value
                .get("status")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            let path = value
                .get("path")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            entries.push(RegistryEntry {
                component_id,
                kind,
                status,
                path,
            });
        }

        // stable sort by component_id
        entries.sort_by(|a, b| a.component_id.cmp(&b.component_id));

        Ok(entries)
    }

    /// 각 component.yml manifest 로드
    pub fn load_manifests(
        checkout_path: &Path,
        entries: &[RegistryEntry],
    ) -> Result<BTreeMap<String, ComponentManifest>, String> {
        Ok(Self::load_manifests_with_errors(checkout_path, entries).0)
    }

    fn load_manifests_with_errors(
        checkout_path: &Path,
        entries: &[RegistryEntry],
    ) -> (BTreeMap<String, ComponentManifest>, Vec<RegistryReadError>) {
        let mut manifests = BTreeMap::new();
        let mut errors = Vec::new();
        let canonical_checkout =
            std::fs::canonicalize(checkout_path).unwrap_or_else(|_| PathBuf::from(checkout_path));

        for entry in entries {
            let error_path = if Path::new(&entry.path).is_absolute() {
                "<absolute-manifest-path>".to_string()
            } else {
                entry.path.clone()
            };
            let declared_path = checkout_path.join(&entry.path);
            let manifest_path = match std::fs::canonicalize(&declared_path) {
                Ok(path) if path.starts_with(&canonical_checkout) && path.is_file() => path,
                Ok(_) => {
                    errors.push(RegistryReadError {
                        category: "manifest_path_escape".to_string(),
                        path: error_path,
                        message: "component manifest resolves outside checkout".to_string(),
                    });
                    continue;
                }
                Err(_) => {
                    errors.push(RegistryReadError {
                        category: "missing_manifest".to_string(),
                        path: error_path,
                        message: "component manifest is missing or inaccessible".to_string(),
                    });
                    continue;
                }
            };

            let text = match std::fs::read_to_string(&manifest_path) {
                Ok(text) => text,
                Err(_) => {
                    errors.push(RegistryReadError {
                        category: "unreadable_manifest".to_string(),
                        path: error_path,
                        message: "component manifest could not be read".to_string(),
                    });
                    continue;
                }
            };

            let parsed: serde_yaml::Value = match serde_yaml::from_str(&text) {
                Ok(value) => value,
                Err(_) => {
                    errors.push(RegistryReadError {
                        category: "malformed_manifest".to_string(),
                        path: error_path,
                        message: "component manifest could not be parsed".to_string(),
                    });
                    continue;
                }
            };

            if parsed
                .get("component_id")
                .and_then(|value| value.as_str())
                .is_some_and(|component_id| component_id != entry.component_id)
            {
                errors.push(RegistryReadError {
                    category: "component_id_mismatch".to_string(),
                    path: error_path,
                    message: "component manifest id does not match registry".to_string(),
                });
                continue;
            }

            let component_id = parsed
                .get("component_id")
                .and_then(|v| v.as_str())
                .unwrap_or(&entry.component_id)
                .to_string();

            let kind = parsed
                .get("kind")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
                .or(Some(entry.kind.clone()));

            let status = parsed
                .get("status")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
                .or(Some(entry.status.clone()));

            let domain = parsed
                .get("domain")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string());

            let title = parsed
                .get("title")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string());

            let summary = parsed
                .get("summary")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string());

            let provenance_mode = parsed
                .get("provenance_mode")
                .and_then(|v| v.as_str())
                .map(|s| s.to_string());

            let owned_files: Vec<String> = parsed
                .get("owned_files")
                .and_then(|v| v.as_sequence())
                .map(|seq| {
                    seq.iter()
                        .filter_map(|v| v.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();

            let scopes: Vec<String> = parsed
                .get("scopes")
                .and_then(|v| v.as_sequence())
                .map(|seq| {
                    seq.iter()
                        .filter_map(|v| v.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();

            let targets = Self::parse_targets(&parsed);

            let adapter = parsed.get("adapter").and_then(|v| v.as_mapping()).cloned();

            let entrypoints = parsed
                .get("entrypoints")
                .and_then(|v| v.as_mapping())
                .cloned();

            manifests.insert(
                entry.component_id.clone(),
                ComponentManifest {
                    component_id,
                    kind,
                    status,
                    domain,
                    title,
                    summary,
                    targets,
                    adapter,
                    owned_files,
                    provenance_mode,
                    scopes,
                    entrypoints,
                },
            );
        }

        (manifests, errors)
    }

    /// targets 맵 파싱
    fn parse_targets(parsed: &serde_yaml::Value) -> BTreeMap<String, TargetConfig> {
        let mut targets = BTreeMap::new();
        if let Some(t) = parsed.get("targets").and_then(|v| v.as_mapping()) {
            for (key, value) in t {
                let target_name = key.as_str().unwrap_or("").to_string();
                let output_path = value
                    .get("output_path")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string());
                targets.insert(target_name, TargetConfig { output_path });
            }
        }
        targets
    }

    /// profiles/*.yml 로드 → component→profile 매핑 역산
    pub fn load_profiles(checkout_path: &Path) -> Result<Vec<ProfileMembership>, String> {
        let profiles_dir = checkout_path.join("profiles");
        if !profiles_dir.exists() {
            return Ok(Vec::new());
        }

        let mut memberships = Vec::new();

        for entry in std::fs::read_dir(&profiles_dir)
            .map_err(|e| format!("Failed to read profiles dir: {}", e))?
            .filter_map(|e| e.ok())
        {
            let path = entry.path();
            if path.extension().and_then(|e| e.to_str()) != Some("yml") {
                continue;
            }

            let text = match std::fs::read_to_string(&path) {
                Ok(t) => t,
                Err(_) => continue,
            };

            let parsed: serde_yaml::Value = match serde_yaml::from_str(&text) {
                Ok(v) => v,
                Err(_) => continue,
            };

            let profile_id = parsed
                .get("profile_id")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();

            let components: Vec<String> = parsed
                .get("components")
                .and_then(|v| v.as_sequence())
                .map(|seq| {
                    seq.iter()
                        .filter_map(|v| v.as_str().map(|s| s.to_string()))
                        .collect()
                })
                .unwrap_or_default();

            // scope_components도 읽기
            let scope_components = parsed
                .get("install_policy")
                .and_then(|v| v.get("scope_components"))
                .and_then(|v| v.as_mapping());

            let mut all_components = components;
            if let Some(sc) = scope_components {
                for (_, list) in sc {
                    if let Some(seq) = list.as_sequence() {
                        for v in seq {
                            if let Some(s) = v.as_str() {
                                all_components.push(s.to_string());
                            }
                        }
                    }
                }
            }

            // dedupe preserving order
            let mut seen = std::collections::HashSet::new();
            all_components.retain(|c| seen.insert(c.clone()));

            memberships.push(ProfileMembership {
                profile_id,
                component_ids: all_components,
            });
        }

        memberships.sort_by(|a, b| a.profile_id.cmp(&b.profile_id));
        Ok(memberships)
    }

    /// 전체 registry data 로드
    pub fn load_all(checkout_path: &Path) -> Result<RegistryData, String> {
        let entries = Self::load_registry(checkout_path)?;
        let (manifests, errors) = Self::load_manifests_with_errors(checkout_path, &entries);
        let entries = entries
            .into_iter()
            .filter(|entry| manifests.contains_key(&entry.component_id))
            .collect();
        let profile_memberships = Self::load_profiles(checkout_path)?;

        Ok(RegistryData {
            entries,
            manifests,
            profile_memberships,
            errors,
        })
    }
}
