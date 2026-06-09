function gcd(a, b) {
  while (b) [a, b] = [b, a % b];
  return a;
}

function simplify(num, den) {
  const g = gcd(num, den);
  return [num / g, den / g];
}

// Source - https://stackoverflow.com/a/60368757
// Posted by David Figatner, modified by community. See post 'Timeline' for change history
// Retrieved 2026-06-03, License - CC BY-SA 4.0
// line intercept math by Paul Bourke http://paulbourke.net/geometry/pointlineplane/
// Determine the intersection point of two line segments
// Return FALSE if the lines don't intersect
function intersect(x1, y1, x2, y2, x3, y3, x4, y4) {

  // Check if none of the lines are of length 0
    if ((x1 === x2 && y1 === y2) || (x3 === x4 && y3 === y4)) {
        return false
    }

    denominator = ((y4 - y3) * (x2 - x1) - (x4 - x3) * (y2 - y1))

  // Lines are parallel
    if (denominator === 0) {
        return false
    }

    let ua = ((x4 - x3) * (y1 - y3) - (y4 - y3) * (x1 - x3)) / denominator
    let ub = ((x2 - x1) * (y1 - y3) - (y2 - y1) * (x1 - x3)) / denominator

  // is the intersection along the segments
    if (ua < 0 || ua > 1 || ub < 0 || ub > 1) {
        return false
    }

  // Return a object with the x and y coordinates of the intersection
    let x = x1 + ua * (x2 - x1)
    let y = y1 + ua * (y2 - y1)

    return {x, y}
}

function isPow2(n) {
    return Math.log2(n) % 1 == 0;
}

function continuedFraction(n,d) {
    const result = [];
    [n,d] = simplify(n,d);

    while (n !== 0) {
        if (n > d && isPow2(Math.max(n, d))) {
            result.push(n/d);
            break;
        }


        const integer = Math.floor(n/d);
        result.push(integer);

        n -= integer*d;
        if (n === 0) break;

        [n,d] = [d,n];
    }

    return result;
}

function binaryDecomp(partialDenom) {
    const result = [];

    if (Number.isInteger(partialDenom)) {
        while (partialDenom > 0) {
            const exp = Math.floor(Math.log2(partialDenom));
            const nearestPowTwo = 2**exp;
            result.push(nearestPowTwo);
            partialDenom -= nearestPowTwo;
        }
        return result;
    } else return [partialDenom];
}

function getDiagonal (n, d) {
    const continuedFractionArray = continuedFraction(n,d);
    let counter = 0;

    const ndCreases = [];
    const diagonalCreases = [];

    for (let i = continuedFractionArray.length-1; i >= 0; i--) {
        const currentPartialDenominator = continuedFractionArray[i];

        const decomposedCPD = binaryDecomp(currentPartialDenominator);
        const horizontal = i%2 !== 0; // we want to flip from building off the bottom edge to building off the right edge depending on where we are in the CF.

        for (let j = decomposedCPD.length-1; j >= 0; j--) {
            const positive = counter == 0 ? !horizontal : horizontal;
            const currentDCPD = decomposedCPD[j];
            const creasesToDCPD = buildCrease(currentDCPD, horizontal, positive);
            if (creasesToDCPD.length === 0) continue;

            ndCreases.push(...creasesToDCPD);
            diagonalCreases.push(creasesToDCPD[creasesToDCPD.length-1]);

            if (diagonalCreases.length > 1) {
                const mostRecentDiagonal = diagonalCreases[diagonalCreases.length-1];
                const secondMRD = diagonalCreases[diagonalCreases.length-2];
                const intersection = intersect(mostRecentDiagonal[0],mostRecentDiagonal[1],mostRecentDiagonal[2],mostRecentDiagonal[3],secondMRD[0],secondMRD[1],secondMRD[2],secondMRD[3]);

                let intersection2x, intersection2y, intersection3x, intersection3y;

                if (horizontal) {
                    intersection2x = positive ? 0 : 1;
                    intersection2y = intersection.y;

                    intersection3x = positive ? 1 : 0;
                    intersection3y = 0;
                } else {
                    intersection2x = intersection.x;
                    intersection2y = positive ? 0 : 1;

                    intersection3x = 1;
                    intersection3y = positive ? 1 : 0;
                }

                ndCreases.push([intersection.x, intersection.y, intersection2x, intersection2y]);
                ndCreases.push([intersection3x, intersection3y, intersection2x, intersection2y]);
                diagonalCreases.push([intersection3x, intersection3y, intersection2x, intersection2y]);
            }

            counter++;
        }
    }

    return ndCreases;
}

function buildCrease (binaryCPD, horizontal, positive) {
    //binaryCPD will be a power of two
    const creases = [];
    const target = 1 / binaryCPD;

    let lo = 0;
    let hi = 1;

    if (target == lo) {
        return [];
    }

    if (target != hi) {
        while (!creases.includes(target)) {
            const mid = (lo + hi) / 2;
            creases.push(mid);
            mid > target ? hi = mid : lo = mid;
        }
    }

    for (let i = 0; i < creases.length; i++) {
        const currentLine = creases[i];

        creases[i] = [0, currentLine, 1, currentLine];
    }

    const diag = positive ? [0, 0, 1, target] : [1, 0, 0, target];

    creases.push(diag);

    if (!horizontal) {
        for (let i = 0; i < creases.length; i++) {
            const currentCrease = creases[i];
            const rotatedCrease = rotateCCW(currentCrease[0],currentCrease[1],currentCrease[2],currentCrease[3],0.5,0.5,90);
            creases[i] = rotatedCrease;
        }
    }

    return creases;
}

function rotateCCW(x1, y1, x2, y2, originX, originY, degrees) {
    x1 -= originX; y1 -= originY;
    x2 -= originX; y2 -= originY;

    const cos = Math.cos(degrees / 180 * Math.PI);
    const sin = Math.sin(degrees / 180 * Math.PI);

    const nx1 = x1 * cos - y1 * sin;
    const ny1 = x1 * sin + y1 * cos;
    const nx2 = x2 * cos - y2 * sin;
    const ny2 = x2 * sin + y2 * cos;

    return [nx1 + originX, ny1 + originY, nx2 + originX, ny2 + originY];
}

function extendToBoundary(x1, y1, x2, y2) {
    if (x1 === x2) return [x1, 0, x1, 1];
    if (y1 === y2) return [0, y1, 1, y1];

    // use a single intercept computation, derive everything from it
    const slope = (y2 - y1) / (x2 - x1);
    const yAtX0 = y1 - slope * x1;   // y intercept at x=0
    const yAtX1 = yAtX0 + slope;      // y intercept at x=1, derived from same base

    const candidates = [
        yAtX0 >= 0 && yAtX0 <= 1 ? [0, yAtX0] : null,
        yAtX1 >= 0 && yAtX1 <= 1 ? [1, yAtX1] : null,
        yAtX0 <= 0 ? [-yAtX0 / slope, 0] : null,
        yAtX0 >= 1 ? [(1 - yAtX0) / slope, 1] : null,
        yAtX1 <= 0 ? [-yAtX0 / slope, 0] : null,
        yAtX1 >= 1 ? [(1 - yAtX0) / slope, 1] : null,
    ].filter(Boolean);

    const pts = [];
    for (const c of candidates) {
        if (!pts.some(p => Math.abs(p[0]-c[0]) < 1e-9 && Math.abs(p[1]-c[1]) < 1e-9))
            pts.push(c);
    }

    return [...pts[0], ...pts[1]];
}

function pruneDuplicates(lines, eps = 1e-6) {
    const canonicalize = ({ x1, y1, x2, y2, class: cls }) => {
        if (x1 > x2 || (x1 === x2 && y1 > y2)) {
            [x1, y1, x2, y2] = [x2, y2, x1, y1];
        }
        return { x1, y1, x2, y2, class: cls };
    };

    const nearlyEqual = (a, b) =>
        Math.abs(a.x1 - b.x1) < eps &&
        Math.abs(a.y1 - b.y1) < eps &&
        Math.abs(a.x2 - b.x2) < eps &&
        Math.abs(a.y2 - b.y2) < eps;

    const isBetter = (a, b) => {
        if (a.class === "final" && b.class !== "final") return true;
        if (a.class !== "final" && b.class === "final") return false;
        return false;
    };

    const result = [];

    for (const line of lines.map(canonicalize)) {
        let found = false;

        for (let i = 0; i < result.length; i++) {
            if (nearlyEqual(result[i], line)) {
                found = true;

                if (isBetter(line, result[i])) {
                    result[i] = line;
                }
                break;
            }
        }

        if (!found) result.push(line);
    }

    const finalIndex = result.findIndex(l => l.class === "final");
    if (finalIndex !== -1) {
        return result.slice(0, finalIndex + 1);
    }

    return result;
}

function stretchArray(array, originX, originY, stretchX, stretchY) {
    for (let i = 0; i < array.length; i++) {
        let x1 = array[i][0];
        let y1 = array[i][1];
        let x2 = array[i][2];
        let y2 = array[i][3];

        x1 -= originX; x2 -= originX; y1 -= originY; y2 -= originY;
        x1 *= stretchX; x2 *= stretchX; y1 *= stretchY; y2 *= stretchY;
        x1 += originX; x2 += originX; y1 += originY; y2 += originY;

        array[i][0] = x1;
        array[i][1] = y1;
        array[i][2] = x2;
        array[i][3] = y2;
    }
}

function drawIntersection(d1, n1, r1, d2, n2, r2) {
    if (n1/d1 < 0 || n2/d2 < 0) {
        return null;
    }

    let diag1 = getDiagonal(n1, d1);
    let diag2 = getDiagonal(n2, d2);

    let stretchX1, stretchY1, stretchX2, stretchY2;

    if (r1 >= 1) {stretchX1 = 1; stretchY1 = 1/r1}
    else {stretchX1 = r1; stretchY1 = 1};

    if (r2 >= 1) {stretchX2 = 1; stretchY2 = 1/r2}
    else {stretchX2 = r2; stretchY2 = 1};

    stretchArray(diag1, 1, 0, stretchX1, stretchY1);
    stretchArray(diag2, 1, 0, stretchX2, stretchY2);

    stretchArray(diag2, 0.5, 0, -1, 1);

    diag1 = diag1.map(([x1, y1, x2, y2]) => extendToBoundary(x1, y1, x2, y2));
    diag2 = diag2.map(([x1, y1, x2, y2]) => extendToBoundary(x1, y1, x2, y2));

    const result = [];

    const add = (arr, cls) =>
        arr.forEach(([x1, y1, x2, y2]) =>
            result.push({ x1, y1, x2, y2, class: cls })
        );

    add(diag1, "diag1");
    add(diag2, "diag2");

    return result;
}

function findY(a, b, c) {
    //this is the master function, it takes as input a, b, c where a reference line defines beneath it a rectangle of width (a + b * rt2) / c

    if ((a + b * Math.SQRT2) / c < 1) {
        return;
    }

    const solutions = [];

    function addSolution(name, preCreasing, cp) {
        if (!cp) return;

        const finalCrease = {x1: 0, y1: c/(a+b*Math.SQRT2), x2: 1, y2: c/(a+b*Math.SQRT2), class: "final"}

        let base;

        if (b === 0 && ["comboA", "comboB", "comboC", "comboD"].includes(name)) {
            base = [...cp, finalCrease]; //no need to precrease 22.5 for rational fractions
        } else {
            base = [...preCreasing, ...cp, finalCrease];
        }

        const combined = pruneDuplicates(base);

        solutions.push({
            name,
            cp: combined,
            score: combined.length
        });
    }

    addSolution("comboA", [
        { x1: 0,            y1: 0, x2: 1,            y2: 1, class: "preCrease"},
        { x1: 0,            y1: 0, x2: Math.SQRT2-1, y2: 1, class: "preCrease"},
        { x1: Math.SQRT2-1, y1: 0, x2: Math.SQRT2-1, y2: 1, class: "preCrease"}
        ], drawIntersection(a+b, c, 1, b, c, Math.SQRT2-1));
    addSolution("comboB", [
        {x1: 1,            y1: 0, x2: 0,            y2: 1, class: "preCrease"},
        {x1: 1,            y1: 0, x2: 2-Math.SQRT2, y2: 1, class: "preCrease"},
        {x1: 2-Math.SQRT2, y1: 0, x2: 2-Math.SQRT2, y2: 1, class: "preCrease"}
        ], drawIntersection(a+2*b, c, 1, -b, c, 2-Math.SQRT2));
    addSolution("comboC", [
        {x1: 0, y1: 0,            x2: 1, y2: 1           , class: "preCrease"},
        {x1: 1, y1: 1,            x2: 0, y2: 2-Math.SQRT2, class: "preCrease"},
        {x1: 1, y1: 2-Math.SQRT2, x2: 0, y2: 2-Math.SQRT2, class: "preCrease"}
        ], drawIntersection(2*b, c, 1 + Math.SQRT2/2, a - 2*b, c, 1));
    addSolution("comboD", [
        {x1: 1, y1: 0,            x2: 0, y2: 1           , class: "preCrease"},
        {x1: 1, y1: 0,            x2: 0, y2: Math.SQRT2-1, class: "preCrease"},
        {x1: 1, y1: Math.SQRT2-1, x2: 0, y2: Math.SQRT2-1, class: "preCrease"}
        ], drawIntersection(b, c, Math.SQRT2+1, a-b, c, 1));
    addSolution("comboE", [
        {x1: 0,            y1: 0, x2: 1,            y2: 1, class: "preCrease"},
        {x1: 0,            y1: 0, x2: Math.SQRT2-1, y2: 1, class: "preCrease"},
        {x1: Math.SQRT2-1, y1: 0, x2: Math.SQRT2-1, y2: 1, class: "preCrease"}
        ], drawIntersection(a+b, c, 2-Math.SQRT2, a+2*b, c, Math.SQRT2-1));
    addSolution("comboF", [
        {x1: 0,            y1: 1,            x2: 1,            y2: 0           , class: "preCrease"},
        {x1: 0,            y1: 1,            x2: Math.SQRT2-1, y2: 0           , class: "preCrease"},
        {x1: Math.SQRT2-1, y1: 0,            x2: Math.SQRT2-1, y2: 1           , class: "preCrease"},
        {x1: 1,            y1: 2-Math.SQRT2, x2: 0,            y2: 2-Math.SQRT2, class: "preCrease"}
        ], drawIntersection(2*a + 2*b, 3*c, 1 + Math.SQRT2/2, -a + 2*b, 3*c, Math.SQRT2-1));
    addSolution("comboG", [
        {x1: 0,            y1: 0,            x2: 1,            y2: 1           , class: "preCrease"},
        {x1: 0,            y1: 0,            x2: Math.SQRT2-1, y2: 1           , class: "preCrease"},
        {x1: Math.SQRT2-1, y1: 0,            x2: Math.SQRT2-1, y2: 1           , class: "preCrease"},
        {x1: 1,            y1: Math.SQRT2-1, x2: 0,            y2: Math.SQRT2-1, class: "preCrease"}
        ], drawIntersection(a+b, 2*c, Math.SQRT2+1, -a+b, 2*c, Math.SQRT2-1));
    addSolution("comboH", [
        {x1: 1,            y1: 1,            x2: 0,            y2: 0           , class: "preCrease"},
        {x1: 1,            y1: 1,            x2: 2-Math.SQRT2, y2: 0           , class: "preCrease"},
        {x1: 2-Math.SQRT2, y1: 0,            x2: 2-Math.SQRT2, y2: 1           , class: "preCrease"},
        {x1: 1,            y1: 2-Math.SQRT2, x2: 0,            y2: 2-Math.SQRT2, class: "preCrease"}
        ], drawIntersection(a+2*b, 2*c, 1+Math.SQRT2/2, a-2*b, 4*c, 2-Math.SQRT2));
    addSolution("comboI", [
        {x1: 1,            y1: 0,            x2: 0,            y2: 1           , class: "preCrease"},
        {x1: 1,            y1: 0,            x2: 2-Math.SQRT2, y2: 1           , class: "preCrease"},
        {x1: 2-Math.SQRT2, y1: 0,            x2: 2-Math.SQRT2, y2: 1           , class: "preCrease"},
        {x1: 1,            y1: Math.SQRT2-1, x2: 0,            y2: Math.SQRT2-1, class: "preCrease"}
        ], drawIntersection(a+2*b, 3*c, Math.SQRT2+1, a-b, 3*c, 2-Math.SQRT2));
    addSolution("comboJ", [
        {x1: 1, y1: 0,            x2: 0, y2: 1           , class: "preCrease"},
        {x1: 1, y1: 0,            x2: 0, y2: Math.SQRT2-1, class: "preCrease"},
        {x1: 1, y1: Math.SQRT2-1, x2: 0, y2: Math.SQRT2-1, class: "preCrease"},
        {x1: 0, y1: 1,            x2: 1, y2: 2-Math.SQRT2, class: "preCrease"},
        {x1: 0, y1: 2-Math.SQRT2, x2: 1, y2: 2-Math.SQRT2, class: "preCrease"}
        ], drawIntersection(-a+2*b, c, Math.SQRT2+1, 2*a-2*b, c, 1+Math.SQRT2/2));

    return solutions;
}

function fourOptions(a1,b1,c1,a2,b2,c2) {
    const x = (a1 + b1 * Math.SQRT2) / c1;
    const y = (a2 + b2 * Math.SQRT2) / c2;
    if (x > 1 || x < 0 || y > 1 || y < 0) {
        console.error("x, y must be between 0 and 1");
        return;
    }

    const abcBelow = {a: a2*c2, b: -b2*c2, c: a2*a2-2*b2*b2};
    const abcAbove = {a: c2*c2-a2*c2, b: b2*c2, c: c2*c2-2*a2*c2+a2*a2-2*b2*b2};
    const abcLeft = {a: a1*c1, b: -b1*c1, c: a1*a1-2*b1*b1};
    const abcRight = {a: c1*c1-a1*c1, b: b1*c1, c: c1*c1-2*a1*c1+a1*a1-2*b1*b1};

    function safeFind(a, b, c) {
        if (c === 0) return [];
        const val = (a + b * Math.SQRT2) / c;
        if (!isFinite(val) || val < 1) return [];
        return findY(a, b, c) ?? [];
    }

    const belowSolution = safeFind(abcBelow.a, abcBelow.b, abcBelow.c);
    const aboveSolution = safeFind(abcAbove.a, abcAbove.b, abcAbove.c);
    const leftSolution  = safeFind(abcLeft.a,  abcLeft.b,  abcLeft.c);
    const rightSolution = safeFind(abcRight.a, abcRight.b, abcRight.c);

    const onEdgeX = (x === 0 || x === 1);
    const onEdgeY = (y === 0 || y === 1);

    const rawSolutions = [
        ...(!onEdgeY ? belowSolution.map(s => ({ ...s, rot: 0   })) : []),
        ...(!onEdgeY ? aboveSolution.map(s => ({ ...s, rot: 180 })) : []),
        ...(!onEdgeX ? leftSolution.map(s  => ({ ...s, rot: 270 })) : []),
        ...(!onEdgeX ? rightSolution.map(s => ({ ...s, rot: 90  })) : []),
    ];

    // 2. geometry helpers
    function rotateCrease(crease, degrees) {
        const [nx1, ny1, nx2, ny2] = rotateCCW(
            crease.x1, crease.y1,
            crease.x2, crease.y2,
            0.5, 0.5,
            degrees
        );

        return {
            x1: nx1,
            y1: ny1,
            x2: nx2,
            y2: ny2,
            class: crease.class
        };
    }

    function rotateCP(cp, degrees) {
        return cp.map(c => rotateCrease(c, degrees));
    }

    function rotateSolution(solution, degrees) {
        return {
            ...solution,
            cp: rotateCP(solution.cp, degrees)
        };
    }

    // 3. single-pass transform
    const finalSolutions = rawSolutions.map(sol => {
        const rotated = rotateSolution(sol, sol.rot);
        delete rotated.rot; // optional cleanup so drawer never sees it
        return rotated;
    });

    finalSolutions.sort((a, b) => a.score - b.score);

    return finalSolutions;
}
