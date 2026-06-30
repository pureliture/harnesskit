# Adapter Authoring

Use after `component-author` has produced approved canonical component records. This skill designs and implements target adapter support while preserving `strict_runtime_truth`.

## Boundaries

- The input must be an existing canonical component under `components/**`.
- Do not perform ideation for what the component should be.
- Do not create a canonical component from scratch; route that to `component-author`.
- When this harness component is being ported itself, treat that as self-porting; it does not relax runtime truth requirements for other target components.
- Do not claim runtime support without observed CLI version, official docs source, isolated workspace probe, and captured probe evidence.
- Do not run live runtime probes unless explicitly requested.
- Do not mark capability status as `runtime_supported` unless `capabilities.yml` has evidence that satisfies the runtime gate.

## Blueprint And Evaluation Handoff

When an approved `blueprint.md` exists, use its adapter target plan and evaluation gates as the static adapter contract.

Before changing adapter files, confirm:

- the canonical component exists and passed component validation;
- the blueprint explicitly names the target or marks target support as deferred;
- evaluation gates separate static adapter evidence from live runtime evidence;
- runtime probe execution is explicitly requested before any live probe command runs.

Do not reinterpret requirements or component kind decisions in this phase. If the blueprint asks for runtime success without evidence, keep the runtime gate pending and report the missing evidence.

## Allowed Changes

Adapter authoring may change these surfaces when the target contract requires them:

- component manifest `targets` and target-specific `adapter` metadata;
- `adapters/<target>/adapter.yml`;
- adapter templates under `adapters/<target>/templates/**`;
- `scripts/adapters/build.py` when a target structure cannot be expressed by templates alone;
- adapter contract, migration, install-plan, and capability-matrix tests;
- runtime probe command metadata and evidence requirements.

It must not write live install surfaces such as `.claude/`, `.codex/`, `.agents/`, or project runtime state directly. Generated outputs belong under `dist/<target>/` through the adapter build path.

## Workflow

1. Inspect the canonical component manifest, body file, registry entry, and provenance.
2. Read `capabilities.yml` for the target adapter version, supported surfaces, and probe requirements.
3. Classify the target capability as `runtime_supported`, `runtime_supported_with_trust_state`, `doc_supported_pending_probe`, or unsupported.
4. Map canonical fields to target output paths and adapter metadata.
5. Update adapter implementation only as needed for the selected component kind and target.
6. Add static tests that prove generated files are current, parseable, and mapped to install-plan runtime surfaces.
7. Define the runtime probe contract, but leave execution to a separate runtime verification gate unless the user explicitly requests live probing.
8. Run static validation such as `uv run python scripts/adapters/build.py --check ...`, targeted pytest, and `uv run python scripts/components/validate.py --component ...` where applicable.

## Prompt-Submit And Pre-Invocation Pass-Through

For prompt-submit, pre-invocation, or similar user-submission hooks, adapter support must preserve the canonical textless-input contract:

- missing, empty, whitespace-only, image-only, or attachment-only submissions are valid user submissions;
- if the target payload or transcript fallback does not provide prompt text, the hook must exit/pass through without context injection;
- failure to build optional query/context must not block the user message flow;
- malformed hook envelope or wrong event wiring can remain fatal and should be reported as adapter/runtime wiring, not as invalid user content;
- static adapter tests should prove generated registrations do not add unsupported project hooks, and runtime support claims still require probe evidence in `capabilities.yml`.

## Completion Claims

Adapter authoring completion means static adapter generation and contracts are correct. It does not mean the target CLI loaded the skill, agent, hook, rule, or command in a real session.

Report completion using this split:

- canonical source changed;
- adapter metadata or implementation changed;
- generated `dist/<target>/...` output checked or written;
- static tests executed;
- runtime gate status from `capabilities.yml`;
- runtime probes not executed, or evidence directory if explicitly executed.
