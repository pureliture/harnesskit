use harness_desktop_lib::inventory_builder::InventoryBuilder;
use harness_desktop_lib::models::{
    ComponentManifest, ForeignClassification, InstallStatus, Inventory, ProfileMembership,
    RegistryEntry, ScanItem, ScanResult, Scope, SurfaceMarker, TargetConfig,
};
use harness_desktop_lib::registry_reader::RegistryData;
use std::collections::{BTreeMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};
use tempfile::TempDir;

const COMPONENT_ID: &str = "harnesskit.skill.owned";
const TARGET: &str = "claude";

struct InventoryFixture {
    _temp: TempDir,
    checkout: PathBuf,
    home: PathBuf,
    registry: RegistryData,
    scan_result: ScanResult,
    classifications: Vec<(String, ForeignClassification)>,
    user_location: String,
    project_alpha_location: String,
    project_zeta_location: String,
    foreign_orphan: String,
    unregistered_orphan: String,
    non_harness_path: String,
}

fn write_fixture(path: &Path, contents: &str) {
    fs::create_dir_all(path.parent().expect("fixture path must have a parent")).unwrap();
    fs::write(path, contents).unwrap();
}

fn string_path(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

fn fixture() -> InventoryFixture {
    let temp = TempDir::new().unwrap();
    let checkout = temp.path().join("checkout");
    let home = temp.path().join("home");
    let canonical = checkout.join("dist/claude/.claude/skills/owned/SKILL.md");
    let user_location = home.join(".claude/skills/owned/SKILL.md");
    let project_alpha_location = home.join("workspace/alpha/.claude/skills/owned/SKILL.md");
    let project_zeta_location = home.join("workspace/zeta/.claude/skills/owned/SKILL.md");
    let foreign_orphan = home.join("workspace/foreign/.claude/skills/foreign/SKILL.md");
    let unregistered_orphan = checkout.join("components/skills/unregistered/SKILL.md");
    let non_harness_path = home.join("workspace/alpha/.claude/skills/owned/NOTES.txt");

    write_fixture(&canonical, "# canonical\n");
    write_fixture(&user_location, "# canonical\n");
    write_fixture(&project_alpha_location, "# drift\n");
    write_fixture(&project_zeta_location, "# canonical\n");
    write_fixture(&foreign_orphan, "# foreign\n");
    write_fixture(&unregistered_orphan, "# unregistered\n");
    write_fixture(&non_harness_path, "notes\n");

    let user_location = string_path(&user_location);
    let project_alpha_location = string_path(&project_alpha_location);
    let project_zeta_location = string_path(&project_zeta_location);
    let foreign_orphan = string_path(&foreign_orphan);
    let unregistered_orphan = string_path(&unregistered_orphan);
    let non_harness_path = string_path(&non_harness_path);

    let scan_result = ScanResult {
        items: vec![
            ScanItem {
                path: foreign_orphan.clone(),
                surface: SurfaceMarker::Claude,
                scope: Scope::Project,
            },
            ScanItem {
                path: project_zeta_location.clone(),
                surface: SurfaceMarker::Claude,
                scope: Scope::Project,
            },
            ScanItem {
                path: non_harness_path.clone(),
                surface: SurfaceMarker::Claude,
                scope: Scope::Project,
            },
            ScanItem {
                path: user_location.clone(),
                surface: SurfaceMarker::Claude,
                scope: Scope::User,
            },
            ScanItem {
                path: unregistered_orphan.clone(),
                surface: SurfaceMarker::Claude,
                scope: Scope::Project,
            },
            ScanItem {
                path: project_alpha_location.clone(),
                surface: SurfaceMarker::Claude,
                scope: Scope::Project,
            },
        ],
        user_level_surfaces: vec![SurfaceMarker::Claude],
        skipped_paths: vec![],
        issues: vec![],
    };

    let classifications = vec![
        (non_harness_path.clone(), ForeignClassification::NonHarness),
        (
            unregistered_orphan.clone(),
            ForeignClassification::RegistryUnregistered,
        ),
        (foreign_orphan.clone(), ForeignClassification::Foreign),
        (
            project_alpha_location.clone(),
            ForeignClassification::HarnessKitOwned,
        ),
        (
            user_location.clone(),
            ForeignClassification::HarnessKitOwned,
        ),
        (
            project_zeta_location.clone(),
            ForeignClassification::HarnessKitOwned,
        ),
    ];

    let manifest = ComponentManifest {
        component_id: COMPONENT_ID.to_string(),
        kind: Some("skill".to_string()),
        status: Some("draft".to_string()),
        domain: Some("core".to_string()),
        title: Some("Owned".to_string()),
        summary: Some("Fixture component".to_string()),
        targets: BTreeMap::from([(
            TARGET.to_string(),
            TargetConfig {
                output_path: Some("dist/claude/.claude/skills/owned/SKILL.md".to_string()),
            },
        )]),
        adapter: None,
        owned_files: vec!["components/skills/owned/SKILL.md".to_string()],
        provenance_mode: Some("original".to_string()),
        scopes: vec!["user".to_string(), "project".to_string()],
        entrypoints: None,
    };
    let registry = RegistryData {
        entries: vec![RegistryEntry {
            component_id: COMPONENT_ID.to_string(),
            kind: "skill".to_string(),
            status: "draft".to_string(),
            path: "components/skills/owned/component.yml".to_string(),
        }],
        manifests: BTreeMap::from([(COMPONENT_ID.to_string(), manifest)]),
        profile_memberships: vec![ProfileMembership {
            profile_id: "harnesskit.profile.engineering".to_string(),
            component_ids: vec![COMPONENT_ID.to_string()],
        }],
        errors: vec![],
    };

    InventoryFixture {
        _temp: temp,
        checkout,
        home,
        registry,
        scan_result,
        classifications,
        user_location,
        project_alpha_location,
        project_zeta_location,
        foreign_orphan,
        unregistered_orphan,
        non_harness_path,
    }
}

fn build_inventory(
    fixture: &InventoryFixture,
    scan_timestamp: &str,
    reverse_inputs: bool,
) -> Inventory {
    let mut scan_result = fixture.scan_result.clone();
    let mut classifications = fixture.classifications.clone();
    if reverse_inputs {
        scan_result.items.reverse();
        classifications.reverse();
    }

    InventoryBuilder::build_with_context(
        &scan_result,
        &fixture.registry,
        &classifications,
        &fixture.checkout,
        &fixture.home,
        "0.1.0",
        scan_timestamp,
    )
    .expect("fixture inventory must build")
}

#[test]
fn component_target_identity_collects_sorted_locations_with_per_location_status() {
    let fixture = fixture();
    let inventory = build_inventory(&fixture, "2026-07-11T00:00:00Z", false);

    assert_eq!(
        inventory.items.len(),
        1,
        "locations must not duplicate identity"
    );
    let item = &inventory.items[0];
    assert_eq!(
        (item.component_id.as_str(), item.target.as_str()),
        (COMPONENT_ID, TARGET)
    );

    let actual_locations = item
        .locations
        .iter()
        .map(|location| {
            (
                location.scope.clone(),
                location.path.clone(),
                location.install_status.clone(),
            )
        })
        .collect::<Vec<_>>();
    assert_eq!(
        actual_locations,
        vec![
            (
                Scope::User,
                fixture.user_location.clone(),
                InstallStatus::Match
            ),
            (
                Scope::Project,
                fixture.project_alpha_location.clone(),
                InstallStatus::Drift,
            ),
            (
                Scope::Project,
                fixture.project_zeta_location.clone(),
                InstallStatus::Match,
            ),
        ],
        "locations must sort by scope then path and compare each destination to dist"
    );
}

#[test]
fn orphan_discoveries_exclude_non_harness_and_canonical_json_is_timestamp_free() {
    let fixture = fixture();
    let first = build_inventory(&fixture, "2026-07-11T00:00:00Z", false);
    let second = build_inventory(&fixture, "2026-07-11T00:00:01Z", true);

    let orphan_paths = first
        .orphan_discoveries
        .iter()
        .map(|orphan| orphan.path.clone())
        .collect::<HashSet<_>>();
    assert_eq!(
        orphan_paths,
        HashSet::from([
            fixture.foreign_orphan.clone(),
            fixture.unregistered_orphan.clone(),
        ])
    );
    assert!(!orphan_paths.contains(&fixture.non_harness_path));
    assert!(first.orphan_discoveries.iter().any(|orphan| {
        orphan.path == fixture.foreign_orphan
            && orphan.scope == Scope::Project
            && orphan.classification == ForeignClassification::Foreign
    }));
    assert!(first.orphan_discoveries.iter().any(|orphan| {
        orphan.path == fixture.unregistered_orphan
            && orphan.scope == Scope::Project
            && orphan.classification == ForeignClassification::RegistryUnregistered
    }));

    assert_ne!(
        first.scan_metadata.scan_timestamp, second.scan_metadata.scan_timestamp,
        "runtime evidence keeps the actual scan time"
    );
    let first_json = first
        .canonical_json()
        .expect("canonical JSON must serialize");
    let second_json = second
        .canonical_json()
        .expect("canonical JSON must serialize");
    let value: serde_json::Value = serde_json::from_slice(&first_json).unwrap();
    assert!(
        value.pointer("/scan_metadata/scan_timestamp").is_none(),
        "canonical snapshots must exclude volatile scan time"
    );
    assert_eq!(
        first_json, second_json,
        "canonical JSON must ignore input order and scan time"
    );
}
