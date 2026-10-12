from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


PROFILE_WORKFLOW_MEMBERSHIP_FORBIDDEN = "profile_workflow_membership_forbidden"


@dataclass(frozen=True, slots=True)
class ProfileSelectionError(ValueError):
    code: str
    profile_id: str
    field_path: str
    component_id: str

    def __str__(self) -> str:
        return (
            f"{self.code}(profile_id={self.profile_id}, "
            f"field_path={self.field_path}, component_id={self.component_id})"
        )


ManifestLoader = Callable[[str], Mapping[str, Any]]


def validate_profile_selection(
    profile: Mapping[str, Any],
    registry_components: Mapping[str, Any],
    *,
    load_manifest: ManifestLoader,
) -> list[str]:
    """Validate a Profile and return its ordered component closure.

    Structural/schema and unknown-reference validation remain owned by their
    existing callers. This resolver owns the cross-domain invariant that Profile
    selection may contain installable Component or Composite identities, never
    Workflow identities, including members reached through a Composite. Composite
    identities are expanded to their ordered component members for adapter output.
    """

    profile_id = profile.get("profile_id")
    if not isinstance(profile_id, str):
        profile_id = "<unknown-profile>"

    resolved_components: list[str] = []
    for candidate, field_path in _selection_candidates(profile):
        _validate_candidate(
            candidate,
            field_path=field_path,
            profile_id=profile_id,
            registry_components=registry_components,
            load_manifest=load_manifest,
            active_composites=set(),
            resolved_components=resolved_components,
        )
    return list(dict.fromkeys(resolved_components))


def _selection_candidates(profile: Mapping[str, Any]):
    components = profile.get("components")
    if isinstance(components, list):
        for index, candidate in enumerate(components):
            if isinstance(candidate, str):
                yield candidate, f"components[{index}]"

    install_policy = profile.get("install_policy")
    if not isinstance(install_policy, Mapping):
        return
    scope_components = install_policy.get("scope_components")
    if not isinstance(scope_components, Mapping):
        return
    for scope, scoped in scope_components.items():
        if not isinstance(scope, str) or not isinstance(scoped, list):
            continue
        for index, candidate in enumerate(scoped):
            if isinstance(candidate, str):
                yield candidate, f"install_policy.scope_components.{scope}[{index}]"


def _validate_candidate(
    candidate: str,
    *,
    field_path: str,
    profile_id: str,
    registry_components: Mapping[str, Any],
    load_manifest: ManifestLoader,
    active_composites: set[str],
    resolved_components: list[str],
) -> None:
    entry = registry_components.get(candidate)
    if not isinstance(entry, Mapping):
        resolved_components.append(candidate)
        return

    kind = entry.get("kind")
    if kind == "workflow":
        raise ProfileSelectionError(
            code=PROFILE_WORKFLOW_MEMBERSHIP_FORBIDDEN,
            profile_id=profile_id,
            field_path=field_path,
            component_id=candidate,
        )
    if kind != "composite":
        resolved_components.append(candidate)
        return

    if candidate in active_composites:
        raise ValueError(f"composite selection cycle: {candidate}")
    manifest_path = entry.get("path")
    if not isinstance(manifest_path, str) or not manifest_path:
        raise ValueError(f"composite registry path is required: {candidate}")
    manifest = load_manifest(manifest_path)
    members = manifest.get("members")
    if not isinstance(members, list):
        raise ValueError(f"composite members must be a list: {candidate}")

    nested_active = {*active_composites, candidate}
    for index, member in enumerate(members):
        if not isinstance(member, str):
            continue
        _validate_candidate(
            member,
            field_path=f"{field_path}.members[{index}]",
            profile_id=profile_id,
            registry_components=registry_components,
            load_manifest=load_manifest,
            active_composites=nested_active,
            resolved_components=resolved_components,
        )
