from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_SOURCE = (
    REPO_ROOT
    / "assets/branding/harness-desktop/source/harness-desktop-cabinet.png"
)
DERIVATION_MANIFEST = (
    REPO_ROOT / "assets/branding/harness-desktop/derivation-manifest.json"
)
DERIVE_SCRIPT = REPO_ROOT / "scripts/package/derive_brand_assets.py"
QUALIFY_SCRIPT = REPO_ROOT / "scripts/package/qualify_visual_assets.py"
PACKAGE_COORDINATOR = REPO_ROOT / "scripts/package/build_verified_macos_package.py"
DMG_LAYOUT_MANIFEST = REPO_ROOT / "assets/packaging/macos/dmg-layout-manifest.json"
DMG_BACKGROUND = REPO_ROOT / "assets/packaging/macos/dmg-background.png"
DMG_BACKGROUND_SVG = REPO_ROOT / "assets/packaging/macos/dmg-background.svg"
TAURI_CONFIG = REPO_ROOT / "src-tauri/tauri.conf.json"
APP_SHELL = REPO_ROOT / "src-frontend/app-shell.js"
PACKAGE_JSON = REPO_ROOT / "package.json"

CANONICAL_SOURCE_SHA256 = (
    "c74519996e2ddefd56a77cb2c17bf6cc38a8ed954966c7a17c63a74bc93b9c5f"
)
CANONICAL_SOURCE_PATH = (
    "assets/branding/harness-desktop/source/harness-desktop-cabinet.png"
)
WEBVIEW_BRAND_PATH = (
    "src-frontend/assets/branding/harness-desktop-64.png"
)
TAURI_BUNDLE_ICON_PATHS = {
    "icons/32x32.png",
    "icons/128x128.png",
    "icons/128x128@2x.png",
    "icons/icon.icns",
    "icons/icon.ico",
}
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _regular_file(path: Path, code: str) -> Path:
    try:
        metadata = path.lstat()
    except OSError:
        pytest.fail(code)
    assert stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode), code
    return path


def _read_bytes(path: Path, code: str) -> bytes:
    return _regular_file(path, code).read_bytes()


def _read_text(path: Path, code: str) -> str:
    try:
        return _read_bytes(path, code).decode("utf-8", "strict")
    except UnicodeDecodeError:
        pytest.fail(code)


def _load_json(path: Path, code: str) -> dict[str, object]:
    try:
        value = json.loads(_read_text(path, code))
    except json.JSONDecodeError:
        pytest.fail(code)
    assert isinstance(value, dict), code
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _png_header(path: Path, code: str) -> tuple[int, int, int, int, int]:
    body = _read_bytes(path, code)
    assert body[:8] == b"\x89PNG\r\n\x1a\n", code
    assert len(body) >= 33 and body[12:16] == b"IHDR", code
    width, height, bit_depth, color_type, _compression, _filter, interlace = struct.unpack(
        ">IIBBBBB", body[16:29]
    )
    return width, height, bit_depth, color_type, interlace


def _png_density(path: Path, code: str) -> tuple[int, int, int]:
    body = _read_bytes(path, code)
    assert body[:8] == b"\x89PNG\r\n\x1a\n", code
    offset = 8
    while offset + 12 <= len(body):
        length = struct.unpack(">I", body[offset : offset + 4])[0]
        chunk_type = body[offset + 4 : offset + 8]
        data_start = offset + 8
        data_end = data_start + length
        assert data_end + 4 <= len(body), code
        if chunk_type == b"pHYs":
            assert length == 9, code
            return struct.unpack(">IIB", body[data_start:data_end])
        offset = data_end + 4
    pytest.fail(code)


def _safe_repo_path(value: object, code: str) -> Path:
    assert isinstance(value, str) and value, code
    pure = PurePosixPath(value)
    assert not pure.is_absolute() and str(pure) == value, code
    assert all(part not in {"", ".", ".."} for part in pure.parts), code
    return REPO_ROOT / pure


def _manifest_outputs(manifest: dict[str, object]) -> dict[str, dict[str, object]]:
    raw = manifest.get("outputs")
    assert isinstance(raw, list) and raw, "icon_derivation_inventory_missing"
    outputs: dict[str, dict[str, object]] = {}
    observed_order: list[str] = []
    for value in raw:
        assert isinstance(value, dict), "icon_derivation_inventory_invalid"
        path = value.get("path")
        assert isinstance(path, str) and path not in outputs, "icon_derivation_inventory_invalid"
        _safe_repo_path(path, "icon_derivation_output_path_invalid")
        digest = value.get("sha256")
        assert isinstance(digest, str) and SHA256.fullmatch(digest), "icon_derivation_output_hash_invalid"
        observed_order.append(path)
        outputs[path] = value
    assert observed_order == sorted(observed_order), "icon_derivation_inventory_not_canonical"
    return outputs


def _assert_no_timestamp_fields(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            assert key not in {
                "created_at",
                "generated_at",
                "generation_timestamp",
                "timestamp",
                "updated_at",
            }, "icon_derivation_manifest_is_time_variant"
            _assert_no_timestamp_fields(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_timestamp_fields(child)


def _literal_argvs(source: str) -> list[tuple[str, ...]]:
    values: list[tuple[str, ...]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        items: list[str] = []
        for element in node.elts:
            if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
                break
            items.append(element.value)
        else:
            values.append(tuple(items))
    return values


def _run_visual_qualifier(repo_root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(QUALIFY_SCRIPT),
            "--repo-root",
            str(repo_root),
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def _visual_fixture(tmp_path: Path) -> Path:
    fixture = tmp_path / "repo"
    for relative in [
        Path("assets/branding/harness-desktop"),
        Path("src-tauri/icons"),
        Path("src-frontend/assets/branding"),
    ]:
        source = REPO_ROOT / relative
        destination = fixture / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination, symlinks=True)
    for relative in [
        Path("src-frontend/app-shell.js"),
        Path("src-tauri/tauri.conf.json"),
    ]:
        destination = fixture / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / relative, destination)
    return fixture


def test_icon_01_canonical_source_is_exact_user_approved_rgba_png() -> None:
    source = _regular_file(CANONICAL_SOURCE, "icon_canonical_source_missing")

    assert _sha256(source) == CANONICAL_SOURCE_SHA256, "icon_canonical_source_hash_mismatch"
    assert _png_header(source, "icon_canonical_source_invalid") == (
        1254,
        1254,
        8,
        6,  # PNG truecolor RGBA with transparent outer canvas.
        0,
    ), "icon_canonical_source_mode_mismatch"


def test_icon_02_03_07_derivation_manifest_is_complete_deterministic_and_byte_bound() -> None:
    manifest = _load_json(DERIVATION_MANIFEST, "icon_derivation_manifest_missing")

    assert manifest.get("schema_version") == 1, "icon_derivation_manifest_schema_invalid"
    source = manifest.get("source")
    assert isinstance(source, dict), "icon_derivation_source_missing"
    assert source.get("path") == CANONICAL_SOURCE_PATH, "icon_derivation_source_path_mismatch"
    assert source.get("sha256") == CANONICAL_SOURCE_SHA256, "icon_derivation_source_hash_mismatch"
    assert source.get("media_type") == "image/png", "icon_derivation_source_media_mismatch"
    assert source.get("dimensions") == {"width": 1254, "height": 1254}, (
        "icon_derivation_source_dimensions_mismatch"
    )
    assert source.get("color_mode") == "RGBA", "icon_derivation_source_mode_mismatch"

    generator = manifest.get("generator")
    assert isinstance(generator, dict), "icon_derivation_generator_missing"
    assert generator.get("name") == "tauri-cli", "icon_derivation_generator_invalid"
    assert isinstance(generator.get("version"), str) and re.fullmatch(
        r"\d+\.\d+\.\d+", str(generator["version"])
    ), "icon_derivation_generator_not_pinned"
    assert manifest.get("operation") == "resize_container_only", (
        "icon_derivation_operation_invalid"
    )
    _assert_no_timestamp_fields(manifest)

    outputs = _manifest_outputs(manifest)
    required_paths = {
        *(f"src-tauri/{path}" for path in TAURI_BUNDLE_ICON_PATHS),
        WEBVIEW_BRAND_PATH,
    }
    assert required_paths <= set(outputs), "icon_derivation_required_output_missing"

    committed_tauri_outputs = {
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / "src-tauri/icons").iterdir()
    }
    manifest_tauri_outputs = {
        path for path in outputs if path.startswith("src-tauri/icons/")
    }
    assert committed_tauri_outputs == manifest_tauri_outputs, (
        "icon_derivation_inventory_stale_or_incomplete"
    )

    for relative, record in outputs.items():
        path = _regular_file(REPO_ROOT / relative, "icon_derivation_output_missing")
        assert _sha256(path) == record["sha256"], "icon_derivation_output_hash_mismatch"
        if record.get("media_type") == "image/png":
            dimensions = record.get("dimensions")
            assert isinstance(dimensions, dict), "icon_derivation_output_dimensions_missing"
            width, height, _depth, _mode, interlace = _png_header(
                path, "icon_derivation_output_media_invalid"
            )
            assert dimensions == {"width": width, "height": height}, (
                "icon_derivation_output_dimensions_mismatch"
            )
            assert interlace == 0, "icon_derivation_output_interlace_invalid"

    evidence = manifest.get("small_size_evidence")
    assert isinstance(evidence, list), "icon_small_size_inventory_missing"
    evidence_by_size = {
        record.get("size"): record
        for record in evidence
        if isinstance(record, dict)
    }
    assert {16, 32, 64} <= set(evidence_by_size), "icon_small_size_inventory_incomplete"
    for size in (16, 32, 64):
        record = evidence_by_size[size]
        path_value = record.get("path")
        assert isinstance(path_value, str) and path_value in outputs, (
            "icon_small_size_output_unbound"
        )
        assert _png_header(
            REPO_ROOT / path_value, "icon_small_size_output_missing"
        )[:2] == (size, size), "icon_small_size_dimensions_mismatch"


def test_icon_02_06_visual_qualifier_rederives_fresh_outputs_without_custom_artwork_transform() -> None:
    derive_source = _read_text(DERIVE_SCRIPT, "icon_derivation_script_missing")
    _read_text(QUALIFY_SCRIPT, "icon_qualification_script_missing")

    tree = ast.parse(derive_source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not imports.intersection({"random", "secrets", "time", "uuid"}), (
        "icon_derivation_nondeterministic_dependency"
    )
    called = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert called.isdisjoint(
        {
            "alpha_composite",
            "blur",
            "colorize",
            "crop",
            "expand",
            "filter",
            "gaussian_blur",
            "pad",
            "putalpha",
            "sharpen",
        }
    ), "icon_derivation_forbidden_transform"
    assert "tauri" in derive_source and "icon" in derive_source, (
        "icon_derivation_pinned_tauri_tool_missing"
    )

    first = _run_visual_qualifier(REPO_ROOT)
    second = _run_visual_qualifier(REPO_ROOT)
    assert first.returncode == 0, first.stderr or first.stdout
    assert second.returncode == 0, second.stderr or second.stdout
    assert first.stdout == second.stdout, "icon_fresh_derivation_report_not_deterministic"
    report = json.loads(first.stdout)
    assert report.get("status") == "qualified", "icon_fresh_derivation_not_qualified"
    assert report.get("canonical_source_sha256") == CANONICAL_SOURCE_SHA256, (
        "icon_qualification_source_identity_mismatch"
    )
    assert report.get("fresh_derivation_match") is True, "icon_fresh_derivation_mismatch"
    assert report.get("placeholder_scan") == "passed", "icon_placeholder_detected"


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("source", "brand_source_hash_mismatch"),
        ("derived", "brand_output_hash_mismatch"),
        ("placeholder", "brand_placeholder_detected"),
    ],
)
def test_icon_01_02_06_visual_qualification_fails_closed(
    tmp_path: Path,
    mutation: str,
    expected_code: str,
) -> None:
    _regular_file(QUALIFY_SCRIPT, "icon_qualification_script_missing")
    fixture = _visual_fixture(tmp_path)
    if mutation == "source":
        target = fixture / CANONICAL_SOURCE_PATH
        target.write_bytes(target.read_bytes()[:-1] + bytes([target.read_bytes()[-1] ^ 1]))
    elif mutation == "derived":
        target = fixture / "src-tauri/icons/32x32.png"
        target.write_bytes(target.read_bytes()[:-1] + bytes([target.read_bytes()[-1] ^ 1]))
    else:
        target = fixture / "src-frontend/app-shell.js"
        body = target.read_text(encoding="utf-8")
        body = re.sub(r"<img[^>]*harness-desktop-64\.png[^>]*>", '<span class="app-mark">H</span>', body)
        target.write_text(body, encoding="utf-8")

    completed = _run_visual_qualifier(fixture)
    assert completed.returncode == 1, "icon_qualification_did_not_fail_closed"
    report = json.loads(completed.stdout)
    assert report.get("status") == "rejected", "icon_qualification_failure_status_invalid"
    assert report.get("code") == expected_code


def test_icon_04_05_06_09_header_and_tauri_bundle_bind_only_qualified_outputs() -> None:
    manifest = _load_json(DERIVATION_MANIFEST, "icon_derivation_manifest_missing")
    outputs = _manifest_outputs(manifest)
    shell = _read_text(APP_SHELL, "app_header_source_missing")
    config = _load_json(TAURI_CONFIG, "tauri_config_missing")

    brand_image = re.search(
        r'<img(?=[^>]*src="\./assets/branding/harness-desktop-64\.png")'
        r'(?=[^>]*alt="")(?=[^>]*aria-hidden="true")[^>]*>',
        shell,
    )
    assert brand_image is not None, "app_header_brand_image_missing"
    assert re.search(r"<h1>\s*HarnessKit\s*</h1>", shell), (
        "app_header_visible_product_label_missing"
    )
    assert not re.search(r'class="app-mark"[^>]*>\s*H\s*<', shell), (
        "app_header_letter_placeholder_present"
    )
    assert WEBVIEW_BRAND_PATH in outputs, "app_header_brand_asset_unbound"

    bundle = config.get("bundle")
    assert isinstance(bundle, dict), "tauri_bundle_config_missing"
    configured_icons = bundle.get("icon")
    assert isinstance(configured_icons, list), "tauri_bundle_icon_binding_missing"
    assert set(configured_icons) == TAURI_BUNDLE_ICON_PATHS, (
        "tauri_bundle_icon_binding_mismatch"
    )
    assert {
        f"src-tauri/{path}" for path in configured_icons
    } <= set(outputs), "tauri_bundle_icon_not_qualified"


def test_macpkg_01_03_09_entrypoint_and_tauri_config_pin_product_target_and_macos13() -> None:
    config = _load_json(TAURI_CONFIG, "tauri_config_missing")
    package = _load_json(PACKAGE_JSON, "package_json_missing")

    assert config.get("productName") == "HarnessKit", "package_product_name_mismatch"
    assert config.get("mainBinaryName") == "harness-desktop", (
        "package_main_binary_name_mismatch"
    )
    assert config.get("identifier") == "io.github.pureliture.harnesskit", (
        "package_bundle_identifier_mismatch"
    )
    windows = config.get("app", {}).get("windows", [])
    assert isinstance(windows, list) and windows, "package_main_window_missing"
    assert windows[0].get("title") == "HarnessKit", "package_window_title_mismatch"
    bundle = config.get("bundle")
    assert isinstance(bundle, dict) and bundle.get("active") is True, (
        "package_bundle_disabled"
    )
    macos = bundle.get("macOS")
    assert isinstance(macos, dict), "package_macos_config_missing"
    assert macos.get("minimumSystemVersion") == "13.0", (
        "package_macos_deployment_target_mismatch"
    )
    assert macos.get("signingIdentity") == "-", "package_adhoc_signing_not_configured"

    scripts = package.get("scripts")
    assert isinstance(scripts, dict), "verified_package_task_missing"
    alias = scripts.get("tauri-build")
    assert isinstance(alias, str) and "scripts/package/build_verified_macos_package.py" in alias, (
        "verified_package_task_bypasses_coordinator"
    )
    assert "HARNESS_VERIFIED_PACKAGE_BUILD" not in alias, (
        "verified_package_task_requires_internal_marker"
    )


def test_macpkg_01_02_verified_coordinator_builds_app_and_dmg_once_and_compares_identity() -> None:
    source = _read_text(PACKAGE_COORDINATOR, "verified_package_coordinator_missing")
    build_argvs = [
        argv for argv in _literal_argvs(source) if argv[:3] == ("cargo", "tauri", "build")
    ]

    assert len(build_argvs) == 1, "verified_package_must_use_one_tauri_build"
    build_argv = build_argvs[0]
    assert "--bundles" in build_argv and "app,dmg" in build_argv, (
        "verified_package_app_dmg_build_missing"
    )
    for marker in [
        "HARNESS_VERIFIED_PACKAGE_BUILD",
        "HARNESS_PACKAGE_BUILD_ID",
        "HARNESS_PACKAGE_BUILD_INPUTS_SHA256",
        "HarnessKit.app",
        ".dmg",
        "standalone_app_manifest_sha256",
        "dmg_contained_app_manifest_sha256",
        "package_build_identity_mismatch",
        "package_app_missing",
        "package_dmg_missing",
    ]:
        assert marker in source, f"verified_package_contract_missing:{marker}"
    assert re.search(
        r"standalone_app_manifest_sha256\s*!=\s*dmg_contained_app_manifest_sha256",
        source,
    ) or re.search(
        r"dmg_contained_app_manifest_sha256\s*!=\s*standalone_app_manifest_sha256",
        source,
    ), "verified_package_contained_app_equality_gate_missing"
    assert "hdiutil" in source and "-readonly" in source, (
        "verified_package_readonly_dmg_audit_missing"
    )


def test_macpkg_04_dmg_layout_is_repo_owned_hash_bound_and_exact() -> None:
    manifest = _load_json(DMG_LAYOUT_MANIFEST, "dmg_layout_manifest_missing")

    assert manifest.get("schema_version") == 2, "dmg_layout_manifest_schema_invalid"
    assert manifest.get("window") == {"width": 660, "height": 400}, (
        "dmg_window_geometry_mismatch"
    )
    items = manifest.get("items")
    assert isinstance(items, dict), "dmg_layout_items_missing"
    assert items.get("app") == {
        "name": "HarnessKit.app",
        "x": 180,
        "y": 190,
    }, "dmg_app_geometry_mismatch"
    assert items.get("applications_alias") == {
        "name": "Applications",
        "target": "/Applications",
        "x": 480,
        "y": 190,
    }, "dmg_applications_alias_geometry_mismatch"
    instruction = manifest.get("instruction")
    assert isinstance(instruction, dict) and instruction.get("text") == (
        "HarnessKit을 Applications로 드래그"
    ), "dmg_visible_instruction_missing"
    background = manifest.get("background")
    assert isinstance(background, dict), "dmg_background_contract_missing"
    assert background.get("path") == "assets/packaging/macos/dmg-background.png", (
        "dmg_background_path_mismatch"
    )
    assert background.get("sha256") == _sha256(
        _regular_file(DMG_BACKGROUND, "dmg_background_missing")
    ), "dmg_background_hash_mismatch"
    assert background.get("logical_dimensions") == {"height": 400, "width": 660}, (
        "dmg_background_logical_dimensions_mismatch"
    )
    assert background.get("pixel_dimensions") == {"height": 800, "width": 1320}, (
        "dmg_background_pixel_dimensions_mismatch"
    )
    assert background.get("dpi") == {"x": 144, "y": 144}, (
        "dmg_background_density_contract_mismatch"
    )
    assert _png_header(DMG_BACKGROUND, "dmg_background_invalid")[:2] == (
        1320,
        800,
    ), "dmg_background_dimensions_mismatch"
    assert _png_density(DMG_BACKGROUND, "dmg_background_density_invalid") == (
        5669,
        5669,
        1,
    ), "dmg_background_density_mismatch"


def test_macpkg_04_background_defers_item_labels_to_finder() -> None:
    root = ET.fromstring(
        _regular_file(DMG_BACKGROUND_SVG, "dmg_background_svg_missing").read_text(
            encoding="utf-8"
        )
    )
    namespace = {"svg": "http:" "//www.w3.org/2000/svg"}
    visible_text = {
        "".join(node.itertext()).strip()
        for node in root.findall(".//svg:text", namespace)
    }
    assert "HarnessKit.app" not in visible_text, (
        "dmg_background_duplicates_finder_app_label"
    )
    assert "Applications" not in visible_text, (
        "dmg_background_duplicates_finder_alias_label"
    )
    label_backings = root.findall(
        ".//*[@data-role='finder-label-backing']"
    )
    assert len(label_backings) == 2, "dmg_finder_label_backing_missing"
    assert {backing.get("data-center-x") for backing in label_backings} == {
        "180",
        "480",
    }, "dmg_finder_label_backing_geometry_mismatch"


def test_macpkg_09_10_architecture_gate_recurses_over_all_native_entries() -> None:
    source = _read_text(PACKAGE_COORDINATOR, "verified_package_coordinator_missing")

    for marker in [
        'TARGET = "aarch64-apple-darwin"',
        'DEPLOYMENT_TARGET = "13.0"',
        'ARTIFACT_STRATEGY = "apple-silicon-arm64-only"',
        "package_architecture_mismatch",
        "Contents/MacOS",
        "Contents/Frameworks",
        "install-runtime/aarch64-apple-darwin",
        "arm64",
        "x86_64",
    ]:
        assert marker in source, f"package_architecture_contract_missing:{marker}"
    assert any(
        marker in source for marker in ("os.walk(", ".rglob(", "os.scandir(")
    ), "package_architecture_audit_not_recursive"
    assert any(marker in source for marker in ("lipo", "/usr/bin/file", "Mach-O")), (
        "package_architecture_inspector_missing"
    )


def test_macpkg_08_12_14_static_report_binds_icon_and_does_not_claim_m6b_or_public_distribution() -> None:
    source = _read_text(PACKAGE_COORDINATOR, "verified_package_coordinator_missing")

    for marker in [
        "canonical_app_icon_sha256",
        "derived_icon_manifest_sha256",
        "packaged_icon_resource_sha256",
        "standalone_app_manifest_sha256",
        "dmg_sha256",
        "dmg_contained_app_manifest_sha256",
        "dmg_layout_manifest_sha256",
        "dmg_background_sha256",
        '"signing_state": "AdHoc"',
        '"notarization_state": "NotConfigured"',
        '"distribution_scope": "local_internal"',
        '"installed_runtime_state": "NotRun"',
        '"installed_icon_evidence": "NotRun"',
        '"install_evidence_chain": "NotRun"',
    ]:
        assert marker in source, f"package_report_contract_missing:{marker}"
    assert "production_distribution_ready\": true" not in source.lower(), (
        "package_public_distribution_claim_invalid"
    )
    assert "gatekeeper_warning_free\": true" not in source.lower(), (
        "package_gatekeeper_claim_invalid"
    )
    assert "fake_notification" not in source.lower(), "package_fake_notification_forbidden"
