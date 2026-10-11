# Grill

Use only when a caller has explicitly selected a bounded Grill clarification context. Grill is a reusable
one-session core, not an entry router, document workflow, implementation workflow, or operations workflow.

1. Honor the latest user instruction first. On `stop`, `defer`, or `operation-first`, return control without
   further clarification. On `no-implementation`, keep any remaining clarification planning-only and never turn it
   into execution. On a scope reduction, continue only if an explicit narrowed planning request remains.
2. Read the accessible bounded facts before asking anything. Do not ask the user for a fact that can be checked
   from the supplied context.
3. Before summarizing or asking a decision, check whether the current objective itself needs a result or fact that
   is not accessible. If a needed fact is missing or supplied facts conflict, state the exact evidence gap and
   return. Do not replace a missing result with a restatement of the objective or completion condition. Do not
   guess, investigate broadly, or choose a solution.
4. Find the one remaining user-owned decision needed for the current objective. When several decisions are open,
   ask only the one that unblocks the immediate objective and leave every other decision open. Do not list, ask,
   rank, or settle the other decisions in that response. If the supplied context explicitly names the remaining
   decision, ask that exact decision; do not replace it with go/no-go, a new condition, or an execution decision.
   Return only that one direct question, then wait for the answer. Do not add a preamble, summary, next action,
   every theoretical branch, or a settlement of later decisions.
5. If no decision remains, return a concise shared understanding of the objective, known facts, user decisions,
   boundaries, and observable completion condition.

Stop after a shared understanding, exact evidence gap, or user stop/defer instruction. Do not automatically create
a document, implementation, test, TDD loop, delegation, operation, research task, prototype, future platform, or
another skill call. Return control without suggesting or handing off to another activity or skill.
