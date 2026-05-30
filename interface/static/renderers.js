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
  const pad = 20, dx = Math.max(b.maxX - b.minX, 1e-6), dy = Math.max(b.maxY - b.minY, 1e-6);
  return Math.min((w - pad * 2) / dx, (h - pad * 2) / dy);
}

export function transformX(x, b, s, w) { return 20 + (x - b.minX) * s + (w - 40 - (b.maxX - b.minX) * s) / 2; }
export function transformY(y, b, s, h) { return h - (20 + (y - b.minY) * s + (h - 40 - (b.maxY - b.minY) * s) / 2); }
export function pointForNode(g, id) { const f = g.nodes.find(n => n.id === id); return f && f.pos ? f.pos : [0, 0]; }

export function renderCpSvg(svg, cp, width, height, options = {}) {
  const bounds = boundsFromSegments(cp.segments);
  const scale = fitScale(bounds, width, height);
  for (const segment of cp.segments) {
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
  const bounds = boundsFromSegments(cp.segments);
  const scale = fitScale(bounds, width, height);
  for (const segment of cp.segments) {
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

export function renderFoldSvg(svg, fold) {
  const faces = fold.faces || [];
  const bounds = boundsFromFaces(faces);
  const scale = fitScale(bounds, 420, 240);
  const BASE_ALPHA = 0.12; 
  
  faces.forEach((face, index) => {
    const mult = fold.multiplicities?.[index] || 1;
    const alphaVal = 1 - Math.pow(1 - BASE_ALPHA, mult);
    
    svg.appendChild(makeSvg("polygon", {
      points: face.map((point) => `${transformX(point[0], bounds, scale, 420)},${transformY(point[1], bounds, scale, 240)}`).join(" "),
      fill: `rgba(122, 211, 255, ${alphaVal})`,
      stroke: "rgba(255,255,255,0.30)", 
      "stroke-width": 0.5, 
    }));
  });
}

export function renderGraphSvg(svg, graph, { nodeFill = "#9ed6ff", width = 420, height = 240 } = {}) {
  const bounds = boundsFromGraph(graph);
  const scale = fitScale(bounds, width, height);
  for (const edge of graph.edges) {
    const start = pointForNode(graph, edge.u);
    const end = pointForNode(graph, edge.v);
    svg.appendChild(makeSvg("line", {
      x1: transformX(start[0], bounds, scale, width),
      y1: transformY(start[1], bounds, scale, height),
      x2: transformX(end[0], bounds, scale, width),
      y2: transformY(end[1], bounds, scale, height),
      class: "edge",
    }));
  }
  for (const node of graph.nodes) {
    if (!node.pos) continue;
    svg.appendChild(makeSvg("circle", {
      cx: transformX(node.pos[0], bounds, scale, width),
      cy: transformY(node.pos[1], bounds, scale, height),
      r: 4, fill: nodeFill, class: "node",
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

export function renderHeatSvg(svg, heat) {
  const width = 420, height = 240, margin = 28;
  const xValues = heat.t_scales || [], query = heat.query || [], result = heat.result || [];
  if (!xValues.length || !query.length || !result.length) return;
  
  const xMin = Math.min(...xValues), xMax = Math.max(...xValues);
  const yValues = [...query, ...result];
  const yMin = Math.min(...yValues), yMax = Math.max(...yValues);

  drawAxes(svg, width, height, margin);
  const safeMin = Math.max(xMin, 1e-12);
  const logMin = Math.log10(safeMin), logMax = Math.log10(Math.max(xMax, safeMin * 10));
  
  for (let p = Math.floor(logMin); p <= Math.ceil(logMax); p++) {
    const val = Math.pow(10, p);
    if (val < safeMin || val > xMax) continue;
    const px = margin + ((Math.log10(val) - logMin) / (logMax - logMin)) * (width - margin * 2);
    svg.appendChild(makeSvg("line", { x1: px, y1: height - margin, x2: px, y2: height - margin + 6, stroke: "#2b3a4a", "stroke-width": 1 }));
    svg.appendChild(makeSvg("text", { x: px + 4, y: height - margin + 18, fill: "#9daccc", "font-size": 11 })).textContent = `10^${p}`;
  }

  const colorQuery = "#5b7b9e";
  const colorResult = "#7ad3ff";
  
  svg.appendChild(makePolyline(xValues, query, xMin, xMax, yMin, yMax, width, height, margin, colorQuery));
  svg.appendChild(makePolyline(xValues, result, xMin, xMax, yMin, yMax, width, height, margin, colorResult));

  const legend = makeSvg("g", {});
  legend.appendChild(makeSvg("line", { x1: width - 80, y1: 20, x2: width - 60, y2: 20, stroke: colorQuery, "stroke-width": 2.3 }));
  const qText = makeSvg("text", { x: width - 55, y: 24, fill: "#9daccc", "font-size": 11 });
  qText.textContent = "Query";
  legend.appendChild(qText);
  legend.appendChild(makeSvg("line", { x1: width - 80, y1: 36, x2: width - 60, y2: 36, stroke: colorResult, "stroke-width": 2.3 }));
  const rText = makeSvg("text", { x: width - 55, y: 40, fill: "#9daccc", "font-size": 11 });
  rText.textContent = "Result";
  legend.appendChild(rText);

  svg.appendChild(legend);
}
