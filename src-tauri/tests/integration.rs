mod test_registry_reader {
    use harness_desktop_lib::registry_reader::RegistryReader;
    use std::path::PathBuf;

    fn repo_root() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .to_path_buf()
    }

    #[test]
    fn load_registry_returns_entries() {
        let path = repo_root();
        let entries = RegistryReader::load_registry(&path).expect("should load registry");
        assert!(!entries.is_empty(), "registry should have entries");
        assert!(
            entries
                .iter()
                .any(|e| e.component_id.contains("grill-to-spec")),
            "should contain grill-to-spec"
        );
    }

    #[test]
    fn load_registry_entries_have_required_fields() {
        let path = repo_root();
        let entries = RegistryReader::load_registry(&path).expect("should load registry");
        for entry in &entries {
            assert!(
                !entry.component_id.is_empty(),
                "component_id must not be empty"
            );
            assert!(
                !entry.kind.is_empty(),
                "kind must not be empty for {}",
                entry.component_id
            );
            assert!(
                !entry.status.is_empty(),
                "status must not be empty for {}",
                entry.component_id
            );
            assert!(
                !entry.path.is_empty(),
                "path must not be empty for {}",
                entry.component_id
            );
        }
    }

    #[test]
    fn load_registry_entries_are_sorted() {
        let path = repo_root();
        let entries = RegistryReader::load_registry(&path).expect("should load registry");
        for i in 1..entries.len() {
            assert!(
                entries[i - 1].component_id <= entries[i].component_id,
                "entries must be sorted by component_id: {} <= {}",
                entries[i - 1].component_id,
                entries[i].component_id
            );
        }
    }

    #[test]
    fn load_registry_missing_file_returns_error() {
        let result = RegistryReader::load_registry(&PathBuf::from("/nonexistent/path"));
        assert!(result.is_err(), "should error on missing registry");
    }

    #[test]
    fn load_manifests_returns_data() {
        let path = repo_root();
        let entries = RegistryReader::load_registry(&path).expect("should load registry");
        let manifests =
            RegistryReader::load_manifests(&path, &entries).expect("should load manifests");
        assert!(!manifests.is_empty(), "should have manifests");
        let grill = manifests.get("harnesskit.skill.grill-to-spec");
        assert!(grill.is_some(), "grill-to-spec manifest should exist");
        let grill = grill.unwrap();
        assert!(grill.title.is_some(), "grill-to-spec should have title");
        assert!(grill.summary.is_some(), "grill-to-spec should have summary");
        assert!(grill.domain.is_some(), "grill-to-spec should have domain");
        assert!(
            !grill.targets.is_empty(),
            "grill-to-spec should have targets"
        );
    }

    #[test]
    fn load_profiles_returns_memberships() {
        let path = repo_root();
        let profiles = RegistryReader::load_profiles(&path).expect("should load profiles");
        assert!(!profiles.is_empty(), "should have profiles");
        assert!(
            profiles
                .iter()
                .any(|p| p.profile_id.contains("engineering")),
            "should have engineering profile"
        );
    }

    #[test]
    fn load_all_returns_consistent_data() {
        let path = repo_root();
        let data = RegistryReader::load_all(&path).expect("should load all");
        assert!(!data.entries.is_empty());
        assert!(!data.manifests.is_empty());
        assert!(!data.profile_memberships.is_empty());
        for cid in data.manifests.keys() {
            assert!(
                data.entries.iter().any(|e| &e.component_id == cid),
                "manifest {} must have matching registry entry",
                cid
            );
        }
    }
}

mod test_scanner {
    use harness_desktop_lib::models::Scope;
    use harness_desktop_lib::scanner::Scanner;
    use std::path::PathBuf;

    fn home_dir() -> PathBuf {
        dirs::home_dir().expect("should find home dir")
    }

    #[test]
    fn detect_user_level_surfaces_finds_existing() {
        let home = home_dir();
        let surfaces = Scanner::detect_user_level_surfaces(&home);
        assert!(
            !surfaces.is_empty(),
            "should find at least one user-level surface"
        );
    }

    #[test]
    fn scan_user_level_returns_items_for_existing_surfaces() {
        let home = home_dir();
        let items = Scanner::scan_user_level(&home);
        let surfaces = Scanner::detect_user_level_surfaces(&home);
        if !surfaces.is_empty() {
            assert!(items.iter().all(|i| i.scope == Scope::User));
        }
    }

    #[test]
    fn scan_project_level_respects_ignore_rules() {
        let home = home_dir();
        let (items, skipped) = Scanner::scan_project_level(&home);
        assert!(
            skipped.iter().any(|p| p.contains("node_modules")),
            "should skip node_modules"
        );
        assert!(
            items.iter().all(|i| !i.path.contains("Library")),
            "should not include Library paths"
        );
    }

    #[test]
    fn scan_returns_stable_sorted_result() {
        let home = home_dir();
        let result1 = Scanner::scan(&home);
        let result2 = Scanner::scan(&home);
        assert_eq!(result1.items.len(), result2.items.len());
        for (a, b) in result1.items.iter().zip(result2.items.iter()) {
            assert_eq!(a.path, b.path, "paths should match");
            assert_eq!(a.scope, b.scope, "scopes should match");
        }
    }
}

mod test_hash_engine {
    use harness_desktop_lib::hash_engine::HashEngine;
    use harness_desktop_lib::models::InstallStatus;
    use std::io::Write;
    use std::path::Path;
    use tempfile::NamedTempFile;

    #[test]
    fn compare_identical_files_returns_match() {
        let f1 = NamedTempFile::new().unwrap();
        let f2 = NamedTempFile::new().unwrap();
        f1.as_file().write_all(b"hello").unwrap();
        f2.as_file().write_all(b"hello").unwrap();
        assert_eq!(
            HashEngine::compare(f1.path(), f2.path()),
            InstallStatus::Match
        );
    }

    #[test]
    fn compare_different_files_returns_drift() {
        let f1 = NamedTempFile::new().unwrap();
        let f2 = NamedTempFile::new().unwrap();
        f1.as_file().write_all(b"hello").unwrap();
        f2.as_file().write_all(b"world").unwrap();
        assert_eq!(
            HashEngine::compare(f1.path(), f2.path()),
            InstallStatus::Drift
        );
    }

    #[test]
    fn compare_missing_installed_returns_missing() {
        let f1 = NamedTempFile::new().unwrap();
        f1.as_file().write_all(b"hello").unwrap();
        assert_eq!(
            HashEngine::compare(f1.path(), Path::new("/nonexistent/file")),
            InstallStatus::Missing
        );
    }

    #[test]
    fn compare_missing_dist_returns_missing() {
        let f2 = NamedTempFile::new().unwrap();
        f2.as_file().write_all(b"hello").unwrap();
        assert_eq!(
            HashEngine::compare(Path::new("/nonexistent/dist"), f2.path()),
            InstallStatus::Missing
        );
    }
}

mod test_foreign_detector {
    use harness_desktop_lib::foreign_detector::ForeignDetector;
    use harness_desktop_lib::models::{
        ForeignClassification, ScanItem, ScanResult, Scope, SurfaceMarker,
    };
    use std::collections::HashSet;

    #[test]
    fn classify_non_harness_file() {
        let scan_result = ScanResult {
            items: vec![ScanItem {
                path: "/tmp/random.txt".to_string(),
                surface: SurfaceMarker::Claude,
                scope: Scope::Project,
            }],
            user_level_surfaces: vec![],
            skipped_paths: vec![],
            issues: vec![],
        };
        let registry_ids: HashSet<String> = HashSet::new();
        let checkout = std::path::Path::new("/tmp/checkout");
        let classifications = ForeignDetector::classify(&scan_result, &registry_ids, checkout);
        assert_eq!(classifications.len(), 1);
        assert_eq!(classifications[0].1, ForeignClassification::NonHarness);
    }

    #[test]
    fn classify_foreign_skill_outside_checkout() {
        let scan_result = ScanResult {
            items: vec![ScanItem {
                path: "/other/project/.claude/skills/foo/SKILL.md".to_string(),
                surface: SurfaceMarker::Claude,
                scope: Scope::Project,
            }],
            user_level_surfaces: vec![],
            skipped_paths: vec![],
            issues: vec![],
        };
        let registry_ids: HashSet<String> = HashSet::new();
        let checkout = std::path::Path::new("/tmp/checkout");
        let classifications = ForeignDetector::classify(&scan_result, &registry_ids, checkout);
        assert_eq!(classifications[0].1, ForeignClassification::Foreign);
    }

    #[test]
    fn classify_registry_unregistered_inside_checkout() {
        let scan_result = ScanResult {
            items: vec![ScanItem {
                path: "/tmp/checkout/components/skills/new-skill/SKILL.md".to_string(),
                surface: SurfaceMarker::Claude,
                scope: Scope::Project,
            }],
            user_level_surfaces: vec![],
            skipped_paths: vec![],
            issues: vec![],
        };
        let registry_ids: HashSet<String> = HashSet::new();
        let checkout = std::path::Path::new("/tmp/checkout");
        let classifications = ForeignDetector::classify(&scan_result, &registry_ids, checkout);
        assert_eq!(
            classifications[0].1,
            ForeignClassification::RegistryUnregistered
        );
    }
}

mod test_inventory_builder {
    use harness_desktop_lib::inventory_builder::InventoryBuilder;
    use harness_desktop_lib::models::{
        ComponentManifest, ForeignClassification, InstallStatus, ProfileMembership, RegistryEntry,
        ScanResult, SurfaceMarker, TargetConfig,
    };
    use harness_desktop_lib::registry_reader::RegistryData;
    use std::collections::BTreeMap;

    type TestData = (
        ScanResult,
        RegistryData,
        Vec<(String, String, InstallStatus)>,
        Vec<(String, ForeignClassification)>,
    );

    fn make_test_data() -> TestData {
        let scan_result = ScanResult {
            items: vec![],
            user_level_surfaces: vec![SurfaceMarker::Claude],
            skipped_paths: vec!["node_modules".to_string()],
            issues: vec![],
        };

        let entries = vec![RegistryEntry {
            component_id: "harnesskit.skill.test".to_string(),
            kind: "skill".to_string(),
            status: "draft".to_string(),
            path: "components/skills/test/component.yml".to_string(),
        }];

        let mut targets = BTreeMap::new();
        targets.insert(
            "claude".to_string(),
            TargetConfig {
                output_path: Some(".claude/skills/test/SKILL.md".to_string()),
            },
        );

        let mut manifests = BTreeMap::new();
        manifests.insert(
            "harnesskit.skill.test".to_string(),
            ComponentManifest {
                component_id: "harnesskit.skill.test".to_string(),
                kind: Some("skill".to_string()),
                status: Some("draft".to_string()),
                domain: Some("core".to_string()),
                title: Some("Test Skill".to_string()),
                summary: Some("A test skill".to_string()),
                targets,
                adapter: None,
                owned_files: vec!["components/skills/test/component.yml".to_string()],
                provenance_mode: Some("adapted".to_string()),
                scopes: vec![],
                entrypoints: None,
            },
        );

        let registry_data = RegistryData {
            entries,
            manifests,
            profile_memberships: vec![ProfileMembership {
                profile_id: "harnesskit.profile.engineering".to_string(),
                component_ids: vec!["harnesskit.skill.test".to_string()],
            }],
            errors: vec![],
        };

        let install_statuses = vec![(
            "harnesskit.skill.test".to_string(),
            "claude".to_string(),
            InstallStatus::Match,
        )];

        let foreign_classifications = vec![];

        (
            scan_result,
            registry_data,
            install_statuses,
            foreign_classifications,
        )
    }

    #[test]
    fn build_provides_inventory_with_items() {
        let (scan_result, registry_data, install_statuses, foreign_classifications) =
            make_test_data();
        let checkout = std::path::Path::new("/tmp/checkout");
        let inventory = InventoryBuilder::build(
            &scan_result,
            &registry_data,
            &install_statuses,
            &foreign_classifications,
            checkout,
            "0.1.0",
        );
        assert_eq!(inventory.items.len(), 1);
        let item = &inventory.items[0];
        assert_eq!(item.component_id, "harnesskit.skill.test");
        assert_eq!(item.target, "claude");
        assert_eq!(item.kind, "skill");
        assert_eq!(item.title.as_deref(), Some("Test Skill"));
        assert_eq!(item.install_status, InstallStatus::Match);
        assert!(item
            .profile_memberships
            .contains(&"harnesskit.profile.engineering".to_string()));
    }

    #[test]
    fn build_metadata_includes_scan_info() {
        let (scan_result, registry_data, install_statuses, foreign_classifications) =
            make_test_data();
        let checkout = std::path::Path::new("/tmp/checkout");
        let inventory = InventoryBuilder::build(
            &scan_result,
            &registry_data,
            &install_statuses,
            &foreign_classifications,
            checkout,
            "0.1.0",
        );
        assert_eq!(inventory.scan_metadata.app_version, "0.1.0");
        assert!(!inventory.scan_metadata.user_level_surfaces.is_empty());
        assert_eq!(inventory.scan_metadata.skipped_path_count, 1);
    }

    #[test]
    fn build_stable_sort_scope_target_kind_component() {
        let scan_result = ScanResult {
            items: vec![],
            user_level_surfaces: vec![],
            skipped_paths: vec![],
            issues: vec![],
        };

        let entries = vec![
            RegistryEntry {
                component_id: "harnesskit.skill.zeta".to_string(),
                kind: "skill".to_string(),
                status: "draft".to_string(),
                path: "z".to_string(),
            },
            RegistryEntry {
                component_id: "harnesskit.skill.alpha".to_string(),
                kind: "skill".to_string(),
                status: "draft".to_string(),
                path: "a".to_string(),
            },
        ];

        let mut manifests = BTreeMap::new();
        for cid in &["harnesskit.skill.zeta", "harnesskit.skill.alpha"] {
            let mut targets = BTreeMap::new();
            targets.insert(
                "claude".to_string(),
                TargetConfig {
                    output_path: Some(format!(".claude/skills/{}/SKILL.md", cid)),
                },
            );
            manifests.insert(
                cid.to_string(),
                ComponentManifest {
                    component_id: cid.to_string(),
                    kind: Some("skill".to_string()),
                    status: Some("draft".to_string()),
                    domain: Some("core".to_string()),
                    title: Some(cid.to_string()),
                    summary: Some("test".to_string()),
                    targets,
                    adapter: None,
                    owned_files: vec![],
                    provenance_mode: None,
                    scopes: vec![],
                    entrypoints: None,
                },
            );
        }

        let registry_data = RegistryData {
            entries,
            manifests,
            profile_memberships: vec![],
            errors: vec![],
        };

        let install_statuses = vec![
            (
                "harnesskit.skill.zeta".to_string(),
                "claude".to_string(),
                InstallStatus::Missing,
            ),
            (
                "harnesskit.skill.alpha".to_string(),
                "claude".to_string(),
                InstallStatus::Missing,
            ),
        ];

        let inventory = InventoryBuilder::build(
            &scan_result,
            &registry_data,
            &install_statuses,
            &[],
            std::path::Path::new("/tmp"),
            "0.1.0",
        );

        assert_eq!(inventory.items[0].component_id, "harnesskit.skill.alpha");
        assert_eq!(inventory.items[1].component_id, "harnesskit.skill.zeta");
    }
}
