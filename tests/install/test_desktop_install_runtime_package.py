from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from scripts.install.plan import build_artifact_projection, build_plan
from scripts.package import prepare_install_runtime


ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = ROOT / "src-tauri/install-runtime.lock.json"
RUNTIME_ROOT = ROOT / "src-tauri/target/install-runtime/aarch64-apple-darwin"


@pytest.fixture(scope="module", autouse=True)
def _ensure_prepared_runtime() -> None:
    """Materialize the pinned runtime before tests inspect its manifest."""
    assert prepare_install_runtime._prepare(ROOT) == RUNTIME_ROOT


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entrypoint_command(workspace: Path, mode: str, expected: dict) -> list[str]:
    command = [
        str(RUNTIME_ROOT / "python/bin/python3"),
        "-I",
        "-B",
        "-S",
        str(RUNTIME_ROOT / "install_entry.py"),
        "--script-id",
        "install-plan",
        "--workspace",
        str(workspace),
        "--mode",
        mode,
        "--profile",
        "engineering",
        "--scope",
        "project",
    ]
    for target in expected["targets"]:
        command.extend(["--target-id", target])
    for component in expected["components"]:
        command.extend(["--component-id", component])
    return command


def _run_entrypoint(workspace: Path, mode: str, expected: dict) -> dict:
    result = subprocess.run(
        _entrypoint_command(workspace, mode, expected),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            "LANG": "C",
            "LC_ALL": "C",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        },
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return json.loads(result.stdout)


def _run_artifact_projection_entrypoint(
    workspace: Path, component_ids: list[str]
) -> dict:
    command = [
        str(RUNTIME_ROOT / "python/bin/python3"),
        "-I",
        "-B",
        "-S",
        str(RUNTIME_ROOT / "install_entry.py"),
        "--script-id",
        "artifact-projection",
        "--workspace",
        str(workspace),
    ]
    for component_id in component_ids:
        command.extend(["--component-id", component_id])
    result = subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={
            "LANG": "C",
            "LC_ALL": "C",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
        },
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    return json.loads(result.stdout)


def test_prepared_runtime_matches_lock_and_file_manifest() -> None:
    lock_bytes = LOCK_PATH.read_bytes()
    lock = json.loads(lock_bytes)
    manifest = json.loads((RUNTIME_ROOT / "runtime-manifest.json").read_text())

    assert manifest["runtime_id"] == lock["runtime_id"]
    assert manifest["target"] == lock["target"]
    assert manifest["lock_sha256"] == hashlib.sha256(lock_bytes).hexdigest()
    expected = {entry["path"]: entry for entry in manifest["entries"]}
    actual = {
        path.relative_to(RUNTIME_ROOT).as_posix(): path
        for path in RUNTIME_ROOT.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    assert set(actual) == set(expected) | {"runtime-manifest.json"}
    for relative, entry in expected.items():
        path = actual[relative]
        assert _sha256(path) == entry["sha256"]
        assert path.stat().st_size == entry["size"]
        assert path.stat().st_mode & 0o777 == entry["mode"]


@pytest.mark.parametrize(
    ("source_mode", "bundled_mode"),
    [(0o775, 0o755), (0o664, 0o644), (0o700, 0o700)],
)
def test_runtime_source_mode_is_stable_under_dmg_bundling(
    tmp_path: Path, source_mode: int, bundled_mode: int
) -> None:
    destination = tmp_path / "python/bin/python3"
    prepare_install_runtime._copy_stream(io.BytesIO(b"runtime"), destination, source_mode)

    assert destination.read_bytes() == b"runtime"
    assert stat.S_IMODE(destination.stat().st_mode) == bundled_mode


def test_prepared_runtime_manifest_permissions_survive_dmg_bundling() -> None:
    manifest = json.loads((RUNTIME_ROOT / "runtime-manifest.json").read_text())
    assert all(entry["mode"] & 0o022 == 0 for entry in manifest["entries"])


def test_package_prepare_reuses_only_a_fully_verified_pinned_runtime(monkeypatch) -> None:
    def unexpected_download(*_args, **_kwargs):
        raise AssertionError("verified runtime must not be downloaded again")

    monkeypatch.setattr(prepare_install_runtime, "_download", unexpected_download)

    assert prepare_install_runtime._prepare(ROOT) == RUNTIME_ROOT


def test_bundled_python_imports_only_locked_vendor_and_runs_dry_plan_entrypoint(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    for relative in [
        "components",
        "profiles",
        "adapters",
        "schemas",
        "scripts/adapters",
        "scripts/install",
        "scripts/profiles",
    ]:
        source = ROOT / relative
        if source.exists():
            shutil.copytree(source, workspace / relative)
    expected = build_plan("engineering", scope="project", mode="dry-run")
    plan = _run_entrypoint(workspace, "dry-run", expected)
    assert plan["install_contract_id"] == "harnesskit.install-target-contract.v1"
    assert plan["mode"] == "dry-run"
    assert set(plan["targets"]) == set(expected["targets"])
    assert set(plan["components"]) == set(expected["components"])
    assert not (workspace / "dist").exists()


def test_apply_entrypoint_materializes_only_dist_with_source_evidence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    for relative in [
        "components",
        "profiles",
        "adapters",
        "schemas",
        "scripts/adapters",
        "scripts/install",
        "scripts/profiles",
    ]:
        source = ROOT / relative
        if source.exists():
            shutil.copytree(source, workspace / relative)
    before = {
        path.relative_to(workspace).as_posix(): _sha256(path)
        for path in workspace.rglob("*")
        if path.is_file()
    }
    expected = build_plan("engineering", scope="project", mode="dry-run")

    plan = _run_entrypoint(workspace, "apply", expected)

    after_inputs = {
        path.relative_to(workspace).as_posix(): _sha256(path)
        for path in workspace.rglob("*")
        if path.is_file() and path.relative_to(workspace).parts[0] != "dist"
    }
    assert after_inputs == before
    assert plan["mode"] == "apply"
    assert plan["artifacts"]
    for artifact in plan["artifacts"]:
        assert len(artifact["source_sha256"]) == 64
        assert 0 <= artifact["mode"] <= 0o777
        source = workspace / artifact["source"]
        assert source.is_file()
        assert _sha256(source) == artifact["source_sha256"]


def test_artifact_projection_entrypoint_is_profile_free_exact_and_read_only(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(mode=0o700)
    for relative in [
        "components",
        "profiles",
        "adapters",
        "schemas",
        "scripts/adapters",
        "scripts/install",
        "scripts/profiles",
    ]:
        source = ROOT / relative
        if source.exists():
            shutil.copytree(source, workspace / relative)
    before = {
        path.relative_to(workspace).as_posix(): _sha256(path)
        for path in workspace.rglob("*")
        if path.is_file()
    }
    component_ids = ["harnesskit.agent.adapter-author"]

    projection = _run_artifact_projection_entrypoint(workspace, component_ids)

    after = {
        path.relative_to(workspace).as_posix(): _sha256(path)
        for path in workspace.rglob("*")
        if path.is_file()
    }
    expected = build_artifact_projection(component_ids)
    assert after == before
    assert projection["adapter_set_revision"] == expected["adapter_set_revision"]
    assert projection["records"] == expected["records"]
    assert projection["issues"] == []
    assert not (workspace / "dist").exists()
