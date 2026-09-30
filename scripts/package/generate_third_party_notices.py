from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path


class NoticeError(ValueError):
    pass


_SECTION = {
    "python": "Python",
    "rust": "Rust",
    "typescript": "TypeScript",
}


def build_inventory(packages: list[dict]) -> dict:
    normalized = []
    for package in packages:
        rights = package.get("rights")
        if rights != "verified":
            raise NoticeError("rights_unverified")
        license_text = str(package.get("license_text") or "").strip()
        if not license_text:
            raise NoticeError("license_text_missing")
        ecosystem = package.get("ecosystem")
        if ecosystem not in _SECTION:
            raise NoticeError("ecosystem_unsupported")
        name = str(package.get("name") or "").strip()
        version = str(package.get("version") or "").strip()
        license_id = str(package.get("license") or "").strip()
        copyright_notice = str(package.get("copyright") or "").strip()
        source = str(package.get("source") or "").strip()
        if not all((name, version, license_id, copyright_notice, source)):
            raise NoticeError("identity_incomplete")
        if package.get("shipped") is not True:
            raise NoticeError("unshipped_package")
        normalized.append(
            {
                "copyright": copyright_notice,
                "ecosystem": ecosystem,
                "license": license_id,
                "license_text": license_text,
                "name": name,
                "rights": "verified",
                "shipped": True,
                "source": source,
                "version": version,
            }
        )
    if not normalized:
        raise NoticeError("inventory_empty")
    normalized.sort(key=lambda item: (item["ecosystem"], item["name"], item["version"]))
    digest = hashlib.sha256(
        json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {"schema_version": 1, "sha256": digest, "packages": normalized}


def render_notices(inventory: dict) -> tuple[str, str]:
    packages = inventory["packages"]
    digest = inventory["sha256"]
    markdown = [
        "# Third-party notices",
        "",
        "This file lists shipped third-party packages. It does not grant trademark rights and does not replace the HarnessKit brand policy.",
        "",
        f"Inventory SHA-256: `{digest}`",
        "",
    ]
    html_parts = [
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head><meta charset=\"utf-8\"><title>Third-party notices</title></head>",
        "<body>",
        '<p><a href="../index.html">&larr; HarnessKit</a></p>',
        "<h1>Third-party notices</h1>",
        "<p>This file lists shipped third-party packages. It does not grant trademark rights and does not replace the HarnessKit brand policy.</p>",
        f"<p>Inventory SHA-256: <code>{digest}</code></p>",
    ]
    for ecosystem in ("python", "rust", "typescript"):
        section = _SECTION[ecosystem]
        markdown.extend([f"## {section}", ""])
        html_parts.append(f"<h2>{section}</h2>")
        html_parts.append("<ul>")
        for item in packages:
            if item["ecosystem"] != ecosystem:
                continue
            markdown.append(
                f"- {item['name']} {item['version']} — {item['license']}. {item['copyright']}. Source: {item['source']}"
            )
            markdown.append("")
            markdown.append("```text")
            markdown.append(item["license_text"])
            markdown.append("```")
            markdown.append("")
            html_parts.append(
                "<li>"
                f"<strong>{html.escape(item['name'])}</strong> "
                f"{html.escape(item['version'])} — {html.escape(item['license'])}. "
                f"{html.escape(item['copyright'])}. "
                f"Source: {html.escape(item['source'])}"
                f"<pre>{html.escape(item['license_text'])}</pre>"
                "</li>"
            )
        html_parts.append("</ul>")
    html_parts.extend(["</body>", "</html>", ""])
    return "\n".join(markdown), "\n".join(html_parts)


_LICENSE_FILE_TOKENS = ("license", "licence", "copying", "copyright", "notice", "unlicense")
_RUST_TARGET = "aarch64-apple-darwin"
_MARKDOWN_OUTPUT = Path("THIRD_PARTY_NOTICES.md")
_HTML_OUTPUT = Path("src-tauri/resources/legal/THIRD_PARTY_NOTICES.html")
_FRONTEND_HTML_OUTPUT = Path("src-frontend/legal/third-party-notices.html")


def _license_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file()
        and any(token in path.name.lower() for token in _LICENSE_FILE_TOKENS)
        and path.suffix.lower() not in {".rs", ".py", ".js", ".toml", ".json"}
    )


def _read_texts(paths: list[Path]) -> str:
    return "\n\n".join(
        path.read_text(encoding="utf-8", errors="replace").strip() for path in paths
    )


def _copyright_line(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("*# ").strip()
        if stripped.lower().startswith("copyright") and any(ch.isdigit() for ch in stripped):
            return stripped
    return fallback


def _load_sources(root: Path) -> dict:
    import yaml

    return yaml.safe_load(
        (root / "assets/legal/notice-sources.yml").read_text(encoding="utf-8")
    )


def _typescript_packages(root: Path) -> list[dict]:
    frontend = root / "src-frontend"
    lock = json.loads((frontend / "package-lock.json").read_text(encoding="utf-8"))
    packages = []
    for lock_path, resolution in lock["packages"].items():
        if not lock_path or resolution.get("dev") is True:
            continue
        directory = frontend / lock_path
        manifest = json.loads((directory / "package.json").read_text(encoding="utf-8"))
        files = _license_files(directory)
        text = _read_texts(files)
        author = manifest.get("author")
        if isinstance(author, dict):
            author = author.get("name")
        fallback = f"Copyright (c) {author}" if author else f"Copyright (c) {manifest['name']} authors"
        repository = manifest.get("repository")
        if isinstance(repository, dict):
            repository = repository.get("url")
        packages.append(
            {
                "ecosystem": "typescript",
                "name": manifest["name"],
                "version": resolution["version"],
                "license": resolution["license"],
                "copyright": _copyright_line(text, fallback),
                "license_text": text,
                "source": repository or manifest.get("homepage") or f"https://www.npmjs.com/package/{manifest['name']}",
                "shipped": True,
                "rights": "verified" if text else "unknown",
            }
        )
    return packages


def _cargo_registry() -> Path:
    import os

    home = Path(os.environ.get("CARGO_HOME", Path.home() / ".cargo"))
    registries = sorted((home / "registry/src").iterdir())
    if not registries:
        raise NoticeError("cargo_registry_missing")
    return registries[0]


def _rust_shipped(root: Path) -> list[tuple[str, str, str, str]]:
    import subprocess

    result = subprocess.run(
        [
            "cargo",
            "tree",
            "--locked",
            "--target",
            _RUST_TARGET,
            "-e",
            "normal",
            "--prefix",
            "none",
            "--format",
            "{p}|{l}|{r}",
        ],
        cwd=root / "src-tauri",
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise NoticeError("cargo_tree_failed")
    rows = set()
    for line in result.stdout.splitlines():
        line = line.replace(" (*)", "").strip()
        if not line or "|" not in line:
            continue
        spec, license_id, repository = line.split("|", 2)
        parts = spec.split()
        name, version = parts[0], parts[1].lstrip("v")
        if name == "harness-desktop":
            continue
        rows.add((name, version, license_id, repository))
    return sorted(rows)


def _crate_authors(directory: Path, name: str) -> str:
    import tomllib

    try:
        manifest = tomllib.loads((directory / "Cargo.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        manifest = {}
    authors = manifest.get("package", {}).get("authors") or []
    names = [author.split("<")[0].strip() for author in authors if author.strip()]
    if names:
        return "Copyright (c) " + ", ".join(names)
    return f"Copyright (c) the {name} contributors"


def _rust_packages(root: Path, sources: dict) -> list[dict]:
    registry = _cargo_registry()
    texts_root = root / "assets/legal/license-texts"
    overrides = {}
    for entry in sources.get("rust_license_overrides", []):
        for crate in entry["crates"]:
            overrides[crate] = entry
    packages = []
    for name, version, license_id, repository in _rust_shipped(root):
        directory = registry / f"{name}-{version}"
        override = overrides.get(name)
        files = _license_files(directory)
        if files:
            text = _read_texts(files)
            copyright_notice = _copyright_line(text, _crate_authors(directory, name))
            source = repository or f"https://crates.io/crates/{name}"
        elif override:
            text = _read_texts([texts_root / file for file in override["files"]])
            copyright_notice = override["copyright"]
            source = override["source"]
        else:
            text = ""
            copyright_notice = f"Copyright (c) {name} authors"
            source = repository or f"https://crates.io/crates/{name}"
        packages.append(
            {
                "ecosystem": "rust",
                "name": name,
                "version": version,
                "license": license_id,
                "copyright": copyright_notice,
                "license_text": text,
                "source": source,
                "shipped": True,
                "rights": "verified" if text else "unknown",
            }
        )
    return packages


def _python_packages(root: Path, sources: dict) -> list[dict]:
    runtime = root / "src-tauri/target/install-runtime" / _RUST_TARGET
    if not runtime.is_dir():
        raise NoticeError("python_runtime_missing")
    texts_root = root / "assets/legal/license-texts"
    packages = []
    for component in sources.get("python_runtime_components", []):
        text = _read_texts([texts_root / component["file"]])
        packages.append(
            {
                "ecosystem": "python",
                "name": component["name"],
                "version": str(component["version"]),
                "license": component["license"],
                "copyright": _copyright_line(text, f"Copyright (c) {component['name']} authors"),
                "license_text": text,
                "source": component["source"],
                "shipped": True,
                "rights": "verified" if text else "unknown",
            }
        )
    seen = set()
    for dist_info in sorted(runtime.rglob("*.dist-info")):
        metadata_path = dist_info / "METADATA"
        if not metadata_path.is_file():
            continue
        metadata = {}
        for line in metadata_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                break
            if ": " in line:
                key, value = line.split(": ", 1)
                metadata.setdefault(key, value)
        name, version = metadata.get("Name"), metadata.get("Version")
        if not name or not version or (name.lower(), version) in seen:
            continue
        seen.add((name.lower(), version))
        files = sorted(
            path
            for path in dist_info.rglob("*")
            if path.is_file() and any(token in path.name.lower() for token in _LICENSE_FILE_TOKENS)
        )
        text = _read_texts(files)
        author = metadata.get("Author") or metadata.get("Author-email", "").split("<")[0].strip()
        urls = [
            value.split(", ", 1)[-1]
            for key, value in (
                line.split(": ", 1)
                for line in metadata_path.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.startswith("Project-URL: ")
            )
        ]
        packages.append(
            {
                "ecosystem": "python",
                "name": name,
                "version": version,
                "license": metadata.get("License-Expression") or metadata.get("License") or "see license text",
                "copyright": _copyright_line(text, f"Copyright (c) {author or name + ' authors'}"),
                "license_text": text,
                "source": metadata.get("Home-page") or (urls[0] if urls else f"https://pypi.org/project/{name}/"),
                "shipped": True,
                "rights": "verified" if text else "unknown",
            }
        )
    return packages


def collect_repository_packages(root: Path) -> list[dict]:
    sources = _load_sources(root)
    return [
        *_typescript_packages(root),
        *_rust_packages(root, sources),
        *_python_packages(root, sources),
    ]


def load_repository_inventory(root: Path) -> dict:
    return build_inventory(collect_repository_packages(root))


def write_repository_notices(root: Path) -> dict:
    inventory = load_repository_inventory(root)
    markdown, html_body = render_notices(inventory)
    for relative, body in (
        (_MARKDOWN_OUTPUT, markdown),
        (_HTML_OUTPUT, html_body),
        (_FRONTEND_HTML_OUTPUT, html_body),
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return inventory


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Generate HarnessKit third-party notices")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--check", action="store_true", help="fail if generated notices are stale")
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()
    try:
        if args.check:
            markdown, html_body = render_notices(load_repository_inventory(root))
            stale = [
                str(relative)
                for relative, body in (
                    (_MARKDOWN_OUTPUT, markdown),
                    (_HTML_OUTPUT, html_body),
                    (_FRONTEND_HTML_OUTPUT, html_body),
                )
                if not (root / relative).is_file()
                or (root / relative).read_text(encoding="utf-8") != body
            ]
            if stale:
                print("notices_stale: " + ", ".join(stale), file=sys.stderr)
                return 1
            print("notices_current")
            return 0
        inventory = write_repository_notices(root)
    except NoticeError as error:
        print(str(error), file=sys.stderr)
        return 1
    counts = {}
    for item in inventory["packages"]:
        counts[item["ecosystem"]] = counts.get(item["ecosystem"], 0) + 1
    print(json.dumps({"sha256": inventory["sha256"], "counts": counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
