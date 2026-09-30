use harness_desktop_lib::dashboard::DashboardBuilder;
use harness_desktop_lib::models::{
    ComponentManifest, ForeignClassification, InstallStatus, InventoryItem, ProfileMembership,
    RegistryEntry, Scope, TargetConfig,
};
use harness_desktop_lib::registry_reader::RegistryData;
use serde_json::{json, Value};
use std::collections::BTreeMap;

const ALPHA: &str = "harnesskit.skill.alpha";
const BETA: &str = "harnesskit.agent.beta";
const GAMMA: &str = "harnesskit.hook.gamma";
const ENGINEERING: &str = "harnesskit.profile.engineering";
const SHARED: &str = "harnesskit.profile.shared";

fn target(output_path: &str) -> TargetConfig {
    TargetConfig {
        output_path: Some(output_path.to_string()),
    }
}

fn component(
    component_id: &str,
    kind: &str,
    status: &str,
    domain: &str,
    provenance_mode: &str,
    targets: &[(&str, &str)],
) -> (RegistryEntry, ComponentManifest) {
    let manifest = ComponentManifest {
        component_id: component_id.to_string(),
        kind: Some(kind.to_string()),
        status: Some(status.to_string()),
        domain: Some(domain.to_string()),
        title: Some(component_id.to_string()),
        summary: Some("dashboard contract fixture".to_string()),
        targets: targets
            .iter()
            .map(|(name, output_path)| ((*name).to_string(), target(output_path)))
            .collect(),
        adapter: None,
        owned_files: vec![],
        provenance_mode: Some(provenance_mode.to_string()),
        scopes: vec![],
        entrypoints: None,
    };
    let entry = RegistryEntry {
        component_id: component_id.to_string(),
        kind: kind.to_string(),
        status: status.to_string(),
        path: format!("components/{kind}/{component_id}/component.yml"),
    };
    (entry, manifest)
}

fn registry_fixture(reverse_inputs: bool) -> RegistryData {
    let components = [
        component(
            ALPHA,
            "skill",
            "stable",
            "core",
            "manual",
            &[
                ("claude", "dist/claude/alpha/SKILL.md"),
                ("codex", "dist/codex/alpha/SKILL.md"),
            ],
        ),
        component(
            BETA,
            "agent",
            "draft",
            "platform",
            "adapted",
            &[("codex", "dist/codex/beta/agent.yml")],
        ),
        component(
            GAMMA,
            "hook",
            "deprecated",
            "core",
            "original",
            &[
                ("claude", "dist/claude/gamma/hook.json"),
                ("gemini", "dist/gemini/gamma/hook.json"),
            ],
        ),
    ];
    let mut entries = components
        .iter()
        .map(|(entry, _)| RegistryEntry {
            component_id: entry.component_id.clone(),
            kind: entry.kind.clone(),
            status: entry.status.clone(),
            path: entry.path.clone(),
        })
        .collect::<Vec<_>>();
    let manifests = components
        .into_iter()
        .map(|(_, manifest)| (manifest.component_id.clone(), manifest))
        .collect::<BTreeMap<_, _>>();
    let mut profile_memberships = vec![
        ProfileMembership {
            profile_id: ENGINEERING.to_string(),
            component_ids: vec![ALPHA.to_string(), ALPHA.to_string(), BETA.to_string()],
        },
        ProfileMembership {
            profile_id: SHARED.to_string(),
            component_ids: vec![ALPHA.to_string()],
        },
    ];

    if reverse_inputs {
        entries.reverse();
        profile_memberships.reverse();
        for membership in &mut profile_memberships {
            membership.component_ids.reverse();
        }
    }

    RegistryData {
        entries,
        manifests,
        profile_memberships,
        errors: vec![],
    }
}

#[allow(clippy::too_many_arguments)]
fn inventory_item(
    component_id: &str,
    target: &str,
    kind: &str,
    status: &str,
    domain: &str,
    provenance_mode: &str,
    install_status: InstallStatus,
    profile_memberships: &[&str],
) -> InventoryItem {
    InventoryItem {
        component_id: component_id.to_string(),
        target: target.to_string(),
        kind: kind.to_string(),
        status: status.to_string(),
        title: Some(component_id.to_string()),
        summary: Some("dashboard contract fixture".to_string()),
        domain: Some(domain.to_string()),
        scope: Scope::User,
        source_path: format!("dist/{target}/{component_id}"),
        install_status,
        profile_memberships: profile_memberships
            .iter()
            .map(|profile| (*profile).to_string())
            .collect(),
        provenance_mode: Some(provenance_mode.to_string()),
        owned_files: vec![],
        foreign_classification: ForeignClassification::HarnessKitOwned,
        locations: vec![],
    }
}

fn inventory_fixture(reverse_inputs: bool) -> Vec<InventoryItem> {
    let mut items = vec![
        inventory_item(
            ALPHA,
            "codex",
            "skill",
            "stable",
            "core",
            "manual",
            InstallStatus::Match,
            &[ENGINEERING, ENGINEERING, SHARED],
        ),
        inventory_item(
            ALPHA,
            "claude",
            "skill",
            "stable",
            "core",
            "manual",
            InstallStatus::Drift,
            &[SHARED, ENGINEERING],
        ),
        inventory_item(
            BETA,
            "codex",
            "agent",
            "draft",
            "platform",
            "adapted",
            InstallStatus::Missing,
            &[ENGINEERING],
        ),
        inventory_item(
            GAMMA,
            "claude",
            "hook",
            "deprecated",
            "core",
            "original",
            InstallStatus::Match,
            &[],
        ),
        inventory_item(
            GAMMA,
            "gemini",
            "hook",
            "deprecated",
            "core",
            "original",
            InstallStatus::Missing,
            &[],
        ),
    ];
    if reverse_inputs {
        items.reverse();
    }
    items
}

fn expected_dashboard_json() -> Value {
    json!({
        "kind_counts": {
            "agent": 1,
            "hook": 1,
            "skill": 1
        },
        "domain_counts": {
            "core": 2,
            "platform": 1
        },
        "status_counts": {
            "deprecated": 1,
            "draft": 1,
            "stable": 1
        },
        "target_installable_counts": {
            "claude": 2,
            "codex": 2,
            "gemini": 1
        },
        "target_install_counts": {
            "claude": {
                "matched": 1,
                "drifted": 1,
                "missing": 0
            },
            "codex": {
                "matched": 1,
                "drifted": 0,
                "missing": 1
            },
            "gemini": {
                "matched": 0,
                "drifted": 0,
                "missing": 1
            }
        },
        "profile_component_counts": {
            "harnesskit.profile.engineering": 2,
            "harnesskit.profile.shared": 1
        },
        "unprofiled_component_count": 1,
        "provenance_mode_counts": {
            "adapted": 1,
            "manual": 1,
            "original": 1
        }
    })
}

#[test]
fn dashboard_builds_the_exact_eight_deterministic_aggregates() {
    let registry = registry_fixture(false);
    let inventory = inventory_fixture(false);

    let dashboard = DashboardBuilder::build(&registry, &inventory);

    assert_eq!(
        serde_json::to_value(dashboard).expect("dashboard aggregates must serialize"),
        expected_dashboard_json()
    );
}

#[test]
fn dashboard_json_is_byte_stable_when_registry_and_inventory_inputs_are_reversed() {
    let forward = DashboardBuilder::build(&registry_fixture(false), &inventory_fixture(false));
    let reversed = DashboardBuilder::build(&registry_fixture(true), &inventory_fixture(true));

    assert_eq!(
        serde_json::to_vec(&forward).expect("forward dashboard must serialize"),
        serde_json::to_vec(&reversed).expect("reversed dashboard must serialize")
    );
}
