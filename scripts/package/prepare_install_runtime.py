from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable


ALLOWED_DOWNLOAD_HOSTS = {
    "github.com",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
    "files.pythonhosted.org",
}
DOWNLOAD_LIMIT = 128 * 1024 * 1024


def _sha256_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RuntimeError("runtime_archive_path_rejected")
    return path


def _validated_https_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_DOWNLOAD_HOSTS:
        raise RuntimeError("runtime_download_url_rejected")
    if parsed.username or parsed.password or parsed.fragment:
        raise RuntimeError("runtime_download_url_rejected")
    return value


def _download(url: str, expected_sha256: str) -> bytes:
    request = urllib.request.Request(
        _validated_https_url(url),
        headers={"User-Agent": "harness-desktop-runtime-prepare/1"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        _validated_https_url(response.geturl())
        body = response.read(DOWNLOAD_LIMIT + 1)
    if len(body) > DOWNLOAD_LIMIT or _sha256_bytes(body) != expected_sha256:
        raise RuntimeError("runtime_download_hash_mismatch")
    return body


def _copy_stream(source: BinaryIO, destination: Path, mode: int) -> None:
    destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    with destination.open("xb") as output:
        shutil.copyfileobj(source, output, length=1024 * 1024)
    destination.chmod(mode & 0o755)


def _extract_python(archive: Path, prefix: str, destination: Path) -> None:
    normalized_prefix = prefix.rstrip("/") + "/"
    with tarfile.open(archive, mode="r:gz") as bundle:
        links: list[tuple[Path, PurePosixPath]] = []
        for member in bundle.getmembers():
            if not member.name.startswith(normalized_prefix):
                continue
            suffix = member.name[len(normalized_prefix) :]
            if not suffix:
                continue
            relative = _safe_relative(suffix)
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(mode=member.mode & 0o755 or 0o755, parents=True, exist_ok=True)
                continue
            if member.issym() or member.islnk():
                link = PurePosixPath(member.linkname)
                linked = link if link.is_absolute() else relative.parent / link
                linked = _safe_relative(linked.as_posix().lstrip("/"))
                links.append((target, linked))
                continue
            if not member.isfile():
                raise RuntimeError("runtime_archive_special_file_rejected")
            source = bundle.extractfile(member)
            if source is None:
                raise RuntimeError("runtime_archive_member_unavailable")
            _copy_stream(source, target, member.mode or 0o644)
        pending = links
        while pending:
            deferred: list[tuple[Path, PurePosixPath]] = []
            progressed = False
            for target, linked in pending:
                source = destination.joinpath(*linked.parts)
                if not source.is_file() or source.is_symlink():
                    deferred.append((target, linked))
                    continue
                target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                target.chmod(stat.S_IMODE(source.stat().st_mode))
                progressed = True
            if not progressed and deferred:
                raise RuntimeError("runtime_archive_link_target_rejected")
            pending = deferred


def _wheel_is_symlink(info: zipfile.ZipInfo) -> bool:
    return stat.S_ISLNK((info.external_attr >> 16) & 0o170000)


def _extract_wheel(archive: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as wheel:
        for info in wheel.infolist():
            relative = _safe_relative(info.filename.rstrip("/"))
            target = destination.joinpath(*relative.parts)
            if info.is_dir():
                target.mkdir(mode=0o755, parents=True, exist_ok=True)
                continue
            if _wheel_is_symlink(info):
                raise RuntimeError("runtime_wheel_symlink_rejected")
            mode = (info.external_attr >> 16) & 0o777 or 0o644
            with wheel.open(info) as source:
                _copy_stream(source, target, mode)


def _manifest_entries(root: Path) -> list[dict[str, object]]:
    entries = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise RuntimeError("runtime_output_symlink_rejected")
        if not stat.S_ISREG(metadata.st_mode):
            continue
        entries.append(
            {
                "path": path.relative_to(root).as_posix(),
                "sha256": _sha256_file(path),
                "size": metadata.st_size,
                "mode": stat.S_IMODE(metadata.st_mode),
            }
        )
    return entries


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o644)


def _runtime_probe(python: Path, expected_version: str, expected_machine: str) -> None:
    result = subprocess.run(
        [
            str(python),
            "-I",
            "-B",
            "-S",
            "-c",
            "import platform,sys;print(platform.machine());print(platform.python_version())",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
        timeout=30,
        check=False,
    )
    lines = result.stdout.decode("utf-8", "strict").splitlines()
    if result.returncode != 0 or lines != [expected_machine, expected_version]:
        raise RuntimeError("runtime_architecture_probe_failed")


def _verify_macho_signatures(root: Path) -> None:
    macho_magics = {
        b"\xcf\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
    }
    for path in root.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as stream:
            if stream.read(4) not in macho_magics:
                continue
        result = subprocess.run(
            ["/usr/bin/codesign", "--verify", "--strict", str(path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env={"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"},
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("runtime_codesign_verification_failed")


def _existing_runtime_is_verified(
    repo_root: Path,
    final: Path,
    lock: dict[str, object],
    lock_bytes: bytes,
) -> bool:
    try:
        root_metadata = final.lstat()
        if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
            return False
        manifest_path = final / "runtime-manifest.json"
        manifest_metadata = manifest_path.lstat()
        expected_manifest_sha256 = (
            repo_root / "src-tauri/install-runtime.manifest.sha256"
        ).read_text(encoding="ascii").strip()
        if (
            stat.S_ISLNK(manifest_metadata.st_mode)
            or not stat.S_ISREG(manifest_metadata.st_mode)
            or stat.S_IMODE(manifest_metadata.st_mode) != 0o644
            or _sha256_file(manifest_path) != expected_manifest_sha256
        ):
            return False
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            manifest.get("schema_version") != 1
            or manifest.get("runtime_id") != lock["runtime_id"]
            or manifest.get("target") != lock["target"]
            or manifest.get("lock_sha256") != _sha256_bytes(lock_bytes)
        ):
            return False
        expected = {entry["path"]: entry for entry in manifest["entries"]}
        actual: dict[str, Path] = {}
        for path in final.rglob("*"):
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                return False
            if stat.S_ISDIR(metadata.st_mode):
                continue
            if not stat.S_ISREG(metadata.st_mode):
                return False
            relative = path.relative_to(final).as_posix()
            if relative != "runtime-manifest.json":
                actual[relative] = path
        if set(actual) != set(expected):
            return False
        for relative, entry in expected.items():
            path = actual[relative]
            metadata = path.lstat()
            if (
                metadata.st_size != entry["size"]
                or stat.S_IMODE(metadata.st_mode) != entry["mode"]
                or _sha256_file(path) != entry["sha256"]
            ):
                return False
        python = final / "python/bin/python3"
        _runtime_probe(python, lock["python"]["version"], "arm64")
        _verify_macho_signatures(final / "python")
        return True
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError, RuntimeError):
        return False


def _prepare(repo_root: Path) -> Path:
    lock_path = repo_root / "src-tauri/install-runtime.lock.json"
    lock_bytes = lock_path.read_bytes()
    lock = json.loads(lock_bytes)
    target = lock["target"]
    if target != "aarch64-apple-darwin":
        raise RuntimeError("runtime_target_unsupported")
    output_parent = repo_root / "src-tauri/target/install-runtime"
    output_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    final = output_parent / target
    if _existing_runtime_is_verified(repo_root, final, lock, lock_bytes):
        return final
    staging = Path(tempfile.mkdtemp(prefix=f".{target}-", dir=output_parent))
    try:
        downloads = staging / ".downloads"
        downloads.mkdir(mode=0o700)
        python_archive = downloads / "python.tar.gz"
        python_archive.write_bytes(
            _download(lock["python"]["url"], lock["python"]["sha256"])
        )
        _extract_python(
            python_archive,
            lock["python"]["archive_prefix"],
            staging / "python",
        )
        vendor = staging / "vendor"
        vendor.mkdir(mode=0o755)
        for index, wheel in enumerate(lock["wheels"]):
            archive = downloads / f"wheel-{index}.whl"
            archive.write_bytes(_download(wheel["url"], wheel["sha256"]))
            _extract_wheel(archive, vendor)
        shutil.rmtree(downloads)

        entrypoint_source = repo_root / lock["entrypoint"]["path"]
        if _sha256_file(entrypoint_source) != lock["entrypoint"]["sha256"]:
            raise RuntimeError("runtime_entrypoint_hash_mismatch")
        shutil.copyfile(entrypoint_source, staging / "install_entry.py")
        (staging / "install_entry.py").chmod(0o644)

        python = staging / "python/bin/python3"
        metadata = python.lstat()
        if not stat.S_ISREG(metadata.st_mode) or not metadata.st_mode & 0o111:
            raise RuntimeError("runtime_python_unavailable")
        _runtime_probe(python, lock["python"]["version"], "arm64")
        _verify_macho_signatures(staging / "python")

        sbom = {
            "schema_version": 1,
            "runtime_id": lock["runtime_id"],
            "python": {
                "distribution": lock["python"]["distribution"],
                "version": lock["python"]["version"],
            },
            "packages": [
                {"name": wheel["name"], "version": wheel["version"]}
                for wheel in lock["wheels"]
            ],
            "licenses": lock["licenses"],
        }
        _write_json(staging / "SBOM.json", sbom)
        manifest = {
            "schema_version": 1,
            "runtime_id": lock["runtime_id"],
            "target": target,
            "lock_sha256": _sha256_bytes(lock_bytes),
            "entries": _manifest_entries(staging),
        }
        manifest_path = staging / "runtime-manifest.json"
        _write_json(manifest_path, manifest)
        expected_manifest_sha256 = (
            repo_root / "src-tauri/install-runtime.manifest.sha256"
        ).read_text(encoding="ascii").strip()
        if _sha256_file(manifest_path) != expected_manifest_sha256:
            raise RuntimeError("runtime_manifest_hash_mismatch")

        backup = output_parent / f".{target}.previous"
        if backup.exists():
            shutil.rmtree(backup)
        if final.exists():
            final.rename(backup)
        staging.rename(final)
        if backup.exists():
            shutil.rmtree(backup)
        return final
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def _verify_prepared_runtime(repo_root: Path) -> Path:
    lock_path = repo_root / "src-tauri/install-runtime.lock.json"
    lock_bytes = lock_path.read_bytes()
    lock = json.loads(lock_bytes)
    target = lock["target"]
    if target != "aarch64-apple-darwin":
        raise RuntimeError("runtime_target_unsupported")
    final = repo_root / "src-tauri/target/install-runtime" / target
    if not _existing_runtime_is_verified(repo_root, final, lock, lock_bytes):
        raise RuntimeError("prepared_runtime_verification_failed")
    return final


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    if args.verify_only:
        _verify_prepared_runtime(repo_root)
    else:
        _prepare(repo_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
