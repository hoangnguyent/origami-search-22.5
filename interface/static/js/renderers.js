import { Locales } from './locales.js';
const SVG_NS = "http://www.w3.org/2000/svg";

export function makeSvg(tag, attrs = {}) {
  const element = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    element.setAttribute(key, String(value));
  }
  return element;
}

export function boundsFromArrays(xs, ys) {
  if (!xs.length || !ys.length) return { minX: 0, maxX: 1, minY: 0, maxY: 1 };
  return { minX: Math.min(...xs), maxX: Math.max(...xs), minY: Math.min(...ys), maxY: Math.max(...ys) };
}

export function boundsFromSegments(segments) {
  const xs = [], ys = [];
  segments.forEach(s => { xs.push(s.x1, s.x2); ys.push(s.y1, s.y2); });
  return boundsFromArrays(xs, ys);
}

export function boundsFromGraph(graph) {
  const xs = [], ys = [];
  graph.nodes.forEach(n => { if (n.pos) { xs.push(n.pos[0]); ys.push(n.pos[1]); }});
  return boundsFromArrays(xs, ys);
}

export function boundsFromFaces(faces) {
  const xs = [], ys = [];
  faces.forEach(f => f.forEach(p => { xs.push(p[0]); ys.push(p[1]); }));
  return boundsFromArrays(xs, ys);
}
export function fitScale(b, w, h) {
  const pad = 10;
  const dx = Math.max(b.maxX - b.minX, 1e-6);
  const dy = Math.max(b.maxY - b.minY, 1e-6);
  return Math.min((w - pad * 2) / dx, (h - pad * 2) / dy);
}

export function transformX(x, b, s, w) { 
  const pad = 10;
  return pad + (x - b.minX) * s + (w - pad * 2 - (b.maxX - b.minX) * s) / 2; 
}

export function transformY(y, b, s, h) { 
  const pad = 10;
  return h - (pad + (y - b.minY) * s + (h - pad * 2 - (b.maxY - b.minY) * s) / 2); 
}

export function pointForNode(g, id) { const f = g.nodes.find(n => n.id === id); return f && f.pos ? f.pos : [0, 0]; }

export function parseCp4D(cp) {
  // Graceful fallback just in case old cached payloads pass through
  if (cp.segments) return cp.segments;
  
  const SQRT2_2 = Math.SQRT2 / 2;
  
  // 1. Calculate Cartesian Coordinates for all unique vertices
  const cartesianVertices = (cp.vertices || []).map(v => {
    // v is [x.num, x.den, y.num, y.den, z.num, z.den, w.num, w.den]
    const x = v[0] / v[1];
    const y = v[2] / v[3];
    const z = v[4] / v[5];
    const w = v[6] / v[7];
    
    return [
      x + SQRT2_2 * (y - w),
      z + SQRT2_2 * (y + w)
    ];
  });

  // 2. Build the standard segments array format
  return (cp.edges || []).map(edge => {
    const [v1_idx, v2_idx, type] = edge;
    const [x1, y1] = cartesianVertices[v1_idx];
    const [x2, y2] = cartesianVertices[v2_idx];
    return { type, x1, y1, x2, y2 };
  });
}


// --- REPLACEMENT: renderCpSvg ---
export function renderCpSvg(svg, cp, width, height, options = {}) {
  // Extract segments from the 4D payload
  const segments = parseCp4D(cp);
  
  const bounds = boundsFromSegments(segments);
  const scale = fitScale(bounds, width, height);
  
  for (const segment of segments) {
    const mv = (segment.type == null) ? "" : String(segment.type).trim().toLowerCase();
    const strokeWidth = mv === "h" ? 1 : 2;
    const classes = ["cp-segment", `cp-${mv}`];

    const line = makeSvg("line", {
      x1: transformX(segment.x1, bounds, scale, width),
      y1: transformY(segment.y1, bounds, scale, height),
      x2: transformX(segment.x2, bounds, scale, width),
      y2: transformY(segment.y2, bounds, scale, height),
      class: classes.join(" "),
    });
    line.setAttribute('stroke-width', String(strokeWidth));
    line.style.strokeWidth = String(strokeWidth);
    svg.appendChild(line);
  }
}

export function renderPackingSvg(svg, cp, width, height, options = {}) {
  const segments = parseCp4D(cp)
  // const bounds = boundsFromSegments(cp.segments);
  const bounds = boundsFromSegments(segments);
  const scale = fitScale(bounds, width, height);
  for (const segment of segments) {
    const mv = (segment.type == null) ? "" : String(segment.type).trim().toLowerCase();
    const strokeWidth = mv === "h" ? 2.5 : mv === "b"? 2: 0.7;
    const classes = ["cp-segment", `packing-${mv}`];

    const line = makeSvg("line", {
      x1: transformX(segment.x1, bounds, scale, width),
      y1: transformY(segment.y1, bounds, scale, height),
      x2: transformX(segment.x2, bounds, scale, width),
      y2: transformY(segment.y2, bounds, scale, height),
      class: classes.join(" "),
    });
    line.setAttribute('stroke-width', String(strokeWidth));
    line.style.strokeWidth = String(strokeWidth);
    svg.appendChild(line);
  }
}

export function renderFoldSvg(svg, fold, width = 400, height = 400) {
  const faces = fold.faces || [];
  const bounds = boundsFromFaces(faces);
  const scale = fitScale(bounds, width, height);
  // const BASE_ALPHA = 0.1; 
  const max_mult = Math.max(fold.multiplicities ? Math.max(...fold.multiplicities) : 1, 1);
  const BASE_ALPHA = 1- Math.pow(0.7, 1 / (max_mult || 1)); // Adjust base alpha to ensure max multiplicity still has some transparency
  faces.forEach((face, index) => {
    const mult = fold.multiplicities?.[index] || 1;
    const alphaVal = 1 - Math.pow(1 - BASE_ALPHA, mult);
    
    svg.appendChild(makeSvg("polygon", {
      points: face.map((point) => `${transformX(point[0], bounds, scale, width)},${transformY(point[1], bounds, scale, height)}`).join(" "),
      
      fill: "var(--accent)", 
      "fill-opacity": alphaVal,
      "stroke-width": 0.5, 
    }));
  });
}

// Helper: Computes a guaranteed non-crossing, exact-length radial layout
export function computeRadialTreeLayout(graph) {
  if (!graph.nodes || graph.nodes.length === 0) return;

  // 1. Build Adjacency List
  const adj = new Map();
  graph.nodes.forEach(n => adj.set(n.id, []));
  graph.edges.forEach(e => {
    const length = e.length !== undefined ? e.length : 1;
    if (adj.has(e.u)) adj.get(e.u).push({ to: e.v, length });
    if (adj.has(e.v)) adj.get(e.v).push({ to: e.u, length });
  });

  // 2. Pick a root (Node with the highest degree for the most balanced spread)
  let rootId = graph.nodes[0].id;
  let maxDegree = -1;
  for (const [id, neighbors] of adj.entries()) {
    if (neighbors.length > maxDegree) {
      maxDegree = neighbors.length;
      rootId = id;
    }
  }

  // 3. Compute subtree weights (count of leaves) to allocate proportional angles
  const weights = new Map();
  function computeWeight(nodeId, parentId) {
    let isLeaf = true;
    let weight = 0;
    for (const edge of adj.get(nodeId)) {
      if (edge.to !== parentId) {
        isLeaf = false;
        weight += computeWeight(edge.to, nodeId);
      }
    }
    if (isLeaf) weight = 1;
    weights.set(nodeId, weight);
    return weight;
  }
  computeWeight(rootId, null);

  const nodeMap = new Map(graph.nodes.map(n => [n.id, n]));

  // 4. Recursive Radial Placement
  function place(nodeId, parentId, x, y, angleStart, angleEnd) {
    const node = nodeMap.get(nodeId);
    node.pos = [x, y]; // Set the position locally!

    const children = adj.get(nodeId).filter(e => e.to !== parentId);
    if (children.length === 0) return;

    let sweep = angleEnd - angleStart;
    const centerAngle = (angleStart + angleEnd) / 2;

    // Constrain spread to at most 180° (PI) for non-root nodes so branches always grow outwards
    if (parentId !== null) {
      sweep = Math.min(sweep, Math.PI);
      angleStart = centerAngle - sweep / 2;
    }

    const totalWeight = children.reduce((sum, e) => sum + weights.get(e.to), 0);
    let currentAngle = angleStart;

    for (const childEdge of children) {
      const childWeight = weights.get(childEdge.to);
      const childSweep = sweep * (childWeight / totalWeight);
      const childCenter = currentAngle + childSweep / 2;

      // Exact edge length preserved here via pure trigonometry
      const cx = x + childEdge.length * Math.cos(childCenter);
      const cy = y + childEdge.length * Math.sin(childCenter);

      place(childEdge.to, nodeId, cx, cy, currentAngle, currentAngle + childSweep);
      currentAngle += childSweep;
    }
  }

  // Start placement at origin (0,0) spanning 360 degrees
  place(rootId, null, 0, 0, 0, 2 * Math.PI);
}

// ---------------------------------------------------------
// Your existing wrapper, updated to trigger the layout 
// ---------------------------------------------------------
export function renderGraphSvg(svg, graph, {width = 420, height = 240 } = {}) {
  const needsLayout = graph.nodes.some(n => !n.pos);
  if (needsLayout) {
    computeRadialTreeLayout(graph);
  }

  const bounds = boundsFromGraph(graph);
  const scale = fitScale(bounds, width, height);
  
  for (const edge of graph.edges) {
    const start = pointForNode(graph, edge.u);
    const end = pointForNode(graph, edge.v);
    const attrs = {
      x1: transformX(start[0], bounds, scale, width),
      y1: transformY(start[1], bounds, scale, height),
      x2: transformX(end[0], bounds, scale, width),
      y2: transformY(end[1], bounds, scale, height),
      class: "edge",
      
    };

    // for tree edge labeling
    if (edge.comp_id !== undefined) {
      attrs["data-comp-id"] = edge.comp_id;
    }
    
    svg.appendChild(makeSvg("line", attrs));
  }
  
  for (const node of graph.nodes) {
    if (!node.pos) continue;
    svg.appendChild(makeSvg("circle", {
      cx: transformX(node.pos[0], bounds, scale, width),
      cy: transformY(node.pos[1], bounds, scale, height),
      r: 4, 
      // fill: nodeFill, 
      class: "node",
    }));
  }
}

export function drawAxes(svg, width, height, margin) {
  svg.appendChild(makeSvg("line", { x1: margin, y1: height - margin, x2: width - margin, y2: height - margin, class: "gridline" }));
  svg.appendChild(makeSvg("line", { x1: margin, y1: margin, x2: margin, y2: height - margin, class: "gridline" }));
}

export function makePolyline(xValues, yValues, xMin, xMax, yMin, yMax, width, height, margin, color) {
  const safeMin = Math.max(xMin, 1e-12);
  const logSpan = Math.max(Math.log10(Math.max(xMax, safeMin * 10)) - Math.log10(safeMin), 1e-9);
  const ySpan = Math.max(yMax - yMin, 1e-9);
  const points = xValues.map((x, index) => {
    const px = margin + ((Math.log10(Math.max(x, safeMin)) - Math.log10(safeMin)) / logSpan) * (width - margin * 2);
    const py = height - margin - ((yValues[index] - yMin) / ySpan) * (height - margin * 2);
    return `${px},${py}`;
  }).join(" ");
  return makeSvg("polyline", { points, fill: "none", stroke: color, "stroke-width": 2.3, "stroke-linejoin": "round", "stroke-linecap": "round" });
}
export function renderHeatSvg(svg, heat, {width = 300, height = 300} = {}) {
  // Increased margin slightly to safely fit the title text without clipping
  const margin = 50;
  const xValues = heat.t_scales || [];
  const result = heat.result || [];
  
  if (!xValues.length || !result.length) return; 
  
  const xMin = Math.min(...xValues), xMax = Math.max(...xValues);
  const yMax = 1;
  const yMin = 0;

  // 2. CSS Variable Styles
  const axisStyle = "stroke: var(--packing-b); stroke-width: 4;";
  const tickStyle = "stroke: var(--packing-b); stroke-width: 2;";
  const textStyle = "fill: var(--packing-b); font-size: 10px;";
  const titleStyle = "fill: var(--packing-b); font-size: 14px; font-weight: 500;";
  const curveStyle = "fill: none; stroke: var(--accent); stroke-width: 4; stroke-linejoin: round;";

  // --- Draw Axes Base ---
  svg.appendChild(makeSvg("line", { x1: margin, y1: height - margin, x2: width - margin, y2: height - margin, style: axisStyle }));
  svg.appendChild(makeSvg("line", { x1: margin, y1: margin, x2: margin, y2: height - margin, style: axisStyle }));

  // --- X-Axis Ticks & Numbers ---
  for (let p = Math.floor(xMin); p <= Math.ceil(xMax); p++) {
    const px = margin + ((p - xMin) / (xMax - xMin)) * (width - margin * 2);
    
    // Short tick mark
    svg.appendChild(makeSvg("line", { 
        x1: px, y1: height - margin, 
        x2: px, y2: height - margin + 6, 
        style: tickStyle
    }));
    
    // Number perfectly tucked below the tick
    const tickText = makeSvg("text", { 
        x: px, y: height - margin + 20, 
        "text-anchor": "middle",
        style: textStyle
    });
    tickText.textContent = p;
    svg.appendChild(tickText);
  }

  // --- Y-Axis Ticks (Only 0 and 1) ---
  [0, 1].forEach(val => {
    const py = height - margin - ((val - yMin) / (yMax - yMin)) * (height - margin * 2);
    
    // Short tick mark
    svg.appendChild(makeSvg("line", { 
        x1: margin - 6, y1: py, 
        x2: margin, y2: py, 
        style: tickStyle 
    }));
    
    // Number tucked to the left of the tick
    const tickText = makeSvg("text", { 
        x: margin - 12, y: py + 4, 
        "text-anchor": "end",
        style: textStyle
    });
    tickText.textContent = val;
    svg.appendChild(tickText);

    // Dashed horizontal line strictly at y = 0
    if (val === 0) {
        svg.appendChild(makeSvg("line", {
            x1: margin, y1: py,
            x2: width - margin, y2: py,
            style: "stroke: var(--packing-b); stroke-width: 2; stroke-dasharray: 8,8; opacity: 1;"
        }));
    }
  });

  const lang = localStorage.getItem('explori_lang') || 'en';
  const dict = Locales[lang] || Locales['en'];

  // --- Axis Labels ---
  // Positioned strictly below the numbers
  const xLabel = makeSvg("text", {
      x: width / 2, y: height - margin + 40,
      "text-anchor": "middle",
      style: titleStyle
  });
  xLabel.textContent = dict.logEigenvalues;
  svg.appendChild(xLabel);

  // Positioned strictly to the left of the numbers
  const yLabel = makeSvg("text", {
      x: -(height / 2), y: margin - 30, 
      "text-anchor": "middle",
      transform: "rotate(-90)",
      style: titleStyle
  });
  yLabel.textContent = dict.normIntensity;
  svg.appendChild(yLabel);

  // --- 5. Plot the Manual Polyline ---
  const points = result.map((y, i) => {
      const px = margin + ((xValues[i] - xMin) / (xMax - xMin)) * (width - margin * 2);
      const py = height - margin - ((y - yMin) / (yMax - yMin)) * (height - margin * 2);
      return `${px},${py}`;
  }).join(" ");

  svg.appendChild(makeSvg("polyline", {
      points: points,
      style: curveStyle
  }));
}