from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.install.managed_artifacts import (
    merge_json_deep,
    merge_managed_block,
    merge_toml_agents,
)
from scripts.install.common import validate_plan_contract


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "src-tauri" / "tests" / "fixtures"
SHARED_NEGATIVE_CASES = {
    "unknown-target",
    "path-traversal",
    "absolute-source",
    "symlink-ish-segment",
    "source-mismatch",
    "merge-key-mismatch",
    "plan-only-surface",
    "closure-mismatch",
}


def _fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _replace_pointer(document: dict, pointer: str, value) -> None:
    parts = pointer.removeprefix("/").split("/")
    current = document
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    leaf = parts[-1]
    if isinstance(current, list):
        current[int(leaf)] = value
    else:
        current[leaf] = value


def test_desktop_positive_plan_fixture_matches_python_common_contract():
    validate_plan_contract(_fixture("m5_install_plan_positive.json"))


@pytest.mark.parametrize(
    "case",
    [
        case
        for case in _fixture("m5_install_plan_negative.json")
        if case["name"] in SHARED_NEGATIVE_CASES
    ],
    ids=lambda case: case["name"],
)
def test_desktop_negative_plan_fixture_matches_python_common_fail_closed(case):
    plan = copy.deepcopy(_fixture("m5_install_plan_positive.json"))
    _replace_pointer(plan, case["pointer"], case["value"])

    with pytest.raises(ValueError):
        validate_plan_contract(plan)


def test_desktop_merge_fixture_matches_python_apply_verify_fixed_points():
    corpus = _fixture("m5_merge_parity.json")
    managed = corpus["managed_block"]
    managed_artifact = {
        "begin_marker": managed["begin_marker"],
        "end_marker": managed["end_marker"],
    }
    managed_output = merge_managed_block(
        managed["current"], managed["body"], managed_artifact
    )
    assert managed_output == managed["expected"]
    assert (
        merge_managed_block(managed_output, managed["body"], managed_artifact)
        == managed_output
    )

    json_case = corpus["json_deep_merge"]
    json_artifact = {"json_merge_key": json_case["merge_key"]}
    json_output = merge_json_deep(
        json_case["current"], json_case["body"], json_artifact
    )
    assert "echo foreign" in json_output
    assert "node /old/" not in json_output
    assert "node new.cjs" in json_output
    assert merge_json_deep(json_output, json_case["body"], json_artifact) == json_output

    toml = corpus["toml_agents_merge"]
    toml_artifact = {"toml_merge_key": toml["merge_key"]}
    toml_output = merge_toml_agents(toml["current"], toml["body"], toml_artifact)
    assert toml_output == toml["expected"]
    assert merge_toml_agents(toml_output, toml["body"], toml_artifact) == toml_output
