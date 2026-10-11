# Intent Guide

Use as the user-visible entry guide at the start of every user-facing task. Give one safe starting result and return
control. This defines the target entry behavior without creating a hidden gate, a routing platform, a researcher, an operator, an implementation or
documentation workflow, or a catalog of specialist skills.

1. Honor the latest user instruction first.
   - On `stop` or `defer`, state that work is paused and return with no recommendation.
   - On `operation-first`, keep a bounded explicit operation or fact lookup as `general-work`; do not turn it into
     planning, a helper script, TDD, or a spec.
   - On `no-implementation`, retain that boundary. On a scope reduction, consider only the narrowed request; return
     when no request remains.
2. Read only the current request, conversation, and accessible bounded evidence. Use an available fact before asking
   for it. Do not research, inspect new sources broadly, compare products, write a document, run an operation, or
   change anything.
3. Before recommending `grill-to-spec` for a request that adds a durable responsibility, custom implementation,
   store, queue, scheduler, lifecycle, public contract, or platform, check whether a materially viable existing,
   commercial, open-source, operational, or scope-reduction direction is known but still undecided.
   - Do not recommend `grill-to-spec` while that direction is open.
   - If one user-owned direction decision can close it with the available facts, recommend `grill`.
   - If real evidence tasks and decisions block one another across sessions, recommend `wayfinder`.
   - Otherwise return `evidence-needed` with only the missing fact or decision.
   - Do not research, compare, choose, or purchase an alternative for the user.
4. Otherwise choose exactly one primary result.
   - `general-work`: an explicit, bounded answer, operation, smoke, rollback, narrow fix, or fact lookup has no
     user-owned decision left to make. It starts no skill. Do not use it merely to list options for a decision the
     user still owns.
   - `grill`: one session has enough facts and a user-owned decision, clarification, or non-document plan remains.
     This includes organizing the options of a known policy choice for the user to make.
   - `grill-to-spec`: the user explicitly wants a document, the problem and current scope are known, and the material
     solution direction is already closed or can safely close through its fixed Grill composition.
   - `wayfinder`: multiple evidence tasks or decisions form a real blocker graph that cannot safely close in one
     session. Difficulty, many repositories, or many options alone are not enough.
   - `evidence-needed`: the minimum fact or user-owned decision for a safe result is absent. It is not a route.
5. State the one primary result, a short reason, the evidence checked, and any one missing condition. Mention a
   secondary option only when it is genuinely relevant; never present a destination catalog. End by returning
   control. A recommendation is not approval, handoff, or execution authority.
6. When the user explicitly selects a recommended route, acknowledge that selection and return control to that
   route's own invocation boundary. Do not call or execute the selected skill yourself. Do not automatically route to
   System Designer, Codebase Architecture Manager, TDD, debugging, or any other specialist.

No recommendation may create a hook, scheduler, route history, database, compatibility path, or new routing
component. This user-visible guidance makes no installation, discovery, or runtime behavior claim.
