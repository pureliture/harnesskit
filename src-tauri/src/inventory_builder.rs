use std::collections::BTreeMap;
use std::path::{Component, Path, PathBuf};

use crate::dashboard::DashboardBuilder;
use crate::hash_engine::HashEngine;
use crate::models::{
    ForeignClassification, InstallStatus, Inventory, InventoryIssue, InventoryItem,
    InventoryLocation, OrphanDiscovery, ScanMetadata, ScanResult,
};
use crate::registry_reader::RegistryData;

pub struct InventoryBuilder;

impl InventoryBuilder {
    /// 모든 데이터를 조합하여 normalized inventory 생성
    pub fn build(
        scan_result: &ScanResult,
        registry_data: &RegistryData,
        install_statuses: &[(String, String, InstallStatus)], // (component_id, target, status)
        foreign_classifications: &[(String, ForeignClassification)], // (path, classification)
        _checkout_path: &Path,
        app_version: &str,
    ) -> Inventory {
        // component_id → profile_ids 매핑
        let mut component_profiles: BTreeMap<String, Vec<String>> = BTreeMap::new();
        for membership in &registry_data.profile_memberships {
            for cid in &membership.component_ids {
                component_profiles
                    .entry(cid.clone())
                    .or_default()
                    .push(membership.profile_id.clone());
            }
        }

        // install status 매핑: (component_id, target) → InstallStatus
        let mut status_map: BTreeMap<(String, String), InstallStatus> = BTreeMap::new();
        for (cid, target, status) in install_statuses {
            status_map.insert((cid.clone(), target.clone()), status.clone());
        }

        // foreign classification 매핑: path → classification
        let mut foreign_map: BTreeMap<String, ForeignClassification> = BTreeMap::new();
        for (path, class) in foreign_classifications {
            foreign_map.insert(path.clone(), class.clone());
        }

        let mut items = Vec::new();

        // registry에 등록된 각 (component_id, target) 조합에 대해 inventory item 생성
        for entry in &registry_data.entries {
            let manifest = match registry_data.manifests.get(&entry.component_id) {
                Some(m) => m,
                None => continue,
            };

            // 각 target에 대해 item 생성
            for (target_name, target_config) in &manifest.targets {
                // source path 추출: dist/<target>/<output_path>
                let Ok(dist_path) = canonical_output_path_checked(
                    target_name,
                    target_config.output_path.as_deref().unwrap_or(""),
                ) else {
                    continue;
                };
                let dist_path = dist_path.to_string_lossy().into_owned();

                // 설치 상태 조회
                let install_status = status_map
                    .get(&(entry.component_id.clone(), target_name.clone()))
                    .cloned()
                    .unwrap_or(InstallStatus::Missing);

                // profile membership
                let profiles = component_profiles
                    .get(&entry.component_id)
                    .cloned()
                    .unwrap_or_default();

                // foreign classification
                let foreign_class = foreign_map
                    .get(&dist_path)
                    .cloned()
                    .unwrap_or(ForeignClassification::HarnessKitOwned);

                // scope 추론: target이 user-level인지 project-level인지
                let scope = if dist_path.starts_with("~") || dist_path.starts_with("/Users") {
                    crate::models::Scope::User
                } else {
                    crate::models::Scope::Project
                };

                items.push(InventoryItem {
                    component_id: entry.component_id.clone(),
                    target: target_name.clone(),
                    kind: entry.kind.clone(),
                    status: entry.status.clone(),
                    title: manifest.title.clone(),
                    summary: manifest.summary.clone(),
                    domain: manifest.domain.clone(),
                    scope,
                    source_path: dist_path,
                    install_status,
                    profile_memberships: profiles,
                    provenance_mode: manifest.provenance_mode.clone(),
                    owned_files: manifest.owned_files.clone(),
                    foreign_classification: foreign_class,
                    locations: Vec::new(),
                });
            }
        }

        // stable sort: scope → target → kind → component_id
        items.sort_by(|a, b| {
            let scope_ord = format!("{:?}", a.scope).cmp(&format!("{:?}", b.scope));
            if scope_ord != std::cmp::Ordering::Equal {
                return scope_ord;
            }
            let target_ord = a.target.cmp(&b.target);
            if target_ord != std::cmp::Ordering::Equal {
                return target_ord;
            }
            let kind_ord = a.kind.cmp(&b.kind);
            if kind_ord != std::cmp::Ordering::Equal {
                return kind_ord;
            }
            a.component_id.cmp(&b.component_id)
        });

        let user_surface_names: Vec<String> = scan_result
            .user_level_surfaces
            .iter()
            .map(|s| format!("{:?}", s))
            .collect();
        let dashboard = DashboardBuilder::build(registry_data, &items);
        let mut issues = scan_result
            .issues
            .iter()
            .map(|issue| InventoryIssue {
                category: issue.category.clone(),
                path: issue.path.clone(),
                message: issue.message.clone(),
            })
            .chain(registry_data.errors.iter().map(|error| InventoryIssue {
                category: error.category.clone(),
                path: error.path.clone(),
                message: error.message.clone(),
            }))
            .collect::<Vec<_>>();
        issues.sort_by(|left, right| {
            (&left.category, &left.path, &left.message).cmp(&(
                &right.category,
                &right.path,
                &right.message,
            ))
        });
        issues.dedup();

        Inventory {
            items,
            dashboard,
            orphan_discoveries: Vec::new(),
            issues,
            scan_metadata: ScanMetadata {
                scan_timestamp: chrono::Utc::now().to_rfc3339(),
                app_version: app_version.to_string(),
                user_level_surfaces: user_surface_names,
                skipped_path_count: scan_result.skipped_paths.len(),
            },
        }
    }

    /// scan evidence를 canonical component-target identity에 연결한 inventory 생성
    pub fn build_with_context(
        scan_result: &ScanResult,
        registry_data: &RegistryData,
        foreign_classifications: &[(String, ForeignClassification)],
        checkout_path: &Path,
        _home_path: &Path,
        app_version: &str,
        scan_timestamp: &str,
    ) -> Result<Inventory, String> {
        let mut inventory = Self::build(
            scan_result,
            registry_data,
            &[],
            foreign_classifications,
            checkout_path,
            app_version,
        );

        let classifications = foreign_classifications
            .iter()
            .cloned()
            .collect::<BTreeMap<_, _>>();

        for item in &mut inventory.items {
            let canonical_relative = PathBuf::from(&item.source_path);
            let destination_relative = destination_relative_path(&item.target, &canonical_relative);
            let canonical_path = checkout_path.join(&canonical_relative);
            let mut locations = BTreeMap::new();

            for scan_item in &scan_result.items {
                if classifications.get(&scan_item.path)
                    != Some(&ForeignClassification::HarnessKitOwned)
                    || destination_relative.as_os_str().is_empty()
                    || !Path::new(&scan_item.path).ends_with(&destination_relative)
                {
                    continue;
                }

                let location = InventoryLocation {
                    scope: scan_item.scope.clone(),
                    path: scan_item.path.clone(),
                    install_status: HashEngine::compare_checked(
                        &canonical_path,
                        Path::new(&scan_item.path),
                    )
                    .map_err(|error| error.to_string())?,
                };
                locations.insert((location.scope.clone(), location.path.clone()), location);
            }

            item.locations = locations.into_values().collect();
            item.install_status = aggregate_install_status(&item.locations);
            item.source_path = canonical_relative.to_string_lossy().into_owned();
        }

        let scan_scopes = scan_result
            .items
            .iter()
            .map(|item| (item.path.clone(), item.scope.clone()))
            .collect::<BTreeMap<_, _>>();
        let mut orphan_discoveries = BTreeMap::new();
        for (path, classification) in classifications {
            if !matches!(
                classification,
                ForeignClassification::Foreign | ForeignClassification::RegistryUnregistered
            ) {
                continue;
            }
            let Some(scope) = scan_scopes.get(&path) else {
                continue;
            };
            let orphan = OrphanDiscovery {
                scope: scope.clone(),
                path: path.clone(),
                classification,
            };
            orphan_discoveries.insert((orphan.scope.clone(), path), orphan);
        }

        inventory.orphan_discoveries = orphan_discoveries.into_values().collect();
        inventory.scan_metadata.scan_timestamp = scan_timestamp.to_string();
        inventory.dashboard = DashboardBuilder::build(registry_data, &inventory.items);
        Ok(inventory)
    }
}

pub(crate) fn canonical_output_path(target: &str, output_path: &str) -> PathBuf {
    let output_path = Path::new(output_path);
    let dist_prefix = Path::new("dist").join(target);
    if output_path.starts_with(&dist_prefix) {
        output_path.to_path_buf()
    } else {
        dist_prefix.join(output_path)
    }
}

pub(crate) fn canonical_output_path_checked(
    target: &str,
    output_path: &str,
) -> Result<PathBuf, String> {
    let target_path = Path::new(target);
    let output_path_value = Path::new(output_path);
    if target.is_empty()
        || output_path.is_empty()
        || target_path.is_absolute()
        || output_path_value.is_absolute()
        || !target_path
            .components()
            .all(|component| matches!(component, Component::Normal(_)))
        || !output_path_value
            .components()
            .all(|component| matches!(component, Component::Normal(_)))
    {
        return Err("HarnessKit adapter output path is invalid".to_string());
    }
    Ok(canonical_output_path(target, output_path))
}

fn destination_relative_path(target: &str, canonical_output_path: &Path) -> PathBuf {
    canonical_output_path
        .strip_prefix(Path::new("dist").join(target))
        .unwrap_or(canonical_output_path)
        .to_path_buf()
}

fn aggregate_install_status(locations: &[InventoryLocation]) -> InstallStatus {
    if locations
        .iter()
        .any(|location| location.install_status == InstallStatus::Drift)
    {
        InstallStatus::Drift
    } else if locations
        .iter()
        .any(|location| location.install_status == InstallStatus::Match)
    {
        InstallStatus::Match
    } else {
        InstallStatus::Missing
    }
}
