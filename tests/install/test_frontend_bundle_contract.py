from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath

import pytest


ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = ROOT / "src-frontend"
DIST_ROOT = ROOT / "src-tauri" / "target" / "frontend-dist"
PACKAGE_JSON = FRONTEND_ROOT / "package.json"
PACKAGE_LOCK = FRONTEND_ROOT / "package-lock.json"
BUNDLE_MANIFEST = DIST_ROOT / "bundle-manifest.json"

EXPECTED_DEPENDENCIES = {
    "@pureliture/graph-workbench": (
        "git+https://github.com/pureliture/graph-workbench.git#8f24852701282828c90eb20c54a298a3f8db4bbb"
    ),
    "3d-force-graph": "1.80.0",
    "three": "0.185.0",
}
EXPECTED_DEV_DEPENDENCIES = {"esbuild": "0.28.1"}
EXPECTED_LOCK_VERSIONS = {
    "@pureliture/graph-workbench": "0.1.0",
    "3d-force-graph": "1.80.0",
    "three": "0.185.0",
    "esbuild": "0.28.1",
}
EXPECTED_ENTRYPOINT = "appearance/bootstrap.js"
EXPECTED_LICENSES = {
    "@pureliture/graph-workbench": ("0.1.0", "MIT", "runtime"),
    "3d-force-graph": ("1.80.0", "MIT", "runtime"),
    "three": ("0.185.0", "MIT", "runtime"),
    "esbuild": ("0.28.1", "MIT", "build"),
}


@pytest.fixture(scope="module", autouse=True)
def _ensure_frontend_bundle() -> None:
    """Build the ignored frontend artifact when a clean checkout lacks it."""
    if BUNDLE_MANIFEST.is_file():
        return

    npm = shutil.which("npm")
    assert npm is not None, "npm is required to build the frontend contract artifact"
    node_modules = FRONTEND_ROOT / "node_modules"
    if not node_modules.is_dir():
        install = subprocess.run(
            [npm, "ci", "--ignore-scripts", "--prefer-offline"],
            cwd=FRONTEND_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert install.returncode == 0, install.stdout + install.stderr

    environment = os.environ.copy()
    environment["HARNESS_FRONTEND_DIST"] = str(DIST_ROOT)
    build = subprocess.run(
        [npm, "run", "build"],
        cwd=FRONTEND_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    assert BUNDLE_MANIFEST.is_file()


def _lock_integrity(resolution: dict) -> str:
    integrity = resolution.get("integrity")
    if isinstance(integrity, str) and integrity.startswith("sha512-"):
        return integrity

    return f"git-commit:{_lock_git_commit(resolution)}"


def _lock_git_commit(resolution: dict) -> str:
    resolved = resolution.get("resolved")
    match = re.fullmatch(
        r"git\+(?:https://github\.com/|ssh://git@github\.com/)"
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git#([0-9a-f]{40})",
        resolved or "",
        flags=re.IGNORECASE,
    )
    assert match, "git dependency must resolve to a full immutable GitHub commit"
    return match.group(1).lower()


def _load_json(path: Path) -> dict:
    assert path.is_file(), f"required generated/source contract is missing: {path}"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict), f"JSON root must be an object: {path}"
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _regular_files(root: Path) -> set[str]:
    assert root.is_dir(), f"generated frontend dist is missing: {root}"
    return {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }


def _lock_inventory(lock: dict) -> list[dict[str, str]]:
    inventory = []
    for package_path, resolution in lock["packages"].items():
        if not package_path:
            continue
        parts = PurePosixPath(package_path).parts
        last_node_modules = max(
            index for index, part in enumerate(parts) if part == "node_modules"
        )
        leaf = parts[last_node_modules + 1 :]
        name = "/".join(leaf) if leaf[0].startswith("@") else leaf[0]
        inventory.append(
            {
                "integrity": _lock_integrity(resolution),
                "license": resolution["license"],
                "name": name,
                "path": package_path,
                "usage": "build" if resolution.get("dev") is True else "runtime",
                "version": resolution["version"],
            }
        )
    return sorted(
        inventory,
        key=lambda item: (item["name"], item["version"], item["path"]),
    )


def _safe_relative_path(value: object, *, field: str) -> str:
    assert isinstance(value, str) and value, f"{field} must be a non-empty string"
    parsed = PurePosixPath(value)
    assert not parsed.is_absolute(), f"{field} must stay inside the same-origin dist"
    assert ".." not in parsed.parts, f"{field} must not escape the same-origin dist"
    assert not re.match(r"^[a-z][a-z0-9+.-]*:", value, re.IGNORECASE), (
        f"{field} must not use a URI scheme"
    )
    return value


def test_frontend_dependency_authority_is_exact_and_lockfile_backed() -> None:
    package = _load_json(PACKAGE_JSON)
    lock = _load_json(PACKAGE_LOCK)

    assert package.get("private") is True
    assert package.get("type") == "module"
    assert package.get("dependencies") == EXPECTED_DEPENDENCIES
    assert package.get("devDependencies") == EXPECTED_DEV_DEPENDENCIES
    build_command = package.get("scripts", {}).get("build")
    assert isinstance(build_command, str) and "build.mjs" in build_command
    assert "npx" not in build_command

    assert lock.get("lockfileVersion") == 3
    root_package = lock.get("packages", {}).get("")
    assert isinstance(root_package, dict)
    assert root_package.get("dependencies") == EXPECTED_DEPENDENCIES
    assert root_package.get("devDependencies") == EXPECTED_DEV_DEPENDENCIES
    for name, version in EXPECTED_LOCK_VERSIONS.items():
        resolution = lock.get("packages", {}).get(f"node_modules/{name}")
        assert isinstance(resolution, dict), f"lockfile is missing {name}"
        assert resolution.get("version") == version
    graph_workbench = lock["packages"]["node_modules/@pureliture/graph-workbench"]
    assert _lock_git_commit(graph_workbench) == "8f24852701282828c90eb20c54a298a3f8db4bbb"


def test_generated_frontend_is_one_verified_offline_same_origin_bundle() -> None:
    manifest = _load_json(BUNDLE_MANIFEST)
    lock = _load_json(PACKAGE_LOCK)
    files = _regular_files(DIST_ROOT)

    assert manifest.get("schema_version") == 1
    assert manifest.get("entrypoint") == EXPECTED_ENTRYPOINT

    lockfile = manifest.get("lockfile")
    assert isinstance(lockfile, dict)
    assert lockfile.get("path") == "package-lock.json"
    assert lockfile.get("sha256") == _sha256(PACKAGE_LOCK)

    output = manifest.get("output")
    assert isinstance(output, dict)
    output_path = _safe_relative_path(output.get("path"), field="output.path")
    output_file = DIST_ROOT / output_path
    assert output_file.is_file()
    assert output_file.suffix == ".js"
    assert output.get("sha256") == _sha256(output_file)

    javascript_files = sorted(path for path in files if path.endswith(".js"))
    assert javascript_files == [output_path]
    assert not any(path.endswith(".map") for path in files)
    assert not any(
        "node_modules" in PurePosixPath(path).parts
        or "esbuild" in path.lower()
        or any(part.startswith("@esbuild") for part in PurePosixPath(path).parts)
        for path in files
    ), "build-only esbuild/platform packages must not enter runtime inventory"

    index = (DIST_ROOT / "index.html").read_text(encoding="utf-8")
    script_sources = re.findall(
        r"<script\b[^>]*\bsrc=[\"']([^\"']+)", index, re.IGNORECASE
    )
    assert script_sources == [f"./{output_path}"]
    assert not re.search(
        r"<(?:script|link|img)\b[^>]*(?:src|href)=[\"'](?:https?:|//)",
        index,
        re.IGNORECASE,
    )

    bundle = output_file.read_text(encoding="utf-8")
    forbidden_runtime_code = {
        "remote static import": r"\bfrom\s*[\"']https?://",
        "remote dynamic import": r"\bimport\s*\(\s*[\"']https?://",
        "remote fetch/socket": (
            r"\b(?:fetch|WebSocket|EventSource)\s*\(\s*[\"'](?:https?:|file:)"
        ),
        "eval": r"(?:^|[^.$\w])eval\s*\(",
        "new Function": r"\bnew\s+Function\s*\(",
        "worker": r"\b(?:new\s+)?(?:SharedWorker|Worker)\s*\(",
        "importScripts": r"\bimportScripts\s*\(",
        "source map directive": r"sourceMappingURL\s*=",
        "absolute local asset": r"(?:file://|/Users/|/private/|/home/|[A-Za-z]:\\\\)",
        "runtime evidence bridge": r"__HARNESS_RUNTIME_EVIDENCE__",
        "runtime evidence protocol": r"harnesskit-runtime-graph-v1",
        "test-only evidence API": r"(?:runtimeEvidence|ForEvidence)",
    }
    for label, pattern in forbidden_runtime_code.items():
        assert not re.search(pattern, bundle), f"production bundle contains {label}"

    licenses = manifest.get("licenses")
    assert isinstance(licenses, list) and licenses
    assert licenses == _lock_inventory(lock)
    license_inventory = _load_json(DIST_ROOT / "license-inventory.json")
    assert license_inventory == {"schema_version": 1, "packages": licenses}
    actual_licenses = {
        item["name"]: (item["version"], item["license"], item["usage"])
        for item in licenses
    }
    for name, expected in EXPECTED_LICENSES.items():
        assert actual_licenses.get(name) == expected
    assert all(
        usage != "runtime" or (name != "esbuild" and not name.startswith("@esbuild/"))
        for name, (_version, _license, usage) in actual_licenses.items()
    )


def test_package_authorities_verify_bundle_manifest_instead_of_raw_module_allowlists() -> None:
    authorities = {
        "src-tauri/build.rs": ROOT / "src-tauri" / "build.rs",
        "scripts/package/build_verified_macos_app.py": (
            ROOT / "scripts" / "package" / "build_verified_macos_app.py"
        ),
    }

    for label, path in authorities.items():
        source = path.read_text(encoding="utf-8")
        assert "FRONTEND_FILES" not in source, (
            f"{label} must not retain the raw JavaScript copy allowlist"
        )
        for required in [
            "src-frontend/package.json",
            "src-frontend/package-lock.json",
            "src-frontend/build.mjs",
            "bundle-manifest.json",
        ]:
            assert required in source, f"{label} must bind {required} into package identity"
