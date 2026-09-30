from __future__ import annotations

import json
import re
import subprocess
import sys
from fnmatch import fnmatch
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]

PUBLIC_COMPONENT_IDS = {
    "harnesskit.agent.reference-curator",
    "harnesskit.agent.harness-requirements-analyst",
    "harnesskit.agent.harness-blueprint-author",
    "harnesskit.agent.component-author",
    "harnesskit.agent.adapter-author",
    "harnesskit.agent.harness-evaluator",
    "harnesskit.skill.harness-requirements",
    "harnesskit.skill.harness-blueprint",
    "harnesskit.skill.component-authoring",
    "harnesskit.skill.adapter-authoring",
    "harnesskit.skill.skill-evaluation",
    "harnesskit.workflow.harness-creation",
    "harnesskit.rule.harness-maintenance-project-context",
}

PUBLIC_COMPONENT_FILES = {
    "components/agents/reference-curator/agent.yml",
    "components/agents/reference-curator/prompt.md",
    "components/agents/reference-curator/provenance.map.yml",
    "components/agents/harness-evaluator/agent.yml",
    "components/agents/harness-evaluator/prompt.md",
    "components/agents/harness-evaluator/provenance.map.yml",
    "components/skills/harness-requirements/component.yml",
    "components/skills/harness-requirements/SKILL.md",
    "components/skills/harness-requirements/provenance.map.yml",
    "components/workflows/harness-creation/workflow.yml",
    "components/workflows/harness-creation/card.md",
    "components/workflows/harness-creation/provenance.map.yml",
    "components/rules/harness-maintenance-project-context/component.yml",
    "components/rules/harness-maintenance-project-context/PROJECT_CONTEXT.md",
    "components/rules/harness-maintenance-project-context/provenance.map.yml",
}

PRIVATE_OR_GENERATED_PATHS = {
    "profiles/engineering.yml",
    "profiles/minimal.yml",
    "scripts/adapters/probe_codex_runtime.py",
    "scripts/adapters/probe_harness_creation_runtime.py",
    "scripts/adapters/probe_optimal_response_behavior.py",
    "dist/codex/.codex/agents/reference_curator.toml",
    ".codex/specs/platform-authoring-ux.md",
    "docs/runtime-evidence/harnesskit.json",
    ".routine-harness/optimal-response-out/content/report.html",
    ".harnesskit/optimal-response-out/content/report.html",
    ".harnesskit/work/skill-foo/intent.md",
    ".harnesskit/private/foo",
}


def _readme_local_image_refs() -> set[str]:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    refs = set(re.findall(r'<img\s+[^>]*src="([^"]+)"', readme))
    refs.update(re.findall(r"!\[[^\]]*\]\(([^)]+)\)", readme))
    return {
        ref.split("#", 1)[0].split("?", 1)[0]
        for ref in refs
        if not re.match(r"^[a-z][a-z0-9+.-]*:", ref)
        and not ref.startswith("/")
        and ref.strip()
    }


@pytest.fixture(scope="module")
def manifest() -> dict:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/projection/manifest.py",
            "--policy",
            "publish/harnesskit.yml",
            "--repo-root",
            ".",
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


def test_manifest_uses_public_source_profile(manifest: dict):
    assert manifest["source_profile"] == {
        "id": "harnesskit.profile.harness-maintenance",
        "path": "profiles/harness-maintenance.yml",
    }


def test_manifest_records_explicit_public_workflows(manifest: dict):
    assert manifest["explicit_public_workflows"] == [
        "harnesskit.workflow.harness-creation"
    ]


def test_manifest_closure_counts_and_component_ids(manifest: dict):
    assert manifest["closure_summary"] == {
        "agents": 6,
        "rules": 1,
        "skills": 5,
        "workflows": 1,
    }

    component_ids = {
        component["id"]
        for components in manifest["components"].values()
        for component in components
    }
    assert component_ids == PUBLIC_COMPONENT_IDS


def test_manifest_includes_profile_and_public_component_files(manifest: dict):
    included_paths = set(manifest["included_paths"])

    assert "profiles/harness-maintenance.yml" in included_paths
    assert "components/registry.yml" in included_paths
    assert PUBLIC_COMPONENT_FILES.issubset(included_paths)


def test_manifest_includes_readme_local_image_assets(manifest: dict):
    included_paths = set(manifest["included_paths"])
    local_assets = _readme_local_image_refs()

    assert local_assets
    assert local_assets.issubset(included_paths)


def _public_source_refs_from_file(rel_path: str) -> list[tuple[str, str | None]]:
    data = yaml.safe_load((REPO_ROOT / rel_path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return []

    refs: list[tuple[str, str | None]] = []
    for ref in data.get("source_refs") or []:
        if isinstance(ref, dict) and isinstance(ref.get("source_id"), str):
            refs.append((ref["source_id"], ref.get("path")))

    provenance = data.get("provenance")
    if isinstance(provenance, dict):
        for ref in provenance.get("sources") or []:
            if not isinstance(ref, dict) or not isinstance(ref.get("source_id"), str):
                continue
            raw_path = ref.get("path") or ref.get("source_path")
            refs.append((ref["source_id"], raw_path))

    return refs


def _repo_source_path(
    source_id: str,
    raw_path: str | None,
    source_registry: dict,
) -> str | None:
    if not raw_path:
        return None
    source_meta = source_registry.get(source_id)
    if isinstance(source_meta, dict) and source_meta.get("kind") != "repository_local":
        return None
    if "://" in raw_path or raw_path.startswith("user prompt"):
        return None
    return raw_path.split(" (", 1)[0]


def test_manifest_includes_public_source_refs_and_registry(manifest: dict):
    included_paths = set(manifest["included_paths"])

    assert "sources/registry.yml" in included_paths
    source_registry = yaml.safe_load((REPO_ROOT / "sources/registry.yml").read_text(encoding="utf-8"))
    registered_sources = source_registry.get("sources") or {}
    registered_source_ids = set(registered_sources)

    public_source_refs: list[tuple[str, str | None]] = []
    for rel_path in sorted(included_paths):
        # Components are stored flat under components/<kind>/<name>/; gather the
        # per-component manifest/provenance YAMLs, skipping the registry itself.
        if (
            rel_path.startswith("components/")
            and rel_path != "components/registry.yml"
            and rel_path.endswith((".yml", ".yaml"))
        ):
            public_source_refs.extend(_public_source_refs_from_file(rel_path))

    missing_ids = sorted(
        source_id
        for source_id, _path in public_source_refs
        if source_id not in registered_source_ids
    )
    missing_paths = sorted(
        path
        for source_id, raw_path in public_source_refs
        for path in [_repo_source_path(source_id, raw_path, registered_sources)]
        if path is not None and path not in included_paths
    )

    assert not missing_ids
    assert not missing_paths


def test_public_blueprint_required_references_are_projected(manifest: dict):
    included_paths = set(manifest["included_paths"])
    blueprint = (REPO_ROOT / "sources/blueprint.md").read_text(encoding="utf-8")
    section = blueprint.split("Approved requirement anchor:", 1)[0]
    required_refs = {
        match
        for match in re.findall(r"- `([^`]+)`", section)
        if "/" in match or match.endswith(".yml")
    }

    assert required_refs
    assert required_refs.issubset(included_paths)


def test_manifest_includes_only_required_adapter_support_files(manifest: dict):
    included_paths = set(manifest["included_paths"])

    assert "scripts/adapters/__init__.py" in included_paths
    assert "scripts/adapters/api.py" in included_paths
    assert "scripts/adapters/build.py" in included_paths
    assert "scripts/adapters/hermes.py" in included_paths
    assert "scripts/profiles/__init__.py" in included_paths
    assert "scripts/profiles/selection.py" in included_paths
    assert not any(
        path.startswith("scripts/adapters/probe_") for path in included_paths
    )


def test_manifest_paths_do_not_match_exclusions(manifest: dict):
    included_paths = manifest["included_paths"]
    excluded_patterns = manifest["excluded_patterns"]

    assert not [
        path
        for path in included_paths
        if any(fnmatch(path, pattern) for pattern in excluded_patterns)
    ]


def test_manifest_has_projection_identity_and_revision(manifest: dict):
    assert manifest["projection_id"]
    assert manifest["source_revision"]


def test_manifest_excludes_private_profiles_and_generated_artifacts(manifest: dict):
    included_paths = set(manifest["included_paths"])

    assert PRIVATE_OR_GENERATED_PATHS.isdisjoint(included_paths)
    assert "profiles/engineering.yml" in manifest["excluded_patterns"]
    assert "profiles/minimal.yml" in manifest["excluded_patterns"]
    assert "dist/**" in manifest["excluded_patterns"]
    assert ".codex/specs/**" in manifest["excluded_patterns"]
    assert ".harnesskit/work/**" in manifest["excluded_patterns"]
    assert ".harnesskit/private/**" in manifest["excluded_patterns"]


def test_cli_rejects_malicious_owned_file_without_traceback(tmp_path: Path):
    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "components" / "bad").mkdir(parents=True)

    (repo_root / "profiles" / "public.yml").write_text(
        "\n".join(
            [
                "profile_id: test.profile.public",
                "components:",
                "  - test.component.bad",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "components" / "registry.yml").write_text(
        "\n".join(
            [
                'version: "1"',
                "components:",
                "  test.component.bad:",
                "    kind: skill",
                "    path: components/bad/component.yml",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "components" / "bad" / "component.yml").write_text(
        "\n".join(
            [
                "component_id: test.component.bad",
                "kind: skill",
                "owned_files:",
                "  - ../secret.txt",
            ]
        ),
        encoding="utf-8",
    )
    policy_path = tmp_path / "policy.yml"
    policy_path.write_text(
        "\n".join(
            [
                "projection_id: test.projection",
                "source_profile: test.profile.public",
                "source_profile_path: profiles/public.yml",
                "registry_path: components/registry.yml",
                "support_paths: []",
                "excluded_patterns: []",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/projection/manifest.py",
            "--policy",
            str(policy_path),
            "--repo-root",
            str(repo_root),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "error:" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_rejects_public_owned_file_matching_private_blocklist_without_traceback(
    tmp_path: Path,
):
    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "components" / "public").mkdir(parents=True)
    (repo_root / "docs" / "runtime-evidence").mkdir(parents=True)

    (repo_root / "profiles" / "public.yml").write_text(
        "\n".join(
            [
                "profile_id: harnesskit.profile.public",
                "components:",
                "  - harnesskit.skill.public",
                "install_policy:",
                "  allowed_scopes:",
                "    - project",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "components" / "registry.yml").write_text(
        "\n".join(
            [
                'version: "1"',
                "components:",
                "  harnesskit.skill.public:",
                "    kind: skill",
                "    path: components/public/component.yml",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "components" / "public" / "component.yml").write_text(
        "\n".join(
            [
                "component_id: harnesskit.skill.public",
                "kind: skill",
                "owned_files:",
                "  - docs/runtime-evidence/private.json",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "docs" / "runtime-evidence" / "private.json").write_text(
        "{}\n",
        encoding="utf-8",
    )
    policy_path = tmp_path / "policy.yml"
    policy_path.write_text(
        "\n".join(
            [
                "projection_id: harnesskit.projection.public",
                "source_profile: harnesskit.profile.public",
                "source_profile_path: profiles/public.yml",
                "registry_path: components/registry.yml",
                "support_paths: []",
                "excluded_patterns:",
                "  - docs/runtime-evidence/**",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/projection/manifest.py",
            "--policy",
            str(policy_path),
            "--repo-root",
            str(repo_root),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "error:" in result.stderr
    assert "private blocklist" in result.stderr
    assert "docs/runtime-evidence/private.json" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_rejects_registered_private_dependency_without_traceback(tmp_path: Path):
    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "components" / "public").mkdir(parents=True)
    (repo_root / "components" / "private").mkdir(parents=True)

    (repo_root / "profiles" / "public.yml").write_text(
        "\n".join(
            [
                "profile_id: harnesskit.profile.public",
                "components:",
                "  - harnesskit.skill.public",
                "install_policy:",
                "  allowed_scopes:",
                "    - project",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "components" / "registry.yml").write_text(
        "\n".join(
            [
                'version: "1"',
                "components:",
                "  harnesskit.skill.public:",
                "    kind: skill",
                "    path: components/public/component.yml",
                "  harnesskit.skill.private-only:",
                "    kind: skill",
                "    path: components/private/component.yml",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "components" / "public" / "component.yml").write_text(
        "\n".join(
            [
                "component_id: harnesskit.skill.public",
                "kind: skill",
                "requires:",
                "  - harnesskit.skill.private-only",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "components" / "private" / "component.yml").write_text(
        "\n".join(
            [
                "component_id: harnesskit.skill.private-only",
                "kind: skill",
            ]
        ),
        encoding="utf-8",
    )
    policy_path = tmp_path / "policy.yml"
    policy_path.write_text(
        "\n".join(
            [
                "projection_id: harnesskit.projection.public",
                "source_profile: harnesskit.profile.public",
                "source_profile_path: profiles/public.yml",
                "registry_path: components/registry.yml",
                "support_paths: []",
                "excluded_patterns: []",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/projection/manifest.py",
            "--policy",
            str(policy_path),
            "--repo-root",
            str(repo_root),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "error:" in result.stderr
    assert "private/out-of-closure dependency" in result.stderr
    assert "harnesskit.skill.private-only" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_rejects_private_registry_path_before_dependency_scan(tmp_path: Path):
    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "components" / "private").mkdir(parents=True)

    (repo_root / "profiles" / "public.yml").write_text(
        "\n".join(
            [
                "profile_id: harnesskit.profile.public",
                "components:",
                "  - harnesskit.skill.public",
                "install_policy:",
                "  allowed_scopes:",
                "    - project",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "components" / "registry.yml").write_text(
        "\n".join(
            [
                'version: "1"',
                "components:",
                "  harnesskit.skill.public:",
                "    kind: skill",
                "    path: components/private/component.yml",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "components" / "private" / "component.yml").write_text(
        "\n".join(
            [
                "component_id: harnesskit.skill.public",
                "kind: skill",
                "requires:",
                "  - harnesskit.skill.missing-private",
            ]
        ),
        encoding="utf-8",
    )
    policy_path = tmp_path / "policy.yml"
    policy_path.write_text(
        "\n".join(
            [
                "projection_id: harnesskit.projection.public",
                "source_profile: harnesskit.profile.public",
                "source_profile_path: profiles/public.yml",
                "registry_path: components/registry.yml",
                "support_paths: []",
                "excluded_patterns:",
                "  - components/private/**",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/projection/manifest.py",
            "--policy",
            str(policy_path),
            "--repo-root",
            str(repo_root),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "error:" in result.stderr
    assert "private blocklist" in result.stderr
    assert "components/private/component.yml" in result.stderr
    assert "unregistered dependency" not in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_rejects_unregistered_dependency_without_traceback(tmp_path: Path):
    repo_root = tmp_path / "repo"
    (repo_root / "profiles").mkdir(parents=True)
    (repo_root / "components" / "public").mkdir(parents=True)

    (repo_root / "profiles" / "public.yml").write_text(
        "\n".join(
            [
                "profile_id: harnesskit.profile.public",
                "components:",
                "  - harnesskit.skill.public",
                "install_policy:",
                "  allowed_scopes:",
                "    - project",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "components" / "registry.yml").write_text(
        "\n".join(
            [
                'version: "1"',
                "components:",
                "  harnesskit.skill.public:",
                "    kind: skill",
                "    path: components/public/component.yml",
            ]
        ),
        encoding="utf-8",
    )
    (repo_root / "components" / "public" / "component.yml").write_text(
        "\n".join(
            [
                "component_id: harnesskit.skill.public",
                "kind: skill",
                "requires:",
                "  - harnesskit.skill.missing-private",
            ]
        ),
        encoding="utf-8",
    )
    policy_path = tmp_path / "policy.yml"
    policy_path.write_text(
        "\n".join(
            [
                "projection_id: harnesskit.projection.public",
                "source_profile: harnesskit.profile.public",
                "source_profile_path: profiles/public.yml",
                "registry_path: components/registry.yml",
                "support_paths: []",
                "excluded_patterns: []",
            ]
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/projection/manifest.py",
            "--policy",
            str(policy_path),
            "--repo-root",
            str(repo_root),
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "error:" in result.stderr
    assert "unregistered dependency" in result.stderr
    assert "harnesskit.skill.missing-private" in result.stderr
    assert "Traceback" not in result.stderr
