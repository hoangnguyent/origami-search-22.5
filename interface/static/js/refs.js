/**
 * Helper to create an SVG element with attributes
 */
function makeSvg(tag, attrs) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const k in attrs) {
        el.setAttribute(k, attrs[k]);
    }
    return el;
}

// 1. Normalizes the Python keys (v, v1, v2) to the JS render keys (xy, xy1, xy2)
function normalizeRefs(refsArray) {
    if (!Array.isArray(refsArray)) return [];
    
    return refsArray.map(ref => {
        if (ref.type === 'vertex') {
            return { type: 'vertex', xy: ref.xy || ref.v };
        } else if (ref.type === 'crease' || ref.type === 'edge') {
            return { type: ref.type, xy1: ref.xy1 || ref.v1, xy2: ref.xy2 || ref.v2 };
        }
        return ref;
    });
}

// 2. Accumulates creases across the ancestry tree
function processAncestry(ancestryArray) {
    const steps = [];
    // Start with the border of the unit square
    const accumulatedCreases = [
        [[0, 0], [1, 0]], [[1, 0], [1, 1]], [[1, 1], [0, 1]], [[0, 1], [0, 0]]
    ];

    for (const entry of ancestryArray) {
        if (entry.function_name === 'root') continue;

        // Extract the pre-transformed floats directly
        const nc1 = entry.new_crease_v1;
        const nc2 = entry.new_crease_v2;
        const refs = normalizeRefs(entry.refs);
        
        // Snapshot the paper state BEFORE this step's crease is folded
        const currentCreases = [...accumulatedCreases];

        steps.push({
            depth: entry.depth,
            function_name: entry.function_name,
            pastCreases: currentCreases,
            newCrease: nc1 && nc2 ? [nc1, nc2] : null,
            refs: refs
        });

        // Add this step's crease to the background for all subsequent steps
        if (nc1 && nc2 && (nc1[0] !== nc2[0] || nc1[1] !== nc2[1])) {
            accumulatedCreases.push([nc1, nc2]);
        }
    }
    return steps;
}

// 3. Translates the Python _instruction function
function getInstructionText(fn, refs) {
    if (!fn || fn === "target") return "Target vertex.";
    if (fn === "vertex_pair") return "Crease through the circled points.";
    if (fn === "parallel_bisector") return "Bring the highlighted creases together.";
    if (fn === "angle_bisector") return "Crease an angle bisector.";
    if (fn === "perp_through_vertex") {
        const hasDiag = refs.some(r => r.type === 'crease');
        return hasDiag
            ? "Crease through the circled point, perpendicular to the diagonal."
            : "Crease through the circled point, perpendicular to the edge.";
    }
    return fn.replace(/_/g, " ").replace(/^\w/, c => c.toUpperCase()) + ".";
}

// 4. Draws a single step to an SVG
function renderStepSvg(svg, step, width, height, isFinal = false, targetXY = null) {
    svg.innerHTML = '';
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.style.display = "block";
    svg.style.margin = "0 auto";

    const scale = Math.min(width, height) * 0.9;
    const padX = (width - scale) / 2;
    const padY = (height - scale) / 2;
    
    const FLIP_Y = true; 

    const tx = (x) => padX + (x * scale);
    const ty = (y) => FLIP_Y ? height - (padY + (y * scale)) : padY + (y * scale);

    // Helper to safely draw lines
    const drawLine = (p1, p2, stroke, strokeWidth, dashed = false) => {
        if (!p1 || !p2) return;
        const attrs = {
            x1: tx(p1[0]), y1: ty(p1[1]), x2: tx(p2[0]), y2: ty(p2[1]),
            stroke: stroke, "stroke-width": strokeWidth
        };
        if (dashed) attrs["stroke-dasharray"] = "4 4";
        svg.appendChild(makeSvg("line", attrs));
    };

    // A. Draw Past Creases (Gray)
    for (const [p1, p2] of step.pastCreases) {
        drawLine(p1, p2, "var(--text-muted, #738090)", "1");
    }

    if (!isFinal) {
        // B. Reference Lines (Dashed Green/Accent)
        for (const ref of step.refs) {
            if (ref.type === 'crease' || ref.type === 'edge') {
                drawLine(ref.xy1, ref.xy2, "var(--accent, #4fa3e0)", "1.5", true);
            }
        }

        // C. New Crease (Thick Blue)
        if (step.newCrease) {
            drawLine(step.newCrease[0], step.newCrease[1], "var(--primary, #4fa3e0)", "2.5");
        }

        // D. Reference Vertices (Hollow circles)
        for (const ref of step.refs) {
            if (ref.type === 'vertex' && ref.xy) {
                svg.appendChild(makeSvg("circle", {
                    cx: tx(ref.xy[0]), cy: ty(ref.xy[1]), r: "4",
                    fill: "none", stroke: "var(--accent, #4fa3e0)", "stroke-width": "2"
                }));
            }
        }
    } else if (targetXY) {
        // E. Final Target Dot
        svg.appendChild(makeSvg("circle", {
            cx: tx(targetXY[0]), cy: ty(targetXY[1]), r: "4",
            fill: "#50e890", stroke: "none"
        }));
    }
}

// 5. Main Workspace Populator
export function renderReferenceWorkspace(ancestryArray, targetXY = null) {
    const workspace = document.getElementById("refsWorkspace");
    if (!workspace || !Array.isArray(ancestryArray)) return;

    workspace.innerHTML = '';
    workspace.style.display = "flex";
    workspace.style.flexWrap = "wrap";
    workspace.style.gap = "1rem";

    const steps = processAncestry(ancestryArray);
    
    // Add the final target step so the last crease remains visible behind the dot
    if (steps.length > 0) {
        const lastStep = steps[steps.length - 1];
        if (lastStep.newCrease) {
            steps.push({ 
                function_name: 'target',
                isFinal: true, 
                pastCreases: lastStep.pastCreases.concat([lastStep.newCrease]),
                refs: []
            });
        }
    }

    steps.forEach((step, index) => {
        const stepDiv = document.createElement("div");
        stepDiv.className = "ref-step-container";
        stepDiv.style.width = "220px";
        stepDiv.style.textAlign = "center";

        const svg = makeSvg("svg", { width: "220", height: "220" });
        stepDiv.appendChild(svg);

        renderStepSvg(svg, step, 220, 220, step.isFinal, targetXY);

        const caption = document.createElement("div");
        caption.style.fontSize = "0.85rem";
        caption.style.marginTop = "0.5rem";
        caption.style.color = "var(--text-main, #d8d8e8)";
        
        const instruction = getInstructionText(step.function_name, step.refs || []);
        caption.innerHTML = `<strong>${index + 1}.</strong> ${instruction}`;
        
        stepDiv.appendChild(caption);
        workspace.appendChild(stepDiv);
    });
}