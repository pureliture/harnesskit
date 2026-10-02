use std::collections::BTreeSet;
use std::fs;
use std::path::PathBuf;

use harness_desktop_lib::contexts::install::{
    embedded_target_contract, embedded_target_contract_hash, exact_copy, merge_json_deep,
    merge_managed_block, merge_toml_agents, InstallPlan, InstallRequest, PlanValidator,
    EMBEDDED_TARGET_CONTRACT_SHA256,
};
use serde_json::{json, Value};

fn source(relative: &str) -> String {
    fs::read_to_string(PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(relative)).unwrap_or_default()
}

#[test]
fn install_context_declares_embedded_contract_validator_and_merge_seams() {
    let module = source("src/contexts/install/mod.rs");
    let contract = source("src/contexts/install/target_contract.rs");
    let plan = source("src/contexts/install/plan.rs");
    let merge = source("src/contexts/install/merge.rs");
    let schema = source("../schemas/install-target-contract-v1.json");

    for declaration in [
        "pub mod merge;",
        "pub mod plan;",
        "pub mod target_contract;",
    ] {
        assert!(
            module.contains(declaration),
            "missing install module seam: {declaration}"
        );
    }
    assert!(contract.contains("EMBEDDED_TARGET_CONTRACT_SHA256"));
    assert!(plan.contains("pub struct PlanValidator"));
    assert!(merge.contains("pub fn exact_copy"));
    assert!(merge.contains("pub fn merge_managed_block"));
    assert!(merge.contains("pub fn merge_json_deep"));
    assert!(merge.contains("pub fn merge_toml_agents"));
    assert!(schema.contains("harnesskit.install-target-contract.v1"));
}

fn fixture_value(name: &str) -> Value {
    serde_json::from_str(&source(&format!("tests/fixtures/{name}"))).unwrap()
}

fn positive_plan() -> InstallPlan {
    let mut value = fixture_value("m5_install_plan_positive.json");
    value["install_contract_hash"] = json!(EMBEDDED_TARGET_CONTRACT_SHA256);
    serde_json::from_value(value).unwrap()
}

fn selected_components() -> BTreeSet<String> {
    [
        "harnesskit.rule.harnesskit-project-context".to_string(),
        "harnesskit.skill.optimal-response".to_string(),
    ]
    .into_iter()
    .collect()
}

fn request(targets: &[&str]) -> InstallRequest {
    InstallRequest {
        scope: "project".to_string(),
        targets: targets.iter().map(|target| (*target).to_string()).collect(),
    }
}

#[test]
fn embedded_contract_has_canonical_hash_and_seven_target_authorities() {
    let contract = embedded_target_contract().unwrap();

    assert_eq!(
        contract.contract_id,
        "harnesskit.install-target-contract.v1"
    );
    assert_eq!(contract.targets.len(), 7);
    assert_eq!(
        contract
            .targets
            .iter()
            .map(|target| target.id.as_str())
            .collect::<Vec<_>>(),
        [
            "project",
            "claude",
            "codex",
            "gemini",
            "antigravity",
            "antigravity-cli",
            "hermes",
        ]
    );
    assert_eq!(
        embedded_target_contract_hash().unwrap(),
        EMBEDDED_TARGET_CONTRACT_SHA256
    );
    assert_eq!(EMBEDDED_TARGET_CONTRACT_SHA256.len(), 64);
}

#[test]
fn embedded_contract_exposes_only_explicit_discovery_correlation_bindings() {
    let contract = embedded_target_contract().unwrap();
    let project_agents = contract.correlation_bindings("project", "project", "AGENTS.md");
    assert_eq!(
        project_agents
            .iter()
            .map(|binding| (binding.tool_id.as_str(), binding.surface_id.as_str()))
            .collect::<Vec<_>>(),
        [
            ("antigravity_cli", "antigravity_cli_project"),
            ("codex", "codex_project"),
        ]
    );
    let codex_user =
        contract.correlation_bindings("codex", "user", ".codex/skills/release/SKILL.md");
    assert_eq!(codex_user.len(), 1);
    assert_eq!(
        codex_user[0].authorized_root_prefix.as_deref(),
        Some(".codex")
    );
    assert!(contract
        .correlation_bindings("codex", "project", ".codex/skills/release/SKILL.md")
        .is_empty());
    for (target, scope, destination) in [
        ("claude", "user", ".claude/settings.json"),
        ("codex", "user", ".codex/hooks.json"),
        ("antigravity", "project", ".agents/hooks.json"),
        ("hermes", "user", ".hermes/config.yaml"),
        ("gemini", "project", ".gemini/skills/release/SKILL.md"),
    ] {
        assert!(
            contract
                .correlation_bindings(target, scope, destination)
                .is_empty(),
            "unsafe correlation binding: {target}:{scope}:{destination}"
        );
    }
}

#[test]
fn validator_accepts_only_the_embedded_contract_and_exact_selected_closure() {
    let validator = PlanValidator::embedded().unwrap();
    let plan = positive_plan();

    let validated = validator
        .validate(
            &plan,
            &request(&["project", "codex", "hermes"]),
            &selected_components(),
        )
        .unwrap();

    assert_eq!(validated.artifact_count(), 6);
    assert_eq!(validated.component_count(), 2);
    assert_eq!(validated.contract_hash(), EMBEDDED_TARGET_CONTRACT_SHA256);
    assert_eq!(validated.semantic_fingerprint().len(), 64);
}

#[test]
fn validator_rejects_the_shared_python_contract_negative_corpus() {
    let validator = PlanValidator::embedded().unwrap();
    let corpus = fixture_value("m5_install_plan_negative.json");

    for case in corpus.as_array().unwrap() {
        let mut value = fixture_value("m5_install_plan_positive.json");
        value["install_contract_hash"] = json!(EMBEDDED_TARGET_CONTRACT_SHA256);
        let pointer = case["pointer"].as_str().unwrap();
        *value.pointer_mut(pointer).unwrap() = case["value"].clone();
        let plan: InstallPlan = serde_json::from_value(value).unwrap();
        let error = validator
            .validate(
                &plan,
                &request(&["project", "codex", "hermes"]),
                &selected_components(),
            )
            .unwrap_err();
        assert_eq!(
            error.code(),
            case["expected"].as_str().unwrap(),
            "negative parity case {}",
            case["name"].as_str().unwrap()
        );
    }
}

#[test]
fn public_plan_ownership_is_not_whole_file_authority() {
    let mut plan = positive_plan();
    let artifact = plan
        .artifacts
        .iter_mut()
        .find(|a| a.target == "project" && a.destination == "AGENTS.md")
        .unwrap();
    artifact.merge_strategy = None;
    artifact.begin_marker = None;
    artifact.end_marker = None;
    artifact.ownership = Some(json!({"type":"path","merge_policy":"full-path-overwrite"}));
    assert_eq!(
        PlanValidator::embedded()
            .unwrap()
            .validate(
                &plan,
                &request(&["project", "codex", "hermes"]),
                &selected_components()
            )
            .unwrap_err()
            .code(),
        "merge_contract_mismatch"
    );
}

#[test]
fn validator_rejects_duplicate_destination_and_cross_target_fan_in() {
    let validator = PlanValidator::embedded().unwrap();
    let mut duplicate = positive_plan();
    duplicate.artifacts.push(duplicate.artifacts[1].clone());
    assert_eq!(
        validator
            .validate(
                &duplicate,
                &request(&["project", "codex", "hermes"]),
                &selected_components(),
            )
            .unwrap_err()
            .code(),
        "duplicate_destination"
    );

    let mut fan_in = positive_plan();
    fan_in.targets.push("antigravity".to_string());
    let mut artifact = fan_in.artifacts[1].clone();
    artifact.target = "antigravity".to_string();
    artifact.source = "dist/antigravity/.agents/skills/optimal-response/SKILL.md".to_string();
    fan_in.artifacts.push(artifact);
    assert_eq!(
        validator
            .validate(
                &fan_in,
                &request(&["project", "codex", "hermes", "antigravity"]),
                &selected_components(),
            )
            .unwrap_err()
            .code(),
        "destination_fan_in"
    );
}

#[test]
fn dry_run_and_apply_share_one_typed_semantic_projection() {
    let validator = PlanValidator::embedded().unwrap();
    let mut dry_run = positive_plan();
    for artifact in &mut dry_run.artifacts {
        artifact.mode = None;
        artifact.source_sha256 = None;
    }
    let mut apply = dry_run.clone();
    apply.mode = "apply".to_string();
    for artifact in &mut apply.artifacts {
        artifact.mode = Some(0o644);
        artifact.source_sha256 = Some("a".repeat(64));
    }

    let fingerprint = validator
        .validate_dry_run_apply_semantic_equality(&dry_run, &apply)
        .unwrap();
    assert_eq!(fingerprint.len(), 64);

    apply.artifacts[1].source =
        "dist/codex/.agents/skills/optimal-response/DIFFERENT.md".to_string();
    assert_eq!(
        validator
            .validate_dry_run_apply_semantic_equality(&dry_run, &apply)
            .unwrap_err()
            .code(),
        "semantic_plan_mismatch"
    );
}

#[test]
fn apply_validation_requires_materialized_source_hash_and_mode() {
    let validator = PlanValidator::embedded().unwrap();
    let mut apply = positive_plan();
    apply.mode = "apply".to_string();
    apply.artifacts[0].mode = None;
    assert_eq!(
        validator
            .validate(
                &apply,
                &request(&["project", "codex", "hermes"]),
                &selected_components(),
            )
            .unwrap_err()
            .code(),
        "source_mode_missing"
    );

    apply.artifacts[0].mode = Some(0o644);
    assert_eq!(
        validator
            .validate(
                &apply,
                &request(&["project", "codex", "hermes"]),
                &selected_components(),
            )
            .unwrap_err()
            .code(),
        "source_hash_missing"
    );
}

#[test]
fn pure_merge_primitives_match_python_apply_and_verify_fixed_points() {
    let corpus = fixture_value("m5_merge_parity.json");

    assert_eq!(exact_copy(b"\0exact\n"), b"\0exact\n");

    let managed = &corpus["managed_block"];
    let managed_output = merge_managed_block(
        managed["current"].as_str().unwrap(),
        managed["body"].as_str().unwrap(),
        managed["begin_marker"].as_str().unwrap(),
        managed["end_marker"].as_str().unwrap(),
    )
    .unwrap();
    assert_eq!(managed_output, managed["expected"].as_str().unwrap());
    assert_eq!(
        merge_managed_block(
            &managed_output,
            managed["body"].as_str().unwrap(),
            managed["begin_marker"].as_str().unwrap(),
            managed["end_marker"].as_str().unwrap(),
        )
        .unwrap(),
        managed_output
    );

    let json_case = &corpus["json_deep_merge"];
    let json_output = merge_json_deep(
        json_case["current"].as_str().unwrap(),
        json_case["body"].as_str().unwrap(),
        json_case["merge_key"].as_str().unwrap(),
    )
    .unwrap();
    let parsed: Value = serde_json::from_str(&json_output).unwrap();
    assert_eq!(parsed["alpha"], true);
    assert_eq!(parsed["zeta"], true);
    assert_eq!(parsed["nested"], json!({"foreign": 1, "owned": 2}));
    assert!(json_output.contains("echo foreign"));
    assert!(!json_output.contains("node /old/"));
    assert!(json_output.contains("node new.cjs"));
    assert!(json_output.ends_with('\n'));
    assert_eq!(
        merge_json_deep(
            &json_output,
            json_case["body"].as_str().unwrap(),
            json_case["merge_key"].as_str().unwrap(),
        )
        .unwrap(),
        json_output
    );

    let toml = &corpus["toml_agents_merge"];
    let toml_output = merge_toml_agents(
        toml["current"].as_str().unwrap(),
        toml["body"].as_str().unwrap(),
        toml["merge_key"].as_str().unwrap(),
    )
    .unwrap();
    assert_eq!(toml_output, toml["expected"].as_str().unwrap());
    assert_eq!(
        merge_toml_agents(
            &toml_output,
            toml["body"].as_str().unwrap(),
            toml["merge_key"].as_str().unwrap(),
        )
        .unwrap(),
        toml_output
    );
}

#[test]
fn merge_primitives_fail_closed_on_ambiguous_or_invalid_inputs() {
    let duplicate_markers = "<!-- BEGIN HARNESSKIT GENERATED CONTEXT -->\na\n<!-- END HARNESSKIT GENERATED CONTEXT -->\n<!-- BEGIN HARNESSKIT GENERATED CONTEXT -->\nb\n<!-- END HARNESSKIT GENERATED CONTEXT -->\n";
    assert_eq!(
        merge_managed_block(
            duplicate_markers,
            "fresh",
            "<!-- BEGIN HARNESSKIT GENERATED CONTEXT -->",
            "<!-- END HARNESSKIT GENERATED CONTEXT -->",
        )
        .unwrap_err()
        .code(),
        "ambiguous_managed_block"
    );
    assert_eq!(
        merge_json_deep("{}", "{}", "unknown").unwrap_err().code(),
        "unsupported_merge_key"
    );
    assert_eq!(
        merge_json_deep("{\"hooks\":[]}", "{\"hooks\":{}}", "codex-hooks")
            .unwrap_err()
            .code(),
        "invalid_json_merge_shape"
    );
    assert_eq!(
        merge_toml_agents(
            "model = \"x\"\n[agents.\"broken\"\n",
            "[agents.\"fresh\"]\ndescription = \"ok\"\n",
            "codex-agents",
        )
        .unwrap_err()
        .code(),
        "invalid_toml"
    );
}
