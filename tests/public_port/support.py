from __future__ import annotations

import subprocess
from pathlib import Path

from scripts.public_port.manifest import PublicRemoteDefault, build_manifest
from scripts.public_port.policy import PublicPortPolicy, parse_policy


PUBLIC_REPOSITORY_URL = "https://github.com/pureliture/harnesskit.git"


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def commit_repo(repo: Path, files: dict[str, str], message: str) -> str:
    repo.mkdir(parents=True)
    git(repo, "init", "--initial-branch=main")
    git(repo, "config", "user.name", "HarnessKit Contributors")
    git(repo, "config", "user.email", "contributors@users.noreply.github.com")
    for relative_path, contents in files.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    git(repo, "add", "--all")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def policy(
    *,
    command: list[str] | None = None,
    generated_output: str = "generated/catalog.json",
) -> PublicPortPolicy:
    return parse_policy(
        {
            "schema_version": 1,
            "target_repository_url": PUBLIC_REPOSITORY_URL,
            "retain_public_baseline": True,
            "ported_authored": {
                "include": ["app/**", "generator/**"],
                "exclude": [generated_output],
            },
            "intentional_removals": ["obsolete.txt"],
            "generated_rules": [
                {
                    "id": "catalog",
                    "command": command
                    or [
                        "python3",
                        "-c",
                        (
                            "from pathlib import Path; "
                            "p=Path('generated/catalog.json'); "
                            "p.parent.mkdir(parents=True, exist_ok=True); "
                            "p.write_text('{\\\"ok\\\":true}\\n', encoding='utf-8')"
                        ),
                    ],
                    "working_directory": ".",
                    "inputs": ["generator/input.txt"],
                    "outputs": [generated_output],
                }
            ],
        }
    )


def repositories(tmp_path: Path) -> tuple[Path, Path, str]:
    baseline = tmp_path / "baseline"
    baseline_commit = commit_repo(
        baseline,
        {
            "README.md": "public baseline\n",
            "keep.txt": "keep\n",
            "obsolete.txt": "remove me\n",
            "app/main.txt": "old app\n",
        },
        "public baseline",
    )
    git(baseline, "remote", "add", "origin", PUBLIC_REPOSITORY_URL)

    source = tmp_path / "source"
    commit_repo(
        source,
        {
            "app/main.txt": "new app\n",
            "generator/input.txt": "input-v1\n",
            "generated/catalog.json": '{"ok":true}\n',
            "private/operator.txt": "must not be projected\n",
        },
        "private source fixture",
    )
    return baseline, source, baseline_commit


def manifest_fixture(tmp_path: Path, *, selected_policy: PublicPortPolicy | None = None):
    baseline, source, baseline_commit = repositories(tmp_path)
    selected_policy = selected_policy or policy()
    manifest = build_manifest(
        selected_policy,
        baseline_root=baseline,
        baseline_commit=baseline_commit,
        source_root=source,
        remote_default_reader=lambda _url: PublicRemoteDefault("main", baseline_commit),
    )
    return baseline, source, manifest


def remote_reader_for(manifest):
    baseline = manifest["baseline"]
    return lambda _url: PublicRemoteDefault(baseline["default_branch"], baseline["commit"])
