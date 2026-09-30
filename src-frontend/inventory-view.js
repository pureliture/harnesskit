export const MAX_VISIBLE_TREE_ROWS = 160;

const SCOPE_ORDER = new Map([
  ["User", 0],
  ["Project", 1],
]);

const ORPHAN_ORDER = new Map([
  ["RegistryUnregistered", 0],
  ["Foreign", 1],
]);

function stableCompare(left, right) {
  const a = String(left ?? "");
  const b = String(right ?? "");
  return a < b ? -1 : a > b ? 1 : 0;
}

function normalizeScope(value) {
  const normalized = String(value ?? "").toLowerCase();
  if (normalized === "user" || normalized === "user-level") return "User";
  if (normalized === "project" || normalized === "project-level") return "Project";
  return String(value || "Project");
}

function scopeCompare(left, right) {
  const leftRank = SCOPE_ORDER.get(left) ?? 99;
  const rightRank = SCOPE_ORDER.get(right) ?? 99;
  return leftRank - rightRank || stableCompare(left, right);
}

function segment(value) {
  return encodeURIComponent(String(value ?? ""));
}

function matchesFilter(values, filter) {
  if (!filter) return true;
  const query = String(filter).trim().toLowerCase();
  return values.some((value) => String(value ?? "").toLowerCase().includes(query));
}

function canonicalLeaves(inventory, filter) {
  const leaves = [];

  for (const item of Array.isArray(inventory?.items) ? inventory.items : []) {
    if (["Foreign", "RegistryUnregistered"].includes(item.foreign_classification)) continue;

    const rawLocations = Array.isArray(item.locations) && item.locations.length
      ? item.locations
      : [{
          scope: item.scope ?? "Project",
          path: item.source_path ?? "",
          install_status: item.install_status,
        }];
    const locationsByScope = new Map();

    for (const location of rawLocations) {
      const scope = normalizeScope(location.scope);
      const locations = locationsByScope.get(scope) ?? [];
      locations.push(location);
      locationsByScope.set(scope, locations);
    }

    for (const [scope, locations] of locationsByScope) {
      if (!matchesFilter([
        scope,
        item.target,
        item.kind,
        item.component_id,
        item.title,
        item.summary,
        item.source_path,
        ...locations.map((location) => location.path),
      ], filter)) continue;

      leaves.push({
        scope,
        target: String(item.target ?? "unknown"),
        kind: String(item.kind ?? "unknown"),
        componentId: String(item.component_id ?? "unknown"),
        label: String(item.title || item.component_id || "데이터 없음"),
        item,
        locations: [...locations].sort((a, b) => stableCompare(a.path, b.path)),
      });
    }
  }

  return leaves.sort((left, right) =>
    scopeCompare(left.scope, right.scope)
    || stableCompare(left.target, right.target)
    || stableCompare(left.kind, right.kind)
    || stableCompare(left.componentId, right.componentId));
}

function orphanLeaves(inventory, filter) {
  return (Array.isArray(inventory?.orphan_discoveries) ? inventory.orphan_discoveries : [])
    .filter((orphan) => ["Foreign", "RegistryUnregistered"].includes(orphan.classification))
    .map((orphan) => ({ ...orphan, scope: normalizeScope(orphan.scope) }))
    .filter((orphan) => matchesFilter([
      orphan.scope,
      orphan.path,
      orphan.classification,
      orphan.classification === "Foreign" ? "foreign" : "registry 미등록",
    ], filter))
    .sort((left, right) =>
      scopeCompare(left.scope, right.scope)
      || (ORPHAN_ORDER.get(left.classification) ?? 99)
        - (ORPHAN_ORDER.get(right.classification) ?? 99)
      || stableCompare(left.path, right.path));
}

function addGroup(rows, seen, row) {
  if (seen.has(row.id)) return;
  seen.add(row.id);
  rows.push({ ...row, expandable: true });
}

export function buildTreeRows(inventory, { filter = "" } = {}) {
  const rows = [];
  const seen = new Set();

  for (const leaf of canonicalLeaves(inventory, filter)) {
    const scopeId = `scope:${segment(leaf.scope)}`;
    const targetId = `${scopeId}/target:${segment(leaf.target)}`;
    const kindId = `${targetId}/kind:${segment(leaf.kind)}`;
    const componentId = `${kindId}/component:${segment(leaf.componentId)}`;

    addGroup(rows, seen, {
      id: scopeId,
      parentId: null,
      type: "group",
      level: 1,
      label: leaf.scope === "User" ? "User level" : "Project level",
      scope: leaf.scope,
    });
    addGroup(rows, seen, {
      id: targetId,
      parentId: scopeId,
      type: "group",
      level: 2,
      label: leaf.target,
      scope: leaf.scope,
      target: leaf.target,
    });
    addGroup(rows, seen, {
      id: kindId,
      parentId: targetId,
      type: "group",
      level: 3,
      label: leaf.kind,
      scope: leaf.scope,
      target: leaf.target,
      kind: leaf.kind,
    });
    rows.push({
      id: componentId,
      parentId: kindId,
      type: "component",
      level: 4,
      label: leaf.label,
      expandable: false,
      scope: leaf.scope,
      target: leaf.target,
      kind: leaf.kind,
      componentId: leaf.componentId,
      item: leaf.item,
      locations: leaf.locations,
    });
  }

  const orphans = orphanLeaves(inventory, filter);
  if (orphans.length) {
    addGroup(rows, seen, {
      id: "orphans",
      parentId: null,
      type: "orphan-group",
      level: 1,
      label: "분리된 harness-like 발견",
    });

    for (const orphan of orphans) {
      const scopeId = `orphans/scope:${segment(orphan.scope)}`;
      const classificationId = `${scopeId}/classification:${segment(orphan.classification)}`;
      const orphanId = `${classificationId}/path:${segment(orphan.path)}`;

      addGroup(rows, seen, {
        id: scopeId,
        parentId: "orphans",
        type: "orphan-group",
        level: 2,
        label: orphan.scope === "User" ? "User level" : "Project level",
        scope: orphan.scope,
      });
      addGroup(rows, seen, {
        id: classificationId,
        parentId: scopeId,
        type: "orphan-group",
        level: 3,
        label: orphan.classification === "Foreign" ? "Foreign" : "Registry 미등록",
        scope: orphan.scope,
        classification: orphan.classification,
      });
      rows.push({
        id: orphanId,
        parentId: classificationId,
        type: "orphan",
        level: 4,
        label: orphan.path.split("/").filter(Boolean).at(-1) || orphan.path,
        expandable: false,
        scope: orphan.scope,
        classification: orphan.classification,
        path: orphan.path,
      });
    }
  }

  return rows;
}

function normalizedExpandedIds(rows, expandedIds) {
  if (!Array.isArray(expandedIds)) {
    return rows.filter((row) => row.expandable).map((row) => row.id);
  }
  const expandable = new Set(rows.filter((row) => row.expandable).map((row) => row.id));
  return [...new Set(expandedIds)].filter((id) => expandable.has(id));
}

export function visibleTreeRows(rows, expandedIds, limit = MAX_VISIBLE_TREE_ROWS) {
  const expanded = new Set(normalizedExpandedIds(rows, expandedIds));
  const visibility = new Map();
  const visible = [];

  for (const row of rows) {
    const isVisible = !row.parentId
      || (visibility.get(row.parentId) === true && expanded.has(row.parentId));
    visibility.set(row.id, isVisible);
    if (isVisible) visible.push(row);
  }

  return {
    rows: visible.slice(0, limit),
    total: visible.length,
    truncated: visible.length > limit,
  };
}

export function reduceTreeKeyboard(treeState, key, rows) {
  const expandedIds = normalizedExpandedIds(rows, treeState.expandedIds);
  const expanded = new Set(expandedIds);
  const visible = visibleTreeRows(rows, expandedIds).rows;
  if (!visible.length) return { ...treeState, selectedId: null, expandedIds };

  let index = visible.findIndex((row) => row.id === treeState.selectedId);
  if (index < 0) index = 0;
  let selectedId = visible[index].id;
  const current = visible[index];

  if (key === "ArrowDown") selectedId = visible[Math.min(index + 1, visible.length - 1)].id;
  if (key === "ArrowUp") selectedId = visible[Math.max(index - 1, 0)].id;
  if (key === "Home") selectedId = visible[0].id;
  if (key === "End") selectedId = visible.at(-1).id;

  if (key === "ArrowRight" && current.expandable) {
    if (!expanded.has(current.id)) {
      expanded.add(current.id);
    } else {
      const child = rows.find((row) => row.parentId === current.id);
      if (child) selectedId = child.id;
    }
  }

  if (key === "ArrowLeft") {
    if (current.expandable && expanded.has(current.id)) {
      expanded.delete(current.id);
    } else if (current.parentId) {
      selectedId = current.parentId;
    }
  }

  return { ...treeState, selectedId, expandedIds: [...expanded] };
}

export function toggleTreeExpanded(treeState, rowId, rows) {
  const row = rows.find((candidate) => candidate.id === rowId);
  if (!row?.expandable) return { ...treeState, selectedId: rowId };

  const expanded = new Set(normalizedExpandedIds(rows, treeState.expandedIds));
  if (expanded.has(rowId)) expanded.delete(rowId);
  else expanded.add(rowId);
  return { ...treeState, selectedId: rowId, expandedIds: [...expanded] };
}
