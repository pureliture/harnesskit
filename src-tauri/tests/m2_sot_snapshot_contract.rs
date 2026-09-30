use std::collections::BTreeSet;
use std::fs;
use std::path::Path;
use std::process::Command;

use harness_desktop_lib::checkout::RegisteredCheckout;
use harness_desktop_lib::contexts::sot::{
    selected_profile_component_closure, SotContext, SotSnapshot,
};
use tempfile::tempdir;

fn write(path: &Path, source: &str) {
    fs::create_dir_all(path.parent().unwrap()).unwrap();
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

fn load_snapshot(root: &Path) -> harness_desktop_lib::contexts::sot::SotSnapshot {
    let checkout = RegisteredCheckout::open(root).unwrap();
    let context = SotContext::with_active_checkout(Some("fixture-checkout".to_string()));
    context.load("fixture-checkout", &checkout).unwrap()
}

fn graph_component_ids(snapshot: &SotSnapshot) -> BTreeSet<&str> {
    snapshot
        .graph_projection
        .nodes
        .iter()
        .filter_map(|node| node.component_id())
        .collect()
}

fn graph_node_ids(snapshot: &SotSnapshot) -> BTreeSet<&str> {
    snapshot
        .graph_projection
        .nodes
        .iter()
        .map(|node| node.node_id())
        .collect()
}

fn graph_link_values(snapshot: &SotSnapshot) -> Vec<serde_json::Value> {
    serde_json::to_value(&snapshot.graph_projection.links)
        .unwrap()
        .as_array()
        .unwrap()
        .clone()
}

fn graph_link_ids(snapshot: &SotSnapshot) -> BTreeSet<String> {
    graph_link_values(snapshot)
        .into_iter()
        .map(|link| link["link_id"].as_str().unwrap().to_string())
        .collect()
}

#[test]
fn snapshot_preserves_scope_specific_profile_inputs_for_independent_install_closure() {
    let fixture = fixture();
    let snapshot = load_snapshot(fixture.path());
    let profile = snapshot
        .profiles
        .iter()
        .find(|profile| profile.profile_id == "harnesskit.profile.fixture-engineering")
        .unwrap();

    assert_eq!(
        profile.base_component_ids,
        [
            "harnesskit.agent.fixture-router".to_string(),
            "harnesskit.skill.fixture-alpha".to_string(),
        ]
    );
    assert_eq!(
        profile.scope_component_ids["project"],
        ["harnesskit.composite.fixture-bundle".to_string()]
    );
    assert_eq!(
        selected_profile_component_closure(
            &snapshot,
            "harnesskit.profile.fixture-engineering",
            "project",
        )
        .unwrap(),
        BTreeSet::from([
            "harnesskit.agent.fixture-router".to_string(),
            "harnesskit.skill.fixture-alpha".to_string(),
        ])
    );
}

fn fixture() -> tempfile::TempDir {
    let temp = tempdir().unwrap();
    write(
        &temp.path().join("components/registry.yml"),
        r#"version: "1"
components:
  harnesskit.skill.fixture-alpha:
    kind: skill
    status: draft
    path: components/skills/alpha/component.yml
  harnesskit.agent.fixture-router:
    kind: agent
    status: draft
    path: components/agents/router/agent.yml
  harnesskit.rule.fixture-core:
    kind: rule
    status: draft
    path: components/rules/core/component.yml
  harnesskit.composite.fixture-bundle:
    kind: composite
    status: draft
    path: components/composites/bundle/composite.yml
  harnesskit.workflow.fixture-delivery:
    kind: workflow
    status: draft
    path: components/workflows/delivery/workflow.yml
  harnesskit.skill.fixture-legacy:
    kind: skill
    status: deprecated
    path: components/skills/legacy/component.yml
    replaced_by: harnesskit.skill.fixture-alpha
    absorbed_by: harnesskit.rule.fixture-core
"#,
    );
    write(
        &temp.path().join("components/skills/alpha/component.yml"),
        r#"component_id: harnesskit.skill.fixture-alpha
kind: skill
status: draft
domain: core
title: Alpha Skill
summary: Alpha fixture
policy_ref: harnesskit.rule.fixture-core
owned_files:
  - components/skills/alpha/SKILL.md
  - /private/local-secret
  - ..\\private-secret
targets:
  codex: { output_path: skills/alpha }
reuses:
  - component_id: harnesskit.agent.fixture-router
"#,
    );
    write(
        &temp.path().join("components/agents/router/agent.yml"),
        r#"component_id: harnesskit.agent.fixture-router
kind: agent
status: draft
domain: core
title: Router
summary: Router fixture
role:
  type: router
routes:
  policy_ref: harnesskit.rule.fixture-core
  workflows:
    - component_id: harnesskit.workflow.fixture-delivery
"#,
    );
    write(
        &temp.path().join("components/rules/core/component.yml"),
        r#"component_id: harnesskit.rule.fixture-core
kind: rule
status: draft
domain: core
title: Core Policy
summary: Core policy fixture
"#,
    );
    write(
        &temp
            .path()
            .join("components/composites/bundle/composite.yml"),
        r#"composite_id: harnesskit.composite.fixture-bundle
kind: composite
status: draft
domain: core
title: Fixture Bundle
summary: Fixture bundle
members:
  - harnesskit.skill.fixture-alpha
  - harnesskit.agent.fixture-router
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
title: Delivery
summary: Fixture delivery workflow
description: Fixture delivery workflow
optional_invoked_by: harnesskit.skill.fixture-alpha
steps:
  - id: prepare
    agent: harnesskit.agent.fixture-router
    skill: harnesskit.skill.fixture-alpha
    rule: harnesskit.rule.fixture-missing
    mode_fanout:
      review:
        agents: [harnesskit.agent.fixture-router]
orchestrates:
  workflow_id: harnesskit.workflow.fixture-shadow
"#,
    );
    write(
        &temp.path().join("profiles/engineering.yml"),
        r#"profile_id: harnesskit.profile.fixture-engineering
status: draft
title: Engineering
summary: Fixture engineering profile
components:
  - harnesskit.skill.fixture-alpha
  - harnesskit.agent.fixture-router
install_policy:
  default_scope: project
  allowed_scopes: [project, user]
  activation_policy: manual
  scope_components:
    project:
      - harnesskit.composite.fixture-bundle
"#,
    );
    write(
        &temp.path().join("profiles/minimal.yml"),
        "profile_id: harnesskit.profile.fixture-minimal\nstatus: draft\ntitle: Minimal\nsummary: Empty fixture profile\ncomponents: []\ninstall_policy:\n  default_scope: project\n  allowed_scopes: [project]\n  activation_policy: manual\n",
    );
    write(
        &temp.path().join("profiles/maintenance.yml"),
        r#"profile_id: harnesskit.profile.fixture-maintenance
status: draft
title: Maintenance
summary: Multiple-membership fixture
components:
  - harnesskit.skill.fixture-alpha
  - harnesskit.skill.fixture-unknown
install_policy:
  default_scope: project
  allowed_scopes: [project]
  activation_policy: manual
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
    git(temp.path(), &["config", "user.name", "HarnessKit Test"]);
    git(
        temp.path(),
        &[
            "config",
            "user.email",
            concat!("harness-desktop-test@", "example.invalid"),
        ],
    );
    git(temp.path(), &["add", "."]);
    git(temp.path(), &["commit", "--quiet", "-m", "fixture"]);
    temp
}

#[test]
fn snapshot_partitions_workflow_from_components_and_keeps_whitelisted_relations() {
    let fixture = fixture();
    let snapshot = load_snapshot(fixture.path());
    let component_ids = snapshot
        .components
        .iter()
        .map(|component| component.component_id.as_str())
        .collect::<BTreeSet<_>>();

    assert_eq!(snapshot.components.len(), 5);
    assert_eq!(component_ids.len(), 5);
    assert!(!component_ids.contains("harnesskit.workflow.fixture-delivery"));
    assert_eq!(snapshot.workflows.len(), 1);
    assert_eq!(
        snapshot.workflows[0].workflow_id,
        "harnesskit.workflow.fixture-delivery"
    );
    assert_eq!(
        snapshot.graph_projection.nodes.len(),
        snapshot.components.len() + snapshot.profiles.len() + snapshot.workflows.len() + 1
    );
    let graph_ids = graph_component_ids(&snapshot);
    assert_eq!(graph_ids, component_ids);
    let registry_ids = BTreeSet::from([
        "harnesskit.skill.fixture-alpha",
        "harnesskit.agent.fixture-router",
        "harnesskit.rule.fixture-core",
        "harnesskit.composite.fixture-bundle",
        "harnesskit.workflow.fixture-delivery",
        "harnesskit.skill.fixture-legacy",
    ]);
    assert!(snapshot.relations.iter().all(|edge| {
        registry_ids.contains(edge.source.as_str())
            && registry_ids.contains(edge.target.as_str())
            && edge.declarative_only
    }));

    let fields = snapshot
        .relations
        .iter()
        .map(|edge| edge.source_field.as_str())
        .collect::<BTreeSet<_>>();
    for expected in [
        "replaced_by",
        "absorbed_by",
        "members[0]",
        "members[1]",
        "policy_ref",
        "routes.policy_ref",
        "routes.workflows[0].component_id",
        "steps[0].agent",
        "steps[0].skill",
        "steps[0].mode_fanout.review.agents[0]",
        "optional_invoked_by",
    ] {
        assert!(
            fields.contains(expected),
            "missing whitelisted field {expected}"
        );
    }
    assert!(!fields.iter().any(|field| field.starts_with("reuses")));
    assert!(!fields.iter().any(|field| field.starts_with("orchestrates")));
    assert!(!snapshot
        .graph_projection
        .nodes
        .iter()
        .any(|node| node.component_id() == Some("harnesskit.rule.fixture-missing")));
    assert!(snapshot
        .issues
        .iter()
        .any(|issue| issue.code == "unresolved_workflow_component_reference"));

    let relation_tuples = snapshot
        .relations
        .iter()
        .map(|edge| {
            (
                edge.source.as_str(),
                edge.target.as_str(),
                edge.relation_type.as_str(),
                edge.source_path.as_str(),
                edge.source_field.as_str(),
                edge.declarative_only,
            )
        })
        .collect::<BTreeSet<_>>();
    let expected = BTreeSet::from([
        (
            "harnesskit.skill.fixture-legacy",
            "harnesskit.skill.fixture-alpha",
            "registry_replaced_by",
            "components/registry.yml",
            "replaced_by",
            true,
        ),
        (
            "harnesskit.skill.fixture-legacy",
            "harnesskit.rule.fixture-core",
            "registry_absorbed_by",
            "components/registry.yml",
            "absorbed_by",
            true,
        ),
        (
            "harnesskit.skill.fixture-alpha",
            "harnesskit.rule.fixture-core",
            "policy_ref",
            "components/skills/alpha/component.yml",
            "policy_ref",
            true,
        ),
        (
            "harnesskit.agent.fixture-router",
            "harnesskit.rule.fixture-core",
            "policy_ref",
            "components/agents/router/agent.yml",
            "routes.policy_ref",
            true,
        ),
        (
            "harnesskit.agent.fixture-router",
            "harnesskit.workflow.fixture-delivery",
            "router_workflow",
            "components/agents/router/agent.yml",
            "routes.workflows[0].component_id",
            true,
        ),
        (
            "harnesskit.composite.fixture-bundle",
            "harnesskit.skill.fixture-alpha",
            "composite_member",
            "components/composites/bundle/composite.yml",
            "members[0]",
            true,
        ),
        (
            "harnesskit.composite.fixture-bundle",
            "harnesskit.agent.fixture-router",
            "composite_member",
            "components/composites/bundle/composite.yml",
            "members[1]",
            true,
        ),
        (
            "harnesskit.skill.fixture-alpha",
            "harnesskit.workflow.fixture-delivery",
            "optional_invokes_workflow",
            "components/workflows/delivery/workflow.yml",
            "optional_invoked_by",
            true,
        ),
        (
            "harnesskit.workflow.fixture-delivery",
            "harnesskit.agent.fixture-router",
            "workflow_agent",
            "components/workflows/delivery/workflow.yml",
            "steps[0].agent",
            true,
        ),
        (
            "harnesskit.workflow.fixture-delivery",
            "harnesskit.skill.fixture-alpha",
            "workflow_skill",
            "components/workflows/delivery/workflow.yml",
            "steps[0].skill",
            true,
        ),
        (
            "harnesskit.workflow.fixture-delivery",
            "harnesskit.agent.fixture-router",
            "workflow_mode_agent",
            "components/workflows/delivery/workflow.yml",
            "steps[0].mode_fanout.review.agents[0]",
            true,
        ),
    ]);
    assert_eq!(relation_tuples, expected);
}

#[test]
fn profiles_include_scope_membership_and_unprofiled_is_neutral() {
    let fixture = fixture();
    let snapshot = load_snapshot(fixture.path());
    let engineering = snapshot
        .profiles
        .iter()
        .find(|profile| profile.profile_id == "harnesskit.profile.fixture-engineering")
        .unwrap();

    assert_eq!(
        engineering.component_ids,
        [
            "harnesskit.agent.fixture-router",
            "harnesskit.composite.fixture-bundle",
            "harnesskit.skill.fixture-alpha",
        ]
    );
    assert_eq!(
        snapshot.unprofiled_component_ids,
        [
            "harnesskit.rule.fixture-core",
            "harnesskit.skill.fixture-legacy",
        ]
    );
    assert!(!snapshot
        .profiles
        .iter()
        .any(|profile| profile.profile_id == "Unprofiled"));
    let alpha = snapshot
        .components
        .iter()
        .find(|component| component.component_id == "harnesskit.skill.fixture-alpha")
        .unwrap();
    assert_eq!(
        alpha.profile_ids,
        [
            "harnesskit.profile.fixture-engineering",
            "harnesskit.profile.fixture-maintenance",
        ]
    );
    assert!(snapshot
        .issues
        .iter()
        .any(|issue| issue.code == "unknown_profile_member"));
}

#[test]
fn stale_profile_workflow_memberships_are_sanitized_before_strict_schema() {
    let fixture = fixture();
    let profile_path = fixture.path().join("profiles/engineering.yml");
    let source = fs::read_to_string(&profile_path)
        .unwrap()
        .replace(
            "  - harnesskit.agent.fixture-router\ninstall_policy:",
            "  - harnesskit.agent.fixture-router\n  - harnesskit.workflow.fixture-delivery\ninstall_policy:",
        )
        .replace(
            "      - harnesskit.composite.fixture-bundle\n",
            "      - harnesskit.composite.fixture-bundle\n      - harnesskit.workflow.fixture-delivery\n",
        );
    fs::write(&profile_path, source).unwrap();
    let composite_path = fixture
        .path()
        .join("components/composites/bundle/composite.yml");
    let composite = fs::read_to_string(&composite_path).unwrap().replace(
        "  - harnesskit.agent.fixture-router\n",
        "  - harnesskit.agent.fixture-router\n  - harnesskit.rule.fixture-core\n  - harnesskit.workflow.fixture-delivery\n",
    );
    fs::write(&composite_path, composite).unwrap();

    let snapshot = load_snapshot(fixture.path());
    let profile = snapshot
        .profiles
        .iter()
        .find(|profile| profile.profile_id == "harnesskit.profile.fixture-engineering")
        .expect("selective recovery must preserve the stale Profile");

    assert!(!profile
        .component_ids
        .iter()
        .any(|component_id| component_id == "harnesskit.workflow.fixture-delivery"));
    let violation_paths = snapshot
        .issues
        .iter()
        .filter(|issue| issue.code == "profile_workflow_membership_forbidden")
        .map(|issue| issue.source_path.as_str())
        .collect::<BTreeSet<_>>();
    assert_eq!(
        violation_paths,
        BTreeSet::from([
            "profiles/engineering.yml#components[2]",
            "profiles/engineering.yml#install_policy.scope_components.project[0].members[3]",
            "profiles/engineering.yml#install_policy.scope_components.project[1]",
        ])
    );
    assert!(profile.scope_component_ids["project"]
        .iter()
        .any(|component_id| component_id == "harnesskit.rule.fixture-core"));
    assert!(!profile.scope_component_ids["project"]
        .iter()
        .any(|component_id| component_id == "harnesskit.workflow.fixture-delivery"));
    assert_eq!(
        selected_profile_component_closure(
            &snapshot,
            "harnesskit.profile.fixture-engineering",
            "project",
        )
        .unwrap(),
        BTreeSet::from([
            "harnesskit.agent.fixture-router".to_string(),
            "harnesskit.rule.fixture-core".to_string(),
            "harnesskit.skill.fixture-alpha".to_string(),
        ])
    );
    let workflow_node_id = "workflow:harnesskit.workflow.fixture-delivery";
    assert!(!graph_link_values(&snapshot).iter().any(|link| {
        link["semantic"] == "profile-membership"
            && (link["profile_node_id"] == workflow_node_id
                || link["component_node_id"] == workflow_node_id)
    }));
    assert!(snapshot.relations.iter().any(|relation| {
        relation.source == "harnesskit.workflow.fixture-delivery"
            && relation.source_field == "steps[0].agent"
    }));
    assert!(!snapshot
        .components
        .iter()
        .any(|component| component.component_id == "harnesskit.workflow.fixture-delivery"));
    assert!(!snapshot
        .unprofiled_component_ids
        .iter()
        .any(|component_id| component_id == "harnesskit.workflow.fixture-delivery"));
}

#[test]
fn identical_source_produces_byte_stable_snapshot_and_topology() {
    let fixture = fixture();
    let first = load_snapshot(fixture.path());
    let second = load_snapshot(fixture.path());

    assert_eq!(first.snapshot_id, second.snapshot_id);
    assert_eq!(
        serde_json::to_vec(&first).unwrap(),
        serde_json::to_vec(&second).unwrap()
    );
    assert_eq!(first.graph_projection.schema_version, 2);
    assert!(!first.graph_projection.layout_seed.is_empty());
    assert_eq!(
        graph_node_ids(&first).len(),
        first.graph_projection.nodes.len(),
        "graph node identities must be unique"
    );
    assert_eq!(
        graph_link_ids(&first).len(),
        first.graph_projection.links.len(),
        "graph link identities must be unique"
    );
}

#[test]
fn component_nodes_are_unique_namespaced_center_entities() {
    let fixture = fixture();
    let snapshot = load_snapshot(fixture.path());
    let alpha = snapshot
        .graph_projection
        .nodes
        .iter()
        .find(|node| node.component_id() == Some("harnesskit.skill.fixture-alpha"))
        .unwrap();
    let legacy = snapshot
        .graph_projection
        .nodes
        .iter()
        .find(|node| node.component_id() == Some("harnesskit.skill.fixture-legacy"))
        .unwrap();

    assert_eq!(
        graph_component_ids(&snapshot).len(),
        snapshot.components.len()
    );
    assert_eq!(alpha.node_id(), "component:harnesskit.skill.fixture-alpha");
    assert_eq!(
        legacy.node_id(),
        "component:harnesskit.skill.fixture-legacy"
    );
    for node in [alpha, legacy] {
        let value = serde_json::to_value(node).unwrap();
        assert_eq!(value["node_type"], "component");
        assert!(
            value.get("shell").is_none(),
            "Component graph identities must not carry renderer shell authority"
        );
        assert!(value["kind"].is_string());
        assert!(value["relation_degree"].as_u64().is_some());
        assert!(value.get("x").is_none());
        assert!(value.get("y").is_none());
    }
}

#[test]
fn live_graph_topology_has_unique_nodes_links_and_valid_endpoints() {
    let repository = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    let snapshot = load_snapshot(repository);
    let node_ids = graph_node_ids(&snapshot);
    let link_values = graph_link_values(&snapshot);

    assert_eq!(
        graph_component_ids(&snapshot).len(),
        snapshot.components.len()
    );
    assert_eq!(node_ids.len(), snapshot.graph_projection.nodes.len());
    assert_eq!(graph_link_ids(&snapshot).len(), link_values.len());
    for link in &link_values {
        match link["semantic"].as_str().unwrap() {
            "profile-membership" => {
                let profile = link["profile_node_id"].as_str().unwrap();
                let component = link["component_node_id"].as_str().unwrap();
                assert!(node_ids.contains(profile));
                assert!(node_ids.contains(component));
                assert!(!profile.starts_with("workflow:"));
                assert!(component.starts_with("component:"));
            }
            "workflow-step" | "invoked-by" => {
                assert!(node_ids.contains(link["workflow_node_id"].as_str().unwrap()));
                assert!(node_ids.contains(link["component_node_id"].as_str().unwrap()));
            }
            "component-cross-link" => {
                assert!(node_ids.contains(
                    format!(
                        "component:{}",
                        link["source_component_id"].as_str().unwrap()
                    )
                    .as_str()
                ));
                assert!(node_ids.contains(
                    format!(
                        "component:{}",
                        link["target_component_id"].as_str().unwrap()
                    )
                    .as_str()
                ));
            }
            semantic => panic!("unexpected graph link semantic: {semantic}"),
        }
    }
}

#[test]
fn relation_provenance_is_exact_and_component_dto_excludes_local_state() {
    let fixture = fixture();
    let snapshot = load_snapshot(fixture.path());
    let edge = snapshot
        .relations
        .iter()
        .find(|edge| edge.source_field == "steps[0].mode_fanout.review.agents[0]")
        .unwrap();

    assert_eq!(edge.source, "harnesskit.workflow.fixture-delivery");
    assert_eq!(edge.target, "harnesskit.agent.fixture-router");
    assert_eq!(edge.relation_type, "workflow_mode_agent");
    assert_eq!(
        edge.source_path,
        "components/workflows/delivery/workflow.yml"
    );

    let component = serde_json::to_value(&snapshot.components[0]).unwrap();
    for forbidden in ["path", "local_path", "hash", "install_status", "locations"] {
        assert!(
            component.get(forbidden).is_none(),
            "forbidden field {forbidden}"
        );
    }
    assert!(snapshot
        .issues
        .iter()
        .any(|issue| issue.code == "invalid_owned_file"));
    assert!(snapshot
        .issues
        .iter()
        .all(|issue| !issue.source_path.starts_with('/')));
}

#[test]
fn non_topology_source_change_updates_snapshot_without_changing_layout_seed() {
    let fixture = fixture();
    let before = load_snapshot(fixture.path());
    let manifest = fixture.path().join("components/skills/alpha/component.yml");
    let mut source = fs::read_to_string(&manifest).unwrap();
    source = source.replace("summary: Alpha fixture", "summary: Updated fixture");
    fs::write(manifest, source).unwrap();
    let after = load_snapshot(fixture.path());

    assert_ne!(before.snapshot_id, after.snapshot_id);
    assert_eq!(
        before.graph_projection.layout_seed,
        after.graph_projection.layout_seed
    );
    assert_eq!(before.graph_projection.nodes, after.graph_projection.nodes);
    assert_eq!(before.graph_projection.links, after.graph_projection.links);
}

#[test]
fn invalid_relation_schema_and_non_router_routes_emit_no_edges() {
    let fixture = fixture();
    let workflow = fixture
        .path()
        .join("components/workflows/delivery/workflow.yml");
    let workflow_source = fs::read_to_string(&workflow)
        .unwrap()
        .replace("description: Fixture delivery workflow\n", "");
    fs::write(&workflow, workflow_source).unwrap();
    let skill = fixture.path().join("components/skills/alpha/component.yml");
    let mut skill_source = fs::read_to_string(&skill).unwrap();
    skill_source.push_str(
        "routes:\n  workflows:\n    - component_id: harnesskit.workflow.fixture-delivery\n",
    );
    fs::write(skill, skill_source).unwrap();

    let snapshot = load_snapshot(fixture.path());
    assert!(snapshot
        .issues
        .iter()
        .any(|issue| issue.code == "manifest_schema_invalid"));
    assert!(!snapshot.relations.iter().any(|edge| {
        edge.source_path == "components/workflows/delivery/workflow.yml"
            || (edge.source == "harnesskit.skill.fixture-alpha"
                && edge.source_field.starts_with("routes.workflows"))
    }));
}

#[test]
fn canonical_schema_is_required_for_projection_and_relation_authority() {
    let fixture = fixture();
    let skill = fixture.path().join("components/skills/alpha/component.yml");
    let skill_source = fs::read_to_string(&skill)
        .unwrap()
        .replace("status: draft", "status: invented")
        .replace("domain: core", "domain: invented")
        .replace("  codex: { output_path: skills/alpha }", "  invented: {} ");
    fs::write(&skill, skill_source).unwrap();

    let composite = fixture
        .path()
        .join("components/composites/bundle/composite.yml");
    let mut composite_source = fs::read_to_string(&composite).unwrap();
    composite_source.push_str("policy_ref: harnesskit.rule.fixture-core\n");
    fs::write(&composite, composite_source).unwrap();

    let workflow = fixture
        .path()
        .join("components/workflows/delivery/workflow.yml");
    let mut workflow_source = fs::read_to_string(&workflow).unwrap();
    workflow_source.push_str("policy_ref: harnesskit.rule.fixture-core\n");
    fs::write(&workflow, workflow_source).unwrap();

    let snapshot = load_snapshot(fixture.path());
    let alpha = snapshot
        .components
        .iter()
        .find(|component| component.component_id == "harnesskit.skill.fixture-alpha")
        .unwrap();

    assert_eq!(alpha.title, "Fixture Alpha");
    assert_eq!(alpha.domain, None);
    assert!(alpha.targets.is_empty());
    assert!(alpha.owned_files.is_empty());
    assert!(!snapshot.relations.iter().any(|relation| {
        relation.source == "harnesskit.skill.fixture-alpha" && relation.source_field == "policy_ref"
    }));
    assert!(!snapshot
        .relations
        .iter()
        .any(|relation| { relation.source == "harnesskit.composite.fixture-bundle" }));
    assert!(!snapshot.relations.iter().any(|relation| {
        relation.source == "harnesskit.workflow.fixture-delivery"
            && relation.source_field == "policy_ref"
    }));
    assert!(snapshot.relations.iter().any(|relation| {
        relation.source == "harnesskit.workflow.fixture-delivery"
            && relation.source_field == "steps[0].agent"
    }));
    assert!(
        snapshot
            .issues
            .iter()
            .filter(|issue| issue.code == "manifest_schema_invalid")
            .count()
            >= 2
    );
}

#[test]
fn registry_version_must_match_the_supported_canonical_contract() {
    let fixture = fixture();
    let registry = fixture.path().join("components/registry.yml");
    let source = fs::read_to_string(&registry)
        .unwrap()
        .replace("version: \"1\"", "version: \"2\"");
    fs::write(registry, source).unwrap();
    let checkout = RegisteredCheckout::open(fixture.path()).unwrap();
    let context = SotContext::with_active_checkout(Some("fixture-checkout".to_string()));

    assert_eq!(
        context.load("fixture-checkout", &checkout).unwrap_err(),
        "SoT registry version is unsupported"
    );
}

#[test]
fn invalid_registry_entry_shape_is_not_an_active_component_identity() {
    let fixture = fixture();
    let registry = fixture.path().join("components/registry.yml");
    let mut source = fs::read_to_string(&registry).unwrap();
    source.push_str(
        "  harnesskit.skill.invalid-entry:\n    kind: skill\n    status: draft\n    path: components/skills/alpha/component.yml\n    unexpected: true\n",
    );
    fs::write(registry, source).unwrap();

    let snapshot = load_snapshot(fixture.path());

    assert!(!snapshot
        .components
        .iter()
        .any(|component| component.component_id == "harnesskit.skill.invalid-entry"));
    assert!(!snapshot
        .graph_projection
        .nodes
        .iter()
        .any(|node| node.component_id() == Some("harnesskit.skill.invalid-entry")));
    assert!(snapshot
        .issues
        .iter()
        .any(|issue| issue.code == "invalid_registry_entry"));
}

#[test]
fn planned_missing_manifest_is_retained_without_hiding_real_missing_sources() {
    let fixture = fixture();
    let registry = fixture.path().join("components/registry.yml");
    let mut source = fs::read_to_string(&registry).unwrap();
    source.push_str(
        r#"  harnesskit.skill.fixture-planned:
    kind: skill
    status: skeleton
    planned: true
    path: components/skills/planned/component.yml
  harnesskit.skill.fixture-missing:
    kind: skill
    status: draft
    path: components/skills/missing/component.yml
  harnesskit.skill.fixture-broken-link:
    kind: skill
    status: skeleton
    planned: true
    path: components/skills/broken-link/component.yml
  harnesskit.skill.fixture-broken-parent:
    kind: skill
    status: skeleton
    planned: true
    path: components/skills/broken-parent/component.yml
  harnesskit.skill.fixture-outside-parent:
    kind: skill
    status: skeleton
    planned: true
    path: components/skills/outside-parent/component.yml
"#,
    );
    fs::write(&registry, source).unwrap();
    let broken = fixture
        .path()
        .join("components/skills/broken-link/component.yml");
    fs::create_dir_all(broken.parent().unwrap()).unwrap();
    std::os::unix::fs::symlink("missing-target.yml", &broken).unwrap();
    let broken_parent = fixture.path().join("components/skills/broken-parent");
    std::os::unix::fs::symlink("missing-directory", &broken_parent).unwrap();
    let outside = tempdir().unwrap();
    write(
        &outside.path().join("component.yml"),
        "component_id: harnesskit.skill.fixture-outside-parent\n",
    );
    let outside_parent = fixture.path().join("components/skills/outside-parent");
    std::os::unix::fs::symlink(outside.path(), &outside_parent).unwrap();

    let snapshot = load_snapshot(fixture.path());

    for identity in [
        "harnesskit.skill.fixture-planned",
        "harnesskit.skill.fixture-missing",
        "harnesskit.skill.fixture-broken-link",
        "harnesskit.skill.fixture-broken-parent",
        "harnesskit.skill.fixture-outside-parent",
    ] {
        assert!(snapshot
            .components
            .iter()
            .any(|component| component.component_id == identity));
        assert!(snapshot
            .graph_projection
            .nodes
            .iter()
            .any(|node| node.component_id() == Some(identity)));
    }
    assert!(!snapshot.issues.iter().any(|issue| {
        issue.code == "missing_manifest"
            && issue.source_path == "components/skills/planned/component.yml"
    }));
    for path in [
        "components/skills/missing/component.yml",
        "components/skills/broken-link/component.yml",
        "components/skills/broken-parent/component.yml",
    ] {
        assert!(snapshot
            .issues
            .iter()
            .any(|issue| issue.code == "missing_manifest" && issue.source_path == path));
    }
    assert!(snapshot.issues.iter().any(|issue| {
        issue.code == "manifest_path_escape"
            && issue.source_path == "components/skills/outside-parent/component.yml"
    }));
}

#[test]
fn registry_status_uses_lifecycle_catalog_and_invalid_domains() {
    let fixture = fixture();
    let registry = fixture.path().join("components/registry.yml");
    let source = fs::read_to_string(&registry)
        .unwrap()
        .replace(
            "harnesskit.skill.fixture-alpha:\n    kind: skill\n    status: draft",
            "harnesskit.skill.fixture-alpha:\n    kind: skill\n    status: deprecated\n    planned: true",
        )
        .replace(
            "harnesskit.agent.fixture-router:\n    kind: agent\n    status: draft",
            "harnesskit.agent.fixture-router:\n    kind: agent\n    status: draft-card-only",
        )
        .replace(
            "harnesskit.rule.fixture-core:\n    kind: rule\n    status: draft",
            "harnesskit.rule.fixture-core:\n    kind: rule\n    status: drafft",
        );
    fs::write(&registry, source).unwrap();

    let snapshot = load_snapshot(fixture.path());
    let mismatches = snapshot
        .issues
        .iter()
        .filter(|issue| issue.code == "manifest_status_mismatch")
        .collect::<Vec<_>>();

    assert_eq!(mismatches.len(), 1);
    assert_eq!(
        mismatches[0].source_path,
        "components/skills/alpha/component.yml"
    );
    assert_eq!(
        snapshot
            .issues
            .iter()
            .filter(|issue| issue.code == "invalid_registry_status")
            .count(),
        1
    );
    assert_eq!(
        snapshot
            .components
            .iter()
            .find(|component| component.component_id == "harnesskit.agent.fixture-router")
            .unwrap()
            .status,
        "draft-card-only"
    );
    assert_eq!(
        snapshot
            .components
            .iter()
            .find(|component| component.component_id == "harnesskit.rule.fixture-core")
            .unwrap()
            .status,
        "drafft"
    );
}

#[test]
fn every_catalog_only_status_skips_manifest_lifecycle_comparison() {
    for status in ["draft-card-only", "skeleton", "semi-concrete"] {
        let fixture = fixture();
        let registry = fixture.path().join("components/registry.yml");
        let source = fs::read_to_string(&registry).unwrap().replace(
            "harnesskit.agent.fixture-router:\n    kind: agent\n    status: draft",
            &format!("harnesskit.agent.fixture-router:\n    kind: agent\n    status: {status}"),
        );
        fs::write(&registry, source).unwrap();

        let snapshot = load_snapshot(fixture.path());

        assert!(!snapshot.issues.iter().any(|issue| {
            issue.code == "manifest_status_mismatch"
                && issue.source_path == "components/agents/router/agent.yml"
        }));
        assert!(!snapshot
            .issues
            .iter()
            .any(|issue| issue.code == "invalid_registry_status"));
        assert_eq!(
            snapshot
                .components
                .iter()
                .find(|component| component.component_id == "harnesskit.agent.fixture-router")
                .unwrap()
                .status,
            status
        );
    }
}

#[test]
fn live_repository_has_no_planned_or_status_false_positive_issues() {
    let repository = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    let snapshot = load_snapshot(repository);
    let unexpected = snapshot
        .issues
        .iter()
        .filter(|issue| {
            matches!(
                issue.code.as_str(),
                "missing_manifest" | "manifest_status_mismatch" | "invalid_registry_status"
            )
        })
        .map(|issue| (issue.code.as_str(), issue.source_path.as_str()))
        .collect::<Vec<_>>();

    assert!(unexpected.is_empty(), "unexpected issues: {unexpected:?}");
    let registry: serde_yaml::Value =
        serde_yaml::from_slice(&fs::read(repository.join("components/registry.yml")).unwrap())
            .unwrap();
    assert_eq!(
        registry["components"]["harnesskit.workflow.spec-to-tdd"]["status"].as_str(),
        Some("draft-card-only")
    );
    assert!(!registry["components"]
        .as_mapping()
        .unwrap()
        .contains_key(&serde_yaml::Value::String(
            "harnesskit.agent.backend-codebase-reviewer".to_string(),
        )));
    assert!(!repository
        .join("components/agents/backend-codebase-reviewer")
        .exists());
    assert!(
        fs::read_to_string(repository.join("components/workflows/spec-to-tdd/workflow.yml"))
            .unwrap()
            .contains("status: draft")
    );
}

#[test]
fn replaced_snapshot_id_fails_closed() {
    let fixture = fixture();
    let checkout = RegisteredCheckout::open(fixture.path()).unwrap();
    let context = SotContext::with_active_checkout(Some("fixture-checkout".to_string()));
    let first = context.load("fixture-checkout", &checkout).unwrap();
    let manifest = fixture.path().join("components/skills/alpha/component.yml");
    let source = fs::read_to_string(&manifest)
        .unwrap()
        .replace("summary: Alpha fixture", "summary: Replacement fixture");
    fs::write(manifest, source).unwrap();
    let second = context.load("fixture-checkout", &checkout).unwrap();

    assert_ne!(first.snapshot_id, second.snapshot_id);
    assert_eq!(
        context.require_snapshot(&first.snapshot_id).unwrap_err(),
        "snapshot_expired"
    );
    assert_eq!(
        context
            .require_snapshot(&second.snapshot_id)
            .unwrap()
            .snapshot_id,
        second.snapshot_id
    );
}

#[test]
fn m2_command_and_context_boundaries_are_wired_without_local_dependencies() {
    let commands = include_str!("../src/api/commands.rs");
    let controller = include_str!("../src/app_controller.rs");
    let runtime = include_str!("../src/lib.rs");
    let checkout = include_str!("../src/checkout.rs");

    assert!(commands.contains("fn get_sot_session_state"));
    assert!(commands.contains("fn load_sot_snapshot"));
    assert!(controller.contains("sot: SotContext"));
    assert!(runtime.contains("api::commands::load_sot_snapshot"));
    let open_body = checkout
        .split("pub fn open")
        .nth(1)
        .unwrap()
        .split("pub fn root")
        .next()
        .unwrap();
    assert!(!open_body.contains("BUILD_SCRIPT_PATH"));

    let sot_root = Path::new(env!("CARGO_MANIFEST_DIR")).join("src/contexts/sot");
    for entry in walkdir::WalkDir::new(sot_root) {
        let entry = entry.unwrap();
        if entry.path().extension().and_then(|value| value.to_str()) != Some("rs") {
            continue;
        }
        let source = fs::read_to_string(entry.path()).unwrap();
        for forbidden in [
            "api::dto",
            "AppService",
            "Inventory",
            "Scanner",
            "SubprocessRunner",
            "run_build_components",
            "dirs::home_dir",
        ] {
            assert!(
                !source.contains(forbidden),
                "{} imports forbidden boundary {forbidden}",
                entry.path().display()
            );
        }
    }
}

#[test]
fn rejected_source_bytes_and_duplicate_profiles_are_revisioned_and_reported() {
    let fixture = fixture();
    let legacy = fixture
        .path()
        .join("components/skills/legacy/component.yml");
    write(&legacy, "not: [valid\n");
    write(
        &fixture.path().join("profiles/duplicate.yml"),
        r#"profile_id: harnesskit.profile.fixture-engineering
status: draft
title: Duplicate Engineering
summary: Duplicate profile identity fixture
components: []
install_policy:
  default_scope: project
  allowed_scopes: [project]
  activation_policy: manual
"#,
    );
    write(
        &fixture.path().join("profiles/invalid.yml"),
        "profile_id: harnesskit.profile.invalid\nstatus: draft\ntitle: Invalid\nsummary: Missing policy\ncomponents: []\n",
    );
    let first = load_snapshot(fixture.path());
    fs::write(&legacy, "still: [malformed\n").unwrap();
    let second = load_snapshot(fixture.path());

    assert_ne!(first.snapshot_id, second.snapshot_id);
    assert_eq!(
        first
            .profiles
            .iter()
            .filter(|profile| profile.profile_id == "harnesskit.profile.fixture-engineering")
            .count(),
        1
    );
    for code in [
        "malformed_manifest",
        "duplicate_profile_id",
        "invalid_profile_schema",
    ] {
        assert!(
            first.issues.iter().any(|issue| issue.code == code),
            "missing {code}"
        );
    }
}

#[test]
fn live_repository_snapshot_retains_planned_registry_nodes() {
    let repository = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    let snapshot = load_snapshot(repository);
    let registry: serde_yaml::Value =
        serde_yaml::from_slice(&fs::read(repository.join("components/registry.yml")).unwrap())
            .unwrap();
    let registry_ids = registry["components"]
        .as_mapping()
        .unwrap()
        .keys()
        .map(|key| key.as_str().unwrap())
        .collect::<BTreeSet<_>>();
    let component_ids = snapshot
        .components
        .iter()
        .map(|component| component.component_id.as_str())
        .collect::<BTreeSet<_>>();
    let graph_ids = graph_component_ids(&snapshot);

    assert_eq!(snapshot.components.len(), 90);
    assert_eq!(snapshot.workflows.len(), 3);
    assert_eq!(snapshot.graph_projection.nodes.len(), 100);
    assert!(component_ids.is_subset(&registry_ids));
    assert_eq!(registry_ids.len() - component_ids.len(), 3);
    assert_eq!(component_ids, graph_ids);
    assert_eq!(snapshot.profiles.len(), 6);
    assert_eq!(snapshot.unprofiled_component_ids.len(), 24);
    assert_eq!(snapshot.relations.len(), 47);
    assert!(snapshot
        .components
        .iter()
        .all(|component| !component.title.trim().is_empty()));
    assert!(Path::new(&snapshot.checkout_summary.canonical_path).is_absolute());
    assert!(
        snapshot.checkout_summary.branch.is_some()
            || snapshot.checkout_summary.detached == Some(true)
    );
    assert!(snapshot.checkout_summary.dirty.is_some());
    assert!(!snapshot.checkout_summary.recent_commits.is_empty());

    let graph = &snapshot.graph_projection;
    assert_eq!(graph.schema_version, 2);
    assert_eq!(graph.snapshot_id, snapshot.snapshot_id);
    assert!(!graph.layout_seed.is_empty());
    assert_eq!(graph_node_ids(&snapshot).len(), graph.nodes.len());
    assert_eq!(graph_link_ids(&snapshot).len(), graph.links.len());
    assert_eq!(
        graph
            .nodes
            .iter()
            .filter(|node| node.node_id().starts_with("profile:"))
            .count(),
        6
    );
    assert_eq!(
        graph
            .nodes
            .iter()
            .filter(|node| {
                let value = serde_json::to_value(node).unwrap();
                value["node_type"] == "relation" && value["relation_kind"] == "workflow"
            })
            .count(),
        3
    );
    assert_eq!(
        graph
            .nodes
            .iter()
            .filter(|node| {
                let value = serde_json::to_value(node).unwrap();
                value["node_type"] == "relation" && value["relation_kind"] == "unprofiled"
            })
            .count(),
        1
    );
    assert_eq!(
        graph
            .nodes
            .iter()
            .filter(|node| node.node_id().starts_with("component:"))
            .count(),
        90
    );

    let links = graph_link_values(&snapshot);
    let memberships = links
        .iter()
        .filter(|link| link["semantic"] == "profile-membership")
        .collect::<Vec<_>>();
    let workflow_incidence = links
        .iter()
        .filter(|link| {
            matches!(
                link["semantic"].as_str(),
                Some("workflow-step" | "invoked-by")
            )
        })
        .collect::<Vec<_>>();
    let component_cross_links = links
        .iter()
        .filter(|link| link["semantic"] == "component-cross-link")
        .collect::<Vec<_>>();
    assert_eq!(
        links.len(),
        memberships.len() + workflow_incidence.len() + component_cross_links.len()
    );
    assert_eq!(memberships.len(), 91);
    assert!(!workflow_incidence.is_empty());
    assert!(component_cross_links.len() < snapshot.relations.len());
    assert_eq!(
        memberships
            .iter()
            .filter(|link| link["provenance"] == "CanonicalProfile")
            .count(),
        67
    );
    assert_eq!(
        memberships
            .iter()
            .filter(|link| link["provenance"] == "DerivedUnprofiled")
            .count(),
        24
    );
    assert_eq!(
        memberships
            .iter()
            .map(|link| link["component_node_id"].as_str().unwrap())
            .collect::<BTreeSet<_>>()
            .len(),
        90
    );
    for node in &graph.nodes {
        let value = serde_json::to_value(node).unwrap();
        if value["node_type"] == "relation" {
            let size_scale = value["size_scale"].as_f64().unwrap();
            assert!((1.0..=1.2).contains(&size_scale));
            assert!(value["anchor_ordinal"].as_u64().unwrap() >= 1);
        }
    }
    assert!(memberships.iter().all(|link| {
        !link["profile_node_id"]
            .as_str()
            .unwrap()
            .starts_with("workflow:")
            && link["component_node_id"]
                .as_str()
                .unwrap()
                .starts_with("component:")
    }));
}

#[test]
fn live_relation_projection_matches_the_exact_approved_tuple_fixture() {
    let repository = Path::new(env!("CARGO_MANIFEST_DIR")).parent().unwrap();
    let snapshot = load_snapshot(repository);
    let actual = serde_json::to_value(&snapshot.relations).unwrap();
    let expected: serde_json::Value =
        serde_json::from_str(include_str!("fixtures/m2_live_relations.json")).unwrap();

    if actual != expected {
        panic!("{}", serde_json::to_string_pretty(&actual).unwrap());
    }
}
