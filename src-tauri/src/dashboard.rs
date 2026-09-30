use std::collections::{BTreeMap, BTreeSet};

use crate::models::{DashboardAggregates, InstallStatus, InventoryItem};
use crate::registry_reader::RegistryData;

pub struct DashboardBuilder;

impl DashboardBuilder {
    pub fn build(registry_data: &RegistryData, inventory: &[InventoryItem]) -> DashboardAggregates {
        let canonical_component_ids = registry_data
            .entries
            .iter()
            .map(|entry| entry.component_id.as_str())
            .collect::<BTreeSet<_>>();

        let mut dashboard = DashboardAggregates::default();

        for component_id in &canonical_component_ids {
            if let Some(entry) = registry_data
                .entries
                .iter()
                .filter(|entry| entry.component_id == *component_id)
                .min_by(|left, right| {
                    (&left.kind, &left.status, &left.path).cmp(&(
                        &right.kind,
                        &right.status,
                        &right.path,
                    ))
                })
            {
                increment(&mut dashboard.kind_counts, &entry.kind);
                increment(&mut dashboard.status_counts, &entry.status);
            }

            if let Some(manifest) = registry_data.manifests.get(*component_id) {
                if let Some(domain) = &manifest.domain {
                    increment(&mut dashboard.domain_counts, domain);
                }
                if let Some(provenance_mode) = &manifest.provenance_mode {
                    increment(&mut dashboard.provenance_mode_counts, provenance_mode);
                }
                for target in manifest.targets.keys() {
                    increment(&mut dashboard.target_installable_counts, target);
                }
            }
        }

        for item in inventory {
            let counts = dashboard
                .target_install_counts
                .entry(item.target.clone())
                .or_default();
            match item.install_status {
                InstallStatus::Match => counts.matched += 1,
                InstallStatus::Drift => counts.drifted += 1,
                InstallStatus::Missing => counts.missing += 1,
            }
        }

        let mut profile_components: BTreeMap<&str, BTreeSet<&str>> = BTreeMap::new();
        for membership in &registry_data.profile_memberships {
            let components = profile_components
                .entry(membership.profile_id.as_str())
                .or_default();
            for component_id in &membership.component_ids {
                if canonical_component_ids.contains(component_id.as_str()) {
                    components.insert(component_id);
                }
            }
        }

        let profiled_component_ids = profile_components
            .values()
            .flat_map(|components| components.iter().copied())
            .collect::<BTreeSet<_>>();
        dashboard.profile_component_counts = profile_components
            .into_iter()
            .map(|(profile_id, components)| (profile_id.to_string(), components.len()))
            .collect();
        dashboard.unprofiled_component_count = canonical_component_ids
            .difference(&profiled_component_ids)
            .count();

        dashboard
    }
}

fn increment(counts: &mut BTreeMap<String, usize>, key: &str) {
    *counts.entry(key.to_string()).or_default() += 1;
}
