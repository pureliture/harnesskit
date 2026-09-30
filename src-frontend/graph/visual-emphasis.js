const EMPHASIS = new Set(["selected", "adjacent", "contextual", "unrelated", "idle"]);

function nodeId(node) {
  const value = node?.node_id;
  return typeof value === "string" && value.length > 0 ? value : null;
}

function linkEndpoints(link) {
  const source = typeof link?.source_node_id === "string" && link.source_node_id.length > 0
    ? link.source_node_id
    : null;
  const target = typeof link?.target_node_id === "string" && link.target_node_id.length > 0
    ? link.target_node_id
    : null;
  return source && target && source !== target ? [source, target] : null;
}

function selectedIdentities(selectedNodeIds) {
  return selectedNodeIds instanceof Set
    ? [...selectedNodeIds].filter((value) => typeof value === "string" && value.length > 0)
    : [];
}

function emphasisForDistance(distance) {
  if (distance === 0) return "selected";
  if (distance === 1) return "adjacent";
  if (distance === 2) return "contextual";
  return "unrelated";
}

export function deriveGraphVisualEmphasis({
  nodes = [],
  links = [],
  selectedNodeIds = new Set(),
  focusNodeId = null,
} = {}) {
  const knownNodeIds = new Set((Array.isArray(nodes) ? nodes : []).map(nodeId).filter(Boolean));
  const distances = new Map();
  const queue = [];
  selectedIdentities(selectedNodeIds).forEach((identity) => {
    if (!knownNodeIds.has(identity) || distances.has(identity)) return;
    distances.set(identity, 0);
    queue.push(identity);
  });
  const adjacency = new Map([...knownNodeIds].map((identity) => [identity, new Set()]));
  (Array.isArray(links) ? links : []).forEach((link) => {
    const endpoints = linkEndpoints(link);
    if (!endpoints) return;
    const [source, target] = endpoints;
    if (!adjacency.has(source) || !adjacency.has(target)) return;
    adjacency.get(source).add(target);
    adjacency.get(target).add(source);
  });

  if (distances.size > 0) {
    for (let cursor = 0; cursor < queue.length; cursor += 1) {
      const current = queue[cursor];
      const distance = distances.get(current);
      if (distance >= 2) continue;
      adjacency.get(current).forEach((neighbor) => {
        if (distances.has(neighbor)) return;
        distances.set(neighbor, distance + 1);
        queue.push(neighbor);
      });
    }
  }

  const exactFocusNodeId = typeof focusNodeId === "string" && knownNodeIds.has(focusNodeId)
    ? focusNodeId
    : null;
  const selectedOneHopLinkIds = new Set();
  if (exactFocusNodeId) {
    (Array.isArray(links) ? links : []).forEach((link) => {
      const endpoints = linkEndpoints(link);
      const linkId = typeof link?.link_id === "string" && link.link_id.length > 0
        ? link.link_id
        : null;
      if (!endpoints || !linkId) return;
      const [source, target] = endpoints;
      if (source === exactFocusNodeId || target === exactFocusNodeId) {
        selectedOneHopLinkIds.add(linkId);
      }
    });
  }

  return {
    emphasisByNodeId: new Map([...knownNodeIds].map((identity) => [
      identity,
      distances.size === 0 ? "idle" : emphasisForDistance(distances.get(identity)),
    ])),
    focusNodeId: exactFocusNodeId,
    selectedOneHopLinkIds,
  };
}

export function deriveGraphNodeEmphasis(args = {}) {
  return deriveGraphVisualEmphasis(args).emphasisByNodeId;
}

export function graphNodeEmphasisFor(interactionModel, nodeIdValue) {
  const supplied = interactionModel?.emphasisByNodeId;
  const emphasis = supplied instanceof Map
    ? supplied.get(nodeIdValue)
    : supplied?.[nodeIdValue];
  if (EMPHASIS.has(emphasis)) return emphasis;
  if (!interactionModel?.hasFocus) return "idle";
  return interactionModel?.activeNodeIds?.has(nodeIdValue) ? "selected" : "unrelated";
}
