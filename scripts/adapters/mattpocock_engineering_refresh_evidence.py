"""Publish the narrow, review-safe evidence projection for the engineering refresh.

The Codex probe keeps controller diagnostics in an ignored private directory. This module is the only
route that can project a fixed subset of that evidence into a tracked manifest or normalized event archive.
It deliberately fails closed: unknown event fields and non-conforming values are not copied through.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
PRIVATE_EVIDENCE_ROOT = (
    REPO_ROOT / ".harnesskit/private/runtime-evidence/mattpocock-engineering-refresh"
)
PUBLIC_MANIFEST_PATH = (
    REPO_ROOT / "docs/runtime-evidence/mattpocock-engineering-refresh/public-manifest.json"
)
ARCHIVE_REF = "evidence/pr-11-mattpocock-engineering-refresh"
PUBLIC_MANIFEST_SCHEMA_VERSION = 1
ARCHIVE_MANIFEST_SCHEMA_VERSION = 1

CLAIM_ATTEMPT_IDS = (
    "VC-01/20260716T161424Z",
    "VC-01/20260716T165240Z",
    "VC-02/20260716T180029Z",
    "VC-02/20260716T181537Z",
    "VC-03/20260717T022807Z",
    "VC-03/20260717T023223Z",
    "VC-04/20260717T041203Z",
    "VC-04/20260717T041912Z",
    "VC-05/20260717T044609Z",
    "VC-06/20260717T064137Z",
    "VC-06/20260717T071043Z",
    "VC-07/20260717T080922Z",
    "VC-07/20260717T082148Z",
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_ATTEMPT_ID = re.compile(r"^(VC-[0-9]{2})/([0-9]{8}T[0-9]{6}Z)$")
_TIMESTAMP = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
_CAPTURED_AT = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_SCENARIO_ID = re.compile(r"^vc[0-9]{2}-[a-z0-9-]+$")
_COMPONENT_ID = re.compile(r"^harnesskit\.(?:agent|skill)\.[a-z0-9-]+$")
_MODEL = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_OBSERVATION = re.compile(r"^[a-z0-9._-]{1,120}$")
_EVENT_LABEL = re.compile(r"^[a-z][a-z0-9_.-]{0,80}$")

_VERDICTS = frozenset({"PASS", "NEEDS_WORK", "BLOCKED"})
_ATTEMPT_KINDS = frozenset({"initial", "failed-only-rerun", "artifact-revalidation"})
_SCENARIO_KINDS = frozenset({"positive", "boundary", "baseline"})
_EVENT_STATUS = frozenset({"unknown", "in_progress", "completed", "failed"})
_TOKEN_FIELDS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "reasoning_output_tokens",
    }
)
_LENGTH_FIELDS = frozenset({"aggregated_output_length", "text_length"})
_SINGLE_HASH_FIELDS = frozenset(
    {
        "command_hash",
        "prompt_hash",
        "aggregated_output_hash",
        "text_hash",
        "thread_id_hash",
        "event_thread_hash",
        "sender_thread_hash",
    }
)
_HASH_LIST_FIELDS = frozenset(
    {
        "message_hashes",
        "receiver_thread_hashes",
        "completed_agent_thread_hashes",
    }
)
_PUBLIC_EVENT_FIELDS = frozenset(
    {
        "scenario_id",
        "event_type",
        "event_index",
        "item_type",
        "status",
        "tool",
        *_TOKEN_FIELDS,
        *_LENGTH_FIELDS,
        "exit_code",
        *_SINGLE_HASH_FIELDS,
        *_HASH_LIST_FIELDS,
    }
)
_SOURCE_ONLY_EVENT_FIELDS = frozenset({"item_id", "usage"})
_SOURCE_EVENT_FIELDS = _PUBLIC_EVENT_FIELDS | _SOURCE_ONLY_EVENT_FIELDS


class EvidencePublicationError(ValueError):
    """Raised when private evidence cannot be safely projected into public proof."""


@dataclass(frozen=True)
class ArchiveReference:
    branch: str
    commit: str
    manifest_sha256: str


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _error(message: str) -> EvidencePublicationError:
    return EvidencePublicationError(message)


def _require_mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _error(f"{name} must be an object")
    return value


def _require_string(value: object, name: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise _error(f"{name} is invalid")
    return value


def _require_hash(value: object, name: str) -> str:
    return _require_string(value, name, _SHA256)


def _require_non_negative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _error(f"{name} must be a non-negative integer")
    return value


def _require_boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise _error(f"{name} must be a boolean")
    return value


def _require_string_list(value: object, name: str, pattern: re.Pattern[str]) -> list[str]:
    if not isinstance(value, list):
        raise _error(f"{name} must be a list")
    return [_require_string(item, f"{name} item", pattern) for item in value]


def _normalized_status(value: object) -> str:
    if value is None:
        return "unknown"
    if not isinstance(value, str) or value not in _EVENT_STATUS:
        raise _error("status is invalid")
    return value


def normalize_event(raw: object, *, event_index: int | None = None) -> dict[str, Any]:
    """Drop private-only source fields and validate every exportable event field."""

    event = _require_mapping(raw, "event")
    unknown = sorted(set(event) - _SOURCE_EVENT_FIELDS)
    if unknown:
        raise _error(f"unknown event field: {unknown[0]}")

    source_event_index = event.get("event_index")
    if source_event_index is None:
        if event_index is None:
            raise _error("event_index must be a non-negative integer")
        normalized_event_index = _require_non_negative_int(event_index, "event_index")
    else:
        normalized_event_index = _require_non_negative_int(source_event_index, "event_index")
    normalized: dict[str, Any] = {
        "scenario_id": _require_string(event.get("scenario_id"), "scenario_id", _SCENARIO_ID),
        "event_type": _require_string(event.get("event_type"), "event_type", _EVENT_LABEL),
        "event_index": normalized_event_index,
    }
    if "item_type" in event:
        normalized["item_type"] = _require_string(event["item_type"], "item_type", _EVENT_LABEL)
    if "status" in event:
        normalized["status"] = _normalized_status(event["status"])
    if "tool" in event:
        normalized["tool"] = _require_string(event["tool"], "tool", _EVENT_LABEL)

    usage = event.get("usage")
    if usage is not None:
        usage = _require_mapping(usage, "usage")
        unknown_usage = sorted(set(usage) - _TOKEN_FIELDS)
        if unknown_usage:
            raise _error(f"unknown usage field: {unknown_usage[0]}")

    for field in _TOKEN_FIELDS:
        in_event = field in event
        in_usage = isinstance(usage, dict) and field in usage
        if in_event and in_usage:
            raise _error(f"{field} cannot appear in both event and usage")
        if in_event:
            normalized[field] = _require_non_negative_int(event[field], field)
        elif in_usage:
            normalized[field] = _require_non_negative_int(usage[field], f"usage.{field}")

    for field in _LENGTH_FIELDS:
        if field in event:
            normalized[field] = _require_non_negative_int(event[field], field)
    if "exit_code" in event:
        exit_code = event["exit_code"]
        if isinstance(exit_code, bool) or not isinstance(exit_code, int) or not -255 <= exit_code <= 255:
            raise _error("exit_code is invalid")
        normalized["exit_code"] = exit_code
    for field in _SINGLE_HASH_FIELDS:
        if field in event:
            normalized[field] = _require_hash(event[field], field)
    for field in _HASH_LIST_FIELDS:
        if field in event:
            normalized[field] = _require_string_list(event[field], field, _SHA256)
    return normalized


def _normalized_event_stream(events_path: Path) -> tuple[list[dict[str, Any]], bytes]:
    if not events_path.is_file():
        raise _error(f"missing private event file for publication: {events_path.name}")
    normalized_events: list[dict[str, Any]] = []
    for line_number, line in enumerate(events_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line:
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _error(f"invalid event JSON at line {line_number}") from exc
        try:
            normalized_events.append(normalize_event(raw, event_index=line_number - 1))
        except EvidencePublicationError as exc:
            raise _error(f"event row {line_number} is not publishable: {exc}") from exc
    payload = b"".join(canonical_json_bytes(event) + b"\n" for event in normalized_events)
    return normalized_events, payload


def _attempt_parts(attempt_id: str) -> tuple[str, str]:
    match = _ATTEMPT_ID.fullmatch(attempt_id)
    if match is None:
        raise _error("attempt_id is invalid")
    return match.group(1), match.group(2)


def _attempt_id_from_path(path: Path, root: Path) -> str:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise _error("private result path is outside the requested root") from exc
    if len(relative.parts) != 3 or relative.name != "result.json":
        raise _error("private result path layout is invalid")
    return f"{relative.parts[0]}/{relative.parts[1]}"


def _component_ids(private_result: dict[str, Any]) -> list[str]:
    combined = private_result.get("component_ids")
    singular = private_result.get("component_id")
    source = combined if combined is not None else [singular]
    if not isinstance(source, list) or not source:
        raise _error("component id is required")
    values = [_require_string(value, "component_id", _COMPONENT_ID) for value in source]
    if len(set(values)) != len(values):
        raise _error("component ids must not repeat")
    if singular is not None and singular not in values:
        raise _error("component_id must be included in component_ids")
    return values


def _all_boolean_values(value: object, name: str) -> bool:
    mapping = _require_mapping(value, name)
    if not mapping:
        raise _error(f"{name} must not be empty")
    return all(_require_boolean(item, f"{name} value") for item in mapping.values())


def _privacy_passed(value: object) -> bool:
    privacy = _require_mapping(value, "privacy")
    required_false = (
        "raw_events_persisted",
        "raw_prompts_persisted",
        "raw_responses_persisted",
        "auth_material_persisted",
    )
    if _require_boolean(privacy.get("synthetic_fixtures_only"), "privacy.synthetic_fixtures_only") is not True:
        return False
    if _require_non_negative_int(
        privacy.get("non_codex_runtime_invocations"), "privacy.non_codex_runtime_invocations"
    ) != 0:
        return False
    return all(_require_boolean(privacy.get(field), f"privacy.{field}") is False for field in required_false)


def _cleanup_passed(value: object) -> bool:
    cleanup = _require_mapping(value, "cleanup")
    return all(
        _require_boolean(cleanup.get(field), f"cleanup.{field}")
        for field in (
            "all_auth_links_removed",
            "all_isolated_workspaces_removed",
            "all_process_groups_absent",
        )
    )


def _scenario_counts(value: object) -> dict[str, int]:
    if not isinstance(value, list):
        raise _error("scenario_results must be a list")
    counts = {"pass": 0, "needs_work": 0, "blocked": 0, "total": 0}
    for scenario in value:
        item = _require_mapping(scenario, "scenario_result")
        _require_string(item.get("scenario_id"), "scenario_result.scenario_id", _SCENARIO_ID)
        kind = item.get("scenario_kind")
        if not isinstance(kind, str) or kind not in _SCENARIO_KINDS:
            raise _error("scenario_result.scenario_kind is invalid")
        verdict = item.get("verdict")
        if not isinstance(verdict, str) or verdict not in _VERDICTS:
            raise _error("scenario_result.verdict is invalid")
        assertions = item.get("expected_vs_actual_assertions")
        if verdict == "PASS" and not _all_boolean_values(
            assertions, "scenario_result.expected_vs_actual_assertions"
        ):
            raise _error("PASS scenario has failed assertions")
        counts["total"] += 1
        counts[{"PASS": "pass", "NEEDS_WORK": "needs_work", "BLOCKED": "blocked"}[verdict]] += 1
    return counts


def _predecessor_attempt_id(private_result: dict[str, Any]) -> str | None:
    attempt = _require_mapping(private_result.get("attempt"), "attempt")
    predecessor = attempt.get("predecessor_result")
    if predecessor is None:
        return None
    if not isinstance(predecessor, str):
        raise _error("attempt.predecessor_result is invalid")
    match = re.search(r"(VC-[0-9]{2})/([0-9]{8}T[0-9]{6}Z)/result\\.json$", predecessor)
    if match is None:
        return None
    return f"{match.group(1)}/{match.group(2)}"


def build_attempt_record(
    private_result: object,
    normalized_events: Iterable[dict[str, Any]],
    *,
    attempt_id: str,
) -> dict[str, Any]:
    """Extract a fixed, review-safe claim record from a private terminal result."""

    result = _require_mapping(private_result, "private result")
    slice_id, timestamp = _attempt_parts(attempt_id)
    if result.get("slice_id") != slice_id:
        raise _error("private result slice_id does not match its path")
    _require_string(timestamp, "attempt timestamp", _TIMESTAMP)
    captured_at = _require_string(result.get("captured_at"), "captured_at", _CAPTURED_AT)
    runtime_target = result.get("runtime_target")
    if runtime_target != "codex":
        raise _error("runtime_target must be codex")
    observed_codex_version = result.get("observed_codex_version")
    if not isinstance(observed_codex_version, str) or not (
        observed_codex_version == "unavailable"
        or re.fullmatch(r"codex-cli [0-9]+(?:\.[0-9]+){1,3}", observed_codex_version)
    ):
        raise _error("observed_codex_version is invalid")
    requested_model = _require_string(result.get("requested_model"), "requested_model", _MODEL)
    if result.get("actual_model") is not None:
        raise _error("actual_model must be null in public evidence")
    actual_model_observation = _require_string(
        result.get("actual_model_observation"), "actual_model_observation", _OBSERVATION
    )
    attempt = _require_mapping(result.get("attempt"), "attempt")
    attempt_kind = attempt.get("kind")
    if not isinstance(attempt_kind, str) or attempt_kind not in _ATTEMPT_KINDS:
        raise _error("attempt.kind is invalid")
    selected_scenario_ids = _require_string_list(
        attempt.get("selected_scenario_ids"), "attempt.selected_scenario_ids", _SCENARIO_ID
    )
    if not selected_scenario_ids:
        raise _error("attempt.selected_scenario_ids must not be empty")
    verdict = result.get("verdict")
    if not isinstance(verdict, str) or verdict not in _VERDICTS:
        raise _error("verdict is invalid")
    failure_category = result.get("failure_category")
    if failure_category is not None:
        _require_string(failure_category, "failure_category", re.compile(r"^[a-z][a-z0-9_]{0,64}$"))

    normalized_event_list = list(normalized_events)
    normalized_event_bytes = b"".join(
        canonical_json_bytes(event) + b"\n" for event in normalized_event_list
    )
    record: dict[str, Any] = {
        "slice_id": slice_id,
        "attempt_id": attempt_id,
        "component_ids": _component_ids(result),
        "runtime_target": "codex",
        "captured_at": captured_at,
        "attempt_kind": attempt_kind,
        "selected_scenario_ids": selected_scenario_ids,
        "observed_codex_version": observed_codex_version,
        "requested_model": requested_model,
        "actual_model": None,
        "actual_model_observation": actual_model_observation,
        "static_and_install_passed": _all_boolean_values(
            result.get("static_and_install_gate"), "static_and_install_gate"
        ),
        "privacy_passed": _privacy_passed(result.get("privacy")),
        "cleanup_passed": _cleanup_passed(result.get("cleanup")),
        "verdict": verdict,
        "failure_category": failure_category,
        "scenario_counts": _scenario_counts(result.get("scenario_results")),
        "normalized_event_count": len(normalized_event_list),
        "normalized_events_sha256": sha256_bytes(normalized_event_bytes),
    }
    predecessor_attempt_id = _predecessor_attempt_id(result)
    if predecessor_attempt_id is not None:
        record["predecessor_attempt_id"] = predecessor_attempt_id
    return record


def _archive_entries(private_root: Path) -> list[tuple[str, list[dict[str, Any]], bytes]]:
    if not private_root.is_dir():
        raise _error("private evidence root does not exist")
    entries: list[tuple[str, list[dict[str, Any]], bytes]] = []
    for events_path in sorted(private_root.glob("VC-*/*/events.sanitized.jsonl")):
        result_path = events_path.with_name("result.json")
        attempt_id = _attempt_id_from_path(result_path, private_root)
        try:
            normalized_events, payload = _normalized_event_stream(events_path)
        except EvidencePublicationError as exc:
            raise _error(f"{attempt_id} cannot be published: {exc}") from exc
        entries.append((attempt_id, normalized_events, payload))
    if not entries:
        raise _error("no private event streams were found")
    return entries


def publish_event_archive(private_root: Path, archive_root: Path) -> dict[str, Any]:
    """Write only normalized event rows and a deterministic archive index."""

    if archive_root.exists() and any(archive_root.iterdir()):
        raise _error("archive destination must be empty")
    archive_root.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for attempt_id, _events, payload in _archive_entries(private_root):
        slice_id, timestamp = _attempt_parts(attempt_id)
        destination = archive_root / slice_id / timestamp / "events.normalized.jsonl"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)
        entries.append(
            {
                "attempt_id": attempt_id,
                "event_count": len(_events),
                "event_sha256": sha256_bytes(payload),
            }
        )
    base_manifest = {
        "schema_version": ARCHIVE_MANIFEST_SCHEMA_VERSION,
        "entries": entries,
    }
    archive_manifest = {**base_manifest, "sha256": sha256_bytes(canonical_json_bytes(base_manifest))}
    (archive_root / "archive-manifest.json").write_bytes(canonical_json_bytes(archive_manifest))
    return archive_manifest


def _read_private_result(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _error("private result cannot be parsed") from exc
    return _require_mapping(value, "private result")


def _validate_archive_reference(reference: ArchiveReference) -> None:
    if reference.branch != ARCHIVE_REF:
        raise _error("archive branch is not the approved fixed reference")
    if _GIT_COMMIT.fullmatch(reference.commit) is None:
        raise _error("archive commit is invalid")
    _require_hash(reference.manifest_sha256, "archive manifest sha256")


def build_public_manifest(
    private_root: Path,
    *,
    archive_reference: ArchiveReference,
    claim_attempt_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build a compact manifest from private evidence without copying its raw fields."""

    _validate_archive_reference(archive_reference)
    selected_attempt_ids = tuple(claim_attempt_ids or CLAIM_ATTEMPT_IDS)
    if len(set(selected_attempt_ids)) != len(selected_attempt_ids):
        raise _error("claim attempt ids must not repeat")
    records: list[dict[str, Any]] = []
    for attempt_id in selected_attempt_ids:
        slice_id, timestamp = _attempt_parts(attempt_id)
        attempt_dir = private_root / slice_id / timestamp
        result_path = attempt_dir / "result.json"
        events_path = attempt_dir / "events.sanitized.jsonl"
        try:
            normalized_events, _payload = _normalized_event_stream(events_path)
            records.append(
                build_attempt_record(
                    _read_private_result(result_path), normalized_events, attempt_id=attempt_id
                )
            )
        except EvidencePublicationError as exc:
            raise _error(f"{attempt_id} cannot be projected: {exc}") from exc
    manifest = {
        "schema_version": PUBLIC_MANIFEST_SCHEMA_VERSION,
        "archive": {
            "branch": archive_reference.branch,
            "commit": archive_reference.commit,
            "manifest_sha256": archive_reference.manifest_sha256,
        },
        "claim_attempts": records,
    }
    validate_public_manifest(manifest)
    return manifest


def validate_public_manifest(value: object) -> None:
    manifest = _require_mapping(value, "public manifest")
    if set(manifest) != {"schema_version", "archive", "claim_attempts"}:
        raise _error("public manifest has unknown or missing fields")
    if manifest.get("schema_version") != PUBLIC_MANIFEST_SCHEMA_VERSION:
        raise _error("public manifest schema version is invalid")
    archive = _require_mapping(manifest.get("archive"), "archive")
    if set(archive) != {"branch", "commit", "manifest_sha256"}:
        raise _error("archive reference has unknown or missing fields")
    _validate_archive_reference(
        ArchiveReference(
            branch=archive.get("branch"),
            commit=archive.get("commit"),
            manifest_sha256=archive.get("manifest_sha256"),
        )
    )
    records = manifest.get("claim_attempts")
    if not isinstance(records, list) or not records:
        raise _error("claim_attempts must be a non-empty list")
    seen_attempt_ids: set[str] = set()
    allowed_fields = {
        "slice_id",
        "attempt_id",
        "component_ids",
        "runtime_target",
        "captured_at",
        "attempt_kind",
        "selected_scenario_ids",
        "observed_codex_version",
        "requested_model",
        "actual_model",
        "actual_model_observation",
        "static_and_install_passed",
        "privacy_passed",
        "cleanup_passed",
        "verdict",
        "failure_category",
        "scenario_counts",
        "normalized_event_count",
        "normalized_events_sha256",
        "predecessor_attempt_id",
    }
    for record in records:
        item = _require_mapping(record, "claim attempt")
        unknown = set(item) - allowed_fields
        if unknown:
            raise _error("claim attempt has unknown fields")
        slice_id, _timestamp = _attempt_parts(item.get("attempt_id"))
        if item.get("slice_id") != slice_id:
            raise _error("claim attempt slice_id does not match attempt_id")
        if item["attempt_id"] in seen_attempt_ids:
            raise _error("claim attempt ids must not repeat")
        seen_attempt_ids.add(item["attempt_id"])
        _component_ids({"component_ids": item.get("component_ids")})
        if item.get("runtime_target") != "codex":
            raise _error("claim attempt runtime_target is invalid")
        _require_string(item.get("captured_at"), "claim captured_at", _CAPTURED_AT)
        if item.get("attempt_kind") not in _ATTEMPT_KINDS:
            raise _error("claim attempt kind is invalid")
        _require_string_list(item.get("selected_scenario_ids"), "claim scenarios", _SCENARIO_ID)
        version = item.get("observed_codex_version")
        if not isinstance(version, str) or not (
            version == "unavailable" or re.fullmatch(r"codex-cli [0-9]+(?:\.[0-9]+){1,3}", version)
        ):
            raise _error("claim Codex version is invalid")
        _require_string(item.get("requested_model"), "claim requested_model", _MODEL)
        if item.get("actual_model") is not None:
            raise _error("claim actual_model must be null")
        _require_string(item.get("actual_model_observation"), "claim actual_model_observation", _OBSERVATION)
        for field in ("static_and_install_passed", "privacy_passed", "cleanup_passed"):
            _require_boolean(item.get(field), f"claim {field}")
        if item.get("verdict") not in _VERDICTS:
            raise _error("claim verdict is invalid")
        category = item.get("failure_category")
        if category is not None:
            _require_string(category, "claim failure_category", re.compile(r"^[a-z][a-z0-9_]{0,64}$"))
        counts = _require_mapping(item.get("scenario_counts"), "claim scenario_counts")
        if set(counts) != {"pass", "needs_work", "blocked", "total"}:
            raise _error("claim scenario_counts has unknown or missing fields")
        count_values = {name: _require_non_negative_int(number, f"scenario_counts.{name}") for name, number in counts.items()}
        if count_values["total"] != (
            count_values["pass"] + count_values["needs_work"] + count_values["blocked"]
        ):
            raise _error("claim scenario_counts total is inconsistent")
        _require_non_negative_int(item.get("normalized_event_count"), "normalized_event_count")
        _require_hash(item.get("normalized_events_sha256"), "normalized_events_sha256")
        predecessor = item.get("predecessor_attempt_id")
        if predecessor is not None:
            _attempt_parts(predecessor)


def write_public_manifest(
    private_root: Path,
    output_path: Path,
    *,
    archive_reference: ArchiveReference,
    claim_attempt_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    manifest = build_public_manifest(
        private_root,
        archive_reference=archive_reference,
        claim_attempt_ids=claim_attempt_ids,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(canonical_json_bytes(manifest))
    return manifest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Project private Codex evidence into strict-allowlist public artifacts."
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    archive = subcommands.add_parser("archive", help="write normalized event archive only")
    archive.add_argument("--private-root", type=Path, default=PRIVATE_EVIDENCE_ROOT)
    archive.add_argument("--archive-root", type=Path, required=True)
    manifest = subcommands.add_parser("manifest", help="write compact public manifest only")
    manifest.add_argument("--private-root", type=Path, default=PRIVATE_EVIDENCE_ROOT)
    manifest.add_argument("--output", type=Path, default=PUBLIC_MANIFEST_PATH)
    manifest.add_argument("--archive-branch", default=ARCHIVE_REF)
    manifest.add_argument("--archive-commit", required=True)
    manifest.add_argument("--archive-manifest-sha256", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "archive":
            archive_manifest = publish_event_archive(args.private_root, args.archive_root)
            print(json.dumps(archive_manifest, sort_keys=True))
            return 0
        manifest = write_public_manifest(
            args.private_root,
            args.output,
            archive_reference=ArchiveReference(
                branch=args.archive_branch,
                commit=args.archive_commit,
                manifest_sha256=args.archive_manifest_sha256,
            ),
        )
        print(json.dumps({"path": args.output.as_posix(), "attempt_count": len(manifest["claim_attempts"])}, sort_keys=True))
        return 0
    except EvidencePublicationError as exc:
        print(f"evidence publication blocked: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
