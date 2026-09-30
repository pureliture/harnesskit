from __future__ import annotations

from dataclasses import dataclass
import binascii
import hashlib
import json
from pathlib import Path
import plistlib
import struct
import zlib

import pytest

from scripts.package import build_verified_macos_app as app_package
from scripts.package import build_verified_macos_package as package


BUILD_ID = "c" * 32
BUILD_ARGV = (
    "cargo",
    "tauri",
    "build",
    "--target",
    "aarch64-apple-darwin",
    "--bundles",
    "app,dmg",
)
BUILD_INPUTS = b'{"fixture":"verified-package-set"}\n'
STANDALONE_MANIFEST_SHA256 = "a" * 64
CANONICAL_ICON_SHA256 = "b" * 64


def _write(path: Path, body: bytes = b"fixture") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def _write_json(path: Path, value: object) -> Path:
    return _write(
        path,
        (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode(),
    )


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", binascii.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _rgba_png(
    width: int,
    height: int,
    pixel: bytes,
    *,
    density: tuple[int, int, int] | None = None,
) -> bytes:
    assert len(pixel) == 4
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    rows = b"".join(b"\x00" + pixel * width for _ in range(height))
    chunks = [_png_chunk(b"IHDR", ihdr)]
    if density is not None:
        chunks.append(_png_chunk(b"pHYs", struct.pack(">IIB", *density)))
    chunks.extend(
        [
            _png_chunk(b"IDAT", zlib.compress(rows, level=9)),
            _png_chunk(b"IEND", b""),
        ]
    )
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)


def _retina_background() -> bytes:
    return _rgba_png(1320, 800, b"\x11\x1d\x33\xff", density=(5669, 5669, 1))


def _error_code(operation: object) -> str:
    with pytest.raises(app_package.PackageVerificationError) as captured:
        operation()  # type: ignore[operator]
    return captured.value.code


@dataclass
class _Result:
    returncode: int = 0
    stdout: bytes = b""
    stderr: bytes = b""


class BuildRunner:
    def __init__(
        self,
        repo: Path,
        *,
        make_app: bool = True,
        make_dmg: bool = True,
        build_returncode: int = 0,
    ) -> None:
        self.repo = repo
        self.make_app = make_app
        self.make_dmg = make_dmg
        self.build_returncode = build_returncode
        self.calls: list[tuple[str, ...]] = []
        self.options: list[dict[str, object]] = []

    def run(self, argv: list[str], **kwargs: object) -> _Result:
        call = tuple(str(value) for value in argv)
        self.calls.append(call)
        self.options.append(kwargs)
        if call == ("cargo", "tauri", "--version"):
            return _Result(stdout=b"tauri-cli 2.0.0\n")
        if call == ("npm", "ci", "--ignore-scripts", "--prefer-offline"):
            return _Result()
        if call == ("npm", "run", "build"):
            return _Result()
        if call == BUILD_ARGV:
            if self.build_returncode == 0:
                if self.make_app:
                    app = self.repo / package.APP_RELATIVE
                    _write(app / "Contents/Resources/icon.icns", b"qualified-icon")
                if self.make_dmg:
                    _write(
                        self.repo
                        / package.DMG_DIRECTORY_RELATIVE
                        / "HarnessKit_0.1.0_aarch64.dmg",
                        b"verified-dmg",
                    )
            return _Result(returncode=self.build_returncode)
        raise AssertionError(f"unexpected command: {call!r}")


@dataclass(frozen=True)
class PackageFixture:
    repo: Path
    evidence: Path
    app_report: Path


@pytest.fixture
def package_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PackageFixture:
    repo = tmp_path / "repo"
    (repo / "src-tauri/target").mkdir(parents=True)
    _write(repo / "src-frontend/package.json", b"{}\n")
    evidence = tmp_path / "evidence"
    app_report = _write_json(
        evidence / BUILD_ID / "qualification-report.json",
        {"bundle_manifest_sha256": STANDALONE_MANIFEST_SHA256},
    )
    for relative in (
        package.ICON_MANIFEST_RELATIVE,
        package.DMG_LAYOUT_RELATIVE,
        package.DMG_BACKGROUND_RELATIVE,
    ):
        _write(repo / relative, relative.as_posix().encode())

    monkeypatch.setattr(
        package,
        "validate_package_inputs",
        lambda _repo, **_kwargs: {"canonical_source_sha256": CANONICAL_ICON_SHA256},
    )
    monkeypatch.setattr(
        app_package,
        "_prepare_build_input_contract",
        lambda _repo, _build_id: BUILD_INPUTS,
    )
    def verify_explicit_target_app(
        *_args: object,
        expected_app_relative: Path | None = None,
        **_kwargs: object,
    ) -> Path:
        assert expected_app_relative == package.APP_RELATIVE
        return app_report

    monkeypatch.setattr(
        app_package,
        "verify_built_macos_app",
        verify_explicit_target_app,
    )
    monkeypatch.setattr(
        package,
        "recursive_architecture_audit",
        lambda *_args, **_kwargs: [
            {"architecture": "arm64", "path": "Contents/MacOS/harness-desktop"}
        ],
    )

    def same_build_identity(
        _repo: Path,
        _dmg: Path,
        standalone_manifest: str,
        _runner: object,
    ) -> tuple[str, str]:
        icon = repo / package.APP_RELATIVE / "Contents/Resources/icon.icns"
        return standalone_manifest, _sha256(icon.read_bytes())

    monkeypatch.setattr(package, "mounted_dmg_identity", same_build_identity)
    return PackageFixture(repo=repo, evidence=evidence, app_report=app_report)


def test_builds_exactly_one_arm64_app_and_dmg_set_and_reports_static_boundary(
    package_fixture: PackageFixture,
) -> None:
    assert package.PRODUCT_NAME == "HarnessKit"
    assert package.APP_RELATIVE.name == "HarnessKit.app"
    runner = BuildRunner(package_fixture.repo)

    app, dmg, report_path = package.build_verified_macos_package(
        package_fixture.repo,
        package_fixture.evidence,
        runner=runner,
        build_id=BUILD_ID,
    )

    build_indexes = [
        index for index, call in enumerate(runner.calls) if call[:3] == BUILD_ARGV[:3]
    ]
    assert [runner.calls[index] for index in build_indexes] == [BUILD_ARGV]
    assert runner.calls.index(("npm", "run", "build")) < build_indexes[0]
    build_options = runner.options[build_indexes[0]]
    assert build_options["cwd"] == package_fixture.repo.resolve()
    environment = build_options["env"]
    assert isinstance(environment, dict)
    assert {
        key: environment[key]
        for key in (
            "CARGO_BUILD_TARGET",
            "HARNESS_PACKAGE_BUILD_ID",
            "HARNESS_PACKAGE_BUILD_INPUTS_SHA256",
            "HARNESS_VERIFIED_PACKAGE_BUILD",
            "MACOSX_DEPLOYMENT_TARGET",
        )
    } == {
        "CARGO_BUILD_TARGET": "aarch64-apple-darwin",
        "HARNESS_PACKAGE_BUILD_ID": BUILD_ID,
        "HARNESS_PACKAGE_BUILD_INPUTS_SHA256": _sha256(BUILD_INPUTS),
        "HARNESS_VERIFIED_PACKAGE_BUILD": "1",
        "MACOSX_DEPLOYMENT_TARGET": "13.0",
    }
    assert "CARGO_TARGET_DIR" not in environment
    assert app == (package_fixture.repo / package.APP_RELATIVE).resolve()
    assert dmg.name == "HarnessKit_0.1.0_aarch64.dmg"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["artifact_strategy"] == "apple-silicon-arm64-only"
    assert report["target"] == "aarch64-apple-darwin"
    assert report["distribution_scope"] == "local_internal"
    assert report["signing_state"] == "AdHoc"
    assert report["notarization_state"] == "NotConfigured"
    assert report["installed_runtime_state"] == "NotRun"
    assert report["installed_icon_evidence"] == "NotRun"
    assert report["install_evidence_chain"] == "NotRun"
    assert not (
        package_fixture.repo / "src-tauri/target/package-build-request.json"
    ).exists()


def test_verified_build_publishes_a_checksum_file_bound_to_the_dmg_and_report(
    package_fixture: PackageFixture,
) -> None:
    _, dmg, report_path = package.build_verified_macos_package(
        package_fixture.repo,
        package_fixture.evidence,
        runner=BuildRunner(package_fixture.repo),
        build_id=BUILD_ID,
    )

    checksum = dmg.with_name(f"{dmg.name}.sha256")
    digest = _sha256(dmg.read_bytes())
    assert checksum.read_text(encoding="ascii") == f"{digest}  {dmg.name}\n"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["dmg_sha256"] == digest
    assert report["dmg_checksum_sha256"] == _sha256(checksum.read_bytes())


def test_failed_build_does_not_leave_an_old_checksum_published(
    package_fixture: PackageFixture,
) -> None:
    checksum = _write(
        package_fixture.repo
        / package.DMG_DIRECTORY_RELATIVE
        / "HarnessKit_0.1.0_aarch64.dmg.sha256",
        b"stale checksum\n",
    )

    code = _error_code(
        lambda: package.build_verified_macos_package(
            package_fixture.repo,
            package_fixture.evidence,
            runner=BuildRunner(package_fixture.repo, build_returncode=1),
            build_id=BUILD_ID,
        )
    )

    assert code == "package_build_failed"
    assert not checksum.exists()


def test_old_checksum_symlink_is_not_followed_or_deleted(
    package_fixture: PackageFixture,
    tmp_path: Path,
) -> None:
    sentinel = _write(tmp_path / "sentinel", b"private fixture\n")
    checksum = (
        package_fixture.repo
        / package.DMG_DIRECTORY_RELATIVE
        / "HarnessKit_0.1.0_aarch64.dmg.sha256"
    )
    checksum.parent.mkdir(parents=True, exist_ok=True)
    checksum.symlink_to(sentinel)

    assert _error_code(lambda: package.clean_dmg_outputs(package_fixture.repo)) == (
        "package_critical_path_rejected"
    )
    assert checksum.is_symlink()
    assert sentinel.read_bytes() == b"private fixture\n"


@pytest.mark.parametrize(("ci", "expected"), [("1", "true"), ("0", "false")])
def test_numeric_ci_environment_is_accepted_by_the_tauri_build(
    package_fixture: PackageFixture,
    monkeypatch: pytest.MonkeyPatch,
    ci: str,
    expected: str,
) -> None:
    monkeypatch.setenv("CI", ci)
    runner = BuildRunner(package_fixture.repo)

    package.build_verified_macos_package(
        package_fixture.repo,
        package_fixture.evidence,
        runner=runner,
        build_id=BUILD_ID,
    )

    build_index = runner.calls.index(BUILD_ARGV)
    environment = runner.options[build_index]["env"]
    assert isinstance(environment, dict)
    assert environment["CI"] == expected


@pytest.mark.parametrize(
    ("make_app", "make_dmg", "expected_code"),
    [
        (False, True, "package_app_missing"),
        (True, False, "package_dmg_missing"),
    ],
)
def test_missing_member_rejects_the_whole_artifact_set_and_cleans_request(
    package_fixture: PackageFixture,
    make_app: bool,
    make_dmg: bool,
    expected_code: str,
) -> None:
    runner = BuildRunner(
        package_fixture.repo,
        make_app=make_app,
        make_dmg=make_dmg,
    )

    code = _error_code(
        lambda: package.build_verified_macos_package(
            package_fixture.repo,
            package_fixture.evidence,
            runner=runner,
            build_id=BUILD_ID,
        )
    )

    assert code == expected_code
    assert not (
        package_fixture.repo / "src-tauri/target/package-build-request.json"
    ).exists()


class MountRunner:
    def __init__(self, *, applications_target: str | None = "/Applications") -> None:
        self.applications_target = applications_target
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: list[str], **_kwargs: object) -> _Result:
        call = tuple(str(value) for value in argv)
        self.calls.append(call)
        if call[:3] == ("/usr/bin/hdiutil", "attach", "-readonly"):
            mountpoint = Path(call[call.index("-mountpoint") + 1])
            app = mountpoint / "HarnessKit.app"
            _write(app / "Contents/Resources/icon.icns", b"mounted-icon")
            _write_json(
                app / "Contents/Info.plist.json",
                {"fixture": True},
            )
            if self.applications_target is not None:
                (mountpoint / "Applications").symlink_to(
                    self.applications_target,
                    target_is_directory=True,
                )
            return _Result()
        if call[:2] == ("/usr/bin/hdiutil", "detach"):
            return _Result()
        raise AssertionError(f"unexpected command: {call!r}")


def test_dmg_contained_app_manifest_must_equal_the_standalone_build(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    dmg = _write(tmp_path / "HarnessKit.dmg")
    runner = MountRunner()

    code = _error_code(
        lambda: package.mounted_dmg_identity(
            repo,
            dmg,
            "0" * 64,
            runner,
        )
    )

    assert code == "package_build_identity_mismatch"
    assert any(call[:2] == ("/usr/bin/hdiutil", "detach") for call in runner.calls)


@pytest.mark.parametrize(
    ("declared_mode", "expected_error"),
    [(0o775, "package_runtime_tree_mismatch"), (0o755, None)],
)
def test_dmg_runtime_files_must_match_the_embedded_manifest(
    tmp_path: Path, declared_mode: int, expected_error: str | None
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    dmg = _write(tmp_path / "HarnessKit.dmg")

    def populate(app: Path) -> None:
        _write(app / "Contents/Resources/icon.icns", b"mounted-icon")
        _write_json(app / "Contents/Info.plist.json", {"fixture": True})
        _write(
            app / "Contents/Info.plist",
            plistlib.dumps(
                {"CFBundleIdentifier": package.BUNDLE_IDENTIFIER, "CFBundleExecutable": package.MAIN_BINARY_NAME}
            ),
        )
        runtime = app / "Contents/Resources/install-runtime/aarch64-apple-darwin"
        executable = _write(runtime / "python/bin/python3", b"runtime-binary")
        executable.chmod(0o755)
        _write_json(
            runtime / "runtime-manifest.json",
            {
                "schema_version": 1,
                "target": package.TARGET,
                "entries": [
                    {
                        "path": "python/bin/python3",
                        "sha256": _sha256(b"runtime-binary"),
                        "size": len(b"runtime-binary"),
                        "mode": declared_mode,
                    }
                ],
            },
        )

    reference = tmp_path / "reference/HarnessKit.app"
    populate(reference)
    manifest_sha256 = _sha256(
        app_package._json_bytes(app_package._canonical_bundle_manifest(reference))
    )

    class RuntimeMountRunner(MountRunner):
        def run(self, argv: list[str], **kwargs: object) -> _Result:
            result = super().run(argv, **kwargs)
            if tuple(argv[:3]) == ("/usr/bin/hdiutil", "attach", "-readonly"):
                mountpoint = Path(argv[argv.index("-mountpoint") + 1])
                populate(mountpoint / "HarnessKit.app")
            return result

    runner = RuntimeMountRunner()
    if expected_error is not None:
        assert _error_code(
            lambda: package.mounted_dmg_identity(repo, dmg, manifest_sha256, runner)
        ) == expected_error
    else:
        assert package.mounted_dmg_identity(repo, dmg, manifest_sha256, runner) == (
            manifest_sha256,
            _sha256(b"mounted-icon"),
        )
    assert any(call[:2] == ("/usr/bin/hdiutil", "detach") for call in runner.calls)


@pytest.mark.parametrize("applications_target", [None, "/tmp/not-applications"])
def test_dmg_applications_alias_must_be_exact(
    tmp_path: Path,
    applications_target: str | None,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    dmg = _write(tmp_path / "HarnessKit.dmg")

    assert (
        _error_code(
            lambda: package.mounted_dmg_identity(
                repo,
                dmg,
                "0" * 64,
                MountRunner(applications_target=applications_target),
            )
        )
        == "package_dmg_layout_invalid"
    )


def _valid_layout(background: bytes) -> dict[str, object]:
    return {
        "schema_version": 2,
        "window": {"height": 400, "width": 660},
        "items": {
            "app": {"name": "HarnessKit.app", "x": 180, "y": 190},
            "applications_alias": {
                "name": "Applications",
                "target": "/Applications",
                "x": 480,
                "y": 190,
            },
        },
        "instruction": {"text": "HarnessKit을 Applications로 드래그"},
        "background": {
            "dpi": {"x": 144, "y": 144},
            "logical_dimensions": {"height": 400, "width": 660},
            "path": package.DMG_BACKGROUND_RELATIVE.as_posix(),
            "pixel_dimensions": {"height": 800, "width": 1320},
            "sha256": _sha256(background),
        },
    }


def test_tauri_dmg_layout_must_match_the_approved_repo_layout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    background = _retina_background()
    _write(repo / package.DMG_BACKGROUND_RELATIVE, background)
    _write_json(repo / package.DMG_LAYOUT_RELATIVE, _valid_layout(background))
    _write_json(
        repo / "src-tauri/tauri.conf.json",
        {
            "productName": "HarnessKit",
            "mainBinaryName": "harness-desktop",
            "identifier": "io.github.pureliture.harnesskit",
            "bundle": {
                "targets": ["app", "dmg"],
                "macOS": {
                    "minimumSystemVersion": "13.0",
                    "signingIdentity": "-",
                    "dmg": {
                        "windowSize": {"width": 1, "height": 1},
                        "appPosition": {"x": 1, "y": 1},
                        "applicationFolderPosition": {"x": 1, "y": 1},
                        "background": "wrong.png",
                    },
                },
            },
        },
    )
    monkeypatch.setattr(
        package,
        "qualify_visual_assets",
        lambda _repo: {"canonical_source_sha256": CANONICAL_ICON_SHA256},
    )

    assert (
        _error_code(lambda: package.validate_package_inputs(repo))
        == "package_dmg_layout_invalid"
    )


class FreshRasterRunner:
    def __init__(self, fresh_preview: bytes) -> None:
        self.fresh_preview = fresh_preview
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: list[str], **_kwargs: object) -> _Result:
        call = tuple(str(value) for value in argv)
        self.calls.append(call)
        assert call[:4] == ("/usr/bin/qlmanage", "-t", "-s", "1320")
        output_directory = Path(call[call.index("-o") + 1])
        source = Path(call[-1])
        _write(output_directory / f"{source.name}.png", self.fresh_preview)
        return _Result()


def _write_valid_package_inputs(repo: Path, background: bytes) -> None:
    _write(repo / package.DMG_BACKGROUND_RELATIVE, background)
    _write(
        repo / package.DMG_BACKGROUND_SVG_RELATIVE,
        b'<svg xmlns="http:' b'//www.w3.org/2000/svg" width="660" height="400"/>',
    )
    _write_json(repo / package.DMG_LAYOUT_RELATIVE, _valid_layout(background))
    _write_json(
        repo / "src-tauri/tauri.conf.json",
        {
            "productName": "HarnessKit",
            "mainBinaryName": "harness-desktop",
            "identifier": "io.github.pureliture.harnesskit",
            "bundle": {
                "targets": ["app", "dmg"],
                "macOS": {
                    "minimumSystemVersion": "13.0",
                    "signingIdentity": "-",
                    "dmg": {
                        "background": "../assets/packaging/macos/dmg-background.png",
                        "windowSize": {"width": 660, "height": 400},
                        "appPosition": {"x": 180, "y": 190},
                        "applicationFolderPosition": {"x": 480, "y": 190},
                    },
                },
            },
        },
    )


def _stub_visual_qualification(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        package,
        "qualify_visual_assets",
        lambda _repo: {"canonical_source_sha256": CANONICAL_ICON_SHA256},
    )


def test_dmg_background_must_equal_a_fresh_raster_of_the_repo_svg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_valid_package_inputs(repo, _retina_background())
    runner = FreshRasterRunner(
        _rgba_png(1320, 1320, b"\xff\x00\x00\xff")
    )
    monkeypatch.setattr(package, "SubprocessRunner", lambda: runner)
    _stub_visual_qualification(monkeypatch)

    assert (
        _error_code(lambda: package.validate_package_inputs(repo))
        == "package_dmg_background_derivation_mismatch"
    )
    assert len(runner.calls) == 1


def test_dmg_background_accepts_the_exact_fresh_raster_pixels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    _write_valid_package_inputs(repo, _retina_background())
    runner = FreshRasterRunner(
        _rgba_png(1320, 1320, b"\x11\x1d\x33\xff")
    )
    monkeypatch.setattr(package, "SubprocessRunner", lambda: runner)
    _stub_visual_qualification(monkeypatch)

    report = package.validate_package_inputs(repo)

    assert report["dmg_background_derivation"] == {
        "pixel_sha256": _sha256(b"\x11\x1d\x33\xff" * 1320 * 800),
        "renderer": "macos-quicklook-1320-top-crop-v1",
        "source_sha256": _sha256(
            b'<svg xmlns="http:' b'//www.w3.org/2000/svg" width="660" height="400"/>'
        ),
    }
    assert len(runner.calls) == 1


class ArchitectureRunner:
    def __init__(self, rejected_name: str) -> None:
        self.rejected_name = rejected_name
        self.calls: list[tuple[str, ...]] = []

    def run(self, argv: list[str], **_kwargs: object) -> _Result:
        call = tuple(str(value) for value in argv)
        self.calls.append(call)
        assert call[:2] == ("/usr/bin/lipo", "-archs")
        architecture = "x86_64" if Path(call[-1]).name == self.rejected_name else "arm64"
        return _Result(stdout=(architecture + "\n").encode())


@pytest.mark.parametrize("bad_architecture", ["fat", "x86_64"])
def test_recursive_architecture_audit_rejects_nested_fat_and_x86_64(
    tmp_path: Path,
    bad_architecture: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    app = tmp_path / "HarnessKit.app"
    thin_macho = b"\xcf\xfa\xed\xfe" + b"fixture"
    _write(app / "Contents/MacOS/harness-desktop", thin_macho)
    _write(
        app
        / "Contents/Resources/install-runtime/aarch64-apple-darwin/python/bin/python3",
        thin_macho,
    )
    nested = app / "Contents/Frameworks/Nested.framework/Versions/A/bad.dylib"
    _write(
        nested,
        b"\xca\xfe\xba\xbe" + b"fixture"
        if bad_architecture == "fat"
        else thin_macho,
    )
    runner = ArchitectureRunner("bad.dylib")

    assert (
        _error_code(
            lambda: package.recursive_architecture_audit(app, runner, repo)
        )
        == "package_architecture_mismatch"
    )
    if bad_architecture == "x86_64":
        assert any(call[-1] == str(nested) for call in runner.calls)
    else:
        assert all(call[-1] != str(nested) for call in runner.calls)


def test_published_build_request_is_cleaned_when_prebuild_dmg_cleanup_fails(
    package_fixture: PackageFixture,
) -> None:
    _write(package_fixture.repo / package.DMG_DIRECTORY_RELATIVE, b"not-a-directory")
    request = package_fixture.repo / "src-tauri/target/package-build-request.json"

    code = _error_code(
        lambda: package.build_verified_macos_package(
            package_fixture.repo,
            package_fixture.evidence,
            runner=BuildRunner(package_fixture.repo),
            build_id=BUILD_ID,
        )
    )

    assert code == "package_critical_path_rejected"
    assert not request.exists(), "package_build_request_cleanup_failed"
