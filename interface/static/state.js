export const state = {
  nodes: {
    0: { id: 0, x: 400, y: 280 },
  },
  edges: [],
  nextNodeId: 1,
  selectedNode: 0,
  draggingNode: null,
  queryResult: null,
  queryNodeCount: 1,
  currentDetailResult: null,
  currentDetailIndex: null,
  detailViewModes: {
    left: "cp",
    right: "tree",
  },
};
