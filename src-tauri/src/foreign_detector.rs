use std::collections::HashSet;
use std::path::{Component, Path, PathBuf};

use crate::models::{ForeignClassification, ScanResult};
use crate::registry_reader::RegistryData;

pub struct ForeignDetector;

impl ForeignDetector {
    pub fn classify_with_registry(
        scan_result: &ScanResult,
        registry_data: &RegistryData,
        checkout_path: &Path,
    ) -> Vec<(String, ForeignClassification)> {
        let registry_ids = registry_data
            .entries
            .iter()
            .map(|entry| entry.component_id.clone())
            .collect::<HashSet<_>>();
        let destination_suffixes = registry_data
            .manifests
            .values()
            .flat_map(|manifest| {
                manifest.targets.iter().filter_map(|(target, config)| {
                    config
                        .output_path
                        .as_deref()
                        .and_then(|output| destination_suffix(target, output))
                })
            })
            .collect::<Vec<_>>();

        Self::classify_with_destinations(
            scan_result,
            &registry_ids,
            &destination_suffixes,
            checkout_path,
        )
    }

    /// 파일 분류: harnesskit_owned / foreign / registry_unregistered / non_harness
    pub fn classify(
        scan_result: &ScanResult,
        registry_ids: &HashSet<String>,
        checkout_path: &Path,
    ) -> Vec<(String, ForeignClassification)> {
        Self::classify_with_destinations(scan_result, registry_ids, &[], checkout_path)
    }

    fn classify_with_destinations(
        scan_result: &ScanResult,
        registry_ids: &HashSet<String>,
        destination_suffixes: &[PathBuf],
        checkout_path: &Path,
    ) -> Vec<(String, ForeignClassification)> {
        let mut classifications = Vec::new();

        for item in &scan_result.items {
            let path = Path::new(&item.path);

            // SoT checkout 경로 내부인지 외부인지 확인
            let is_inside_checkout = path.starts_with(checkout_path);

            // 경로에서 component_id 추론 (파일명/디렉터리명 기반은 아님)
            // registry_ids와 직접 매칭은 component_id를 알아야 함
            // 여기서는 파일 경로를 key로 사용하고, 실제 component_id 매칭은
            // InventoryBuilder에서 수행

            let file_name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");

            let parent_name = path
                .parent()
                .and_then(|p| p.file_name())
                .and_then(|n| n.to_str())
                .unwrap_or("");

            // harness component 파일인지 확인
            let is_harness_file = matches!(
                file_name,
                "SKILL.md"
                    | "component.yml"
                    | "agent.yml"
                    | "prompt.md"
                    | "hook.md"
                    | "workflow.yml"
                    | "composite.yml"
                    | "provenance.map.yml"
            );

            if !is_harness_file {
                classifications.push((item.path.clone(), ForeignClassification::NonHarness));
                continue;
            }

            let is_registered = registry_ids
                .iter()
                .any(|component_id| component_id.rsplit('.').next() == Some(parent_name));
            if is_inside_checkout {
                let classification = if is_registered {
                    ForeignClassification::HarnessKitOwned
                } else {
                    ForeignClassification::RegistryUnregistered
                };
                classifications.push((item.path.clone(), classification));
            } else if destination_suffixes
                .iter()
                .any(|suffix| path.ends_with(suffix))
            {
                classifications.push((item.path.clone(), ForeignClassification::HarnessKitOwned));
            } else {
                classifications.push((item.path.clone(), ForeignClassification::Foreign));
            }
        }

        classifications
    }
}

fn destination_suffix(target: &str, output: &str) -> Option<PathBuf> {
    let output = Path::new(output);
    if output.is_absolute()
        || output
            .components()
            .any(|component| !matches!(component, Component::Normal(_)))
    {
        return None;
    }
    let prefix = Path::new("dist").join(target);
    let destination = output.strip_prefix(prefix).unwrap_or(output);
    (!destination.as_os_str().is_empty()).then(|| destination.to_path_buf())
}
