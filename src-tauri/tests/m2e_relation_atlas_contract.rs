use std::collections::BTreeSet;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;

use harness_desktop_lib::checkout::RegisteredCheckout;
use harness_desktop_lib::contexts::sot::{SotContext, SotSnapshot};
use serde_json::Value;
use tempfile::tempdir;

const FIXTURE_WORKFLOW_ID: &str = "harnesskit.workflow.fixture-delivery";
const FIXTURE_PROFILE_ID: &str = "harnesskit.profile.fixture-engineering";
const FIXTURE_SKILL_ID: &str = "harnesskit.skill.fixture-alpha";

fn repository_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("src-tauri must have a repository parent")
        .to_path_buf()
}

fn write(path: &Path, source: &str) {
    fs::create_dir_all(path.parent().expect("fixture path must have a parent")).unwrap();
    fs::write(path, source).unwrap();
}

fn git(root: &Path, args: &[&str]) {
    let status = Command::new("git")
        .current_dir(root)
        .args(args)
        .status()
        .unwrap();
    assert!(status.success(), "git fixture command failed: {args:?}");
}

fn load_snapshot(root: &Path) -> SotSnapshot {
    let checkout = RegisteredCheckout::open(root).unwrap();
    let context = SotContext::with_active_checkout(Some("m2e-fixture".to_string()));
    context.load("m2e-fixture", &checkout).unwrap()
}

fn snapshot_json(root: &Path) -> Value {
    serde_json::to_value(load_snapshot(root)).unwrap()
}

fn array_at<'a>(value: &'a Value, key: &str) -> &'a Vec<Value> {
    value
        .get(key)
        .and_then(Value::as_array)
        .unwrap_or_else(|| panic!("M2E snapshot must expose `{key}[]`"))
}

fn text_at<'a>(value: &'a Value, key: &str) -> &'a str {
    value
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_else(|| panic!("M2E DTO must expose text field `{key}`"))
}

fn identity_set(values: &[Value], key: &str) -> BTreeSet<String> {
    values
        .iter()
        .map(|value| text_at(value, key).to_string())
        .collect()
}

fn string_set(values: &[Value]) -> BTreeSet<String> {
    values
        .iter()
        .map(|value| {
            value
                .as_str()
                .expect("identity list entries must be text")
                .to_string()
        })
        .collect()
}

fn registry_identity_count(root: &Path) -> usize {
    registry_identity_set(root).len()
}

fn registry_identity_set(root: &Path) -> BTreeSet<String> {
    let registry: serde_yaml::Value =
        serde_yaml::from_slice(&fs::read(root.join("components/registry.yml")).unwrap()).unwrap();
    registry["components"]
        .as_mapping()
        .expect("current fixture registry must expose components")
        .keys()
        .map(|identity| {
            identity
                .as_str()
                .expect("registry identity must be text")
                .to_string()
        })
        .collect()
}

fn relation_nodes(projection: &Value) -> Vec<&Value> {
    array_at(projection, "nodes")
        .iter()
        .filter(|node| node.get("relation_kind").is_some())
        .collect()
}

fn relation_node<'a>(projection: &'a Value, canonical_id: &str) -> &'a Value {
    relation_nodes(projection)
        .into_iter()
        .find(|node| node.get("canonical_id").and_then(Value::as_str) == Some(canonical_id))
        .unwrap_or_else(|| panic!("missing relation node for {canonical_id}"))
}

fn relation_links(projection: &Value) -> &Vec<Value> {
    array_at(projection, "links")
}

fn fixture() -> tempfile::TempDir {
    let temp = tempdir().unwrap();
    write(
        &temp.path().join("components/registry.yml"),
        r#"version: "1"
components:
  harnesskit.agent.fixture-router:
    kind: agent
    status: draft
    path: components/agents/router/agent.yml
  harnesskit.skill.fixture-alpha:
    kind: skill
    status: draft
    path: components/skills/alpha/component.yml
  harnesskit.workflow.fixture-delivery:
    kind: workflow
    status: draft
    path: components/workflows/delivery/workflow.yml
"#,
    );
    write(
        &temp.path().join("components/agents/router/agent.yml"),
        r#"component_id: harnesskit.agent.fixture-router
kind: agent
status: draft
title: Fixture Router
summary: Fixture router
domain: core
"#,
    );
    write(
        &temp.path().join("components/skills/alpha/component.yml"),
        r#"component_id: harnesskit.skill.fixture-alpha
kind: skill
status: draft
title: Fixture Alpha
summary: Fixture alpha
domain: core
"#,
    );
    write(
        &temp
            .path()
            .join("components/workflows/delivery/workflow.yml"),
        r#"workflow_id: harnesskit.workflow.fixture-delivery
kind: workflow
status: draft
domain: core
title: Fixture Delivery
summary: Fixture delivery
description: Ordered repeated occurrence fixture
runtime_implemented: false
optional_invoked_by: harnesskit.skill.fixture-alpha
steps:
  - id: prepare
    description: Prepare with the shared skill and router
    agent: harnesskit.agent.fixture-router
    skill: harnesskit.skill.fixture-alpha
  - id: verify
    description: Reuse the same skill at a later ordinal
    skill: harnesskit.skill.fixture-alpha
"#,
    );
    write(
        &temp.path().join("profiles/engineering.yml"),
        r#"profile_id: harnesskit.profile.fixture-engineering
status: draft
title: Fixture Engineering
summary: Stale Profile containing forbidden Workflow selections
components:
  - harnesskit.skill.fixture-alpha
  - harnesskit.workflow.fixture-delivery
install_policy:
  default_scope: project
  allowed_scopes: [project]
  activation_policy: manual
  scope_components:
    project:
      - harnesskit.workflow.fixture-delivery
"#,
    );
    for (relative, schema) in [
        (
            "schemas/component.schema.json",
            include_str!("../../schemas/component.schema.json"),
        ),
        (
            "schemas/agent.schema.json",
            include_str!("../../schemas/agent.schema.json"),
        ),
        (
            "schemas/workflow.schema.json",
            include_str!("../../schemas/workflow.schema.json"),
        ),
        (
            "schemas/composite.schema.json",
            include_str!("../../schemas/composite.schema.json"),
        ),
        (
            "schemas/profile.schema.json",
            include_str!("../../schemas/profile.schema.json"),
        ),
    ] {
        write(&temp.path().join(relative), schema);
    }
    git(temp.path(), &["init", "--quiet"]);
    git(temp.path(), &["config", "user.name", "HarnessKit M2E Test"]);
    git(
        temp.path(),
        &[
            "config",
            "user.email",
            concat!("harnesskit-m2e-test@", "example.invalid"),
        ],
    );
    git(temp.path(), &["add", "."]);
    git(temp.path(), &["commit", "--quiet", "-m", "fixture"]);
    temp
}

#[test]
fn current_repository_fixture_partitions_registry_as_90_components_and_3_workflows() {
    let root = repository_root();
    let snapshot = snapshot_json(&root);
    let components = array_at(&snapshot, "components");
    let workflows = array_at(&snapshot, "workflows");
    let component_ids = identity_set(components, "component_id");
    let workflow_ids = identity_set(workflows, "workflow_id");
    let projected_ids = component_ids
        .union(&workflow_ids)
        .cloned()
        .collect::<BTreeSet<_>>();

    // 90/3 is current-corpus evidence, while the equality below is the product contract.
    assert_eq!(
        components.len(),
        90,
        "current fixture component count drifted"
    );
    assert_eq!(workflows.len(), 3, "current fixture workflow count drifted");
    assert_eq!(
        components.len() + workflows.len(),
        registry_identity_count(&root),
        "every registry identity must be classified exactly once"
    );
    assert_eq!(
        component_ids.len(),
        components.len(),
        "duplicate Component DTO"
    );
    assert_eq!(
        workflow_ids.len(),
        workflows.len(),
        "duplicate Workflow DTO"
    );
    assert!(component_ids.is_disjoint(&workflow_ids));
    assert_eq!(projected_ids, registry_identity_set(&root));
}

#[test]
fn component_kind_universe_excludes_workflow_and_mode_metadata() {
    let root = repository_root();
    let snapshot = snapshot_json(&root);
    let projection = &snapshot["graph_projection"];
    let allowed_component_kinds =
        BTreeSet::from(["agent", "command", "composite", "hook", "rule", "skill"]);

    let component_kinds = array_at(projection, "nodes")
        .iter()
        .filter(|node| node["node_type"] == "component")
        .map(|node| text_at(node, "kind"))
        .collect::<BTreeSet<_>>();
    assert!(component_kinds.is_subset(&allowed_component_kinds));
    assert!(!component_kinds.contains("workflow"));
    assert!(!component_kinds.contains("mode"));

    let mode_occurrences = relation_links(projection)
        .iter()
        .filter(|link| link["semantic"] == "workflow-step")
        .flat_map(|link| array_at(link, "occurrences"))
        .filter(|occurrence| occurrence["mode"].is_string())
        .collect::<Vec<_>>();
    assert!(
        !mode_occurrences.is_empty(),
        "current Workflow fixture must preserve mode_fanout as occurrence metadata"
    );
    assert!(mode_occurrences
        .iter()
        .all(|occurrence| { text_at(occurrence, "source_field").contains("mode_fanout") }));
}

#[test]
fn workflow_identity_never_overlaps_component_entities_or_unprofiled() {
    let fixture = fixture();
    let snapshot = snapshot_json(fixture.path());
    let component_ids = identity_set(array_at(&snapshot, "components"), "component_id");
    let workflow_ids = identity_set(array_at(&snapshot, "workflows"), "workflow_id");
    let unprofiled_ids = string_set(array_at(&snapshot, "unprofiled_component_ids"));

    assert!(component_ids.is_disjoint(&workflow_ids));
    assert!(unprofiled_ids.is_disjoint(&workflow_ids));
    assert_eq!(
        workflow_ids,
        BTreeSet::from([FIXTURE_WORKFLOW_ID.to_string()])
    );
}

#[test]
fn workflow_incidence_bundles_preserve_order_and_repeated_occurrences() {
    let fixture = fixture();
    let snapshot = snapshot_json(fixture.path());
    let projection = &snapshot["graph_projection"];
    let workflow = array_at(&snapshot, "workflows")
        .iter()
        .find(|workflow| workflow["workflow_id"] == FIXTURE_WORKFLOW_ID)
        .expect("fixture Workflow DTO must be present");
    let steps = array_at(workflow, "steps");

    assert_eq!(
        steps
            .iter()
            .map(|step| (step["ordinal"].as_u64().unwrap(), text_at(step, "step_id")))
            .collect::<Vec<_>>(),
        vec![(1, "prepare"), (2, "verify")]
    );

    let bundle = relation_links(projection)
        .iter()
        .find(|link| {
            link["semantic"] == "workflow-step"
                && link["workflow_node_id"] == format!("workflow:{FIXTURE_WORKFLOW_ID}")
                && link["component_node_id"] == format!("component:{FIXTURE_SKILL_ID}")
        })
        .expect("repeated Workflow endpoint must be represented by one bundled link");
    let occurrences = array_at(bundle, "occurrences");
    assert_eq!(
        occurrences
            .iter()
            .map(|occurrence| {
                (
                    occurrence["ordinal"].as_u64().unwrap(),
                    text_at(occurrence, "step_id"),
                    text_at(occurrence, "source_field"),
                )
            })
            .collect::<Vec<_>>(),
        vec![
            (1, "prepare", "steps[0].skill"),
            (2, "verify", "steps[1].skill"),
        ]
    );
    assert_eq!(
        relation_links(projection)
            .iter()
            .filter(|link| {
                link["semantic"] == "workflow-step"
                    && link["workflow_node_id"] == format!("workflow:{FIXTURE_WORKFLOW_ID}")
                    && link["component_node_id"] == format!("component:{FIXTURE_SKILL_ID}")
            })
            .count(),
        1,
        "repeated occurrences must not duplicate physical endpoint links"
    );
}

#[test]
fn stale_profile_workflow_membership_emits_issues_without_a_graph_bridge() {
    let fixture = fixture();
    let snapshot = snapshot_json(fixture.path());
    let projection = &snapshot["graph_projection"];
    let workflow_node_id = format!("workflow:{FIXTURE_WORKFLOW_ID}");

    let profile = array_at(&snapshot, "profiles")
        .iter()
        .find(|profile| profile["profile_id"] == FIXTURE_PROFILE_ID)
        .expect("sanitized stale Profile must remain available");
    assert_eq!(
        string_set(array_at(profile, "component_ids")),
        BTreeSet::from([FIXTURE_SKILL_ID.to_string()])
    );

    let issues = array_at(&snapshot, "issues")
        .iter()
        .filter(|issue| issue["code"] == "profile_workflow_membership_forbidden")
        .collect::<Vec<_>>();
    assert_eq!(
        issues.len(),
        2,
        "direct and scope violations must both survive"
    );
    assert!(issues
        .iter()
        .all(|issue| { text_at(issue, "source_path").starts_with("profiles/engineering.yml#") }));

    assert!(relation_links(projection).iter().all(|link| {
        !(link["semantic"] == "profile-membership"
            && (link["component_node_id"] == workflow_node_id
                || link["profile_node_id"] == workflow_node_id))
    }));
    assert!(relation_node(projection, FIXTURE_WORKFLOW_ID)["relation_kind"] == "workflow");
    assert!(!array_at(&snapshot, "unprofiled_component_ids")
        .iter()
        .any(|component_id| component_id == FIXTURE_WORKFLOW_ID));
}

#[test]
fn graph_projection_serialization_seed_and_relation_scale_are_stable() {
    let fixture = fixture();
    let first = snapshot_json(fixture.path());
    let second = snapshot_json(fixture.path());
    let first_projection = &first["graph_projection"];
    let second_projection = &second["graph_projection"];

    assert_eq!(first_projection["schema_version"], 2);
    let layout_seed = &first_projection["layout_seed"];
    assert!(
        layout_seed.is_string() || layout_seed.as_u64().is_some(),
        "layout_seed must be a stable JSON string or unsigned integer"
    );
    assert_eq!(
        first_projection["layout_seed"],
        second_projection["layout_seed"]
    );
    assert_eq!(
        serde_json::to_vec(first_projection).unwrap(),
        serde_json::to_vec(second_projection).unwrap(),
        "identical canonical input must produce byte-stable projection JSON"
    );

    let first_relations = relation_nodes(first_projection);
    let second_relations = relation_nodes(second_projection);
    assert_eq!(first_relations.len(), second_relations.len());
    assert!(!first_relations.is_empty());
    for node in first_relations {
        let scale = node["size_scale"]
            .as_f64()
            .expect("relation node must expose numeric size_scale");
        assert!((1.0..=1.2).contains(&scale));
        assert!(node["anchor_ordinal"].as_u64().is_some());
    }
}

#[test]
fn workflow_semantic_issues_preserve_valid_steps_and_exclude_only_invalid_incidence() {
    let fixture = fixture();
    let workflow_path = fixture
        .path()
        .join("components/workflows/delivery/workflow.yml");
    write(
        &workflow_path,
        r#"workflow_id: harnesskit.workflow.fixture-delivery
kind: workflow
status: draft
domain: core
title: Fixture Delivery
summary: Fixture delivery
description: Semantic validation fixture
runtime_implemented: false
steps:
  - id: prepare
    skill: harnesskit.skill.fixture-alpha
  - id: prepare
    agent: harnesskit.agent.fixture-router
  - id: gated
    agent: harnesskit.agent.missing
    requires_pass_from: missing-gate
    loop_back_to: missing-loop
"#,
    );

    let snapshot = snapshot_json(fixture.path());
    let workflow = array_at(&snapshot, "workflows")
        .iter()
        .find(|workflow| workflow["workflow_id"] == FIXTURE_WORKFLOW_ID)
        .expect("semantic errors must not erase the authored Workflow");
    assert_eq!(array_at(workflow, "steps").len(), 3);

    let issue_codes = array_at(&snapshot, "issues")
        .iter()
        .map(|issue| text_at(issue, "code"))
        .collect::<Vec<_>>();
    assert_eq!(
        issue_codes
            .iter()
            .filter(|code| **code == "duplicate_workflow_step_id")
            .count(),
        1
    );
    assert_eq!(
        issue_codes
            .iter()
            .filter(|code| **code == "unresolved_workflow_step_reference")
            .count(),
        2
    );
    assert_eq!(
        issue_codes
            .iter()
            .filter(|code| **code == "unresolved_workflow_component_reference")
            .count(),
        1
    );
    assert!(relation_links(&snapshot["graph_projection"])
        .iter()
        .any(|link| {
            link["semantic"] == "workflow-step"
                && link["component_node_id"] == format!("component:{FIXTURE_SKILL_ID}")
        }));
    assert!(!relation_links(&snapshot["graph_projection"])
        .iter()
        .any(|link| link["component_node_id"] == "component:harnesskit.agent.missing"));
}

#[test]
fn workflow_component_field_kind_mismatch_is_not_projected_as_incidence() {
    let fixture = fixture();
    let workflow_path = fixture
        .path()
        .join("components/workflows/delivery/workflow.yml");
    write(
        &workflow_path,
        r#"workflow_id: harnesskit.workflow.fixture-delivery
kind: workflow
status: draft
domain: core
title: Fixture Delivery
summary: Fixture delivery
description: Field kind mismatch fixture
runtime_implemented: false
steps:
  - id: prepare
    skill: harnesskit.agent.fixture-router
"#,
    );

    let snapshot = snapshot_json(fixture.path());
    assert!(array_at(&snapshot, "issues")
        .iter()
        .any(|issue| issue["code"] == "workflow_reference_kind_mismatch"));
    assert!(!relation_links(&snapshot["graph_projection"])
        .iter()
        .any(|link| {
            link["semantic"] == "workflow-step"
                && link["component_node_id"] == "component:harnesskit.agent.fixture-router"
        }));
}

#[test]
fn workflow_raw_yaml_is_the_exact_inert_source_and_invoked_by_has_no_ordinal() {
    let fixture = fixture();
    let source_path = fixture
        .path()
        .join("components/workflows/delivery/workflow.yml");
    let authored = fs::read_to_string(&source_path).unwrap();
    let snapshot = snapshot_json(fixture.path());
    let workflow = array_at(&snapshot, "workflows")
        .iter()
        .find(|workflow| workflow["workflow_id"] == FIXTURE_WORKFLOW_ID)
        .unwrap();
    assert_eq!(workflow["raw_yaml"], authored);

    let invoked_by = relation_links(&snapshot["graph_projection"])
        .iter()
        .find(|link| link["semantic"] == "invoked-by")
        .expect("optional_invoked_by must be a distinct non-ordinal directed relation");
    assert!(invoked_by.get("ordinal").is_none());
    assert!(invoked_by.get("occurrences").is_none());
    assert_eq!(invoked_by["directionality"], "directed");
    assert_eq!(
        invoked_by["source_node_id"],
        "component:harnesskit.skill.fixture-alpha"
    );
    assert_eq!(
        invoked_by["target_node_id"],
        "workflow:harnesskit.workflow.fixture-delivery"
    );
}
