# Grill to Spec

Use only when the user explicitly asks to record an agreed plan or decision as a document. This is a fixed
composition over `/grill`.

1. Invoke `/grill` first in the same session. Do not restate or reproduce its fact checking, evidence-gap handling,
   or one-decision questioning. If it is unavailable, or returns an evidence gap, unresolved decision, stop, or defer,
   create no document and return control.
2. After Grill returns a shared understanding, write only the document or documents the user explicitly requested.
   If the requested document type or location is missing, do not choose one or create a generic spec; return that
   missing request detail.
3. Mark every requested document as a draft. Every document-body sentence must correspond to an agreed objective,
   known fact, user decision, boundary, observable completion condition, or relevant existing seam/reuse rationale
   supplied by Grill. Add only the draft and per-document approval state as wrapper metadata. Omit a detail that has
   no supplied agreement.
4. Each document needs its own explicit approval. End each draft by explicitly stating it awaits approval of that
   document. A requirements document followed by a design document is allowed only when both were requested;
   approval of one neither approves the other nor opens implementation.
5. On approval, mark only that document approved and return control. Its approval does not begin other work.
6. The Markdown document is the source of truth. If optional HTML is requested, keep the document approval boundary
   unchanged and leave generation or rendering to the separately selected presentation owner.
