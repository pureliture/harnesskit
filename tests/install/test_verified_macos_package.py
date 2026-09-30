from __future__ import annotations

import base64
import hashlib
import json
import os
import plistlib
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from scripts.package import build_verified_macos_app as package


BUILD_ID = "a" * 32
FRONTEND_SOURCE_CONTRACT_FILES = (
    "src-frontend/package.json",
    "src-frontend/package-lock.json",
    "src-frontend/build.mjs",
)
TOOL_IDENTITY_ASSET_FILES = (
    "src-frontend/assets/tool-identities/codex.png",
    "src-frontend/assets/tool-identities/claude.png",
    "src-frontend/assets/tool-identities/antigravity.png",
    "src-frontend/assets/tool-identities/hermesagent.png",
)
TOOL_IDENTITY_MANIFEST = "assets/tool-identities/manifest.json"
BRAND_PACKAGING_FILES = (
    "assets/branding/harness-desktop/BRAND_ASSET_POLICY.md",
    "assets/branding/harness-desktop/source/harness-desktop-cabinet.png",
    "assets/branding/harness-desktop/derivation-manifest.json",
    "assets/packaging/macos/dmg-background.png",
    "assets/packaging/macos/dmg-background.svg",
    "assets/packaging/macos/dmg-layout-manifest.json",
)
ICON_FILES = (
    "32x32.png",
    "128x128.png",
    "128x128" "@2x.png",
    "icon.icns",
    "icon.ico",
)
GIT_GRAPH_WORKBENCH_PIN = (
    "git+https://github.com/pureliture/graph-workbench.git#"
    "8f24852701282828c90eb20c54a298a3f8db4bbb"
)


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _sri(label: str) -> str:
    digest = hashlib.sha512(label.encode()).digest()
    return "sha512-" + base64.b64encode(digest).decode()


def _binary_marker(build_inputs: bytes) -> bytes:
    return (
        f"HARNESS_PACKAGE_BUILD_ID={BUILD_ID}\n"
        f"HARNESS_PACKAGE_BUILD_INPUTS_SHA256={_sha256_bytes(build_inputs)}\n"
    ).encode()


def _write(path: Path, body: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    path.chmod(mode)


def _runtime_manifest(runtime: Path) -> bytes:
    entries = []
    for path in sorted(runtime.rglob("*"), key=lambda item: item.relative_to(runtime).as_posix()):
        if path.is_file() and not path.is_symlink():
            metadata = path.stat()
            entries.append(
                {
                    "path": path.relative_to(runtime).as_posix(),
                    "sha256": _sha256_bytes(path.read_bytes()),
                    "size": metadata.st_size,
                    "mode": metadata.st_mode & 0o777,
                }
            )
    return (
        json.dumps(
            {
                "schema_version": 1,
                "runtime_id": "fixture-runtime",
                "target": "aarch64-apple-darwin",
                "lock_sha256": "b" * 64,
                "entries": entries,
            },
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode()


def _write_frontend_bundle(repo: Path) -> None:
    package_json = {
        "name": "harnesskit-desktop-frontend",
        "private": True,
        "type": "module",
        "scripts": {"build": "node build.mjs"},
        "dependencies": {"3d-force-graph": "1.80.0", "three": "0.185.0"},
        "devDependencies": {"esbuild": "0.28.1"},
    }
    package_lock = {
        "name": "harnesskit-desktop-frontend",
        "lockfileVersion": 3,
        "packages": {
            "": {
                "dependencies": package_json["dependencies"],
                "devDependencies": package_json["devDependencies"],
            },
            "node_modules/3d-force-graph": {
                "integrity": _sri("3d-force-graph@1.80.0"),
                "license": "MIT",
                "version": "1.80.0",
            },
            "node_modules/esbuild": {
                "dev": True,
                "integrity": _sri("esbuild@0.28.1"),
                "license": "MIT",
                "version": "0.28.1",
            },
            "node_modules/three": {
                "integrity": _sri("three@0.185.0"),
                "license": "MIT",
                "version": "0.185.0",
            },
        },
    }
    _write(
        repo / "src-frontend/package.json",
        (json.dumps(package_json, sort_keys=True) + "\n").encode(),
    )
    lock_body = (json.dumps(package_lock, sort_keys=True) + "\n").encode()
    _write(repo / "src-frontend/package-lock.json", lock_body)
    _write(repo / "src-frontend/build.mjs", b"// fixture bundle builder\n")

    dist = repo / "src-tauri/target/frontend-dist"
    output_body = b"console.log('fixture bundle');\n"
    license_inventory = {
        "schema_version": 1,
        "packages": [
            {
                "integrity": _sri("3d-force-graph@1.80.0"),
                "license": "MIT",
                "name": "3d-force-graph",
                "path": "node_modules/3d-force-graph",
                "usage": "runtime",
                "version": "1.80.0",
            },
            {
                "integrity": _sri("esbuild@0.28.1"),
                "license": "MIT",
                "name": "esbuild",
                "path": "node_modules/esbuild",
                "usage": "build",
                "version": "0.28.1",
            },
            {
                "integrity": _sri("three@0.185.0"),
                "license": "MIT",
                "name": "three",
                "path": "node_modules/three",
                "usage": "runtime",
                "version": "0.185.0",
            },
        ],
    }
    license_body = (json.dumps(license_inventory, sort_keys=True) + "\n").encode()
    _write(dist / "assets/app.bundle.js", output_body)
    _write(dist / "license-inventory.json", license_body)
    index_body = b'<script type="module" src="./assets/app.bundle.js"></script>\n'
    styles_body = b"body {}\n"
    _write(dist / "index.html", index_body)
    _write(dist / "styles.css", styles_body)
    files = [
        {"path": "assets/app.bundle.js", "sha256": _sha256_bytes(output_body)},
        {"path": "index.html", "sha256": _sha256_bytes(index_body)},
        {
            "path": "license-inventory.json",
            "sha256": _sha256_bytes(license_body),
        },
        {"path": "styles.css", "sha256": _sha256_bytes(styles_body)},
    ]
    _write(
        dist / "bundle-manifest.json",
        (
            json.dumps(
                {
                    "entrypoint": "appearance/bootstrap.js",
                    "files": files,
                    "license_inventory": {
                        "path": "license-inventory.json",
                        "sha256": _sha256_bytes(license_body),
                    },
                    "licenses": license_inventory["packages"],
                    "lockfile": {
                        "path": "package-lock.json",
                        "sha256": _sha256_bytes(lock_body),
                    },
                    "output": {
                        "path": "assets/app.bundle.js",
                        "sha256": _sha256_bytes(output_body),
                    },
                    "schema_version": 1,
                    "source_map": False,
                },
                sort_keys=True,
            )
            + "\n"
        ).encode(),
    )


def _fixture_repo(tmp_path: Path) -> tuple[Path, Path, Path]:
    repo = tmp_path / "repo"
    src_tauri = repo / "src-tauri"
    _write(
        src_tauri / "Cargo.toml",
        b'[package]\nname = "harness-desktop"\nversion = "0.1.0"\n',
    )
    _write(src_tauri / "Cargo.lock", b"# fixture lock\n")
    _write(src_tauri / "build.rs", b"fn main() {}\n")
    _write(src_tauri / "build_support/release_manifest.rs", b"// fixture\n")
    _write(src_tauri / "src/lib.rs", b"pub fn fixture() {}\n")
    _write(src_tauri / "capabilities/default.json", b"{}\n")
    for relative in ICON_FILES:
        _write(src_tauri / "icons" / relative, f"fixture:{relative}\n".encode())
    _write_frontend_bundle(repo)
    for relative in TOOL_IDENTITY_ASSET_FILES:
        _write(repo / relative, f"fixture:{relative}\n".encode())
    for relative in BRAND_PACKAGING_FILES:
        _write(repo / relative, f"fixture:{relative}\n".encode())
    _write(
        repo / TOOL_IDENTITY_MANIFEST,
        b'{"schema_version":1,"assets":[]}\n',
    )
    _write(
        src_tauri / "tauri.conf.json",
        json.dumps(
            {
                "productName": "HarnessKit",
                "mainBinaryName": "harness-desktop",
                "version": "0.1.0",
                "identifier": "io.github.pureliture.harnesskit",
                "app": {
                    "windows": [
                        {
                            "label": "main",
                            "title": "HarnessKit",
                        }
                    ]
                },
            },
            sort_keys=True,
        ).encode(),
    )
    prepared = src_tauri / "target/install-runtime/aarch64-apple-darwin"
    _write(prepared / "python/bin/python3", b"fixture-python", 0o755)
    _write(prepared / "install_entry.py", b"fixture-entry\n")
    _write(prepared / "SBOM.json", b"{}\n")
    manifest = _runtime_manifest(prepared)
    _write(prepared / "runtime-manifest.json", manifest)
    _write(
        src_tauri / "install-runtime.manifest.sha256",
        (_sha256_bytes(manifest) + "\n").encode(),
    )
    _write(src_tauri / "install-runtime.lock.json", b"{}\n")

    contract = b'{"contract_id":"harnesskit.install-target-contract.v1","version":1}\n'
    _write(repo / "schemas/install-target-contract-v1.json", contract)

    app = src_tauri / "target/release/bundle/macos/HarnessKit.app"
    bundled_runtime = (
        app / "Contents/Resources/install-runtime/aarch64-apple-darwin"
    )
    shutil.copytree(prepared, bundled_runtime)
    bundled_contract = (
        app / "Contents/Resources/package-inputs/install-target-contract-v1.json"
    )
    _write(bundled_contract, contract)
    build_inputs_bytes = package._prepare_build_input_contract(repo, BUILD_ID)
    _write(src_tauri / "target/package-build-request.json", build_inputs_bytes)
    _write(
        app / "Contents/Resources/package-inputs/package-build-inputs.json",
        build_inputs_bytes,
    )
    _write(
        app / "Contents/MacOS/harness-desktop",
        b"fixture-main\0" + _binary_marker(build_inputs_bytes),
        0o755,
    )
    _write(
        app / "Contents/Info.plist",
        plistlib.dumps(
            {
                "CFBundleIdentifier": "io.github.pureliture.harnesskit",
                "CFBundleExecutable": "harness-desktop",
                "CFBundleDisplayName": "HarnessKit",
                "CFBundleName": "HarnessKit",
                "CFBundleShortVersionString": "0.1.0",
            },
            sort_keys=True,
        ),
    )
    evidence_root = tmp_path / "evidence"
    return repo, app, evidence_root


def _add_immutable_git_frontend_dependency(repo: Path) -> None:
    package_path = repo / "src-frontend/package.json"
    package_json = json.loads(package_path.read_text(encoding="utf-8"))
    dependencies = package_json["dependencies"]
    dependencies["@pureliture/graph-workbench"] = GIT_GRAPH_WORKBENCH_PIN
    package_path.write_text(
        json.dumps(package_json, sort_keys=True) + "\n", encoding="utf-8"
    )

    lock_path = repo / "src-frontend/package-lock.json"
    package_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    package_lock["packages"][""]["dependencies"] = dependencies
    package_lock["packages"]["node_modules/@pureliture/graph-workbench"] = {
        "integrity": _sri("@pureliture/graph-workbench@0.1.0"),
        "license": "MIT",
        "resolved": GIT_GRAPH_WORKBENCH_PIN,
        "version": "0.1.0",
    }
    lock_body = (json.dumps(package_lock, sort_keys=True) + "\n").encode()
    lock_path.write_bytes(lock_body)

    dist_root = repo / "src-tauri/target/frontend-dist"
    license_path = dist_root / "license-inventory.json"
    license_inventory = json.loads(license_path.read_text(encoding="utf-8"))
    license_inventory["packages"].append(
        {
            "integrity": _sri("@pureliture/graph-workbench@0.1.0"),
            "license": "MIT",
            "name": "@pureliture/graph-workbench",
            "path": "node_modules/@pureliture/graph-workbench",
            "usage": "runtime",
            "version": "0.1.0",
        }
    )
    license_inventory["packages"].sort(
        key=lambda item: (item["name"], item["version"], item["path"])
    )
    license_body = (json.dumps(license_inventory, sort_keys=True) + "\n").encode()
    license_path.write_bytes(license_body)

    manifest_path = dist_root / "bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["licenses"] = license_inventory["packages"]
    manifest["lockfile"]["sha256"] = _sha256_bytes(lock_body)
    manifest["license_inventory"]["sha256"] = _sha256_bytes(license_body)
    next(
        item
        for item in manifest["files"]
        if item["path"] == "license-inventory.json"
    )["sha256"] = _sha256_bytes(license_body)
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )


@dataclass
class _Result:
    returncode: int = 0
    stdout: bytes = b""
    stderr: bytes = b""


class FakeRunner:
    def __init__(
        self,
        *,
        codesign_returncode: int = 0,
        main_arch: str = "arm64",
        python_arch: str = "arm64",
        build_returncode: int = 0,
        frontend_build_returncode: int = 0,
        tauri_cli_returncode: int = 0,
        build_request_path: Path | None = None,
    ) -> None:
        self.codesign_returncode = codesign_returncode
        self.main_arch = main_arch
        self.python_arch = python_arch
        self.build_returncode = build_returncode
        self.frontend_build_returncode = frontend_build_returncode
        self.tauri_cli_returncode = tauri_cli_returncode
        self.build_request_path = build_request_path
        self.calls: list[tuple[str, ...]] = []
        self.call_options: list[dict[str, object]] = []
        self.build_request_observations: list[bool] = []

    def run(self, argv: list[str], **kwargs: object) -> _Result:
        call = tuple(str(value) for value in argv)
        self.calls.append(call)
        self.call_options.append(kwargs)
        if self.build_request_path is not None:
            self.build_request_observations.append(self.build_request_path.exists())
        if call == ("cargo", "tauri", "--version"):
            return _Result(
                returncode=self.tauri_cli_returncode,
                stdout=b"tauri-cli 2.0.0\n",
                stderr=b"error: no such command: tauri\n",
            )
        if call == ("npm", "ci", "--ignore-scripts", "--prefer-offline"):
            return _Result()
        if call == ("npm", "run", "build"):
            return _Result(returncode=self.frontend_build_returncode)
        if call == ("cargo", "tauri", "build", "--bundles", "app"):
            return _Result(returncode=self.build_returncode)
        if call[:4] == ("/usr/bin/codesign", "--verify", "--deep", "--strict"):
            return _Result(returncode=self.codesign_returncode, stderr=b"codesign failed")
        if call[:2] == ("/usr/bin/lipo", "-archs"):
            architecture = self.python_arch if call[-1].endswith("python3") else self.main_arch
            return _Result(stdout=(architecture + "\n").encode())
        raise AssertionError(f"unexpected command: {call!r}")


def test_verified_package_non_frontend_source_allowlists_match_the_rust_build_contract() -> None:
    build_source = (Path(__file__).resolve().parents[2] / "src-tauri/build.rs").read_text(
        encoding="utf-8"
    )

    def rust_string_array(name: str) -> tuple[str, ...]:
        matched = re.search(
            rf"const {name}: &\[&str\] = &\[(.*?)\];",
            build_source,
            re.DOTALL,
        )
        assert matched, f"missing Rust source allowlist: {name}"
        return tuple(
            "".join(re.findall(r'"([^"\\]+)"', entry))
            for entry in matched.group(1).splitlines()
            if entry.strip()
        )

    assert not hasattr(package, "FRONTEND_FILES")
    source = Path(package.__file__).read_text(encoding="utf-8")
    for relative in FRONTEND_SOURCE_CONTRACT_FILES:
        assert relative in source
    assert "bundle-manifest.json" in source
    assert package.TOOL_IDENTITY_ASSET_FILES == TOOL_IDENTITY_ASSET_FILES
    assert package.TOOL_IDENTITY_ASSET_FILES == rust_string_array("TOOL_IDENTITY_ASSET_FILES")
    assert package.BRAND_PACKAGING_FILES == BRAND_PACKAGING_FILES
    assert package.BRAND_PACKAGING_FILES == rust_string_array("BRAND_PACKAGING_FILES")
    assert package.ICON_FILES == ICON_FILES == rust_string_array("ICON_FILES")
    assert getattr(package, "TOOL_IDENTITY_MANIFEST", None) == TOOL_IDENTITY_MANIFEST
    assert '"product_name": "HarnessKit"' in build_source


def _error_code(callable_: object) -> str:
    with pytest.raises(package.PackageVerificationError) as captured:
        callable_()  # type: ignore[operator]
    return captured.value.code


def test_frontend_bundle_contract_accepts_an_immutable_git_commit_pin(
    tmp_path: Path,
) -> None:
    repo, _app, _evidence_root = _fixture_repo(tmp_path)
    _add_immutable_git_frontend_dependency(repo)

    package._prepare_build_input_contract(repo, BUILD_ID)


def test_frontend_bundle_contract_accepts_package_manager_ssh_resolution_for_same_git_pin(
    tmp_path: Path,
) -> None:
    repo, _app, _evidence_root = _fixture_repo(tmp_path)
    _add_immutable_git_frontend_dependency(repo)
    lock_path = repo / "src-frontend/package-lock.json"
    package_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    package_lock["packages"]["node_modules/@pureliture/graph-workbench"][
        "resolved"
    ] = GIT_GRAPH_WORKBENCH_PIN.replace(
        "git+https://github.com/",
        "git+ssh://git" "@github.com/",
    )
    lock_body = (json.dumps(package_lock, sort_keys=True) + "\n").encode()
    lock_path.write_bytes(lock_body)

    manifest_path = repo / "src-tauri/target/frontend-dist/bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["lockfile"]["sha256"] = _sha256_bytes(lock_body)
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    package._prepare_build_input_contract(repo, BUILD_ID)


def test_frontend_bundle_contract_rejects_a_git_pin_with_different_lock_resolution(
    tmp_path: Path,
) -> None:
    repo, _app, _evidence_root = _fixture_repo(tmp_path)
    _add_immutable_git_frontend_dependency(repo)
    lock_path = repo / "src-frontend/package-lock.json"
    package_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    package_lock["packages"]["node_modules/@pureliture/graph-workbench"][
        "resolved"
    ] = "git+https://github.com/pureliture/graph-workbench.git#" + "a" * 40
    lock_body = (json.dumps(package_lock, sort_keys=True) + "\n").encode()
    lock_path.write_bytes(lock_body)

    manifest_path = repo / "src-tauri/target/frontend-dist/bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["lockfile"]["sha256"] = _sha256_bytes(lock_body)
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )

    assert (
        _error_code(lambda: package._prepare_build_input_contract(repo, BUILD_ID))
        == "package_frontend_bundle_invalid"
    )


def test_frontend_bundle_contract_keeps_semver_dependencies_version_pinned(
    tmp_path: Path,
) -> None:
    repo, _app, _evidence_root = _fixture_repo(tmp_path)
    package_path = repo / "src-frontend/package.json"
    package_json = json.loads(package_path.read_text(encoding="utf-8"))
    package_json["dependencies"]["three"] = "^0.185.0"
    package_path.write_text(
        json.dumps(package_json, sort_keys=True) + "\n", encoding="utf-8"
    )

    lock_path = repo / "src-frontend/package-lock.json"
    package_lock = json.loads(lock_path.read_text(encoding="utf-8"))
    package_lock["packages"][""]["dependencies"] = package_json["dependencies"]
    lock_body = (json.dumps(package_lock, sort_keys=True) + "\n").encode()
    lock_path.write_bytes(lock_body)

    manifest_path = repo / "src-tauri/target/frontend-dist/bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["lockfile"]["sha256"] = _sha256_bytes(lock_body)
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8"
    )

    assert (
        _error_code(lambda: package._prepare_build_input_contract(repo, BUILD_ID))
        == "package_frontend_bundle_invalid"
    )


def test_verified_wrapper_runs_exact_build_and_returns_the_exact_fresh_app(tmp_path: Path) -> None:
    repo, app, evidence_root = _fixture_repo(tmp_path)
    runner = FakeRunner()

    built, report = package.build_verified_macos_app(
        repo, evidence_root, runner=runner, build_id=BUILD_ID
    )

    assert built == app.resolve()
    assert report == (evidence_root / BUILD_ID / "qualification-report.json").resolve()
    frontend_build_call = ("npm", "run", "build")
    frontend_install_call = ("npm", "ci", "--ignore-scripts", "--prefer-offline")
    build_call = ("cargo", "tauri", "build", "--bundles", "app")
    assert runner.calls.index(frontend_install_call) < runner.calls.index(
        frontend_build_call
    )
    assert runner.calls.index(frontend_build_call) < runner.calls.index(build_call)
    frontend_install_index = runner.calls.index(frontend_install_call)
    frontend_build_index = runner.calls.index(frontend_build_call)
    isolated_frontend = runner.call_options[frontend_install_index]["cwd"]
    assert isolated_frontend != (repo / "src-frontend").resolve()
    assert runner.call_options[frontend_build_index]["cwd"] == isolated_frontend
    build_index = runner.calls.index(build_call)
    assert runner.call_options[build_index]["cwd"] == repo.resolve()
    build_env = runner.call_options[build_index]["env"]
    assert isinstance(build_env, dict)
    assert build_env["HARNESS_VERIFIED_PACKAGE_BUILD"] == "1"
    assert build_env["HARNESS_PACKAGE_BUILD_ID"] == BUILD_ID
    bundled_inputs = (
        app / "Contents/Resources/package-inputs/package-build-inputs.json"
    ).read_bytes()
    assert build_env["HARNESS_PACKAGE_BUILD_INPUTS_SHA256"] == _sha256_bytes(bundled_inputs)
    assert not (repo / "src-tauri/target/package-build-request.json").exists()
    assert runner.calls[-1] == (
        "/usr/bin/codesign",
        "--verify",
        "--deep",
        "--strict",
        str(app.resolve()),
    )


def test_frontend_build_uses_a_clean_isolated_cache_preferred_install(
    tmp_path: Path,
) -> None:
    repo, _app, _evidence_root = _fixture_repo(tmp_path)
    untrusted_modules = repo / "src-frontend/node_modules"
    _write(untrusted_modules / "transitive-package/index.js", b"tampered\n")
    runner = FakeRunner()

    package._build_frontend_bundle(repo, runner, {})

    assert runner.calls == [
        ("npm", "ci", "--ignore-scripts", "--prefer-offline"),
        ("npm", "run", "build"),
    ]
    install_options, build_options = runner.call_options
    install_root = install_options["cwd"]
    assert isinstance(install_root, Path)
    assert install_root != (repo / "src-frontend").resolve()
    assert build_options["cwd"] == install_root
    assert not str(install_root).startswith(str(untrusted_modules))
    build_environment = build_options["env"]
    assert isinstance(build_environment, dict)
    assert build_environment["HARNESS_FRONTEND_DIST"] == str(
        (repo / "src-tauri/target/frontend-dist").resolve()
    )
    assert build_environment["npm_config_cache"] == str(
        (repo / "src-tauri/target/npm-cache").resolve()
    )


def test_verified_release_reuses_the_wrapper_built_frontend_bundle() -> None:
    build_source = (Path(__file__).resolve().parents[2] / "src-tauri/build.rs").read_text(
        encoding="utf-8"
    )

    assert "let reuse_prebuilt_frontend = release && marker.as_deref() == Some(\"1\");" in build_source
    assert "prepare_frontend_bundle(&tool_identity_qualification, reuse_prebuilt_frontend)" in build_source
    assert "if reuse_prebuilt" in build_source
    assert "return verify_frontend_bundle_dist(repo_root, &final_dir, qualification);" in build_source


def test_bundled_build_inputs_must_equal_the_wrapper_prebuild_contract_bytes(
    tmp_path: Path,
) -> None:
    repo, app, evidence_root = _fixture_repo(tmp_path)
    bundled = app / "Contents/Resources/package-inputs/package-build-inputs.json"
    bundled.write_bytes(bundled.read_bytes().rstrip() + b"  \n")

    assert (
        _error_code(
            lambda: package.build_verified_macos_app(
                repo, evidence_root, runner=FakeRunner(), build_id=BUILD_ID
            )
        )
        == "package_build_inputs_mismatch"
    )


@pytest.mark.parametrize(
    "relative",
    [
        "src-tauri/src/lib.rs",
        "src-tauri/build.rs",
        "src-frontend/build.mjs",
        "src-tauri/capabilities/default.json",
        "src-tauri/icons/icon.icns",
        "assets/branding/harness-desktop/derivation-manifest.json",
        "assets/packaging/macos/dmg-background.png",
    ],
)
def test_build_input_contract_changes_when_an_app_source_member_changes(
    tmp_path: Path,
    relative: str,
) -> None:
    repo, _app, _evidence_root = _fixture_repo(tmp_path)
    before = json.loads(package._prepare_build_input_contract(repo, BUILD_ID))
    path = repo / relative
    path.write_bytes(path.read_bytes() + b"changed\n")
    after = json.loads(package._prepare_build_input_contract(repo, BUILD_ID))

    assert before["app_source_manifest_sha256"] != after["app_source_manifest_sha256"]
    assert before["source_identity_sha256"] != after["source_identity_sha256"]


def test_build_input_contract_changes_when_a_valid_bundle_is_rebuilt(
    tmp_path: Path,
) -> None:
    repo, _app, _evidence_root = _fixture_repo(tmp_path)
    before = json.loads(package._prepare_build_input_contract(repo, BUILD_ID))
    output = repo / "src-tauri/target/frontend-dist/assets/app.bundle.js"
    output.write_bytes(b"console.log('rebuilt bundle');\n")
    manifest_path = repo / "src-tauri/target/frontend-dist/bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["output"]["sha256"] = _sha256_bytes(output.read_bytes())
    next(
        item
        for item in manifest["files"]
        if item["path"] == "assets/app.bundle.js"
    )["sha256"] = manifest["output"]["sha256"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    after = json.loads(package._prepare_build_input_contract(repo, BUILD_ID))

    assert before["frontend_bundle_output_sha256"] != after[
        "frontend_bundle_output_sha256"
    ]
    assert before["frontend_bundle_manifest_sha256"] != after[
        "frontend_bundle_manifest_sha256"
    ]
    assert before["source_identity_sha256"] != after["source_identity_sha256"]


@pytest.mark.parametrize(
    ("relative", "expected_code"),
    [
        (
            "src-tauri/target/frontend-dist/assets/app.bundle.js",
            "package_frontend_bundle_invalid",
        ),
        (
            "src-tauri/target/frontend-dist/license-inventory.json",
            "package_frontend_bundle_invalid",
        ),
        (
            "src-frontend/package-lock.json",
            "package_frontend_bundle_invalid",
        ),
        (
            "src-tauri/target/frontend-dist/index.html",
            "package_frontend_bundle_invalid",
        ),
    ],
)
def test_frontend_bundle_contract_rejects_hash_drift(
    tmp_path: Path,
    relative: str,
    expected_code: str,
) -> None:
    repo, _app, _evidence_root = _fixture_repo(tmp_path)
    path = repo / relative
    path.write_bytes(path.read_bytes() + b"tampered\n")

    assert (
        _error_code(lambda: package._prepare_build_input_contract(repo, BUILD_ID))
        == expected_code
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("entrypoint", "app.js"),
        ("source_map", True),
    ],
)
def test_frontend_bundle_contract_rejects_unsupported_manifest_shape(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    repo, _app, _evidence_root = _fixture_repo(tmp_path)
    manifest_path = repo / "src-tauri/target/frontend-dist/bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert (
        _error_code(lambda: package._prepare_build_input_contract(repo, BUILD_ID))
        == "package_frontend_bundle_invalid"
    )


def test_frontend_bundle_contract_rejects_missing_transitive_license(
    tmp_path: Path,
) -> None:
    repo, _app, _evidence_root = _fixture_repo(tmp_path)
    lock_path = repo / "src-frontend/package-lock.json"
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["packages"]["node_modules/3d-force-graph"]["dependencies"] = {
        "transitive-package": "1.0.0"
    }
    lock["packages"]["node_modules/transitive-package"] = {
        "integrity": _sri("transitive-package@1.0.0"),
        "license": "MIT",
        "version": "1.0.0",
    }
    lock_body = (json.dumps(lock, sort_keys=True) + "\n").encode()
    lock_path.write_bytes(lock_body)

    manifest_path = repo / "src-tauri/target/frontend-dist/bundle-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["lockfile"]["sha256"] = _sha256_bytes(lock_body)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert (
        _error_code(lambda: package._prepare_build_input_contract(repo, BUILD_ID))
        == "package_frontend_bundle_invalid"
    )


def test_main_binary_must_embed_the_build_id_and_contract_hash(tmp_path: Path) -> None:
    repo, app, evidence_root = _fixture_repo(tmp_path)
    _write(app / "Contents/MacOS/harness-desktop", b"fixture-main", 0o755)

    assert (
        _error_code(
            lambda: package.build_verified_macos_app(
                repo, evidence_root, runner=FakeRunner(), build_id=BUILD_ID
            )
        )
        == "package_binary_build_identity_mismatch"
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("CFBundleIdentifier", "io.example.tampered"),
        ("CFBundleExecutable", "other-executable"),
        ("CFBundleDisplayName", "HarnessKit Tampered"),
        ("CFBundleName", "HarnessKit Tampered"),
        ("CFBundleShortVersionString", "9.9.9"),
    ],
)
def test_info_plist_identity_must_match_the_build_contract_before_codesign(
    tmp_path: Path, key: str, value: str
) -> None:
    repo, app, evidence_root = _fixture_repo(tmp_path)
    info_path = app / "Contents/Info.plist"
    info = plistlib.loads(info_path.read_bytes())
    info[key] = value
    info_path.write_bytes(plistlib.dumps(info, sort_keys=True))
    runner = FakeRunner()

    assert (
        _error_code(
            lambda: package.build_verified_macos_app(
                repo, evidence_root, runner=runner, build_id=BUILD_ID
            )
        )
        == "package_bundle_identity_mismatch"
    )
    assert not any(call[0] == "/usr/bin/codesign" for call in runner.calls)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mainBinaryName", "harnesskit-tampered"),
        ("windowTitle", "HarnessKit Tampered"),
    ],
)
def test_tauri_config_visible_and_executable_identity_is_build_authority(
    tmp_path: Path, field: str, value: str
) -> None:
    repo, _app, _evidence_root = _fixture_repo(tmp_path)
    config_path = repo / "src-tauri/tauri.conf.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if field == "windowTitle":
        config["app"]["windows"][0]["title"] = value
    else:
        config[field] = value
    config_path.write_text(json.dumps(config, sort_keys=True), encoding="utf-8")

    assert (
        _error_code(lambda: package._prepare_build_input_contract(repo, BUILD_ID))
        == "package_source_identity_invalid"
    )


@pytest.mark.parametrize("component", ["repo_target", "resources", "runtime", "python"])
def test_critical_intermediate_symlinks_fail_closed(
    tmp_path: Path, component: str
) -> None:
    repo, app, evidence_root = _fixture_repo(tmp_path)
    paths = {
        "repo_target": repo / "src-tauri/target",
        "resources": app / "Contents/Resources",
        "runtime": app / "Contents/Resources/install-runtime",
        "python": app
        / "Contents/Resources/install-runtime/aarch64-apple-darwin/python",
    }
    path = paths[component]
    external = tmp_path / f"external-{component}"
    path.rename(external)
    os.symlink(os.path.relpath(external, path.parent), path)

    assert (
        _error_code(
            lambda: package.build_verified_macos_app(
                repo, evidence_root, runner=FakeRunner(), build_id=BUILD_ID
            )
        )
        == "package_critical_path_rejected"
    )


def test_bundle_manifest_rejects_a_relative_symlink_that_escapes_the_app(
    tmp_path: Path,
) -> None:
    repo, app, evidence_root = _fixture_repo(tmp_path)
    os.symlink("../../../../outside", app / "Contents/Resources/escape")

    assert (
        _error_code(
            lambda: package.build_verified_macos_app(
                repo, evidence_root, runner=FakeRunner(), build_id=BUILD_ID
            )
        )
        == "package_bundle_symlink_target_rejected"
    )


def test_bundle_manifest_normalizes_only_the_dmg_group_write_transition(
    tmp_path: Path,
) -> None:
    standalone = tmp_path / "standalone.app"
    mounted = tmp_path / "mounted.app"
    for root, data_mode, executable_mode in (
        (standalone, 0o664, 0o775),
        (mounted, 0o644, 0o755),
    ):
        _write(root / "Contents/Resources/data.json", b"same\n", data_mode)
        _write(root / "Contents/MacOS/harness-desktop", b"same\n", executable_mode)

    assert package._canonical_bundle_manifest(
        standalone
    ) == package._canonical_bundle_manifest(mounted)

    (mounted / "Contents/Resources/data.json").chmod(0o600)
    assert package._canonical_bundle_manifest(
        standalone
    ) != package._canonical_bundle_manifest(mounted)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("missing_app", "package_app_missing"),
        ("missing_main", "package_main_executable_missing"),
        ("tampered_runtime", "package_runtime_tree_mismatch"),
        ("manifest_mismatch", "package_runtime_manifest_mismatch"),
        ("embedded_contract_mismatch", "package_target_contract_mismatch"),
        ("stale_build_identity", "package_build_inputs_mismatch"),
    ],
)
def test_bundle_tampering_fails_closed(
    tmp_path: Path, mutation: str, expected_code: str
) -> None:
    repo, app, evidence_root = _fixture_repo(tmp_path)
    if mutation == "missing_app":
        shutil.rmtree(app)
    elif mutation == "missing_main":
        (app / "Contents/MacOS/harness-desktop").unlink()
    elif mutation == "tampered_runtime":
        (app / "Contents/Resources/install-runtime/aarch64-apple-darwin/install_entry.py").write_bytes(
            b"tampered"
        )
    elif mutation == "manifest_mismatch":
        (app / "Contents/Resources/install-runtime/aarch64-apple-darwin/runtime-manifest.json").write_bytes(
            b"{}\n"
        )
    elif mutation == "embedded_contract_mismatch":
        (app / "Contents/Resources/package-inputs/install-target-contract-v1.json").write_bytes(
            b"{}\n"
        )
    else:
        build_inputs = app / "Contents/Resources/package-inputs/package-build-inputs.json"
        value = json.loads(build_inputs.read_text(encoding="utf-8"))
        value["build_id"] = "c" * 32
        build_inputs.write_text(json.dumps(value), encoding="utf-8")

    assert (
        _error_code(
            lambda: package.build_verified_macos_app(
                repo, evidence_root, runner=FakeRunner(), build_id=BUILD_ID
            )
        )
        == expected_code
    )
    assert not (evidence_root / BUILD_ID / "qualification-report.json").exists()


def test_codesign_and_both_arm64_architecture_checks_are_nonzero_gates(
    tmp_path: Path,
) -> None:
    for runner, code in [
        (FakeRunner(codesign_returncode=1), "package_codesign_failed"),
        (FakeRunner(main_arch="x86_64"), "package_main_architecture_mismatch"),
        (FakeRunner(python_arch="x86_64"), "package_python_architecture_mismatch"),
    ]:
        repo, _app, evidence_root = _fixture_repo(tmp_path / code)
        assert (
            _error_code(
                lambda: package.build_verified_macos_app(
                    repo, evidence_root, runner=runner, build_id=BUILD_ID
                )
            )
            == code
        )


def test_inner_build_failure_stops_before_bundle_qualification(tmp_path: Path) -> None:
    repo, _app, evidence_root = _fixture_repo(tmp_path)
    runner = FakeRunner(build_returncode=17)
    assert (
        _error_code(
            lambda: package.build_verified_macos_app(
                repo, evidence_root, runner=runner, build_id=BUILD_ID
            )
        )
        == "package_build_failed"
    )
    assert runner.calls[-1] == ("cargo", "tauri", "build", "--bundles", "app")
    assert not any(call[0] in {"/usr/bin/codesign", "/usr/bin/lipo"} for call in runner.calls)
    assert not evidence_root.exists()


def test_frontend_build_fails_before_build_request_or_cargo_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _app, evidence_root = _fixture_repo(tmp_path)
    request = repo / "src-tauri/target/package-build-request.json"
    request.unlink()
    monkeypatch.setenv("HARNESS_FRONTEND_DIST", str(tmp_path / "redirected"))
    monkeypatch.setenv("NODE_OPTIONS", "--require=untrusted.js")
    runner = FakeRunner(frontend_build_returncode=23, build_request_path=request)

    code = _error_code(
        lambda: package.build_verified_macos_app(
            repo, evidence_root, runner=runner, build_id=BUILD_ID
        )
    )

    assert code == "package_frontend_build_failed"
    assert runner.calls == [
        ("cargo", "tauri", "--version"),
        ("npm", "ci", "--ignore-scripts", "--prefer-offline"),
        ("npm", "run", "build"),
    ]
    frontend_environment = runner.call_options[2]["env"]
    assert isinstance(frontend_environment, dict)
    assert frontend_environment["HARNESS_FRONTEND_DIST"] == str(
        (repo / "src-tauri/target/frontend-dist").resolve()
    )
    assert "NODE_OPTIONS" not in frontend_environment
    assert not request.exists()
    assert not evidence_root.exists()


def test_missing_tauri_cli_fails_typed_before_build_request_or_evidence_publish(
    tmp_path: Path,
) -> None:
    repo, _app, evidence_root = _fixture_repo(tmp_path)
    build_request = repo / "src-tauri/target/package-build-request.json"
    build_request.unlink()
    runner = FakeRunner(
        tauri_cli_returncode=1,
        build_returncode=101,
        build_request_path=build_request,
    )

    code = _error_code(
        lambda: package.build_verified_macos_app(
            repo, evidence_root, runner=runner, build_id=BUILD_ID
        )
    )

    assert (
        code,
        runner.calls,
        runner.build_request_observations,
        build_request.exists(),
        evidence_root.exists(),
    ) == (
        "package_tauri_cli_missing",
        [("cargo", "tauri", "--version")],
        [False],
        False,
        False,
    )


def test_success_report_is_canonical_path_free_and_hash_bound(tmp_path: Path) -> None:
    repo, app, evidence_root = _fixture_repo(tmp_path)
    link = app / "Contents/Resources/runtime-link"
    os.symlink("install-runtime", link)

    _, report_path = package.build_verified_macos_app(
        repo, evidence_root, runner=FakeRunner(), build_id=BUILD_ID
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    manifest_path = report_path.with_name("bundle-manifest.json")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)

    assert report["schema_version"] == 1
    assert report["status"] == "passed"
    assert report["claim_scope"] == "official_wrapper_pipeline"
    assert report["build_identity"]["build_id"] == BUILD_ID
    assert report["qualification"] == {
        "binary_build_identity": "verified",
        "build_input_contract": "verified",
        "bundle_identity": "verified",
        "codesign": "verified",
        "official_wrapper_pipeline": "verified",
        "main_executable_architecture": "arm64",
        "python_architecture": "arm64",
        "runtime_tree": "verified",
        "embedded_inputs": "verified",
    }
    assert report["bundle_manifest_sha256"] == _sha256_bytes(manifest_bytes)
    assert [entry["path"] for entry in manifest] == sorted(
        entry["path"] for entry in manifest
    )
    assert next(entry for entry in manifest if entry["path"] == "Contents/Resources/runtime-link") == {
        "path": "Contents/Resources/runtime-link",
        "type": "symlink",
        "target": "install-runtime",
    }
    encoded = json.dumps(report, sort_keys=True) + manifest_path.read_text(encoding="utf-8")
    assert str(tmp_path) not in encoded
    assert report_path.stat().st_mode & 0o777 == 0o600
    assert manifest_path.stat().st_mode & 0o777 == 0o600


def test_cli_returns_nonzero_and_only_the_typed_code_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def fail(*_args: object, **_kwargs: object) -> tuple[Path, Path]:
        raise package.PackageVerificationError("package_codesign_failed")

    monkeypatch.setattr(package, "build_verified_macos_app", fail)
    assert package.main(["--repo-root", str(tmp_path)]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "package_codesign_failed\n"
    assert str(tmp_path) not in output.err
