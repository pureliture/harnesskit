from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

import yaml


CANONICAL_PUBLIC_REPOSITORY_URL = "https://github.com/pureliture/harnesskit.git"
REVIEWED_PUBLIC_REPOSITORIES = frozenset({"harnesskit", "graph-workbench"})

_TOP_LEVEL_KEYS = {
    "schema_version",
    "target_repository_url",
    "retain_public_baseline",
    "ported_authored",
    "intentional_removals",
    "generated_rules",
}
_PORTED_KEYS = {"include", "exclude"}
_RULE_KEYS = {"id", "command", "working_directory", "inputs", "outputs"}
_FORBIDDEN_AUTHORED_PREFIXES = (
    ".git",
    ".harnesskit",
    ".routine-harness",
    "docs/harness-requirements",
    "docs/runtime-evidence",
    "graphify-out",
    "src-frontend/node_modules",
    "src-tauri/target",
)


class PolicyError(ValueError):
    """The reviewed public-port policy is invalid or ambiguous."""


@dataclass(frozen=True)
class GeneratedRulePolicy:
    rule_id: str
    command: tuple[str, ...]
    working_directory: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]


@dataclass(frozen=True)
class PublicPortPolicy:
    schema_version: int
    target_repository_url: str
    retain_public_baseline: bool
    ported_includes: tuple[str, ...]
    ported_excludes: tuple[str, ...]
    intentional_removals: tuple[str, ...]
    generated_rules: tuple[GeneratedRulePolicy, ...]


def normalized_policy(policy: PublicPortPolicy) -> dict[str, Any]:
    """Return the reviewed policy authority in a deterministic serializable form."""
    return {
        "schema_version": policy.schema_version,
        "target_repository_url": policy.target_repository_url,
        "retain_public_baseline": policy.retain_public_baseline,
        "ported_authored": {
            "include": list(policy.ported_includes),
            "exclude": list(policy.ported_excludes),
        },
        "intentional_removals": list(policy.intentional_removals),
        "generated_rules": [
            {
                "id": rule.rule_id,
                "command": list(rule.command),
                "working_directory": rule.working_directory,
                "inputs": list(rule.inputs),
                "outputs": list(rule.outputs),
            }
            for rule in policy.generated_rules
        ],
    }


def policy_sha256(policy: PublicPortPolicy) -> str:
    body = json.dumps(
        normalized_policy(policy),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PolicyError(f"{label} must be a mapping")
    return value


def _reject_unknown_keys(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise PolicyError(f"{label} has unknown keys: {', '.join(unknown)}")


def _require_string_list(value: Any, label: str, *, nonempty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise PolicyError(f"{label} must be a list of non-empty strings")
    if nonempty and not value:
        raise PolicyError(f"{label} must not be empty")
    if len(value) != len(set(value)):
        raise PolicyError(f"{label} contains duplicate entries")
    return tuple(value)


def validate_relative_path(value: str, label: str, *, allow_dot: bool = False) -> str:
    if "\\" in value or "\x00" in value:
        raise PolicyError(f"{label} must use a canonical POSIX relative path")
    if allow_dot and value == ".":
        return value
    path = PurePosixPath(value)
    if not value or path.is_absolute() or value.startswith("/"):
        raise PolicyError(f"{label} must be relative")
    if value != path.as_posix() or any(part in {"", ".", ".."} for part in path.parts):
        raise PolicyError(f"{label} must not escape or contain non-canonical segments")
    return value


def _validate_pattern(value: str, label: str, *, authored_include: bool = False) -> str:
    validate_relative_path(value, label)
    first_segment = value.split("/", 1)[0]
    if authored_include and any(token in first_segment for token in "*?["):
        raise PolicyError(f"{label} must name a literal reviewed top-level path")
    literal_prefix = value.split("*", 1)[0].split("?", 1)[0].rstrip("/")
    if authored_include and (
        value in {"*", "**", "**/*", "docs/**"}
        or any(
            literal_prefix == prefix or literal_prefix.startswith(f"{prefix}/")
            for prefix in _FORBIDDEN_AUTHORED_PREFIXES
        )
    ):
        raise PolicyError(f"{label} includes a private or generated-only path")
    return value


def _matches_pattern(path: str, pattern: str) -> bool:
    if pattern.endswith("/**"):
        prefix = pattern[:-3].rstrip("/")
        return path == prefix or path.startswith(f"{prefix}/")
    return PurePosixPath(path).match(pattern)


def path_matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(_matches_pattern(path, pattern) for pattern in patterns)


def reject_forbidden_authored_paths(paths: Iterable[str]) -> None:
    """Reject selected authored paths that enter private or generated-only components."""
    for path in sorted(paths):
        if any(
            path == prefix or path.startswith(f"{prefix}/")
            for prefix in _FORBIDDEN_AUTHORED_PREFIXES
        ):
            raise PolicyError(f"selected authored path includes a private or generated-only path: {path}")


def parse_policy(raw: Mapping[str, Any]) -> PublicPortPolicy:
    data = _require_mapping(raw, "public-port policy")
    _reject_unknown_keys(data, _TOP_LEVEL_KEYS, "public-port policy")

    if data.get("schema_version") != 1:
        raise PolicyError("public-port policy schema_version must be 1")
    if data.get("target_repository_url") != CANONICAL_PUBLIC_REPOSITORY_URL:
        raise PolicyError(
            f"target_repository_url must be the canonical public target {CANONICAL_PUBLIC_REPOSITORY_URL}"
        )
    if data.get("retain_public_baseline") is not True:
        raise PolicyError("retain_public_baseline must be true")

    authored = _require_mapping(data.get("ported_authored"), "ported_authored")
    _reject_unknown_keys(authored, _PORTED_KEYS, "ported_authored")
    includes = _require_string_list(authored.get("include"), "ported_authored.include", nonempty=True)
    excludes = _require_string_list(authored.get("exclude"), "ported_authored.exclude")
    includes = tuple(
        _validate_pattern(item, f"ported_authored.include[{index}]", authored_include=True)
        for index, item in enumerate(includes)
    )
    excludes = tuple(
        _validate_pattern(item, f"ported_authored.exclude[{index}]")
        for index, item in enumerate(excludes)
    )

    removals = _require_string_list(data.get("intentional_removals"), "intentional_removals")
    removals = tuple(
        validate_relative_path(item, f"intentional_removals[{index}]")
        for index, item in enumerate(removals)
    )

    rules_raw = data.get("generated_rules")
    if not isinstance(rules_raw, list):
        raise PolicyError("generated_rules must be a list")
    rules: list[GeneratedRulePolicy] = []
    rule_ids: set[str] = set()
    generated_outputs: set[str] = set()
    for index, raw_rule in enumerate(rules_raw):
        rule = _require_mapping(raw_rule, f"generated_rules[{index}]")
        _reject_unknown_keys(rule, _RULE_KEYS, f"generated_rules[{index}]")
        rule_id = rule.get("id")
        if not isinstance(rule_id, str) or not rule_id or not rule_id.replace("-", "").isalnum():
            raise PolicyError(f"generated_rules[{index}].id must be a stable kebab-case identifier")
        if rule_id in rule_ids:
            raise PolicyError(f"generated rule id is duplicated: {rule_id}")
        rule_ids.add(rule_id)

        command = _require_string_list(rule.get("command"), f"generated_rules[{index}].command", nonempty=True)
        for argument in command:
            if "\x00" in argument or "\n" in argument or argument.startswith("/"):
                raise PolicyError(
                    f"generated_rules[{index}].command must not contain absolute or multiline private paths"
                )
        working_directory = rule.get("working_directory")
        if not isinstance(working_directory, str):
            raise PolicyError(f"generated_rules[{index}].working_directory must be a string")
        working_directory = validate_relative_path(
            working_directory,
            f"generated_rules[{index}].working_directory",
            allow_dot=True,
        )
        inputs = _require_string_list(rule.get("inputs"), f"generated_rules[{index}].inputs", nonempty=True)
        outputs = _require_string_list(rule.get("outputs"), f"generated_rules[{index}].outputs", nonempty=True)
        inputs = tuple(
            validate_relative_path(item, f"generated_rules[{index}].inputs[{item}]") for item in inputs
        )
        outputs = tuple(
            validate_relative_path(item, f"generated_rules[{index}].outputs[{item}]") for item in outputs
        )
        overlap = generated_outputs.intersection(outputs)
        if overlap:
            raise PolicyError(f"generated output is owned by multiple rules: {sorted(overlap)[0]}")
        generated_outputs.update(outputs)
        rules.append(
            GeneratedRulePolicy(
                rule_id=rule_id,
                command=command,
                working_directory=working_directory,
                inputs=inputs,
                outputs=outputs,
            )
        )

    removal_output_overlap = set(removals).intersection(generated_outputs)
    if removal_output_overlap:
        raise PolicyError(
            f"path cannot be both generated and removed: {sorted(removal_output_overlap)[0]}"
        )
    non_excluded_outputs = sorted(
        output for output in generated_outputs if not path_matches_any(output, excludes)
    )
    if non_excluded_outputs:
        raise PolicyError(
            "generated outputs must be explicitly excluded from ported_authored: "
            + ", ".join(non_excluded_outputs)
        )

    return PublicPortPolicy(
        schema_version=1,
        target_repository_url=CANONICAL_PUBLIC_REPOSITORY_URL,
        retain_public_baseline=True,
        ported_includes=includes,
        ported_excludes=excludes,
        intentional_removals=removals,
        generated_rules=tuple(rules),
    )


def load_policy(path) -> PublicPortPolicy:
    with open(path, "r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    return parse_policy(_require_mapping(raw, "public-port policy"))
