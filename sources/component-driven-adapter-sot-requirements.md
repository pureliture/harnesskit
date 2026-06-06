# Component-Driven Adapter SoT Requirements Source

This public source record summarizes the approved private requirements used by
the HarnessKit public projection provenance. The private drafting path remains
excluded from the public artifact; this file is the resolvable public reference
for projection consumers.

## Approved Requirements

- Canonical components distinguish installable kinds from internal or
  source-only records.
- Installable kinds are `skill`, `agent`, `hook`, and `rule`.
- `workflow` and `command` records are not directly installable adapter kinds.
- Executable workflows should be modeled as `skill` records with
  `subtype: workflow_trigger`.
- Adapter rendering must consume component SoT fields such as kind, subtype,
  scopes, target support, ownership, runtime contract, and evidence status.
- User-scope components must not leak into project-scope installs, and
  project-only components must not leak into user-scope installs.
- User-level JSON hook merges must update only HarnessKit-owned hook groups and
  preserve unrelated user entries.
- Runtime support claims require matching runtime evidence; static proof alone
  must not be recorded as runtime proof.

## Public Projection Use

The public `harnesskit.workflow.harness-creation` component uses this source
only for the non-installable workflow boundary and adapter SoT sequencing. No
private drafting artifact is required at runtime.
