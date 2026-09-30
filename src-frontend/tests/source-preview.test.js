import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  createSourcePreviewController,
  renderSourcePreviewContent,
} from "../source-preview.js";

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolveValue, rejectValue) => {
    resolve = resolveValue;
    reject = rejectValue;
  });
  return { promise, resolve, reject };
}

function flush() {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

function header(instanceId, generation, selectedChunkIndex = 0) {
  return {
    preview_session_id: `session-${instanceId}`,
    view_generation: generation,
    snapshot_id: "snapshot-1",
    instance_id: instanceId,
    canonical_path: `/fixture-home/${instanceId}.md`,
    source_revision: `revision-${instanceId}`,
    changed_since_snapshot: false,
    format: "text",
    total_bytes: 1024,
    total_chunks: 12,
    chunk_bytes: 65536,
    selected_chunk_index: selectedChunkIndex,
    issue: null,
  };
}

function chunk(instanceId, index, content = null) {
  return {
    preview_session_id: `session-${instanceId}`,
    source_revision: `revision-${instanceId}`,
    chunk_index: index,
    is_last: index === 11,
    content: content ?? {
      format: "text",
      before: `chunk-${index}`,
      selected: null,
      after: "",
    },
  };
}

test("source controller coalesces an active index and keeps only the latest desired chunk", async () => {
  const opening = deferred();
  const reads = [];
  const readDeferred = new Map();
  const backend = {
    openLocalSourcePreview: () => opening.promise,
    readLocalSourcePreviewChunk(request) {
      reads.push(request.chunkIndex);
      const pending = deferred();
      readDeferred.set(request.chunkIndex, pending);
      return pending.promise;
    },
    closeLocalSourcePreview: async () => ({ closed: true, cancelled: false }),
  };
  const controller = createSourcePreviewController({ backend });

  controller.select({ snapshotId: "snapshot-1", instanceId: "item-a" });
  opening.resolve(header("item-a", 1));
  await flush();
  assert.deepEqual(reads, [0]);

  const same = controller.requestChunk(0);
  controller.requestChunk(2);
  const latest = controller.requestChunk(3);
  const latestDuplicate = controller.requestChunk(3);
  assert.equal(same, controller.requestChunk(0));
  assert.equal(latest, latestDuplicate);
  assert.deepEqual(reads, [0]);

  readDeferred.get(0).resolve(chunk("item-a", 0));
  await flush();
  assert.deepEqual(reads, [0, 3]);
  readDeferred.get(3).resolve(chunk("item-a", 3));
  await latest;
  assert.equal(controller.getState().currentChunkIndex, 3);
  assert.deepEqual([...controller.getState().chunks.keys()], [0, 3]);
});

test("source controller invalidates a stale open and opens only the latest selection next", async () => {
  const opens = [deferred(), deferred()];
  const openRequests = [];
  const closes = [];
  const backend = {
    openLocalSourcePreview(request) {
      openRequests.push(request);
      return opens[openRequests.length - 1].promise;
    },
    readLocalSourcePreviewChunk: async (request) => chunk("item-b", request.chunkIndex),
    closeLocalSourcePreview(request) {
      closes.push(request);
      return Promise.resolve({ closed: true, cancelled: true });
    },
  };
  const controller = createSourcePreviewController({ backend });

  controller.select({ snapshotId: "snapshot-1", instanceId: "item-a" });
  controller.select({ snapshotId: "snapshot-1", instanceId: "item-b" });
  assert.equal(openRequests.length, 1);
  assert.equal(closes[0].viewGeneration, 1);

  opens[0].resolve(header("item-a", 1));
  await flush();
  assert.equal(openRequests.length, 2);
  assert.ok(closes.some((request) => request.previewSessionId === "session-item-a"));

  opens[1].resolve(header("item-b", 2));
  await flush();
  assert.equal(controller.getState().header.instanceId, "item-b");
  assert.equal(controller.getState().viewGeneration, 2);
});

test("source controller retains at most seven chunks and can reread an evicted chunk", async () => {
  const reads = [];
  const backend = {
    openLocalSourcePreview: async (request) => header(request.instanceId, request.viewGeneration),
    readLocalSourcePreviewChunk: async (request) => {
      reads.push(request.chunkIndex);
      return chunk("item-a", request.chunkIndex);
    },
    closeLocalSourcePreview: async () => ({ closed: true, cancelled: false }),
  };
  const controller = createSourcePreviewController({ backend });

  controller.select({ snapshotId: "snapshot-1", instanceId: "item-a" });
  await flush();
  for (let index = 1; index <= 7; index += 1) {
    await controller.requestChunk(index);
  }
  const retained = [...controller.getState().chunks.keys()];
  assert.equal(retained.length, 7);
  assert.deepEqual(retained, [1, 2, 3, 4, 5, 6, 7]);

  await controller.requestChunk(0);
  assert.equal(reads.filter((index) => index === 0).length, 2);
  assert.deepEqual([...controller.getState().chunks.keys()], [2, 3, 4, 5, 6, 7, 0]);
});

test("cached navigation stays selected when an older in-flight read settles", async () => {
  const reads = new Map();
  const backend = {
    openLocalSourcePreview: async (request) => header(request.instanceId, request.viewGeneration),
    readLocalSourcePreviewChunk(request) {
      const pending = deferred();
      reads.set(request.chunkIndex, pending);
      return pending.promise;
    },
    closeLocalSourcePreview: async () => ({ closed: true, cancelled: false }),
  };
  const controller = createSourcePreviewController({ backend });

  controller.select({ snapshotId: "snapshot-1", instanceId: "item-a" });
  await flush();
  reads.get(0).resolve(chunk("item-a", 0));
  await flush();
  controller.requestChunk(1);
  await flush();
  assert.ok(reads.has(1));

  await controller.requestChunk(0);
  reads.get(1).resolve(chunk("item-a", 1));
  await flush();

  assert.equal(controller.getState().currentChunkIndex, 0);
});

test("clearing a source preview discards its in-flight read and latest queued chunk", async () => {
  const reads = [];
  const pendingReads = new Map();
  const backend = {
    openLocalSourcePreview: async (request) => header(request.instanceId, request.viewGeneration),
    readLocalSourcePreviewChunk(request) {
      reads.push(request.chunkIndex);
      const pending = deferred();
      pendingReads.set(request.chunkIndex, pending);
      return pending.promise;
    },
    closeLocalSourcePreview: async () => ({ closed: true, cancelled: true }),
  };
  const controller = createSourcePreviewController({ backend });

  controller.select({ snapshotId: "snapshot-1", instanceId: "item-a" });
  await flush();
  const inFlight = controller.requestChunk(0);
  const queued = controller.requestChunk(7);

  controller.clear();
  assert.equal(await queued, null);
  assert.equal(controller.getState().phase, "idle");
  assert.equal(controller.getState().selection, null);
  assert.equal(controller.getState().currentChunkIndex, null);
  assert.deepEqual([...controller.getState().chunks.keys()], []);

  pendingReads.get(0).resolve(chunk("item-a", 0));
  assert.equal(await inFlight, null);
  await flush();

  assert.deepEqual(reads, [0]);
  assert.equal(controller.getState().phase, "idle");
  assert.equal(controller.getState().error, null);
  assert.deepEqual([...controller.getState().chunks.keys()], []);
});

for (const outcome of ["resolve", "reject"]) {
  test(`a new source selection ignores an old read ${outcome} before opening its session`, async () => {
    const oldRead = deferred();
    const newRead = deferred();
    const openRequests = [];
    const backend = {
      openLocalSourcePreview(request) {
        openRequests.push(request);
        return Promise.resolve({
          ...header(request.instanceId, request.viewGeneration),
          snapshot_id: request.snapshotId,
        });
      },
      readLocalSourcePreviewChunk(request) {
        return request.previewSessionId === "session-item-a"
          ? oldRead.promise
          : newRead.promise;
      },
      closeLocalSourcePreview: async () => ({ closed: true, cancelled: true }),
    };
    const controller = createSourcePreviewController({ backend });

    controller.select({ snapshotId: "snapshot-1", instanceId: "item-a" });
    await flush();
    controller.select({ snapshotId: "snapshot-2", instanceId: "item-b" });

    if (outcome === "resolve") oldRead.resolve(chunk("item-a", 0));
    else oldRead.reject(new Error("old read failed"));
    await flush();

    assert.deepEqual(openRequests.map((request) => request.snapshotId), ["snapshot-1", "snapshot-2"]);
    assert.equal(controller.getState().phase, "ready");
    assert.equal(controller.getState().selection.snapshotId, "snapshot-2");
    assert.equal(controller.getState().header.instanceId, "item-b");
    assert.equal(controller.getState().error, null);
    assert.deepEqual([...controller.getState().chunks.keys()], []);

    newRead.resolve(chunk("item-b", 0));
    await flush();
  });
}

test("the same instance in a newer snapshot opens and reads with a new preview identity", async () => {
  const reads = [];
  const pendingReads = [deferred(), deferred()];
  const closes = [];
  const backend = {
    openLocalSourcePreview(request) {
      const suffix = request.snapshotId === "snapshot-1" ? "old" : "new";
      return Promise.resolve({
        ...header(request.instanceId, request.viewGeneration),
        preview_session_id: `session-${suffix}`,
        snapshot_id: request.snapshotId,
        source_revision: `revision-${suffix}`,
      });
    },
    readLocalSourcePreviewChunk(request) {
      reads.push(request);
      return pendingReads[reads.length - 1].promise;
    },
    closeLocalSourcePreview(request) {
      closes.push(request);
      return Promise.resolve({ closed: true, cancelled: true });
    },
  };
  const controller = createSourcePreviewController({ backend });

  controller.select({ snapshotId: "snapshot-1", instanceId: "item-a" });
  await flush();
  controller.select({ snapshotId: "snapshot-2", instanceId: "item-a" });
  pendingReads[0].resolve({
    ...chunk("item-a", 0),
    preview_session_id: "session-old",
    source_revision: "revision-old",
  });
  await flush();

  assert.deepEqual(reads.map(({ previewSessionId, sourceRevision }) => ({
    previewSessionId,
    sourceRevision,
  })), [
    { previewSessionId: "session-old", sourceRevision: "revision-old" },
    { previewSessionId: "session-new", sourceRevision: "revision-new" },
  ]);
  assert.ok(closes.some((request) => request.previewSessionId === "session-old"));
  assert.equal(controller.getState().selection.snapshotId, "snapshot-2");
  assert.equal(controller.getState().header.snapshotId, "snapshot-2");
  assert.equal(controller.getState().header.previewSessionId, "session-new");
  assert.deepEqual([...controller.getState().chunks.keys()], []);

  pendingReads[1].resolve({
    ...chunk("item-a", 0, {
      format: "text", before: "new snapshot", selected: null, after: "",
    }),
    preview_session_id: "session-new",
    source_revision: "revision-new",
  });
  await flush();

  assert.equal(controller.getState().chunks.get(0).content.before, "new snapshot");
});

test("rapid source range input keeps only the latest desired chunk", async () => {
  const reads = [];
  const pendingReads = new Map();
  const backend = {
    openLocalSourcePreview: async (request) => header(request.instanceId, request.viewGeneration),
    readLocalSourcePreviewChunk(request) {
      reads.push(request.chunkIndex);
      const pending = deferred();
      pendingReads.set(request.chunkIndex, pending);
      return pending.promise;
    },
    closeLocalSourcePreview: async () => ({ closed: true, cancelled: false }),
  };
  const controller = createSourcePreviewController({ backend });

  controller.select({ snapshotId: "snapshot-1", instanceId: "item-a" });
  await flush();
  const firstDesired = controller.requestChunk(1);
  const secondDesired = controller.requestChunk(5);
  const latestDesired = controller.requestChunk(11);

  assert.equal(await firstDesired, null);
  assert.equal(await secondDesired, null);
  assert.deepEqual(reads, [0]);

  pendingReads.get(0).resolve(chunk("item-a", 0));
  await flush();
  assert.deepEqual(reads, [0, 11]);

  pendingReads.get(11).resolve(chunk("item-a", 11));
  await latestDesired;
  assert.equal(controller.getState().currentChunkIndex, 11);
  assert.deepEqual([...controller.getState().chunks.keys()], [0, 11]);
});

class FakeNode {
  constructor(tagName, ownerDocument) {
    this.tagName = tagName.toUpperCase();
    this.ownerDocument = ownerDocument;
    this.children = [];
    this.attributes = new Map();
    this.textContent = "";
  }

  append(...children) {
    this.children.push(...children);
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  replaceChildren(...children) {
    this.children = [...children];
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  querySelectorAll(selector) {
    const matches = [];
    const visit = (node) => {
      for (const child of node.children) {
        if (!(child instanceof FakeNode)) continue;
        const attribute = selector.startsWith("[") && selector.endsWith("]")
          ? selector.slice(1, -1)
          : null;
        if (attribute ? child.attributes.has(attribute) : child.tagName === selector.toUpperCase()) {
          matches.push(child);
        }
        visit(child);
      }
    };
    visit(this);
    return matches;
  }

  set innerHTML(_value) {
    throw new Error("source renderer must not use innerHTML");
  }
}

class FakeDocument {
  createElement(tagName) {
    return new FakeNode(tagName, this);
  }
}

function allNodes(node) {
  return [node, ...node.children.filter((child) => child instanceof FakeNode).flatMap(allNodes)];
}

function visibleText(node) {
  return String(node.textContent ?? "")
    + node.children.map((child) => (
      child instanceof FakeNode ? visibleText(child) : String(child)
    )).join("");
}

const markdownSemanticFixture = JSON.parse(readFileSync(
  new URL("../../src-tauri/tests/fixtures/m4e_markdown_semantic_contract.json", import.meta.url),
  "utf8",
));

test("source renderer consumes the shared backend semantic tree without Markdown parsing", () => {
  const document = new FakeDocument();
  const host = new FakeNode("div", document);

  renderSourcePreviewContent(host, {
    currentChunkIndex: 0,
    chunks: new Map([[0, chunk("item-a", 0, markdownSemanticFixture.content)]]),
  });

  assert.deepEqual(allNodes(host).map((node) => node.tagName), markdownSemanticFixture.expected_dom_tags);
  assert.equal(visibleText(host), markdownSemanticFixture.expected_visible_text);

  const rendererSource = readFileSync(new URL("../source-preview.js", import.meta.url), "utf8");
  for (const forbiddenParser of [
    "appendSafeInlineMarkdown",
    "stripHeadingMarkers",
    "stripBlockquoteMarkers",
    "stripFencedCodeMarkers",
    "markdownList(",
    "tokenPattern",
  ]) {
    assert.equal(
      rendererSource.includes(forbiddenParser),
      false,
      `frontend renderer must not own Markdown parser ${forbiddenParser}`,
    );
  }
});

test("source renderer creates only inert allowlisted nodes and assigns source through textContent", () => {
  const document = new FakeDocument();
  const host = new FakeNode("div", document);
  const hostile = "<script>steal()</script><img src=https://example.test/x>";

  renderSourcePreviewContent(host, {
    currentChunkIndex: 0,
    chunks: new Map([[0, chunk("item-a", 0, {
      format: "text",
      before: hostile,
      selected: "<a href=https://example.test>selected</a>",
      after: "tail",
    })]]),
  });
  const textNodes = allNodes(host);
  assert.deepEqual(textNodes.map((node) => node.tagName), ["DIV", "PRE", "MARK"]);
  assert.equal(host.attributes.has("aria-description"), false);
  assert.equal(textNodes[1].textContent, hostile);
  assert.equal(textNodes[2].textContent, "<a href=https://example.test>selected</a>");

  renderSourcePreviewContent(host, {
    currentChunkIndex: 1,
    chunks: new Map([[1, chunk("item-a", 1, {
      format: "markdown",
      block_fragments: [{
        block_id: "block-1",
        kind: "paragraph",
        nodes: [{ kind: "paragraph", children: [{ kind: "text", text: hostile }] }],
        starts_block: true,
        ends_block: true,
      }],
    })]]),
  });
  const markdownNodes = allNodes(host);
  assert.deepEqual(markdownNodes.map((node) => node.tagName), ["DIV", "DIV", "P"]);
  assert.equal(visibleText(markdownNodes[2]), hostile);
});

test("source renderer builds an inert semantic table from adjacent table fragments", () => {
  const document = new FakeDocument();
  const host = new FakeNode("div", document);
  const hostile = "<img src=https://example.invalid/secret>";

  renderSourcePreviewContent(host, {
    currentChunkIndex: 0,
    chunks: new Map([[0, chunk("item-a", 0, {
      format: "markdown",
      block_fragments: [{
        block_id: "table-1",
        kind: "table",
        nodes: [{
          kind: "table",
          children: [
            {
              kind: "table_head",
              children: [{
                kind: "table_row",
                children: [
                  { kind: "table_header_cell", children: [{ kind: "text", text: "Name" }] },
                  { kind: "table_header_cell", children: [{ kind: "text", text: "Value" }] },
                ],
              }],
            },
            {
              kind: "table_body",
              children: [{
                kind: "table_row",
                children: [
                  { kind: "table_cell", children: [{ kind: "text", text: "safe" }] },
                  { kind: "table_cell", children: [{ kind: "text", text: hostile }] },
                ],
              }],
            },
          ],
        }],
        starts_block: true,
        ends_block: true,
      }],
    })]]),
  });

  const nodes = allNodes(host);
  assert.deepEqual(
    nodes.map((node) => node.tagName),
    ["DIV", "DIV", "TABLE", "THEAD", "TR", "TH", "TH", "TBODY", "TR", "TD", "TD"],
  );
  assert.equal(visibleText(nodes.at(-1)), hostile);
});

test("source renderer keeps adjacent tables with different block identities separate", () => {
  const document = new FakeDocument();
  const host = new FakeNode("div", document);

  renderSourcePreviewContent(host, {
    currentChunkIndex: 0,
    chunks: new Map([[0, chunk("item-a", 0, {
      format: "markdown",
      block_fragments: [
        {
          block_id: "table-1",
          kind: "table",
          nodes: [{
            kind: "table",
            children: [{
              kind: "table_body",
              children: [{
                kind: "table_row",
                children: [{ kind: "table_cell", children: [{ kind: "text", text: "A1" }] }],
              }],
            }],
          }],
          starts_block: true,
          ends_block: true,
        },
        {
          block_id: "table-2",
          kind: "table",
          nodes: [{
            kind: "table",
            children: [{
              kind: "table_body",
              children: [{
                kind: "table_row",
                children: [{ kind: "table_cell", children: [{ kind: "text", text: "B2" }] }],
              }],
            }],
          }],
          starts_block: true,
          ends_block: true,
        },
      ],
    })]]),
  });

  assert.equal(allNodes(host).filter((node) => node.tagName === "TABLE").length, 2);
});

test("source renderer joins inline fragments into one semantic block and removes syntax markers", () => {
  const document = new FakeDocument();
  const host = new FakeNode("div", document);

  renderSourcePreviewContent(host, {
    currentChunkIndex: 0,
    chunks: new Map([[0, chunk("item-a", 0, {
      format: "markdown",
      block_fragments: [
        {
          block_id: "paragraph-1",
          kind: "paragraph",
          nodes: [{ kind: "paragraph", children: [{ kind: "text", text: "before " }] }],
          starts_block: true,
          ends_block: false,
        },
        {
          block_id: "paragraph-1",
          kind: "inline_code",
          nodes: [{ kind: "paragraph", children: [{ kind: "inline_code", text: "code" }] }],
          starts_block: false,
          ends_block: false,
        },
        {
          block_id: "paragraph-1",
          kind: "paragraph",
          nodes: [{ kind: "paragraph", children: [{ kind: "text", text: " after\n" }] }],
          starts_block: false,
          ends_block: true,
        },
        {
          block_id: "heading-1",
          kind: "heading",
          nodes: [{
            kind: "heading", level: 2, children: [{ kind: "text", text: "Heading" }],
          }],
          starts_block: true,
          ends_block: true,
        },
        {
          block_id: "list-1",
          kind: "list",
          nodes: [{
            kind: "unordered_list",
            children: [
              { kind: "list_item", children: [{ kind: "text", text: "first" }] },
              { kind: "list_item", children: [{ kind: "text", text: "second" }] },
            ],
          }],
          starts_block: true,
          ends_block: true,
        },
        {
          block_id: "quote-1",
          kind: "blockquote",
          nodes: [{ kind: "blockquote", children: [{ kind: "text", text: "quoted" }] }],
          starts_block: true,
          ends_block: true,
        },
        {
          block_id: "fence-1",
          kind: "fenced_code",
          nodes: [{ kind: "code_block", text: "let x = 1;\n" }],
          starts_block: true,
          ends_block: true,
        },
      ],
    })]]),
  });

  const nodes = allNodes(host);
  assert.deepEqual(
    nodes.map((node) => node.tagName),
    ["DIV", "DIV", "P", "CODE", "H2", "UL", "LI", "LI", "BLOCKQUOTE", "PRE", "CODE"],
  );
  assert.equal(visibleText(nodes[2]), "before code after\n");
  assert.equal(visibleText(nodes[4]), "Heading");
  assert.deepEqual(nodes.filter((node) => node.tagName === "LI").map(visibleText), ["first", "second"]);
  assert.equal(visibleText(nodes[8]), "quoted");
  assert.equal(nodes[10].textContent, "let x = 1;\n");
});

test("source renderer preserves heading levels and ordered list semantics", () => {
  const document = new FakeDocument();
  const host = new FakeNode("div", document);

  renderSourcePreviewContent(host, {
    currentChunkIndex: 0,
    chunks: new Map([[0, chunk("item-a", 0, {
      format: "markdown",
      block_fragments: [
        {
          block_id: "heading-1",
          kind: "heading",
          nodes: [{ kind: "heading", level: 1, children: [{ kind: "text", text: "First" }] }],
          starts_block: true,
          ends_block: true,
        },
        {
          block_id: "heading-4",
          kind: "heading",
          nodes: [{ kind: "heading", level: 4, children: [{ kind: "text", text: "Fourth" }] }],
          starts_block: true,
          ends_block: true,
        },
        {
          block_id: "list-ordered",
          kind: "list",
          nodes: [{
            kind: "ordered_list",
            start: 3,
            children: [
              { kind: "list_item", children: [{ kind: "text", text: "third" }] },
              { kind: "list_item", children: [{ kind: "text", text: "fourth" }] },
            ],
          }],
          starts_block: true,
          ends_block: true,
        },
      ],
    })]]),
  });

  const nodes = allNodes(host);
  assert.deepEqual(nodes.map((node) => node.tagName), ["DIV", "DIV", "H1", "H4", "OL", "LI", "LI"]);
  assert.equal(visibleText(nodes[2]), "First");
  assert.equal(visibleText(nodes[3]), "Fourth");
  assert.equal(nodes[4].attributes.get("start"), "3");
  assert.deepEqual(nodes.filter((node) => node.tagName === "LI").map(visibleText), ["third", "fourth"]);
});

test("source renderer keeps mixed root list types in source order", () => {
  const document = new FakeDocument();
  const host = new FakeNode("div", document);

  renderSourcePreviewContent(host, {
    currentChunkIndex: 0,
    chunks: new Map([[0, chunk("item-a", 0, {
      format: "markdown",
      block_fragments: [{
        block_id: "list-mixed",
        kind: "list",
        nodes: [
          {
            kind: "unordered_list",
            children: [{ kind: "list_item", children: [{ kind: "text", text: "first" }] }],
          },
          {
            kind: "ordered_list",
            start: 1,
            children: [{ kind: "list_item", children: [{ kind: "text", text: "second" }] }],
          },
          {
            kind: "unordered_list",
            children: [{ kind: "list_item", children: [{ kind: "text", text: "third" }] }],
          },
        ],
        starts_block: true,
        ends_block: true,
      }],
    })]]),
  });

  const nodes = allNodes(host);
  assert.deepEqual(
    nodes.map((node) => node.tagName),
    ["DIV", "DIV", "UL", "LI", "OL", "LI", "UL", "LI"],
  );
  assert.deepEqual(nodes.filter((node) => node.tagName === "LI").map(visibleText), [
    "first",
    "second",
    "third",
  ]);
});

test("source renderer joins a retained adjacent continuation across chunk boundaries", () => {
  const document = new FakeDocument();
  const host = new FakeNode("div", document);
  const first = chunk("item-a", 0, {
    format: "markdown",
    block_fragments: [{
      block_id: "paragraph-1",
      kind: "paragraph",
      nodes: [{ kind: "paragraph", children: [{ kind: "text", text: "first " }] }],
      starts_block: true,
      ends_block: false,
    }],
  });
  const second = chunk("item-a", 1, {
    format: "markdown",
    block_fragments: [{
      block_id: "paragraph-1",
      kind: "paragraph",
      nodes: [{ kind: "paragraph", children: [{ kind: "text", text: "second" }] }],
      starts_block: false,
      ends_block: true,
    }],
  });

  renderSourcePreviewContent(host, {
    currentChunkIndex: 1,
    chunks: new Map([[0, first], [1, second]]),
  });

  const nodes = allNodes(host);
  assert.deepEqual(nodes.map((node) => node.tagName), ["DIV", "DIV", "P"]);
  assert.equal(visibleText(nodes[2]), "first second");
});

test("complex Markdown DTO preserves semantic order and inert visible text without executable or navigation nodes", () => {
  const document = new FakeDocument();
  const host = new FakeNode("div", document);
  const hostileHtml = '<script>steal()</script><img src="https://evil.invalid/x">';

  renderSourcePreviewContent(host, {
    currentChunkIndex: 0,
    chunks: new Map([[0, chunk("item-a", 0, {
      format: "markdown",
      block_fragments: [
        {
          block_id: "frontmatter-1",
          kind: "literal_text",
          nodes: [{ kind: "literal_text", text: "---\nname: Complex Skill\n---\n" }],
          starts_block: true,
          ends_block: true,
        },
        {
          block_id: "heading-1",
          kind: "heading",
          nodes: [{
            kind: "heading", level: 2, children: [{ kind: "text", text: "Safe Preview" }],
          }],
          starts_block: true,
          ends_block: true,
        },
        {
          block_id: "paragraph-1",
          kind: "paragraph",
          nodes: [{
            kind: "paragraph",
            children: [
              { kind: "text", text: "Use " },
              { kind: "strong", children: [{ kind: "text", text: "strong" }] },
              { kind: "text", text: ", " },
              { kind: "emphasis", children: [{ kind: "text", text: "emphasis" }] },
              { kind: "text", text: ", " },
              { kind: "link_text", children: [{ kind: "text", text: "Guide" }] },
              { kind: "text", text: ", and " },
              { kind: "link_text", children: [{ kind: "text", text: "diagram" }] },
              { kind: "text", text: ".\n" },
            ],
          }],
          starts_block: true,
          ends_block: true,
        },
        {
          block_id: "list-1",
          kind: "list",
          nodes: [{
            kind: "unordered_list",
            children: [
              {
                kind: "list_item",
                children: [
                  { kind: "text", text: "parent" },
                  {
                    kind: "unordered_list",
                    children: [{
                      kind: "list_item", children: [{ kind: "text", text: "child" }],
                    }],
                  },
                ],
              },
              { kind: "list_item", children: [{ kind: "text", text: "final" }] },
            ],
          }],
          starts_block: true,
          ends_block: true,
        },
        {
          block_id: "quote-1",
          kind: "blockquote",
          nodes: [{ kind: "blockquote", children: [{ kind: "text", text: "quoted" }] }],
          starts_block: true,
          ends_block: true,
        },
        {
          block_id: "break-1",
          kind: "thematic_break",
          nodes: [{ kind: "thematic_break" }],
          starts_block: true,
          ends_block: true,
        },
        {
          block_id: "table-1",
          kind: "table",
          nodes: [{
            kind: "table",
            children: [
              {
                kind: "table_head",
                children: [{
                  kind: "table_row",
                  children: [
                    { kind: "table_header_cell", children: [{ kind: "text", text: "Name" }] },
                    { kind: "table_header_cell", children: [{ kind: "text", text: "Value" }] },
                  ],
                }],
              },
              {
                kind: "table_body",
                children: [{
                  kind: "table_row",
                  children: [
                    { kind: "table_cell", children: [{ kind: "text", text: "safe" }] },
                    { kind: "table_cell", children: [{ kind: "text", text: "complete" }] },
                  ],
                }],
              },
            ],
          }],
          starts_block: true,
          ends_block: true,
        },
        {
          block_id: "fence-1",
          kind: "fenced_code",
          nodes: [{ kind: "code_block", text: `${hostileHtml}\n` }],
          starts_block: true,
          ends_block: true,
        },
        {
          block_id: "raw-1",
          kind: "literal_text",
          nodes: [{ kind: "literal_text", text: hostileHtml }],
          starts_block: true,
          ends_block: true,
        },
      ],
    })]]),
  });

  const nodes = allNodes(host);
  const tags = nodes.map((node) => node.tagName);
  const rendered = visibleText(host);
  const rootList = nodes.find((node) => node.tagName === "UL");

  assert.ok(rendered.indexOf("name: Complex Skill") < rendered.indexOf("Safe Preview"));
  assert.ok(rendered.indexOf("Safe Preview") < rendered.indexOf("Use strong, emphasis, Guide, and diagram."));
  assert.ok(rendered.indexOf("parent") < rendered.indexOf("child"));
  assert.ok(rendered.indexOf("child") < rendered.indexOf("quoted"));
  assert.ok(rendered.indexOf("quoted") < rendered.indexOf("safecomplete"));
  assert.ok(rendered.indexOf("safecomplete") < rendered.indexOf(hostileHtml));
  assert.ok(tags.includes("STRONG"));
  assert.ok(tags.includes("EM"));
  assert.equal(rootList?.children[0]?.children.some((node) => node.tagName === "UL"), true);
  assert.equal(tags.filter((tag) => ["SCRIPT", "IMG", "A", "IFRAME", "OBJECT"].includes(tag)).length, 0);
  assert.equal(nodes.flatMap((node) => [...node.attributes.keys()]).filter((name) => ["href", "src", "srcset"].includes(name)).length, 0);
  assert.doesNotMatch(rendered, /example\.invalid/);
  assert.equal(rendered.match(/<script>steal\(\)<\/script>/g)?.length, 2);
});
