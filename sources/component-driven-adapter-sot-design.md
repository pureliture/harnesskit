# Component-Driven Adapter SoT Design Source

This public source record summarizes the approved private design used by the
HarnessKit public projection provenance. The private drafting path remains
excluded from the public artifact; this file is the resolvable public reference
for projection consumers.

## Design Summary

- The registry and profiles select canonical components first; adapter renderers
  then emit target-specific outputs.
- The adapter boundary is target-specific rendering, not component intent
  inference from path names.
- Profile and scope selection happen before install-plan emission.
- Shared config artifacts need explicit ownership and merge policy metadata.
- Public workflow records are card/process sources unless a runtime runner is
  explicitly introduced.
- Path-preserving changes can reuse current evidence only when the runtime
  family, surface, command identity, and scope are unchanged.

## Public Projection Use

The public `harnesskit.workflow.harness-creation` component uses this design
source for workflow-card sequencing and the public adapter authoring boundary.
No private drafting artifact is required at runtime.
