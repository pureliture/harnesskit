from __future__ import annotations

import copy
from pathlib import Path

import pytest

from scripts.public_port.manifest import canonical_json, sha256_bytes
from scripts.public_port.materialize import materialize
from scripts.public_port.regenerate import RegenerateError, regenerate
from tests.public_port.support import manifest_fixture, policy, remote_reader_for


def _command(body: str) -> list[str]:
    return ["python3", "-c", body]


def _runner(body: str):
    def run(_rule, root: Path) -> None:
        namespace = {"Path": Path, "root": root}
        exec(body, {"__builtins__": {}}, namespace)

    return run


def _write_catalog(body: str = '{"ok":true}\n'):
    def run(_rule, root: Path) -> None:
        assert not (root / ".git").exists()
        output = root / "generated/catalog.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(body, encoding="utf-8")

    return run


def _resign_manifest(manifest: dict) -> None:
    tree_digest_entries = [
        {"path": entry["path"], "mode": entry["mode"], "sha256": entry["sha256"]}
        for entry in manifest["expected_tree"]
    ]
    manifest["expected_tree_sha256"] = sha256_bytes(canonical_json(tree_digest_entries))
    unsigned = dict(manifest)
    unsigned.pop("manifest_sha256")
    manifest["manifest_sha256"] = sha256_bytes(canonical_json(unsigned))


def test_regenerate_creates_only_reviewed_outputs_and_closes_tree(tmp_path: Path):
    selected = policy(
        command=_command(
            "from pathlib import Path; p=Path('generated/catalog.json'); "
            "p.parent.mkdir(parents=True, exist_ok=True); "
            "p.write_text('{\\\"ok\\\":true}\\n', encoding='utf-8')"
        )
    )
    baseline, source, manifest = manifest_fixture(tmp_path, selected_policy=selected)
    materialize(
        manifest,
        policy=selected,
        source_root=source,
        public_clone_root=baseline,
        remote_default_reader=remote_reader_for(manifest),
    )

    report = regenerate(
        manifest,
        policy=selected,
        source_root=source,
        public_clone_root=baseline,
        generator_runner=_write_catalog(),
    )

    assert report["generated_rule_ids"] == ["catalog"]
    assert report["final_tree_sha256"] == manifest["expected_tree_sha256"]
    assert (baseline / "generated/catalog.json").read_text(encoding="utf-8") == '{"ok":true}\n'


def test_regenerate_rejects_unlisted_output(tmp_path: Path):
    selected = policy(
        command=_command(
            "from pathlib import Path; p=Path('generated/catalog.json'); "
            "p.parent.mkdir(parents=True, exist_ok=True); "
            "p.write_text('{\\\"ok\\\":true}\\n', encoding='utf-8'); "
            "Path('generated/extra.txt').write_text('extra', encoding='utf-8')"
        )
    )
    baseline, source, manifest = manifest_fixture(tmp_path, selected_policy=selected)
    materialize(
        manifest,
        policy=selected,
        source_root=source,
        public_clone_root=baseline,
        remote_default_reader=remote_reader_for(manifest),
    )

    before = (baseline / "app/main.txt").read_bytes()

    def write_extra(_rule, root: Path) -> None:
        _write_catalog()(_rule, root)
        (root / "generated/extra.txt").write_text("extra", encoding="utf-8")

    with pytest.raises(RegenerateError, match="unlisted output"):
        regenerate(
            manifest,
            policy=selected,
            source_root=source,
            public_clone_root=baseline,
            generator_runner=write_extra,
        )

    assert (baseline / "app/main.txt").read_bytes() == before
    assert not (baseline / "generated/catalog.json").exists()
    assert not (baseline / "generated/extra.txt").exists()


def test_regenerate_rejects_expected_output_hash_drift(tmp_path: Path):
    selected = policy(
        command=_command(
            "from pathlib import Path; p=Path('generated/catalog.json'); "
            "p.parent.mkdir(parents=True, exist_ok=True); "
            "p.write_text('wrong\\n', encoding='utf-8')"
        )
    )
    baseline, source, manifest = manifest_fixture(tmp_path, selected_policy=selected)
    materialize(
        manifest,
        policy=selected,
        source_root=source,
        public_clone_root=baseline,
        remote_default_reader=remote_reader_for(manifest),
    )

    with pytest.raises(RegenerateError, match="generated output hash mismatch"):
        regenerate(
            manifest,
            policy=selected,
            source_root=source,
            public_clone_root=baseline,
            generator_runner=_write_catalog("wrong\n"),
        )

    assert not (baseline / "generated/catalog.json").exists()


def test_regenerate_rejects_private_listed_output_without_promoting_it(tmp_path: Path):
    selected = policy()
    baseline, source, manifest = manifest_fixture(tmp_path, selected_policy=selected)
    materialize(
        manifest,
        policy=selected,
        source_root=source,
        public_clone_root=baseline,
        remote_default_reader=remote_reader_for(manifest),
    )
    before = (baseline / "app/main.txt").read_bytes()
    private_body = b"checkout = /" + b"Users/private-user\n"
    poisoned = copy.deepcopy(manifest)
    output_entry = poisoned["generated_rules"][0]["outputs"][0]
    output_entry["sha256"] = sha256_bytes(private_body)
    expected_entry = next(
        entry
        for entry in poisoned["expected_tree"]
        if entry["path"] == "generated/catalog.json"
    )
    expected_entry["sha256"] = sha256_bytes(private_body)
    _resign_manifest(poisoned)

    with pytest.raises(RegenerateError, match="public content policy"):
        regenerate(
            poisoned,
            policy=selected,
            source_root=source,
            public_clone_root=baseline,
            generator_runner=_write_catalog(private_body.decode("utf-8")),
        )

    assert (baseline / "app/main.txt").read_bytes() == before
    assert not (baseline / "generated/catalog.json").exists()


def test_regenerate_rejects_self_resigned_authored_category_outside_reviewed_policy(
    tmp_path: Path,
):
    selected = policy()
    baseline, source, manifest = manifest_fixture(tmp_path, selected_policy=selected)
    materialize(
        manifest,
        policy=selected,
        source_root=source,
        public_clone_root=baseline,
        remote_default_reader=remote_reader_for(manifest),
    )
    poisoned = copy.deepcopy(manifest)
    keep_entry = next(
        entry for entry in poisoned["retained_baseline"] if entry["path"] == "keep.txt"
    )
    poisoned["retained_baseline"] = [
        entry for entry in poisoned["retained_baseline"] if entry["path"] != "keep.txt"
    ]
    poisoned["ported_authored"].append(keep_entry)
    poisoned["ported_authored"].sort(key=lambda entry: entry["path"])
    expected_entry = next(
        entry for entry in poisoned["expected_tree"] if entry["path"] == "keep.txt"
    )
    expected_entry["origin"] = "ported_authored"
    _resign_manifest(poisoned)

    with pytest.raises(RegenerateError, match="reviewed policy"):
        regenerate(
            poisoned,
            policy=selected,
            source_root=source,
            public_clone_root=baseline,
            generator_runner=_write_catalog(),
        )

    assert not (baseline / "generated/catalog.json").exists()


def test_regenerate_rejects_symlinked_public_clone_root(tmp_path: Path):
    selected = policy()
    baseline, source, manifest = manifest_fixture(tmp_path, selected_policy=selected)
    materialize(
        manifest,
        policy=selected,
        source_root=source,
        public_clone_root=baseline,
        remote_default_reader=remote_reader_for(manifest),
    )
    alias = tmp_path / "public-clone-alias"
    alias.symlink_to(baseline, target_is_directory=True)

    with pytest.raises(RegenerateError, match="symlink"):
        regenerate(
            manifest,
            policy=selected,
            source_root=source,
            public_clone_root=alias,
            generator_runner=_write_catalog(),
        )

    assert not (baseline / "generated/catalog.json").exists()


def test_regenerate_rejects_missing_expected_output(tmp_path: Path):
    selected = policy(command=_command("pass"))
    baseline, source, manifest = manifest_fixture(tmp_path, selected_policy=selected)
    materialize(
        manifest,
        policy=selected,
        source_root=source,
        public_clone_root=baseline,
        remote_default_reader=remote_reader_for(manifest),
    )

    with pytest.raises(RegenerateError, match="missing generated output"):
        regenerate(
            manifest,
            policy=selected,
            source_root=source,
            public_clone_root=baseline,
            generator_runner=lambda _rule, _root: None,
        )

    assert not (baseline / "generated/catalog.json").exists()


def test_regenerate_rejects_manifest_command_injection(tmp_path: Path):
    selected = policy()
    baseline, source, manifest = manifest_fixture(tmp_path, selected_policy=selected)
    materialize(
        manifest,
        policy=selected,
        source_root=source,
        public_clone_root=baseline,
        remote_default_reader=remote_reader_for(manifest),
    )
    manifest["generated_rules"][0]["command"] = ["rm", "-rf", "../outside"]

    with pytest.raises(RegenerateError, match="schema validation"):
        regenerate(
            manifest,
            policy=selected,
            source_root=source,
            public_clone_root=baseline,
            generator_runner=_write_catalog(),
        )


def test_regenerate_requires_exact_reviewed_policy_and_explicit_runner(tmp_path: Path):
    selected = policy()
    baseline, source, manifest = manifest_fixture(tmp_path, selected_policy=selected)
    materialize(
        manifest,
        policy=selected,
        source_root=source,
        public_clone_root=baseline,
        remote_default_reader=remote_reader_for(manifest),
    )

    with pytest.raises(RegenerateError, match="trusted generator runner"):
        regenerate(
            manifest,
            policy=selected,
            source_root=source,
            public_clone_root=baseline,
        )

    changed_policy = policy(command=["python3", "-c", "pass"])
    with pytest.raises(RegenerateError, match="policy digest"):
        regenerate(
            manifest,
            policy=changed_policy,
            source_root=source,
            public_clone_root=baseline,
            generator_runner=_write_catalog(),
        )


def test_regenerate_rejects_git_metadata_created_by_runner_without_touching_clone(tmp_path: Path):
    selected = policy()
    baseline, source, manifest = manifest_fixture(tmp_path, selected_policy=selected)
    materialize(
        manifest,
        policy=selected,
        source_root=source,
        public_clone_root=baseline,
        remote_default_reader=remote_reader_for(manifest),
    )

    def poison_git_metadata(_rule, root: Path) -> None:
        (root / ".git").mkdir()
        output = root / "generated/catalog.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text('{"ok":true}\n', encoding="utf-8")

    with pytest.raises(RegenerateError, match="unlisted output"):
        regenerate(
            manifest,
            policy=selected,
            source_root=source,
            public_clone_root=baseline,
            generator_runner=poison_git_metadata,
        )

    assert (baseline / ".git").is_dir()
    assert not (baseline / "generated/catalog.json").exists()
