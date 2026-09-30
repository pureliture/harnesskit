const MAX_RETAINED_CHUNKS = 7;

function deferred() {
  let resolve;
  const promise = new Promise((resolveValue) => {
    resolve = resolveValue;
  });
  return { promise, resolve };
}

function optionalInteger(value) {
  const normalized = Number(value);
  return Number.isSafeInteger(normalized) && normalized >= 0 ? normalized : null;
}

function normalizeHeader(value) {
  const viewGeneration = optionalInteger(value?.view_generation ?? value?.viewGeneration);
  const totalChunks = optionalInteger(value?.total_chunks ?? value?.totalChunks);
  const selectedChunkIndex = optionalInteger(
    value?.selected_chunk_index ?? value?.selectedChunkIndex,
  );
  const header = {
    previewSessionId: String(value?.preview_session_id ?? value?.previewSessionId ?? ""),
    viewGeneration,
    snapshotId: String(value?.snapshot_id ?? value?.snapshotId ?? ""),
    instanceId: String(value?.instance_id ?? value?.instanceId ?? ""),
    canonicalPath: String(value?.canonical_path ?? value?.canonicalPath ?? ""),
    sourceRevision: String(value?.source_revision ?? value?.sourceRevision ?? ""),
    changedSinceSnapshot:
      (value?.changed_since_snapshot ?? value?.changedSinceSnapshot) === true,
    format: String(value?.format ?? "text"),
    totalBytes: optionalInteger(value?.total_bytes ?? value?.totalBytes),
    totalChunks,
    chunkBytes: optionalInteger(value?.chunk_bytes ?? value?.chunkBytes),
    selectedChunkIndex,
    issue: value?.issue ?? null,
  };
  if (!header.previewSessionId
    || viewGeneration === null
    || !header.snapshotId
    || !header.instanceId
    || !header.canonicalPath
    || !header.sourceRevision
    || totalChunks === null
    || totalChunks < 1) return null;
  return header;
}

function normalizeChunk(value) {
  const chunkIndex = optionalInteger(value?.chunk_index ?? value?.chunkIndex);
  const normalized = {
    previewSessionId: String(value?.preview_session_id ?? value?.previewSessionId ?? ""),
    sourceRevision: String(value?.source_revision ?? value?.sourceRevision ?? ""),
    chunkIndex,
    isLast: (value?.is_last ?? value?.isLast) === true,
    content: value?.content,
  };
  if (!normalized.previewSessionId
    || !normalized.sourceRevision
    || chunkIndex === null
    || !normalized.content
    || typeof normalized.content !== "object") return null;
  return normalized;
}

export function createSourcePreviewState() {
  return {
    phase: "idle",
    viewGeneration: 0,
    selection: null,
    header: null,
    currentChunkIndex: null,
    chunks: new Map(),
    error: null,
  };
}

function safeClose(backend, request) {
  if (typeof backend?.closeLocalSourcePreview !== "function") return;
  try {
    Promise.resolve(backend.closeLocalSourcePreview(request)).catch(() => {});
  } catch (_error) {
    // Close is best-effort cancellation; stale results remain invalidated locally.
  }
}

export function createSourcePreviewController({ backend, onChange = () => {} } = {}) {
  let state = createSourcePreviewState();
  let generation = 0;
  let active = null;
  let desiredOpen = null;
  let desiredChunk = null;
  let latestRequestedChunkIndex = null;
  let disposed = false;

  const snapshot = () => ({
    ...state,
    selection: state.selection ? { ...state.selection } : null,
    header: state.header ? { ...state.header } : null,
    chunks: new Map(state.chunks),
  });

  const publish = () => {
    if (!disposed) onChange(snapshot());
  };

  const isCurrentSelection = (selection) => !disposed
    && state.viewGeneration === selection.viewGeneration
    && state.selection?.snapshotId === selection.snapshotId
    && state.selection?.instanceId === selection.instanceId;

  const retainedChunksWith = (chunk) => {
    const chunks = new Map(state.chunks);
    chunks.delete(chunk.chunkIndex);
    chunks.set(chunk.chunkIndex, chunk);
    while (chunks.size > MAX_RETAINED_CHUNKS) {
      chunks.delete(chunks.keys().next().value);
    }
    return chunks;
  };

  const setChunk = (chunk) => {
    state = {
      ...state,
      phase: "ready",
      currentChunkIndex: chunk.chunkIndex,
      chunks: retainedChunksWith(chunk),
      error: null,
    };
    publish();
  };

  const pump = () => {
    if (disposed || active) return;
    if (desiredOpen) {
      const pending = desiredOpen;
      desiredOpen = null;
      active = {
        kind: "open",
        viewGeneration: pending.selection.viewGeneration,
        promise: pending.deferred.promise,
      };
      let invocation;
      try {
        invocation = backend.openLocalSourcePreview(pending.selection);
      } catch (error) {
        invocation = Promise.reject(error);
      }
      Promise.resolve(invocation)
        .then((value) => {
          const header = normalizeHeader(value);
          if (!header || !isCurrentSelection(pending.selection)
            || header.viewGeneration !== pending.selection.viewGeneration
            || header.snapshotId !== pending.selection.snapshotId
            || header.instanceId !== pending.selection.instanceId) {
            if (header) {
              safeClose(backend, {
                viewGeneration: header.viewGeneration,
                previewSessionId: header.previewSessionId,
              });
            }
            pending.deferred.resolve(null);
            if (isCurrentSelection(pending.selection)) {
              state = { ...state, phase: "error", error: "source_preview_invalid" };
              publish();
            }
            return;
          }
          const initialIndex = Math.min(
            header.selectedChunkIndex ?? 0,
            header.totalChunks - 1,
          );
          state = {
            ...state,
            phase: "ready",
            header,
            currentChunkIndex: initialIndex,
            chunks: new Map(),
            error: null,
          };
          latestRequestedChunkIndex = initialIndex;
          publish();
          pending.deferred.resolve(header);
          const firstRead = deferred();
          desiredChunk = { index: initialIndex, deferred: firstRead };
        })
        .catch(() => {
          pending.deferred.resolve(null);
          if (!isCurrentSelection(pending.selection)) return;
          state = { ...state, phase: "error", error: "source_preview_open_failed" };
          publish();
        })
        .finally(() => {
          active = null;
          pump();
        });
      return;
    }

    if (!desiredChunk || !state.header || !state.selection) return;
    const pending = desiredChunk;
    desiredChunk = null;
    const identity = {
      viewGeneration: state.viewGeneration,
      previewSessionId: state.header.previewSessionId,
      sourceRevision: state.header.sourceRevision,
      chunkIndex: pending.index,
    };
    active = {
      kind: "read",
      ...identity,
      promise: pending.deferred.promise,
    };
    let invocation;
    try {
      invocation = backend.readLocalSourcePreviewChunk({
        previewSessionId: identity.previewSessionId,
        sourceRevision: identity.sourceRevision,
        chunkIndex: identity.chunkIndex,
      });
    } catch (error) {
      invocation = Promise.reject(error);
    }
    Promise.resolve(invocation)
      .then((value) => {
        const chunk = normalizeChunk(value);
        const current = !disposed
          && state.viewGeneration === identity.viewGeneration
          && state.header?.previewSessionId === identity.previewSessionId
          && state.header?.sourceRevision === identity.sourceRevision;
        if (!current
          || !chunk
          || chunk.previewSessionId !== identity.previewSessionId
          || chunk.sourceRevision !== identity.sourceRevision
          || chunk.chunkIndex !== identity.chunkIndex) {
          pending.deferred.resolve(null);
          if (current) {
            state = { ...state, error: "source_preview_chunk_invalid" };
            publish();
          }
          return;
        }
        if (latestRequestedChunkIndex === identity.chunkIndex) {
          setChunk(chunk);
        } else {
          state = { ...state, chunks: retainedChunksWith(chunk) };
        }
        pending.deferred.resolve(chunk);
      })
      .catch(() => {
        pending.deferred.resolve(null);
        if (disposed
          || state.viewGeneration !== identity.viewGeneration
          || state.header?.previewSessionId !== identity.previewSessionId) return;
        state = { ...state, error: "source_preview_read_failed" };
        publish();
      })
      .finally(() => {
        active = null;
        pump();
      });
  };

  const requestChunk = (requestedIndex) => {
    const index = optionalInteger(requestedIndex);
    const totalChunks = state.header?.totalChunks ?? 0;
    if (disposed || index === null || index >= totalChunks) return Promise.resolve(null);
    const cached = state.chunks.get(index);
    if (cached) {
      latestRequestedChunkIndex = index;
      setChunk(cached);
      return Promise.resolve(cached);
    }
    if (active?.kind === "read"
      && active.viewGeneration === state.viewGeneration
      && active.chunkIndex === index) return active.promise;
    if (desiredChunk?.index === index) return desiredChunk.deferred.promise;
    latestRequestedChunkIndex = index;
    if (desiredChunk) desiredChunk.deferred.resolve(null);
    const pending = deferred();
    desiredChunk = { index, deferred: pending };
    state = { ...state, currentChunkIndex: index, error: null };
    publish();
    pump();
    return pending.promise;
  };

  const select = ({ snapshotId, instanceId } = {}) => {
    const nextSnapshotId = String(snapshotId ?? "").trim();
    const nextInstanceId = String(instanceId ?? "").trim();
    if (disposed || !nextSnapshotId || !nextInstanceId) return Promise.resolve(null);

    const previousGeneration = state.viewGeneration;
    const previousSessionId = state.header?.previewSessionId ?? null;
    if (previousGeneration > 0) {
      safeClose(backend, {
        viewGeneration: previousGeneration,
        previewSessionId: previousSessionId,
      });
    }
    generation += 1;
    if (desiredOpen) desiredOpen.deferred.resolve(null);
    if (desiredChunk) desiredChunk.deferred.resolve(null);
    desiredChunk = null;
    latestRequestedChunkIndex = null;
    const selection = {
      snapshotId: nextSnapshotId,
      instanceId: nextInstanceId,
      viewGeneration: generation,
    };
    const pending = deferred();
    desiredOpen = { selection, deferred: pending };
    state = {
      phase: "opening",
      viewGeneration: generation,
      selection: { snapshotId: nextSnapshotId, instanceId: nextInstanceId },
      header: null,
      currentChunkIndex: null,
      chunks: new Map(),
      error: null,
    };
    publish();
    pump();
    return pending.promise;
  };

  const clear = () => {
    if (disposed) return;
    const previousGeneration = state.viewGeneration;
    const previousSessionId = state.header?.previewSessionId ?? null;
    generation += 1;
    if (desiredOpen) desiredOpen.deferred.resolve(null);
    if (desiredChunk) desiredChunk.deferred.resolve(null);
    desiredOpen = null;
    desiredChunk = null;
    latestRequestedChunkIndex = null;
    if (previousGeneration > 0) {
      safeClose(backend, {
        viewGeneration: previousGeneration,
        previewSessionId: previousSessionId,
      });
    }
    state = { ...createSourcePreviewState(), viewGeneration: generation };
    publish();
  };

  const dispose = () => {
    if (disposed) return;
    const previousGeneration = state.viewGeneration;
    const previousSessionId = state.header?.previewSessionId ?? null;
    disposed = true;
    if (desiredOpen) desiredOpen.deferred.resolve(null);
    if (desiredChunk) desiredChunk.deferred.resolve(null);
    desiredOpen = null;
    desiredChunk = null;
    latestRequestedChunkIndex = null;
    if (previousGeneration > 0) {
      safeClose(backend, {
        viewGeneration: previousGeneration,
        previewSessionId: previousSessionId,
      });
    }
    state = createSourcePreviewState();
  };

  return Object.freeze({
    select,
    clear,
    requestChunk,
    getState: snapshot,
    dispose,
  });
}

function appendText(parent, text) {
  if (text) parent.append(String(text));
}

function semanticNodes(fragment) {
  const nodes = fragment?.nodes;
  return Array.isArray(nodes) ? nodes : [];
}

function semanticChildren(node) {
  return Array.isArray(node?.children) ? node.children : [];
}

function appendSemanticChildren(document, parent, nodes) {
  for (const node of nodes) appendSemanticNode(document, parent, node);
}

function appendSemanticNode(document, parent, node) {
  const kind = String(node?.kind ?? "");
  if (kind === "text") {
    appendText(parent, node?.text);
    return;
  }
  let element = null;
  if (kind === "heading") {
    const level = Math.min(6, Math.max(1, Number(node?.level) || 1));
    element = document.createElement(`h${level}`);
  } else if (kind === "paragraph") {
    element = document.createElement("p");
  } else if (kind === "unordered_list") {
    element = document.createElement("ul");
  } else if (kind === "ordered_list") {
    element = document.createElement("ol");
    const start = Number(node?.start);
    if (Number.isSafeInteger(start) && start > 1) element.setAttribute("start", start);
  } else if (kind === "list_item") {
    element = document.createElement("li");
  } else if (kind === "strong") {
    element = document.createElement("strong");
  } else if (kind === "emphasis") {
    element = document.createElement("em");
  } else if (kind === "link_text") {
    element = document.createElement("span");
    element.setAttribute("class", "local-source-link-text");
  } else if (kind === "inline_code") {
    element = document.createElement("code");
    element.textContent = String(node?.text ?? "");
  } else if (kind === "blockquote") {
    element = document.createElement("blockquote");
  } else if (kind === "table") {
    element = document.createElement("table");
    element.setAttribute("class", "local-source-table");
  } else if (kind === "table_head") {
    element = document.createElement("thead");
  } else if (kind === "table_body") {
    element = document.createElement("tbody");
  } else if (kind === "table_row") {
    element = document.createElement("tr");
  } else if (kind === "table_header_cell") {
    element = document.createElement("th");
    element.setAttribute("scope", "col");
  } else if (kind === "table_cell") {
    element = document.createElement("td");
  } else if (kind === "code_block") {
    element = document.createElement("pre");
    const code = document.createElement("code");
    code.textContent = String(node?.text ?? "");
    element.appendChild(code);
  } else if (kind === "thematic_break") {
    element = document.createElement("hr");
  } else if (kind === "literal_text") {
    element = document.createElement("pre");
    element.textContent = String(node?.text ?? "");
  }
  if (!element) return;
  if (!new Set(["inline_code", "code_block", "thematic_break", "literal_text"]).has(kind)) {
    appendSemanticChildren(document, element, semanticChildren(node));
  }
  parent.appendChild(element);
}

function mergeSemanticNodes(nodes) {
  const merged = [];
  for (const node of nodes) {
    if (!node || typeof node !== "object") continue;
    const previous = merged.at(-1);
    if (previous?.kind === node.kind
      && new Set([
        "heading",
        "paragraph",
        "unordered_list",
        "ordered_list",
        "blockquote",
        "table",
      ]).has(node.kind)) {
      previous.children = [...semanticChildren(previous), ...semanticChildren(node)];
    } else if (previous?.kind === node.kind
      && new Set(["code_block", "literal_text"]).has(node.kind)) {
      previous.text = `${String(previous.text ?? "")}${String(node.text ?? "")}`;
    } else {
      merged.push({ ...node, children: semanticChildren(node).map((child) => ({ ...child })) });
    }
  }
  return merged;
}

function markdownFragments(content) {
  const fragments = content?.block_fragments ?? content?.blockFragments;
  return Array.isArray(fragments) ? fragments : [];
}

function markdownSemanticGroups(sourcePreview, currentIndex) {
  const groups = [];
  const fragments = adjacentMarkdownChunks(sourcePreview, currentIndex)
    .flatMap((visibleChunk) => markdownFragments(visibleChunk.content));
  for (const fragment of fragments) {
    const blockId = String(fragment?.block_id ?? fragment?.blockId ?? "");
    const startsBlock = (fragment?.starts_block ?? fragment?.startsBlock) === true;
    const endsBlock = (fragment?.ends_block ?? fragment?.endsBlock) === true;
    const previous = groups.at(-1);
    if (previous && blockId && previous.blockId === blockId && !previous.endsBlock && !startsBlock) {
      previous.nodes.push(...semanticNodes(fragment));
      previous.endsBlock = endsBlock;
    } else {
      groups.push({ blockId, nodes: [...semanticNodes(fragment)], endsBlock });
    }
  }
  return groups;
}

function commitSourcePreview(host, ...children) {
  host.replaceChildren(...children);
}

function adjacentMarkdownChunks(sourcePreview, currentIndex) {
  const chunks = sourcePreview?.chunks;
  const current = chunks?.get?.(currentIndex);
  if (current?.content?.format !== "markdown") return current ? [current] : [];
  let first = currentIndex;
  let last = currentIndex;
  while (chunks.get(first - 1)?.content?.format === "markdown") first -= 1;
  while (chunks.get(last + 1)?.content?.format === "markdown") last += 1;
  const adjacent = [];
  for (let index = first; index <= last; index += 1) adjacent.push(chunks.get(index));
  return adjacent;
}

export function renderSourcePreviewContent(host, sourcePreview) {
  const document = host?.ownerDocument;
  if (!host?.replaceChildren || !document?.createElement) return;
  const index = optionalInteger(sourcePreview?.currentChunkIndex);
  const chunk = index === null ? null : sourcePreview?.chunks?.get?.(index);
  if (!chunk) {
    const status = document.createElement("p");
    status.textContent = sourcePreview?.error
      ? "원문을 불러오지 못했습니다. 다시 선택하거나 스캔을 갱신하세요."
      : "원문을 불러오는 중입니다.";
    status.setAttribute("role", sourcePreview?.error ? "alert" : "status");
    commitSourcePreview(host, status);
    return;
  }

  const content = chunk.content ?? {};
  if (content.format === "text") {
    const pre = document.createElement("pre");
    pre.textContent = String(content.before ?? "");
    if (typeof content.selected === "string") {
      const mark = document.createElement("mark");
      mark.textContent = content.selected;
      pre.appendChild(mark);
    }
    appendText(pre, content.after);
    commitSourcePreview(host, pre);
    return;
  }

  const wrapper = document.createElement("div");
  wrapper.setAttribute("role", "document");
  for (const group of markdownSemanticGroups(sourcePreview, index)) {
    appendSemanticChildren(document, wrapper, mergeSemanticNodes(group.nodes));
  }
  commitSourcePreview(host, wrapper);
}
