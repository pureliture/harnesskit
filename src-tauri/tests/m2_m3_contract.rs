use harness_desktop_lib::foreign_detector::ForeignDetector;
use harness_desktop_lib::inventory_builder::InventoryBuilder;
use harness_desktop_lib::models::{
    ComponentManifest, ForeignClassification, InstallStatus, Inventory, ProfileMembership,
    RegistryEntry, ScanItem, ScanResult, Scope, SurfaceMarker, TargetConfig,
};
use harness_desktop_lib::registry_reader::RegistryData;
use harness_desktop_lib::scanner::Scanner;
use std::collections::{BTreeMap, HashSet};
use std::fs;
use std::path::Path;
use tempfile::TempDir;

fn write_fixture(path: &Path, contents: &str) {
    fs::create_dir_all(path.parent().expect("fixture path must have a parent")).unwrap();
    fs::write(path, contents).unwrap();
}

fn scan_signature(result: &ScanResult) -> Vec<(Scope, SurfaceMarker, String)> {
    result
        .items
        .iter()
        .map(|item| (item.scope.clone(), item.surface.clone(), item.path.clone()))
        .collect()
}

#[test]
fn scan_separates_user_and_project_surfaces_without_duplicates_in_stable_order() {
    let fixture = TempDir::new().unwrap();
    let home = fixture.path();
    let user_skill = home.join(".claude/skills/user-skill/SKILL.md");
    let project_agent = home.join("workspace/app/.codex/agents/project-agent/agent.yml");
    let ignored_skill =
        home.join("workspace/app/node_modules/dependency/.claude/skills/ignored/SKILL.md");
    write_fixture(&user_skill, "# User skill\n");
    write_fixture(&project_agent, "name: project-agent\n");
    write_fixture(&ignored_skill, "# Ignored skill\n");

    let first = Scanner::scan(home);
    let second = Scanner::scan(home);
    let expected = vec![
        (
            Scope::User,
            SurfaceMarker::Claude,
            user_skill.to_string_lossy().into_owned(),
        ),
        (
            Scope::Project,
            SurfaceMarker::Codex,
            project_agent.to_string_lossy().into_owned(),
        ),
    ];

    assert_eq!(
        scan_signature(&first),
        expected,
        "a user-level surface must not be rediscovered as project-level"
    );
    assert_eq!(
        scan_signature(&second),
        expected,
        "the same fixture snapshot must preserve canonical ordering"
    );
    assert!(
        first
            .items
            .iter()
            .all(|item| item.path != ignored_skill.to_string_lossy()),
        "ignored dependency paths must not leak into the inventory"
    );
}

#[test]
fn foreign_detector_distinguishes_owned_unregistered_and_foreign_fixtures() {
    let fixture = TempDir::new().unwrap();
    let checkout = fixture.path().join("checkout");
    let owned = checkout.join("components/skills/owned/SKILL.md");
    let unregistered = checkout.join("components/skills/unregistered/SKILL.md");
    let foreign = fixture
        .path()
        .join("other-project/.claude/skills/foreign/SKILL.md");
    write_fixture(&owned, "# Owned\n");
    write_fixture(&unregistered, "# Unregistered\n");
    write_fixture(&foreign, "# Foreign\n");

    let scan_result = ScanResult {
        items: vec![
            ScanItem {
                path: owned.to_string_lossy().into_owned(),
                surface: SurfaceMarker::Claude,
                scope: Scope::Project,
            },
            ScanItem {
                path: unregistered.to_string_lossy().into_owned(),
                surface: SurfaceMarker::Claude,
                scope: Scope::Project,
            },
            ScanItem {
                path: foreign.to_string_lossy().into_owned(),
                surface: SurfaceMarker::Claude,
                scope: Scope::Project,
            },
        ],
        user_level_surfaces: vec![],
        skipped_paths: vec![],
        issues: vec![],
    };
    let registry_ids = HashSet::from(["harnesskit.skill.owned".to_string()]);

    let classifications = ForeignDetector::classify(&scan_result, &registry_ids, &checkout)
        .into_iter()
        .collect::<BTreeMap<_, _>>();

    assert_eq!(
        classifications.get(&owned.to_string_lossy().into_owned()),
        Some(&ForeignClassification::HarnessKitOwned)
    );
    assert_eq!(
        classifications.get(&unregistered.to_string_lossy().into_owned()),
        Some(&ForeignClassification::RegistryUnregistered)
    );
    assert_eq!(
        classifications.get(&foreign.to_string_lossy().into_owned()),
        Some(&ForeignClassification::Foreign)
    );
}

fn registry_fixture() -> RegistryData {
    let component_id = "harnesskit.skill.owned".to_string();
    let mut targets = BTreeMap::new();
    targets.insert(
        "claude".to_string(),
        TargetConfig {
            output_path: Some("dist/claude/.claude/skills/owned/SKILL.md".to_string()),
        },
    );
    targets.insert(
        "codex".to_string(),
        TargetConfig {
            output_path: Some(".agents/skills/owned/SKILL.md".to_string()),
        },
    );

    let manifest = ComponentManifest {
        component_id: component_id.clone(),
        kind: Some("skill".to_string()),
        status: Some("draft".to_string()),
        domain: Some("core".to_string()),
        title: Some("Owned".to_string()),
        summary: Some("Fixture component".to_string()),
        targets,
        adapter: None,
        owned_files: vec!["components/skills/owned/SKILL.md".to_string()],
        provenance_mode: Some("original".to_string()),
        scopes: vec![],
        entrypoints: None,
    };

    RegistryData {
        entries: vec![RegistryEntry {
            component_id: component_id.clone(),
            kind: "skill".to_string(),
            status: "draft".to_string(),
            path: "components/skills/owned/component.yml".to_string(),
        }],
        manifests: BTreeMap::from([(component_id, manifest)]),
        profile_memberships: vec![ProfileMembership {
            profile_id: "harnesskit.profile.engineering".to_string(),
            component_ids: vec!["harnesskit.skill.owned".to_string()],
        }],
        errors: vec![],
    }
}

#[test]
fn registered_external_destination_is_classified_as_harnesskit_owned() {
    let home = TempDir::new().unwrap();
    let checkout = TempDir::new().unwrap();
    let installed = home.path().join(".agents/skills/owned/SKILL.md");
    write_fixture(&installed, "# Installed owned component\n");
    let scan_result = ScanResult {
        items: vec![ScanItem {
            path: installed.to_string_lossy().into_owned(),
            surface: SurfaceMarker::Agents,
            scope: Scope::User,
        }],
        user_level_surfaces: vec![SurfaceMarker::Agents],
        skipped_paths: vec![],
        issues: vec![],
    };

    let classifications =
        ForeignDetector::classify_with_registry(&scan_result, &registry_fixture(), checkout.path());

    assert_eq!(
        classifications,
        vec![(
            installed.to_string_lossy().into_owned(),
            ForeignClassification::HarnessKitOwned,
        )]
    );
}

fn empty_scan_result() -> ScanResult {
    ScanResult {
        items: vec![],
        user_level_surfaces: vec![SurfaceMarker::Claude, SurfaceMarker::Codex],
        skipped_paths: vec!["node_modules".to_string()],
        issues: vec![],
    }
}

fn canonical_json_without_evidence_timestamp(inventory: &Inventory) -> Vec<u8> {
    let mut value = serde_json::to_value(inventory).unwrap();
    value
        .get_mut("scan_metadata")
        .and_then(serde_json::Value::as_object_mut)
        .expect("scan metadata must be an object")
        .remove("scan_timestamp");
    serde_json::to_vec(&value).unwrap()
}

#[test]
fn inventory_normalizes_canonical_output_paths_without_duplicate_dist_prefix() {
    let fixture = TempDir::new().unwrap();
    let inventory = InventoryBuilder::build(
        &empty_scan_result(),
        &registry_fixture(),
        &[
            (
                "harnesskit.skill.owned".to_string(),
                "claude".to_string(),
                InstallStatus::Match,
            ),
            (
                "harnesskit.skill.owned".to_string(),
                "codex".to_string(),
                InstallStatus::Missing,
            ),
        ],
        &[],
        fixture.path(),
        "0.1.0",
    );
    let source_paths = inventory
        .items
        .iter()
        .map(|item| (item.target.as_str(), item.source_path.as_str()))
        .collect::<BTreeMap<_, _>>();

    assert_eq!(
        source_paths.get("claude"),
        Some(&"dist/claude/.claude/skills/owned/SKILL.md")
    );
    assert_eq!(
        source_paths.get("codex"),
        Some(&"dist/codex/.agents/skills/owned/SKILL.md")
    );
}

#[test]
fn inventory_is_canonical_json_stable_when_evidence_timestamp_is_excluded() {
    let fixture = TempDir::new().unwrap();
    let first = InventoryBuilder::build(
        &empty_scan_result(),
        &registry_fixture(),
        &[],
        &[],
        fixture.path(),
        "0.1.0",
    );
    let second = InventoryBuilder::build(
        &empty_scan_result(),
        &registry_fixture(),
        &[],
        &[],
        fixture.path(),
        "0.1.0",
    );

    assert!(!first.scan_metadata.scan_timestamp.is_empty());
    assert!(!second.scan_metadata.scan_timestamp.is_empty());
    assert_eq!(
        canonical_json_without_evidence_timestamp(&first),
        canonical_json_without_evidence_timestamp(&second),
        "volatile evidence time must be excluded from canonical inventory equality"
    );
}
