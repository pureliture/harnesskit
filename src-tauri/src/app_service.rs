use std::path::Path;

use crate::checkout::RegisteredCheckout;
use crate::foreign_detector::ForeignDetector;
use crate::hash_engine::HashEngine;
use crate::inventory_builder::{canonical_output_path_checked, InventoryBuilder};
use crate::models::Inventory;
use crate::registry_reader::RegistryReader;
use crate::scanner::Scanner;
use crate::subprocess_runner::SubprocessRunner;

pub struct AppService;

impl AppService {
    pub fn scan(
        checkout: &RegisteredCheckout,
        home: &Path,
        app_version: &str,
        timestamp: &str,
    ) -> Result<Inventory, String> {
        let registry_data = RegistryReader::load_all(checkout.root())?;
        let mut component_ids: Vec<String> = registry_data
            .entries
            .iter()
            .map(|entry| entry.component_id.clone())
            .collect();
        component_ids.sort();
        component_ids.dedup();

        let build_result = SubprocessRunner::run_build_components(checkout, &component_ids)?;
        if build_result.exit_code != 0 {
            return Err("HarnessKit component build failed".to_string());
        }

        for entry in &registry_data.entries {
            let Some(manifest) = registry_data.manifests.get(&entry.component_id) else {
                continue;
            };
            for (target_name, target_config) in &manifest.targets {
                let Some(output_path) = target_config.output_path.as_deref() else {
                    continue;
                };
                let relative_path = canonical_output_path_checked(target_name, output_path)
                    .map_err(|_| "HarnessKit adapter output path is invalid".to_string())?;
                let declared_path = checkout.root().join(relative_path);
                let canonical_path = std::fs::canonicalize(&declared_path).map_err(|_| {
                    format!(
                        "HarnessKit adapter output missing or unreadable: {}/{}",
                        entry.component_id, target_name
                    )
                })?;
                if !canonical_path.starts_with(checkout.root())
                    || HashEngine::file_hash(&canonical_path).is_err()
                {
                    return Err(format!(
                        "HarnessKit adapter output missing or unreadable: {}/{}",
                        entry.component_id, target_name
                    ));
                }
            }
        }

        let scan_result = Scanner::scan(home);
        let foreign_classifications =
            ForeignDetector::classify_with_registry(&scan_result, &registry_data, checkout.root());

        InventoryBuilder::build_with_context(
            &scan_result,
            &registry_data,
            &foreign_classifications,
            checkout.root(),
            home,
            app_version,
            timestamp,
        )
    }
}
