/* Innerlatch's Adventure page: the overworld. Every counted lesson from GET /api/party/map stands on a pixel continent as
   an encounter, in the territory of its first module: six regions, one per attribute (a forge town, a lake country,
   server mountains, a walled march, a wizard's land, an old forest), each cut into one territory per module. A module
   with no lessons yet lies under fog. The main quest's bosses wait at their region's gate; the party stands where the
   newest lesson was learned.

   The art is pixel maps, as in sprites.js and scene.js: no images, styles only through the CSSOM, no eval. The land is
   painted once into a canvas one pixel a world pixel (ImageData, then the Cast's props); each frame draws the part of it
   on screen, scaled, and the encounters, bosses and party over it. One requestAnimationFrame loop serves every mounted
   map; it rests while the page is hidden, the map is off screen, or (with reduced motion) nothing moves.

   window.Adventure = {
     mountMap(host, opts) -> { destroy(), focusLesson(id), focusRegion(attrId), setCleared(ids), redraw() }
       opts.data           the /api/party/map payload
       opts.labels         overrides for LABELS below (the page passes Japanese)
       opts.reducedMotion  no idle animation, no camera easing
       opts.onHover(item, at)  item = {type: "lesson", lesson, cleared} | {type: "boss", boss} | {type: "party", agents}
                           | {type: "region", region, module, fog} | null; at = {x, y} in client pixels
       opts.onPick(item)   click, tap or Enter on a lesson, a boss or the party
       opts.height         css pixels (otherwise the map fills its host, 320 at least)
       opts.seed           the layout's seed (1)
     layout(data, seed = 1) -> the world, pure and deterministic (no DOM): see layout() below
     LABELS                the English defaults
   } */
(() => {
  "use strict";

  /* Labels, English. Every key may be overridden through opts.labels; {placeholders} are filled in.
       map           the map's accessible name: how to move around it
       regionTitle   a region's banner: {name}
       regionStats   under the banner, zoomed in: {cleared}, {lessons}
       fog           a territory with no lessons yet
       progress      under a territory's name, zoomed in: {cleared}, {lessons}
       boss          the tag over a boss: {text} (the milestone)
       bossAsleep, bossAwake, bossDefeated   a boss's state, read out with its tag
       party         the party, read out when focused
       lesson        a lesson, read out when focused: {title}, {kind}, {agent}
       cleared       added to a lesson read out when it has been beaten
       zoomIn, zoomOut, home, fit   the map's buttons (home: back to the party; fit: the whole map)
       empty         over the map when there is no lesson at all
       regions       {attr id: name}: renames regions (the payload's names otherwise)
       modules       {module id: name}: renames territories */
  const LABELS = Object.freeze({
    map: "Adventure map. Drag or use the arrow keys to look around; scroll, pinch or press + and − to zoom; N and P step through the encounters on screen and Enter opens one.",
    regionTitle: "{name}",
    regionStats: "{cleared}/{lessons} cleared",
    fog: "Unexplored",
    progress: "{cleared}/{lessons}",
    boss: "Boss: {text}",
    bossAsleep: "asleep",
    bossAwake: "awake: evidence found",
    bossDefeated: "defeated",
    party: "Your party",
    lesson: "{title}",
    cleared: "cleared",
    zoomIn: "Zoom in",
    zoomOut: "Zoom out",
    home: "Back to the party",
    fit: "Show the whole map",
    empty: "No lessons yet: every lesson your agents learn will stand here as an encounter",
    regions: Object.freeze({}),
    modules: Object.freeze({}),
  });

  const root = typeof window !== "undefined" ? window : globalThis;
  const FONT = '"IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace';
  const AGENT_ORDER = ["claude", "codex", "copilot", "bob", "antigravity", "opencode"];

  // ---- the world's measures, in world pixels (one pixel of the land's art)
  const MIN_DIST = 16;                 // no two encounters stand closer
  const LESSON_AREA = 2.8 * MIN_DIST * MIN_DIST;
  const MARGIN = 8;                    // an encounter's feet stay this far inside its territory
  const HOLE = { w: 46, h: 32 };       // a clearing at each explored territory's heart: its name, and the party's spot
  const FOG_AREA = 2400, EMPTY_AREA = 4200, MIN_REGION = 30000;
  const SEA = 84, ASPECT = 1.6, ROW_SHIFT = 40;
  const GATE = { w: 168, d: 52 };      // the gate's yard: the arch and up to four bosses
  const BANNER = { w: 196, h: 30 };    // the region's banner
  const BOSS_SLOTS = [36, -36, 72, -72];
  const BUCKETS = (() => { const b = []; for (let v = 4; v < 1e7; v = Math.ceil(v * 1.3)) b.push(v); return b; })();
  const bucket = (n) => { if (n <= 0) return 0; for (const v of BUCKETS) if (v >= n) return v; return n; };

  const fill = (tpl, vars) => String(tpl).replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

  // =====================================================================================
  // Randomness: seeded, so the same payload always makes the same world
  // =====================================================================================
  function rng(seed) {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function mixInt(a, b) {
    let h = Math.imul((a | 0) ^ 0x9e3779b9, 0x85ebca6b) ^ Math.imul((b | 0) + 0x632be5ab, 0xc2b2ae35);
    h ^= h >>> 15; h = Math.imul(h, 0x2c1b3c6d); h ^= h >>> 12; h = Math.imul(h, 0x297a2d39); h ^= h >>> 15;
    return h >>> 0;
  }
  const hash01 = (x, y, s) => mixInt(mixInt(x, y), s) / 4294967296;
  function strHash(str) {
    let h = 0x811c9dc5;
    const s = String(str);
    for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); }
    return h >>> 0;
  }
  // smooth value noise in [0, 1), `cell` world pixels a wave
  function noise(seed, cell) {
    return (x, y) => {
      const gx = x / cell, gy = y / cell, x0 = Math.floor(gx), y0 = Math.floor(gy), fx = gx - x0, fy = gy - y0;
      const sx = fx * fx * (3 - 2 * fx), sy = fy * fy * (3 - 2 * fy);
      const a = hash01(x0, y0, seed), b = hash01(x0 + 1, y0, seed), c = hash01(x0, y0 + 1, seed), d = hash01(x0 + 1, y0 + 1, seed);
      return a + (b - a) * sx + (c - a) * sy + (a - b - c + d) * sx * sy;
    };
  }

  // =====================================================================================
  // Geometry: convex polygons as [[x, y], …]
  // =====================================================================================
  const rectPoly = (r) => [[r.x, r.y], [r.x + r.w, r.y], [r.x + r.w, r.y + r.h], [r.x, r.y + r.h]];
  // the part of `poly` where a·x + b·y ≤ c
  function clip(poly, a, b, c) {
    const out = [];
    for (let k = 0; k < poly.length; k++) {
      const P = poly[k], Q = poly[(k + 1) % poly.length];
      const fp = a * P[0] + b * P[1] - c, fq = a * Q[0] + b * Q[1] - c;
      if (fp <= 0) out.push(P);
      if ((fp <= 0) !== (fq <= 0)) { const t = fp / (fp - fq); out.push([P[0] + (Q[0] - P[0]) * t, P[1] + (Q[1] - P[1]) * t]); }
    }
    return out;
  }
  function polyArea(p) {
    let s = 0;
    for (let i = 0; i < p.length; i++) { const a = p[i], b = p[(i + 1) % p.length]; s += a[0] * b[1] - b[0] * a[1]; }
    return s / 2;
  }
  function centroid(p) {
    let x = 0, y = 0, s = 0;
    for (let i = 0; i < p.length; i++) {
      const a = p[i], b = p[(i + 1) % p.length], k = a[0] * b[1] - b[0] * a[1];
      s += k; x += (a[0] + b[0]) * k; y += (a[1] + b[1]) * k;
    }
    if (Math.abs(s) < 1e-9) return p.length ? [p[0][0], p[0][1]] : [0, 0];
    return [x / (3 * s), y / (3 * s)];
  }
  // how far (x, y) lies inside a convex polygon (negative outside)
  function depth(p, x, y) {
    if (p.length < 3) return -Infinity;
    const sgn = polyArea(p) >= 0 ? 1 : -1;
    let d = Infinity;
    for (let i = 0; i < p.length; i++) {
      const a = p[i], b = p[(i + 1) % p.length], ex = b[0] - a[0], ey = b[1] - a[1], len = Math.hypot(ex, ey);
      if (len < 1e-9) continue;
      d = Math.min(d, (sgn * (ex * (y - a[1]) - ey * (x - a[0]))) / len);
    }
    return d;
  }
  // a convex polygon's edges as inner normals [ax, ay, nx, ny]: depth = min(nx·(x − ax) + ny·(y − ay))
  function edgesOf(p) {
    const sgn = polyArea(p) >= 0 ? 1 : -1, out = [];
    for (let i = 0; i < p.length; i++) {
      const a = p[i], b = p[(i + 1) % p.length], ex = b[0] - a[0], ey = b[1] - a[1], len = Math.hypot(ex, ey);
      if (len >= 1e-9) out.push([a[0], a[1], (-sgn * ey) / len, (sgn * ex) / len]);
    }
    return out;
  }
  // the x range of row y lying at least m inside (null when none)
  function span(E, y, m) {
    let lo = -Infinity, hi = Infinity;
    for (const [ax, ay, nx, ny] of E) {
      const need = m - ny * (y - ay);
      if (Math.abs(nx) < 1e-12) { if (need > 0) return null; continue; }
      const b = ax + need / nx;
      if (nx > 0) lo = Math.max(lo, b); else hi = Math.min(hi, b);
    }
    return lo <= hi ? [lo, hi] : null;
  }
  // every `step`-th point of a territory an encounter's feet may stand on: MARGIN inside, its head inside too, and its
  // body clear of the `keep` rectangles
  function standable(poly, keep, step, visit) {
    const E = edgesOf(poly), xs = poly.map((q) => q[0]), ys = poly.map((q) => q[1]), x0 = Math.ceil(Math.min(...xs));
    for (let y = Math.ceil(Math.min(...ys)); y < Math.max(...ys); y += step) {
      const a = span(E, y, MARGIN), b = a && span(E, y - 11, 3);
      if (!b) continue;
      const lo = Math.max(a[0], b[0]), hi = Math.min(a[1], b[1]), rowKeep = keep.filter((q) => q.y < y + 1 && y - 16 < q.y + q.h);
      for (let x = x0 + Math.ceil((lo - x0) / step) * step; x <= hi; x += step) {
        if (!rowKeep.some((q) => x - 9 < q.x + q.w && q.x < x + 9)) visit(x, y);
      }
    }
  }
  const inRect = (r, x, y) => x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h;
  const overlaps = (a, b) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

  // power cells (a weighted Voronoi diagram) of `sites` inside the box
  function powerCells(sites, w, box) {
    return sites.map((s, i) => {
      let poly = box;
      for (let j = 0; j < sites.length && poly.length; j++) {
        if (j === i) continue;
        const t = sites[j];
        poly = clip(poly, 2 * (t[0] - s[0]), 2 * (t[1] - s[1]), t[0] * t[0] + t[1] * t[1] - s[0] * s[0] - s[1] * s[1] - w[j] + w[i]);
      }
      return poly;
    });
  }
  // the length of a cell's edges that face other cells (not the region's own edge)
  function innerPerimeter(poly, r) {
    const on = (a, b) => (Math.abs(a[0] - r.x) < 1e-6 && Math.abs(b[0] - r.x) < 1e-6) || (Math.abs(a[0] - r.x - r.w) < 1e-6 && Math.abs(b[0] - r.x - r.w) < 1e-6)
      || (Math.abs(a[1] - r.y) < 1e-6 && Math.abs(b[1] - r.y) < 1e-6) || (Math.abs(a[1] - r.y - r.h) < 1e-6 && Math.abs(b[1] - r.y - r.h) < 1e-6);
    let s = 0;
    for (let i = 0; i < poly.length; i++) { const a = poly[i], b = poly[(i + 1) % poly.length]; if (!on(a, b)) s += Math.hypot(b[0] - a[0], b[1] - a[1]); }
    return s;
  }
  // a Voronoi treemap: the rectangle cut into convex cells (a power diagram) with areas close to `targets`' shares.
  // For fixed sites the weights that give the areas maximise a concave function whose gradient is (target − area), so a
  // damped step up it always converges, and an emptied cell comes back; between solves the sites move to their cells'
  // centroids (Lloyd), which rounds the territories out.
  function treemap(rect, targets, rand, pins = []) {
    const n = targets.length, box = rectPoly(rect), A = rect.w * rect.h;
    if (!n) return { cells: [], pins: [] };
    // the pins (the gate's yard, the banner's square) hold their place and their share; the territories share the rest
    const pinned = pins.reduce((a, p) => a + p.target, 0), room = Math.max(A * 0.3, A - pinned);
    const sum = targets.reduce((a, b) => a + b, 0) || 1, T = targets.map((t) => (t / sum) * room).concat(pins.map((p) => p.target));
    const m = T.length;
    const cols = Math.max(1, Math.round(Math.sqrt((n * rect.w) / rect.h))), rows = Math.ceil(n / cols);
    // the biggest territories start in the middle of the region
    const slots = [];
    for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) slots.push([(c + 0.5) / cols, (r + 0.5) / rows]);
    slots.sort((p, q) => Math.hypot(p[0] - 0.5, p[1] - 0.5) - Math.hypot(q[0] - 0.5, q[1] - 0.5) || p[1] - q[1] || p[0] - q[0]);
    const order = targets.map((t, i) => i).sort((a, b) => T[b] - T[a] || a - b);
    const sites = new Array(m), w = new Array(m).fill(0);
    order.forEach((i, k) => {
      const [u, v] = slots[k];
      sites[i] = [rect.x + rect.w * clamp(u + ((rand() - 0.5) * 0.5) / cols, 0.03, 0.97), rect.y + rect.h * clamp(v + ((rand() - 0.5) * 0.5) / rows, 0.03, 0.97)];
    });
    pins.forEach((p, k) => { sites[n + k] = p.site.slice(); });
    const solve = (iters) => {
      let cells = powerCells(sites, w, box);
      for (let it = 0; it < iters; it++) {
        const areas = cells.map((c) => Math.abs(polyArea(c)));
        let err = 0;
        for (let i = 0; i < m; i++) err += Math.abs(areas[i] - T[i]);
        if (err / A < 0.003) break;
        for (let i = 0; i < m; i++) {
          const per = Math.max(innerPerimeter(cells[i], rect), Math.sqrt(T[i])), reach = 2 * Math.sqrt(Math.max(areas[i], T[i]) / Math.PI);
          w[i] += (0.45 * (T[i] - areas[i]) * reach) / per;
        }
        cells = powerCells(sites, w, box);
      }
      return cells;
    };
    let cells = solve(400);
    for (let k = 0; k < 8; k++) {
      cells.forEach((c, i) => { if (i < n && Math.abs(polyArea(c)) > 1) sites[i] = centroid(c); });
      cells = solve(250);
    }
    const out = cells.map((poly, i) => ({ site: sites[i].slice(), weight: w[i], poly }));
    return { cells: out.slice(0, n), pins: out.slice(n) };
  }

  // =====================================================================================
  // The layout: pure, no DOM. layout(data, seed) returns
  //   { seed, width, height, minDist, land: [rects of the rows], boundary (y between the rows, or null),
  //     regions: [{id, name, index, row, col, rect, gate, arch, banner, reserve: [rects], plazas: [{kind, site, weight, poly}],
  //                modules: [ids], lessons}]  (plazas: the gate's yard and the banner's square, no one's territory)
  //     territories: [{id, name, attr, region, index, lessons, count, fog, poly, center, hole: {x, y, w, h} | null, site, weight, area}],
  //     lessons: [{id, x, y, module, attr, territory, index}]  (x, y: the encounter's feet, integers)
  //     bosses: [{id, n, attr, region, x, y, status}], party: {x, y, module, territory, lesson} | null,
  //     unplaced: [ids of lessons whose first module is on no region] }
  // =====================================================================================
  function layout(data, seed = 1) {
    const s0 = (Math.round(Number(seed)) >>> 0) || 1;
    const rand = rng(mixInt(s0, 0x51ed));
    const regionsIn = Array.isArray(data && data.regions) ? data.regions : [];
    const lessonsIn = Array.isArray(data && data.lessons) ? data.lessons : [];
    // whose territory each module is (the first region to name it), and how many lessons stand in it
    const home = new Map();
    regionsIn.forEach((r) => (Array.isArray(r.modules) ? r.modules : []).forEach((m) => { if (m && m.id != null && !home.has(m.id)) home.set(m.id, r.id); }));
    const first = (l) => (Array.isArray(l.modules) && l.modules.length ? l.modules[0] : null);
    const placedIn = new Map();
    for (const l of lessonsIn) { const m = first(l); if (home.has(m)) placedIn.set(m, (placedIn.get(m) || 0) + 1); }
    const regions = regionsIn.map((r, index) => {
      const terrs = (Array.isArray(r.modules) ? r.modules : []).filter((m) => m && home.get(m.id) === r.id).map((m) => {
        const count = placedIn.get(m.id) || 0, lessons = Math.max(0, Number(m.lessons) || 0), fog = count === 0 && lessons === 0;
        const target = count > 0 ? (Math.sqrt(bucket(count) * LESSON_AREA + HOLE.w * HOLE.h * 1.5) + 2 * MARGIN) ** 2 : fog ? FOG_AREA : EMPTY_AREA;
        return { id: m.id, name: m.name || String(m.id), attr: r.id, lessons, count, fog, target };
      });
      const need = Math.max(MIN_REGION, 1.12 * (terrs.reduce((a, t) => a + t.target, 0) + GATE.w * GATE.d + BANNER.w * BANNER.h));
      return { id: r.id, name: r.name || String(r.id), index, terrs, need };
    });
    const out = { seed: s0, width: 2 * SEA + 240, height: 2 * SEA + 160, minDist: MIN_DIST, land: [], boundary: null,
      regions: [], territories: [], lessons: [], bosses: [], party: null, unplaced: [] };
    if (!regions.length) { out.unplaced = lessonsIn.map((l) => l.id); return out; }
    // the continent: two rows of regions (one for three or fewer), each as wide as the land, heights by need
    const mean = regions.reduce((a, r) => a + r.need, 0) / regions.length;
    for (const r of regions) r.need = Math.max(r.need, mean * 0.8); // no region a sliver beside a big one
    const rowsN = regions.length > 3 ? 2 : 1, perRow = Math.ceil(regions.length / rowsN);
    const rows = [];
    for (let k = 0; k < rowsN; k++) rows.push(regions.slice(k * perRow, (k + 1) * perRow));
    const total = regions.reduce((a, r) => a + r.need, 0), Wl = Math.round(Math.sqrt(total * ASPECT));
    const shift = rowsN > 1 ? Math.round((rand() - 0.5) * 2 * ROW_SHIFT) : 0, x0 = SEA + Math.max(0, -shift);
    let y = SEA;
    rows.forEach((row, k) => {
      const rowNeed = row.reduce((a, r) => a + r.need, 0), h = Math.max(120, Math.round(rowNeed / Wl)), rx = x0 + (k ? shift : 0);
      let acc = 0;
      row.forEach((r, i) => {
        const xa = Math.round(rx + (Wl * acc) / rowNeed);
        acc += r.need;
        const xb = Math.round(rx + (Wl * acc) / rowNeed);
        Object.assign(r, { row: k, col: i, rect: { x: xa, y, w: xb - xa, h } });
      });
      out.land.push({ x: rx, y, w: Wl, h });
      y += h;
    });
    if (rowsN > 1) out.boundary = out.land[1].y;
    out.width = Wl + Math.abs(shift) + 2 * SEA;
    out.height = y + SEA;
    // each region: its gate on the inner edge (the bottom for one row), its banner on the outer one, its territories
    for (const r of regions) {
      const R = r.rect, cx = Math.round(R.x + R.w / 2), down = rowsN > 1 && r.row === 1; // a bottom-row region opens upward
      const edge = down ? R.y : R.y + R.h, gw = Math.min(GATE.w, R.w - 2 * MARGIN);
      r.gate = { x: cx, y: edge };
      r.arch = { x: cx, y: down ? edge + 20 : edge - 4 };
      const reserve = [{ x: cx - gw / 2, y: down ? edge : edge - GATE.d, w: gw, h: GATE.d }];
      const bw = Math.min(BANNER.w, R.w - 2 * MARGIN), by = down ? R.y + R.h - 4 - BANNER.h : R.y + 4;
      reserve.push({ x: cx - bw / 2, y: by, w: bw, h: BANNER.h });
      r.banner = { x: cx, y: by + BANNER.h / 2 };
      r.reserve = reserve;
      // cut the region; a territory the gate, the banner or its own clearing leaves too little room is given more and
      // the region cut again (the same seed each time, so the result stays deterministic)
      const targets = r.terrs.map((t) => t.target);
      const pins = [{ kind: "gate", site: [cx, down ? edge + GATE.d * 0.45 : edge - GATE.d * 0.45], target: GATE.w * GATE.d * 0.75 },
        { kind: "banner", site: [cx, by + BANNER.h / 2], target: bw * BANNER.h * 0.8 }];
      let cells = null, holes = null;
      for (let pass = 0; pass < 5; pass++) {
        const cut = treemap(R, targets, rng(mixInt(s0, strHash(r.id))), pins);
        cells = cut.cells;
        r.plazas = cut.pins.map((c, k) => ({ kind: pins[k].kind, site: c.site, weight: c.weight, poly: c.poly }));
        holes = cells.map((c, i) => (r.terrs[i].fog ? null : clearing(c.poly, centroid(c.poly), reserve)));
        let short = false;
        r.terrs.forEach((t, i) => {
          if (!t.count) return;
          const room = usable(cells[i].poly, reserve, holes[i]), need = t.count * 2.1 * MIN_DIST * MIN_DIST + (holes[i] ? 0 : HOLE.w * HOLE.h);
          if (room < need) { targets[i] *= Math.min(3, (need + 400) / Math.max(room, 200)); short = true; }
          else if (!holes[i]) { targets[i] *= 1.25; short = true; } // too thin for its clearing
        });
        if (!short) break;
      }
      r.terrs.forEach((t, i) => {
        const c = cells[i], ctr = centroid(c.poly);
        Object.assign(t, { region: r.index, poly: c.poly.map(([px, py]) => [px, py]), site: c.site, weight: c.weight,
          area: Math.abs(polyArea(c.poly)), center: { x: ctr[0], y: ctr[1] }, hole: holes[i] });
        t.index = out.territories.length;
        out.territories.push(t);
      });
    }
    const terrOf = new Map(out.territories.map((t) => [t.id, t]));
    // encounters, oldest first: a seeded dart-throw inside the territory, kept MIN_DIST apart (closer only if crowded)
    const grid = new Map(), CELL = MIN_DIST;
    const nearAny = (x, y, d) => {
      const gx = Math.floor(x / CELL), gy = Math.floor(y / CELL), reach = Math.ceil(d / CELL);
      for (let i = -reach; i <= reach; i++) for (let j = -reach; j <= reach; j++) {
        const list = grid.get((gx + i) * 65536 + (gy + j));
        if (list) for (const p of list) if ((p.x - x) ** 2 + (p.y - y) ** 2 < d * d) return true;
      }
      return false;
    };
    // where in a territory an encounter may stand: every other pixel inside it, clear of the gate, banner and clearing
    const spotsOf = new Map();
    const spots = (t) => {
      if (!spotsOf.has(t.index)) {
        const list = [];
        standable(t.poly, regions[t.region].reserve.concat(t.hole ? [holeRect(t.hole)] : []), 2, (x, y) => list.push([x, y]));
        spotsOf.set(t.index, list);
      }
      return spotsOf.get(t.index);
    };
    const nearest = (x, y, cap) => {
      let best = cap * cap;
      const gx = Math.floor(x / CELL), gy = Math.floor(y / CELL), reach = Math.ceil(cap / CELL);
      for (let i = -reach; i <= reach; i++) for (let j = -reach; j <= reach; j++) {
        const list = grid.get((gx + i) * 65536 + (gy + j));
        if (list) for (const p of list) best = Math.min(best, (p.x - x) ** 2 + (p.y - y) ** 2);
      }
      return Math.sqrt(best);
    };
    let used = MIN_DIST;
    lessonsIn.forEach((l, index) => {
      const t = terrOf.get(first(l));
      if (!t) { out.unplaced.push(l.id); return; }
      const r = rng(mixInt(s0, strHash(l.id))), list = spots(t);
      let spot = null;
      for (let k = 0; k < 120 && list.length && !spot; k++) {
        const [x, y] = list[Math.floor(r() * list.length)];
        if (!nearAny(x, y, MIN_DIST)) spot = { x, y };
      }
      if (!spot && list.length) { // crowded: the spot farthest from the others
        let far = -1;
        for (const [x, y] of list) { const d = nearest(x, y, MIN_DIST); if (d > far) { far = d; spot = { x, y }; } }
        used = Math.min(used, far);
      }
      if (!spot) { // no room at all: the territory's middle
        spot = { x: Math.round(t.center.x), y: Math.round(t.center.y) };
        used = 0;
      }
      const p = { id: l.id, x: spot.x, y: spot.y, module: t.id, attr: t.attr, territory: t.index, index };
      const key = Math.floor(p.x / CELL) * 65536 + Math.floor(p.y / CELL);
      if (!grid.has(key)) grid.set(key, []);
      grid.get(key).push(p);
      out.lessons.push(p);
    });
    out.minDist = used;
    // the bosses wait at their region's gate, by milestone
    const bossesIn = Array.isArray(data && data.bosses) ? data.bosses : [];
    const byRegion = new Map(regions.map((r) => [r.id, []]));
    for (const b of bossesIn) if (byRegion.has(b.attr)) byRegion.get(b.attr).push(b);
    for (const r of regions) {
      const mine = byRegion.get(r.id).slice().sort((a, b) => (a.n || 0) - (b.n || 0));
      mine.slice(0, BOSS_SLOTS.length).forEach((b, k) => {
        const off = BOSS_SLOTS[k] * Math.min(1, (r.rect.w - 2 * MARGIN) / GATE.w);
        out.bosses.push({ id: b.id, n: b.n, attr: r.id, region: r.index, x: Math.round(r.arch.x + off), y: r.arch.y, status: b.status || "open" });
      });
    }
    // the party: in the clearing of the newest lesson's territory
    const newest = out.lessons.find((p) => data && data.newest != null && String(p.id) === String(data.newest)) || out.lessons[out.lessons.length - 1];
    if (newest) {
      const t = out.territories[newest.territory], h = t.hole || { x: t.center.x, y: t.center.y };
      out.party = { x: Math.round(h.x), y: Math.round(h.y + (h.h || HOLE.h) / 2 - 1), module: t.id, territory: t.index, lesson: newest.id };
    }
    out.regions = regions.map((r) => ({ id: r.id, name: r.name, index: r.index, row: r.row, col: r.col, rect: r.rect, gate: r.gate,
      arch: r.arch, banner: r.banner, reserve: r.reserve, plazas: r.plazas || [], modules: r.terrs.map((t) => t.id),
      lessons: r.terrs.reduce((a, t) => a + t.count, 0) }));
    for (const t of out.territories) delete t.target;
    return out;
  }
  const holeRect = (c) => { const w = c.w || HOLE.w, h = c.h || HOLE.h; return { x: c.x - w / 2, y: c.y - h / 2, w, h }; };
  // how much of a territory an encounter can stand on (sampled every 3 pixels)
  function usable(poly, reserve, hole) {
    let n = 0;
    standable(poly, hole ? reserve.concat([holeRect(hole)]) : reserve, 3, () => { n++; });
    return n * 9;
  }
  // a narrow territory gets a narrower clearing
  function clearing(poly, ctr, reserve) {
    for (const [w, h] of [[HOLE.w, HOLE.h], [36, 30], [28, 28]]) {
      for (let ring = 0; ring <= 20; ring++) {
        const n = ring ? 8 * ring : 1;
        for (let k = 0; k < n; k++) {
          const a = (k / n) * Math.PI * 2, x = Math.round(ctr[0] + Math.cos(a) * ring * 6), y = Math.round(ctr[1] + Math.sin(a) * ring * 5);
          const box = holeRect({ x, y, w, h });
          if (rectPoly(box).some(([px, py]) => depth(poly, px, py) < 2)) continue;
          if (reserve.some((q) => overlaps(box, q))) continue;
          return { x, y, w, h };
        }
      }
    }
    return null;
  }

  // =====================================================================================
  // Colour and the page's theme
  // =====================================================================================
  const rgbOf = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const hexOf = (rgb) => "#" + rgb.map((v) => clamp(Math.round(v), 0, 255).toString(16).padStart(2, "0")).join("");
  const mix = (a, b, u) => { const x = rgbOf(a), y = rgbOf(b); return hexOf(x.map((v, i) => v + (y[i] - v) * u)); };
  const rgba = (hex, alpha) => { const [r, g, b] = rgbOf(hex); return `rgba(${r}, ${g}, ${b}, ${alpha})`; };
  const isHex = (v) => /^#[0-9a-f]{6}$/i.test(v);
  const pack = (hex) => { const [r, g, b] = rgbOf(hex); return ((255 << 24) | (b << 16) | (g << 8) | r) >>> 0; };
  function themeDark() {
    const t = document.documentElement.getAttribute("data-theme");
    if (t === "dark" || t === "light") return t === "dark";
    return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;
  }
  // the land keeps its own colours; the sea, the night, the fog and the panels come from the page's tokens, so the
  // map is a whiteprint chart by day, a navy one by night, and violet in Innerlatch
  function readTheme() {
    const dark = themeDark(), css = getComputedStyle(document.documentElement);
    const tok = (name, fallback) => { const v = css.getPropertyValue(name).trim(); return isHex(v) ? v.toLowerCase() : fallback; };
    const page = tok("--page", dark ? "#071729" : "#eef3f9"), surface = tok("--surface", dark ? "#0e2c51" : "#ffffff");
    const surface2 = tok("--surface-2", dark ? "#10345e" : "#e9eff7"), ink = tok("--ink", dark ? "#f3f8ff" : "#071629");
    const muted = tok("--muted", dark ? "#9bb8da" : "#455f7e"), accent = tok("--accent", dark ? "#8ccfff" : "#164c80");
    const chart = tok("--chart", dark ? "#0695f9" : "#0577cc"), baseline = tok("--baseline", dark ? "#4a74a6" : "#7f9cc0");
    const rim = tok("--border-strong", dark ? "#4573a8" : "#8ea8c8"), cta = tok("--cta", "#ffb45e");
    const gold = dark ? tok("--gold", "#ffb45e") : tok("--gold", "#8a4f06");
    const hue = mix(accent, chart, 0.5);
    const sea = dark
      ? { deep: mix(page, surface, 0.3), mid: mix(page, surface, 0.62), shallow: mix(surface, hue, 0.24), foam: mix(surface, accent, 0.62) }
      : { deep: mix(surface2, hue, 0.42), mid: mix(surface2, hue, 0.32), shallow: mix(surface, hue, 0.2), foam: mix(surface, "#ffffff", 0.85) };
    sea.grid = mix(sea.deep, dark ? baseline : "#ffffff", dark ? 0.32 : 0.3);
    sea.wave = mix(sea.deep, sea.foam, dark ? 0.4 : 0.45);
    sea.glint = dark ? mix(accent, "#ffffff", 0.4) : "#ffffff";
    return { dark, page, surface, surface2, ink, muted, accent, gold, cta, rim, baseline, sea,
      water: dark ? mix(surface, hue, 0.4) : mix(surface2, hue, 0.5),
      waterDeep: dark ? mix(mix(surface, hue, 0.4), page, 0.35) : mix(surface2, hue, 0.62),
      tint: dark ? mix(surface, "#ffffff", 0.56) : mix(surface2, "#ffffff", 0.45), // the land is multiplied by it
      fog: dark ? { base: mix(page, surface, 0.6), hi: mix(surface, baseline, 0.55) } : { base: mix(surface2, "#ffffff", 0.3), hi: "#ffffff" },
      panel: dark ? rgba(mix(page, surface, 0.5), 0.92) : "rgba(255, 255, 255, 0.94)",
      outline: dark ? "rgba(4, 8, 22, 0.94)" : "rgba(255, 255, 255, 0.96)",
      shadow: dark ? "rgba(0, 0, 0, 0.5)" : "rgba(26, 30, 60, 0.28)" };
  }

  // =====================================================================================
  // Biomes: one per attribute (scene.js's zone hues, for the banners and roofs), each with its ground, its border
  // (what runs between its territories), its props and a landmark
  // =====================================================================================
  const BIOMES = {
    craft: { hue: "#3a7be0", ground: "#a7c97d", flower: "#ffd66b", border: "lane", spacing: 13, landmark: "forge",
      deco: [["tree", 4], ["house", 4], ["bush", 2], ["field", 1], ["pine", 1], ["berry", 1]] },
    data: { hue: "#1fae96", ground: "#8fcd98", flower: "#ffffff", border: "canal", spacing: 14, landmark: "lighthouse", lakes: 7,
      deco: [["willow", 4], ["bush", 3], ["tree", 2], ["rock", 1], ["berry", 1]] },
    ops: { hue: "#8a96a8", ground: "#b5ad9a", flower: null, border: "ridge", spacing: 14, landmark: "datacenter", lakes: 1,
      deco: [["mountain", 3], ["peak", 4], ["pine", 3], ["tower", 1], ["rock", 2]] },
    guard: { hue: "#d0605a", ground: "#cfae80", flower: "#e8584f", border: "wall", spacing: 15, landmark: "keep", lakes: 1,
      deco: [["turret", 1], ["rock", 3], ["bush", 2], ["deadtree", 2], ["pine", 2], ["tent", 1]] },
    arcana: { hue: "#9a6ae0", ground: "#b9a7dc", flower: "#ffc8e8", border: "ley", spacing: 13, landmark: "wizard", lakes: 2,
      deco: [["crystal", 4], ["magictree", 3], ["mushroom", 2], ["stone", 1]] },
    lore: { hue: "#e8a040", ground: "#93ae6e", flower: "#f29a3a", border: "hedge", spacing: 10, landmark: "library", lakes: 1,
      deco: [["autumn", 5], ["tree", 3], ["pine", 2], ["stone", 1], ["bush", 1]] },
  };
  const BIOME_ORDER = ["craft", "data", "ops", "guard", "arcana", "lore"];
  const biomeOf = (region) => BIOMES[region.id] || BIOMES[BIOME_ORDER[region.index % BIOME_ORDER.length]];

  // props, in the Cast's style (Sprites.art shades and edges them): rows of letters, a palette; `glow` letters are lit
  // windows (re-lit at night); smoke/blink/sparkle/beam: where a prop's animation sits
  const DECO = {
    tree: { rows: ["..lll..", ".lllll.", "lllllll", "lllllll", "lllllll", ".lllll.", "..ltl..", "...t...", "...t..."],
      pal: { l: "#4f9a4c", t: "#7a5232" } },
    willow: { rows: ["..llll..", ".llllll.", "llllllll", "llllllll", "llllllll", "l.llll.l", "l..tt..l", "...tt..."],
      pal: { l: "#5fae7a", t: "#7a5232" } },
    pine: { rows: ["...p...", "..ppp..", ".ppppp.", "..ppp..", ".ppppp.", "ppppppp", "..ppp..", ".ppppp.", "ppppppp", "...t...", "...t..."],
      pal: { p: "#2f7552", t: "#6a4428" } },
    deadtree: { rows: ["t...t.", ".t.t..", "..t..t", "..ttt.", "..t...", "..t...", ".ttt.."], pal: { t: "#7d6553" } },
    bush: { rows: [".bbb.", "bbbbb", "bbbbb"], pal: { b: "#5c9c52" } },
    berry: { rows: [".bbb.", "bbrbb", "brbbb"], pal: { b: "#5c9c52", r: "=#e8584f" } },
    rock: { rows: [".rr..", "rrrr.", "rrrrr"], pal: { r: "#9b968d" } },
    stone: { rows: [".ss.", "ssss", "ssss", "ssss", "ssss"], pal: { s: "#a7a4b2" } },
    mushroom: { rows: [".rrr.", "rrwrr", "..s..", "..s.."], pal: { r: "*#e05a8a", w: "=#ffffff", s: "#efe6d6" } },
    crystal: { rows: ["...c...", "..cCc..", "..cCc..", "c.cCc..", "cCcCc.c", "cCcCccC", ".cccccc"], pal: { c: "*#79d6f0", C: "*#c8f6ff" },
      sparkle: [4, 1] },
    house: { rows: ["......c..", "...RRRc..", "..RRRRR..", ".RRRRRRR.", "RRRRRRRRR", ".wwwwwww.", ".wywwwyw.", ".wwwdwww.", ".wwwdwww."],
      pal: { R: "#c0563a", c: "#6a6670", w: "#ecdfc6", y: "=#ffd66b", d: "#7a5232" }, glow: "y", smoke: [7, 0] },
    field: { rows: ["cdcdcdcdcdc", "cdcdcdcdcdc", "cdcdcdcdcdc", "cdcdcdcdcdc", "cdcdcdcdcdc"], pal: { c: "=#d8c46a", d: "=#a77d4b" }, flat: true },
    tent: { rows: ["...h...", "..www..", ".wwhww.", "wwwhwww", "wwhhhww"], pal: { w: "#efe3c8", h: "#d0605a" } },
    turret: { rows: ["...pff.", "...pff.", "...p...", "s.s.s.s", "sssssss", ".sssss.", ".sskss.", ".sssss.", ".sssss.", ".sskss.", "sssssss"],
      pal: { p: "#c9c2b4", f: "#d0605a", s: "#aaa497", k: "=#3a3340" } },
    tower: { rows: ["...a...", "...a...", "..nnn..", ".nnnnn.", ".ngnbn.", ".nnnnn.", ".nbngn.", ".nnnnn.", ".ngnbn.", ".nnnnn.", ".nbngn.", ".nnnnn.", "nnnnnnn"],
      pal: { a: "#c9d2de", n: "#3d5280", g: "=#7fd99a", b: "=#6cb6ff" }, glow: "gb", blink: [4, 1] },
    mountain: { rows: ["........sss........", ".......sssss.......", "......sssrsss......", ".....ssrrrrrss.....", "....rrrrrrrrrrr....",
      "...rrrrrrrrrrrrr...", "..rrrrrrrrrrrrrrr..", ".rrrrrrrrrrrrrrrrr.", "rrrrrrrrrrrrrrrrrrr"], pal: { r: "#8e8a84", s: "*#f2f5fa" } },
    peak: { rows: ["....s....", "...sss...", "..srrrs..", ".rrrrrrr.", "rrrrrrrrr"], pal: { r: "#958f86", s: "*#f2f5fa" } },
    reed: { rows: ["r.r", "r.r", "rrr"], pal: { r: "=#4f7a3a" }, flat: true },
    forge: { rows: ["............cc...", "............cc...", "....RRRRRRR.cc...", "...RRRRRRRRRcc...", "..RRRRRRRRRRRRR..", ".RRRRRRRRRRRRRRR.",
      "RRRRRRRRRRRRRRRRR", ".wwwwwwwwwwwwwww.", ".wyywwwwwwwwwyyw.", ".wyywwoooowwwyyw.", ".wwwwwoooowwwwww.", ".wwwwwoooowwwwww."],
      pal: { R: "#3a6fc8", c: "#6a6670", w: "#d9c8a8", y: "=#ffd66b", o: "=#ff8a3c" }, glow: "yo", smoke: [13, 0] },
    lighthouse: { rows: ["...yyy...", "..kyyyk..", "..kkkkk..", "...www...", "...rrr...", "...www...", "..rrrrr..", "..wwwww..", "..rrrrr..",
      ".wwwwwww.", ".rrrrrrr.", "sssssssss"], pal: { y: "=#fff1a0", k: "#3a3f4a", w: "*#f4f7fb", r: "#d0605a", s: "#9b968d" }, glow: "y", beam: [5, 2] },
    datacenter: { rows: ["..a.......a....", "..a.......a....", ".aaa.....aaa...", "nnnnnnnnnnnnnnn", "nbgnnbgnnbgnnbg", "nnnnnnnnnnnnnnn",
      "ngbnngbnngbnngb", "nnnnnnnnnnnnnnn", "nbgnnbgnnbgnnbg", "nnnnnnnnnnnnnnn", "nnnnnnddnnnnnnn", "nnnnnnddnnnnnnn"],
      pal: { a: "#c9d2de", n: "#3d5280", g: "=#7fd99a", b: "=#6cb6ff", d: "=#ffd66b" }, glow: "gbd", blink: [3, 1] },
    keep: { rows: ["s.s.s....pff..s.s.s", "sssss....pff..sssss", "sssss....p....sssss", "syss.s.s.s.s.s.sys", "sssssssssssssssssss",
      "sssssssssssssssssss", "syssssssssssssssyss", "ssssssssdddssssssss", "sssssssdddddsssssss", "sssssssdddddsssssss", "sssssssdddddsssssss"],
      pal: { s: "#aca698", y: "=#ffd66b", d: "#5a4632", p: "#c9c2b4", f: "#d0605a" }, glow: "y" },
    wizard: { rows: ["....Y....", "....v....", "...vvv...", "...vvv...", "..vvvvv..", "..vvvvv..", ".vvvvvvv.", "vvvvvvvvv", ".sssssss.",
      ".ssyssss.", ".sssssss.", ".sssssys.", ".sssssss.", ".ssyssss.", ".sssssss.", "sssssssss", "sssdddsss", "sssdddsss"],
      pal: { Y: "=#ffe27a", v: "*#7a4ad0", s: "#bcb4c8", y: "=#ffd66b", d: "#5a4632" }, glow: "yY", sparkle: [5, 1] },
    library: { rows: ["..........R..........", "........RRRRR........", "......RRRRRRRRR......", "....RRRRRRRRRRRRR....", "..RRRRRRRRRRRRRRRRR..",
      "wwwwwwwwwwwwwwwwwwwww", ".ckkckkckyyykckkckkc.", ".ckkckkckyyykckkckkc.", ".ckkckkckyyykckkckkc.", ".ckkckkckyyykckkckkc.",
      "wwwwwwwwwwwwwwwwwwwww", "bbbbbbbbbbbbbbbbbbbbb"],
      pal: { R: "#8e5e3c", w: "#eee4cf", c: "*#f6f1e6", k: "=#4a3a5a", y: "=#ffd66b", b: "#c9bfa8" }, glow: "y" },
    arch: { rows: ["sssssssssssssssssss", "sssssssssssssssssss", "sss.hhh.....hhh.sss", "sss.hhh.....hhh.sss", "sss.hhh.....hhh.sss",
      "sss..h.......h..sss", "sss.............sss", "sss.............sss", "sss.............sss", "sss.............sss",
      "sss.............sss", "sss.............sss", "sssss.........sssss"], pal: { s: "#b3ad9f", h: "#3a7be0" } },
    ship: { rows: ["....f....", "....m....", "...wmw...", "..wwmww..", ".wwwmwww.", "....m....", "hhhhhhhhh", ".hhhhhhh."],
      pal: { f: "=#e5484d", m: "#6a4428", w: "*#f4efe4", h: "#8a5a3a" } },
    flag: { rows: ["pff...", "pfff..", "pffff.", "pff...", "p.....", "p.....", "p.....", "bb...."], pal: { p: "#d5d0c4", f: "#3a7be0", b: "#6b5a4a" } },
  };
  // a prop's look in one biome: roofs, leaves and banners take the region's colours
  function decoVariant(type, bio, v) {
    const base = DECO[type === "autumn" || type === "magictree" ? "tree" : type];
    const pal = { ...base.pal };
    if (type === "autumn") pal.l = ["#d98a32", "#c8602e", "#e2b040"][v % 3];
    if (type === "magictree") pal.l = ["#9a7ae0", "#7fc8e0", "#c88ae0"][v % 3];
    if (type === "house") pal.R = [bio.hue, "#c0563a", "#9a6a3a"][v % 3];
    if (type === "crystal" && v % 2) { pal.c = "*#f08ad0"; pal.C = "*#ffd0ee"; }
    if (type === "arch" || type === "flag") pal[type === "arch" ? "h" : "f"] = bio.hue;
    if (type === "turret" || type === "keep" || type === "tent") pal[type === "tent" ? "h" : "f"] = bio.hue;
    if (type === "pine" && bio === BIOMES.lore) pal.p = "#2d5e40";
    return { ...base, pal };
  }
  // a pixel map as a canvas, in the Cast's style when Sprites is there, flat otherwise
  // w, h: its size in world pixels (k world pixels an art pixel); pad: the edge Sprites.art adds, in art pixels
  function paintArt(rows, pal, flat, k = 1) {
    const S = root.Sprites;
    if (!flat && S && typeof S.art === "function") {
      try { const a = S.art([[0, 0, rows]], pal, k); return { cv: a.cv, w: a.w * k, h: a.h * k, pad: 1, k }; } catch (e) { /* flat, below */ }
    }
    const w = Math.max(...rows.map((r) => r.length)), h = rows.length, cv = blank(w * k, h * k), g = cv.getContext("2d");
    rows.forEach((row, y) => {
      for (let x = 0; x < row.length; x++) {
        const c = pal[row[x]];
        if (!c) continue;
        g.fillStyle = c.replace(/^[*=]/, "");
        g.fillRect(x * k, y * k, k, k);
      }
    });
    return { cv, w: w * k, h: h * k, pad: 0, k };
  }
  function blank(w, h) {
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.ceil(w));
    c.height = Math.max(1, Math.ceil(h));
    return c;
  }

  // =====================================================================================
  // Painting the land, once per theme: sea, coast, ground, rivers, lakes, borders, roads, props, night, fog
  // =====================================================================================
  const SEA_BIT = 1, WATER = 2, ROAD = 4, BORDER = 8, TAKEN = 16, ENC = 32, KEEP = 64;
  const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5];

  // A generator, so the page stays responsive: the caller runs it in short slices (yields come every few rows). With
  // q > 1 it paints a quick preview, one pixel every q world pixels, without roads, props or lakes.
  function* paintSteps(L, theme, seed, q = 1) {
    const W = L.width, H = L.height, full = q === 1, Wq = Math.ceil(W / q), Hq = Math.ceil(H / q), N = Wq * Hq;
    const cv = blank(Wq, Hq), g = cv.getContext("2d");
    const img = g.createImageData(Wq, Hq), buf = new Uint32Array(img.data.buffer);
    const terr = new Int16Array(N).fill(-1), bd = new Uint8Array(N), occ = new Uint8Array(N);
    const r = rng(mixInt(seed, 0xa11));
    const anims = [], glints = [], ships = [];
    const stamp = (x, y, w, h, bit) => {
      const x0 = Math.max(0, Math.floor(x)), y0 = Math.max(0, Math.floor(y)), x1 = Math.min(W, Math.ceil(x + w)), y1 = Math.min(H, Math.ceil(y + h));
      for (let yy = y0; yy < y1; yy++) for (let xx = x0; xx < x1; xx++) occ[yy * W + xx] |= bit;
    };
    const free = (x, y, w, h, bits) => {
      const x0 = Math.floor(x), y0 = Math.floor(y), x1 = Math.ceil(x + w), y1 = Math.ceil(y + h);
      if (x0 < 0 || y0 < 0 || x1 > W || y1 > H) return false;
      for (let yy = y0; yy < y1; yy++) for (let xx = x0; xx < x1; xx++) if (occ[yy * W + xx] & bits) return false;
      return true;
    };
    // what already stands on the land: the encounters, the gates and banners, the clearings
    if (full) {
      for (const e of L.lessons) stamp(e.x - 10, e.y - 18, 20, 21, ENC);
      for (const reg of L.regions) for (const b of reg.reserve) stamp(b.x, b.y, b.w, b.h, KEEP);
      for (const t of L.territories) if (t.hole) { const b = holeRect(t.hole); stamp(b.x, b.y, b.w, b.h, KEEP); }
      if (L.party) stamp(L.party.x - 30, L.party.y - 46, 60, 48, KEEP);
    }

    // ---- per region: palettes as packed pixels, sites for the territories
    const regs = L.regions.map((reg) => {
      const bio = biomeOf(reg), base = mix(bio.ground, bio.hue, 0.1);
      const terrs = L.territories.filter((t) => t.region === reg.index), cells = terrs.concat(reg.plazas || []);
      const n = cells.length, sx = new Float64Array(n), sy = new Float64Array(n), sw = new Float64Array(n), inv = new Float64Array(n * n);
      cells.forEach((t, i) => { sx[i] = t.site[0]; sy[i] = t.site[1]; sw[i] = t.weight; });
      for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) inv[i * n + j] = i === j ? 0 : 1 / (2 * Math.hypot(sx[i] - sx[j], sy[i] - sy[j]) || 1);
      return { reg, bio, terrs, cells, n, sx, sy, sw, inv,
        tones: [mix(base, "#1a2418", 0.26), mix(base, "#1a2418", 0.12), base, mix(base, "#ffffff", 0.13)].map(pack),
        // each territory a shade of its own, so the provinces read apart
        shades: cells.map((c, i) => {
          const u = hash01(i, reg.index, 31), b = u < 0.5 ? mix(base, "#1a2418", 0.1 * (0.5 - u) * 2 + 0.02) : mix(base, "#ffffff", 0.14 * (u - 0.5) * 2 + 0.02);
          return [mix(b, "#1a2418", 0.26), mix(b, "#1a2418", 0.12), b, mix(b, "#ffffff", 0.13)].map(pack);
        }),
        flower: bio.flower ? pack(bio.flower) : 0, speck: pack(mix(base, "#14200f", 0.36)) };
    });
    const C = {
      sand: pack("#e6d6a4"), sandDark: pack("#cdb983"), bank: pack("#d3c08e"),
      water: pack(theme.water), waterDeep: pack(theme.waterDeep), waterHi: pack(mix(theme.water, "#ffffff", 0.35)),
      deep: pack(theme.sea.deep), mid: pack(theme.sea.mid), shallow: pack(theme.sea.shallow), foam: pack(theme.sea.foam),
      grid: pack(theme.sea.grid), wave: pack(theme.sea.wave),
      cobble: pack("#c4baa6"), cobbleDark: pack("#a1977f"), rock: [pack("#5f5a54"), pack("#77726b"), pack("#8f8a82"), pack("#d6d1c6")],
      stone: pack("#b8b1a2"), stoneTop: pack("#d0c9ba"), mortar: pack("#878073"), wallShade: pack("#6f685c"),
      ley: pack("#efe2ff"), leyGlow: pack("#c9a6ff"), hedge: [pack("#2f5528"), pack("#3d6b34"), pack("#4f8240")],
    };

    // ---- night (the land multiplied by the page's paper) and the fog of war, in one pass over the pixels
    function* finish(g) {
      const out = g.getImageData(0, 0, Wq, Hq), px = new Uint32Array(out.data.buffer);
      const [tr, tg, tb] = rgbOf(theme.tint), [fr, fg, fb] = rgbOf(theme.fog.base), [hr, hg, hb] = rgbOf(theme.fog.hi);
      const fogOf = new Uint8Array(L.territories.length);
      L.territories.forEach((t) => { fogOf[t.index] = t.fog ? 1 : 0; });
      // a region with nothing explored yet: its plazas lie under the fog too
      const plazaFog = L.regions.map((reg) => L.territories.filter((t) => t.region === reg.index).every((t) => t.fog));
      const cloud = noise(mixInt(seed, 9), 22), puff = noise(mixInt(seed, 10), 8);
      for (let yq = 0; yq < Hq; yq++) {
        if (full && yq % 40 === 39) yield;
        const y = yq * q;
        for (let xq = 0, i = yq * Wq; xq < Wq; xq++, i++) {
          const o = occ[i];
          if (o & SEA_BIT) continue;
          const v = px[i];
          let R = v & 255, G = (v >>> 8) & 255, B = (v >>> 16) & 255;
          if (!(o & WATER) || (o & ROAD)) { R = (R * tr) / 255; G = (G * tg) / 255; B = (B * tb) / 255; }
          const ti = terr[i];
          if (ti >= 0 ? fogOf[ti] : ti <= -2 && plazaFog[-2 - ti]) {
            const x = xq * q, cl = cloud(x, y) * 0.75 + puff(x, y) * 0.35;
            const edge = Math.min(1, bd[i] / 8 / 9), dither = (BAYER[(y & 3) * 4 + (x & 3)] / 16 - 0.5) * 0.16;
            const a = clamp(Math.round(((0.5 + 0.38 * edge) * (0.78 + 0.3 * cl) + dither) * 5) / 5, 0, 0.92);
            const hi = cl > 0.72 && edge > 0.5 ? 0.55 : 0;
            const cr = fr + (hr - fr) * hi, cg = fg + (hg - fg) * hi, cb = fb + (hb - fb) * hi;
            R += (cr - R) * a; G += (cg - G) * a; B += (cb - B) * a;
          }
          px[i] = ((255 << 24) | (Math.round(B) << 16) | (Math.round(G) << 8) | Math.round(R)) >>> 0;
        }
      }
      g.putImageData(out, 0, 0);
      return fogOf;
    }

    // ---- the coast: the rows' rectangles, grown outward by a noisy margin, and a few islands off shore
    const coastA = noise(mixInt(seed, 3), 52), coastB = noise(mixInt(seed, 4), 13), patch = noise(mixInt(seed, 5), 18);
    const sdRect = (x, y, R) => {
      const dx = Math.max(R.x - x, x - (R.x + R.w)), dy = Math.max(R.y - y, y - (R.y + R.h));
      return dx > 0 || dy > 0 ? Math.hypot(Math.max(dx, 0), Math.max(dy, 0)) : Math.max(dx, dy);
    };
    const islands = [];
    for (let k = 0; k < 7; k++) {
      const side = k % 4, R = L.land[side < 2 ? 0 : L.land.length - 1];
      const along = 0.1 + r() * 0.8, out = 22 + r() * 34, rad = 6 + r() * 11;
      const x = side === 0 || side === 2 ? R.x + R.w * along : side === 1 ? R.x - out : R.x + R.w + out;
      const y = side === 0 ? R.y - out : side === 2 ? R.y + R.h + out : R.y + R.h * along;
      if (x - rad > 4 && y - rad > 4 && x + rad < W - 4 && y + rad < H - 4) islands.push({ x, y, rad });
    }
    // ---- lakes, where no encounter stands
    const lakes = [];
    for (const R of full ? regs : []) {
      const want = R.bio.lakes || 0, rect = R.reg.rect;
      for (let k = 0, made = 0; k < want * 30 && made < want; k++) {
        const big = R.bio === BIOMES.data;
        const rx = big ? 9 + r() * 20 : 6 + r() * 6, ry = rx * (0.55 + r() * 0.3);
        const x = rect.x + 12 + rx + r() * (rect.w - 24 - 2 * rx), y = rect.y + 12 + ry + r() * (rect.h - 24 - 2 * ry);
        if (!free(x - rx - 5, y - ry - 5, 2 * rx + 10, 2 * ry + 10, ENC | KEEP | WATER)) continue;
        lakes.push({ x, y, rx, ry, s: k });
        stamp(x - rx - 2, y - ry - 2, 2 * rx + 4, 2 * ry + 4, WATER);
        made++;
      }
    }
    for (let i = 0; i < N; i++) occ[i] &= ~WATER; // the lakes' marks were for spacing; the pixels mark the water below

    const rows = L.land, two = rows.length > 1, boundary = L.boundary;
    const rowRegs = rows.map((row, k) => regs.filter((R) => R.reg.row === k).sort((a, b) => a.reg.col - b.reg.col));
    const s1 = r() * 6.28, s2 = r() * 6.28, s3 = r() * 6.28, s4 = r() * 6.28;
    const wobbleX = (x, y) => x + 1.7 * Math.sin(y * 0.13 + s1) + 1.0 * Math.sin((x + y) * 0.21 + s2);
    const wobbleY = (x, y) => y + 1.7 * Math.sin(x * 0.12 + s3) + 1.0 * Math.sin((x - y) * 0.19 + s4);

    for (let yq = 0; yq < Hq; yq++) {
      if (full && yq % 6 === 5) yield;
      const y = yq * q;
      for (let xq = 0; xq < Wq; xq++) {
        const x = xq * q, i = yq * Wq + xq;
        // ---- land or sea
        let sd = Infinity;
        for (const R of rows) sd = Math.min(sd, sdRect(x, y, R));
        let c = sd < -8 ? 99 : -99;
        if (sd >= -8 && sd < 48) {
          const a = coastA(x, y), amp = 4 + 34 * a * a + 6 * coastB(x, y);
          c = amp - sd;
        }
        for (const isl of islands) {
          const dx = x - isl.x, dy = y - isl.y;
          if (Math.abs(dx) > isl.rad + 14 || Math.abs(dy) > isl.rad + 14) continue;
          c = Math.max(c, isl.rad * (0.7 + 0.5 * coastB(x * 1.7, y * 1.7)) - Math.hypot(dx, dy));
        }
        if (c < 0) {
          const off = -c, b = BAYER[(y & 3) * 4 + (x & 3)] / 16;
          let col = C.deep;
          if (off < 1.4) col = C.foam;
          else if (off < 5 + 2 * b) col = C.shallow;
          else if (off < 16 + 6 * b) col = off < 11 + 6 * b ? C.mid : (b > 0.5 ? C.mid : C.deep);
          else if ((x % 48 === 0 && (y & 1)) || (y % 48 === 0 && (x & 1))) col = C.grid;
          else if ((y & 3) === 0 && hash01(x >> 3, y >> 2, 77) < 0.05 && (x & 7) < 3) col = C.wave;
          buf[i] = col;
          occ[i] |= SEA_BIT;
          continue;
        }
        // ---- which region and territory (the borders wobble a little; encounters keep well clear of them)
        const wx = wobbleX(x, y), wy = wobbleY(x, y);
        const k = two && wy >= boundary ? 1 : 0, row = rows[k], list = rowRegs[k];
        const px = clamp(wx, row.x + 0.01, row.x + row.w - 0.01), py = clamp(wy, row.y + 0.01, row.y + row.h - 0.01);
        let ci = 0;
        while (ci < list.length - 1 && px >= list[ci].reg.rect.x + list[ci].reg.rect.w) ci++;
        const R = list[ci], rect = R.reg.rect;
        const dCol = Math.min(ci > 0 ? px - rect.x : Infinity, ci < list.length - 1 ? rect.x + rect.w - px : Infinity);
        let best = 0, bp = Infinity;
        for (let j = 0; j < R.n; j++) {
          const p = (px - R.sx[j]) ** 2 + (py - R.sy[j]) ** 2 - R.sw[j];
          if (p < bp) { bp = p; best = j; }
        }
        let dT = Infinity;
        for (let j = 0; j < R.n; j++) {
          if (j === best) continue;
          const d = ((px - R.sx[j]) ** 2 + (py - R.sy[j]) ** 2 - R.sw[j] - bp) * R.inv[best * R.n + j];
          if (d < dT) dT = d;
        }
        const t = R.terrs[best], plaza = t ? null : R.cells[best].kind;
        terr[i] = t ? t.index : -2 - R.reg.index; // a plaza: -2 - its region
        bd[i] = Math.min(255, Math.round(Math.min(dT, dCol) * 8));
        // ---- ground, then what lies on it
        const v = patch(x, y) + (hash01(x, y, 11) - 0.5) * 0.34, hh = hash01(x, y, 12);
        const tones = R.shades[best] || R.tones;
        let col = tones[v < 0.3 ? 1 : v > 0.74 ? 3 : 2];
        if (hh < 0.008) col = R.speck;
        else if (hh < 0.012 && R.flower) col = R.flower;
        else if (hh < 0.03 && v < 0.3) col = tones[0];
        const riverW = 2 + 0.9 * Math.sin(y * 0.05 + x * 0.01 + s2);
        let water = false;
        if (dCol < riverW) { col = dCol < riverW * 0.45 ? C.waterDeep : C.water; water = true; }
        else if (dCol < riverW + 1.3) col = C.bank;
        else if (c < 3.4) col = c < 1.1 ? C.sandDark : C.sand;
        else {
          for (const lk of lakes) {
            const dx = (x - lk.x) / lk.rx, dy = (y - lk.y) / lk.ry;
            if (Math.abs(dx) > 1.4 || Math.abs(dy) > 1.4) continue;
            const e = (dx * dx + dy * dy) * (1 + 0.22 * (coastB(x * 2 + lk.s * 50, y * 2) - 0.5));
            if (e < 1) { col = e < 0.38 ? C.waterDeep : C.water; water = true; }
            else if (e < 1.28) col = e < 1.12 ? C.bank : col;
            break;
          }
        }
        if (!water && plaza === "gate" && dT > 1.2 && c >= 3.4) col = (x + (y & 1) * 2) % 4 === 0 || y % 3 === 0 ? C.cobbleDark : C.cobble;
        if (!water && dT < 3.2 && dCol > 3) {
          const style = R.bio.border;
          if (style === "lane" && dT < 2.1) col = dT > 1.4 ? C.cobbleDark : (x + (y & 1) * 2) % 3 === 0 ? C.cobbleDark : C.cobble;
          else if (style === "canal" && dT < 1.6) { col = dT < 0.6 ? C.waterDeep : C.water; water = true; }
          else if (style === "canal" && dT < 2.5) col = C.bank;
          else if (style === "ridge" && dT < 2.8) col = dT < 0.9 ? C.rock[3] : dT > 2 ? C.rock[0] : C.rock[(hash01(x, y, 13) * 2) | 0];
          else if (style === "wall" && dT < 2.3) col = dT < 0.7 ? C.stoneTop : y % 3 === 0 || (x + ((y / 3) | 0) * 2) % 5 === 0 ? C.mortar : C.stone;
          else if (style === "wall" && dT < 3.1) col = C.wallShade;
          else if (style === "ley" && dT < 0.8) col = C.ley;
          else if (style === "ley" && dT < 1.9 && (x + y) % 2) col = C.leyGlow;
          else if (style === "hedge" && dT < 2.4) col = C.hedge[(hash01(x >> 1, y >> 1, 14) * 3) | 0];
          if (dT < 3.2) occ[i] |= BORDER;
        }
        if (water) occ[i] |= WATER;
        buf[i] = col;
      }
    }

    if (!full) { // the preview: just the land, lit and fogged
      g.putImageData(img, 0, 0);
      yield* finish(g);
      return { canvas: cv, k: 1 / q, anims: [], glints: [], ships: [] };
    }
    // ---- roads: the highway between the rows, a trail from each gate to its explored territories; bridges over water
    const roadCols = { fill: pack("#c8a46a"), light: pack("#d6b880"), edge: pack("#9a7a4c"), plank: pack("#a8794a"), plank2: pack("#8a5e36"), rail: pack("#5e4028") };
    function road(points, r0, r1, seedK) {
      const samples = [];
      for (let k = 0; k + 1 < points.length; k++) {
        const [ax, ay] = points[k], [bx, by] = points[k + 1], len = Math.hypot(bx - ax, by - ay), n = Math.max(1, Math.ceil(len * 2));
        const nx = -(by - ay) / (len || 1), ny = (bx - ax) / (len || 1);
        for (let s = 0; s <= n; s++) {
          const u = s / n, wob = 1.4 * Math.sin((k * 13 + u * len) * 0.09 + seedK) * Math.sin(Math.PI * u);
          samples.push([ax + (bx - ax) * u + nx * wob, ay + (by - ay) * u + ny * wob]);
        }
      }
      for (const pass of [0, 1]) {
        const rr = pass ? r0 : r1, lim = Math.ceil(rr);
        for (const [sx, sy] of samples) {
          for (let dy = -lim; dy <= lim; dy++) for (let dx = -lim; dx <= lim; dx++) {
            const x = Math.round(sx) + dx, y = Math.round(sy) + dy;
            if (x < 0 || y < 0 || x >= W || y >= H || dx * dx + dy * dy > rr * rr) continue;
            const i = y * W + x;
            if (occ[i] & SEA_BIT) continue;
            if (!pass) { if (!(occ[i] & ROAD)) buf[i] = occ[i] & WATER ? roadCols.rail : roadCols.edge; continue; }
            occ[i] |= ROAD;
            buf[i] = occ[i] & WATER ? ((x + y) & 1 ? roadCols.plank : roadCols.plank2) : hash01(x, y, 15) < 0.18 ? roadCols.light : roadCols.fill;
          }
        }
      }
    }
    if (two) {
      const left = Math.min(...rows.map((R) => R.x)) + 6, right = Math.max(...rows.map((R) => R.x + R.w)) - 6;
      road([[left, boundary], [right, boundary]], 1.6, 2.7, 1);
    }
    for (const reg of L.regions) {
      const a = reg.arch, edge = reg.gate.y;
      if (two) road([[a.x, edge], [a.x, a.y]], 1.6, 2.7, 2);
      // trails: the shortest network joining the gate to every explored territory's clearing (Prim's tree)
      const nodes = [[a.x, a.y + (reg.row === 1 && two ? -2 : -12)]].concat(L.territories
        .filter((t) => t.region === reg.index && t.hole).map((t) => [t.hole.x, t.hole.y + t.hole.h / 2 - 2]));
      const inTree = [0], rest = nodes.map((nd, i) => i).slice(1);
      while (rest.length) {
        let bi = -1, bj = -1, bd2 = Infinity;
        for (const i of inTree) for (const j of rest) {
          const d2 = (nodes[i][0] - nodes[j][0]) ** 2 + (nodes[i][1] - nodes[j][1]) ** 2;
          if (d2 < bd2) { bd2 = d2; bi = i; bj = j; }
        }
        rest.splice(rest.indexOf(bj), 1);
        inTree.push(bj);
        const [sx, sy] = nodes[bi], [tx, ty] = nodes[bj], len = Math.sqrt(bd2);
        const bend = (hash01(bj, reg.index, 16) - 0.5) * 0.25 * len, nx = -(ty - sy) / (len || 1), ny = (tx - sx) / (len || 1);
        road([[sx, sy], [(sx + tx) / 2 + nx * bend, (sy + ty) / 2 + ny * bend], [tx, ty]], 0.9, 1.8, bj + 3);
      }
      yield;
    }
    g.putImageData(img, 0, 0);
    yield;

    // ---- props: the arches, a landmark per region, then the biome's props wherever nothing stands
    const props = [];
    const cache = new Map();
    const artFor = (type, bio, v, k = 1) => {
      const key = `${type}|${bio.hue}|${v}|${k}`;
      if (!cache.has(key)) { const d = decoVariant(type, bio, v); cache.set(key, { ...paintArt(d.rows, d.pal, d.flat, k), def: d }); }
      return cache.get(key);
    };
    const propBox = (a, x, y) => ({ x: x - Math.floor(a.w / 2), y: y - a.h + 1, w: a.w, h: a.h });
    const fits = (a, x, y, ti, strict) => {
      const b = propBox(a, x, y), foot = Math.max(3, Math.round(a.h * 0.42));
      if (!free(b.x, b.y, b.w, b.h, ENC | KEEP)) return false;
      if (!free(b.x + 1, y - foot + 1, b.w - 2, foot, SEA_BIT | WATER | ROAD | TAKEN | (strict ? BORDER : 0))) return false;
      for (const [cx, cy] of [[b.x + 1, y], [b.x + b.w - 2, y], [x, y]]) if (terr[cy * W + cx] !== ti) return false;
      return true;
    };
    const place = (a, x, y, type, bio, ti, pad = 1) => {
      const b = propBox(a, x, y), foot = Math.max(3, Math.round(a.h * 0.42));
      stamp(b.x - pad, y - foot + 1 - pad, b.w + 2 * pad, foot + 2 * pad, TAKEN);
      props.push({ a, x: b.x, y: b.y, foot: y, type, ti, bio });
    };
    for (const R of regs) { // the gate's arch stands on the road
      const a = artFor("arch", R.bio, 0), at = R.reg.arch;
      props.push({ a, x: at.x - Math.floor(a.w / 2), y: at.y - a.h + 1, foot: at.y, type: "arch", ti: -1, bio: R.bio });
    }
    for (const R of regs) { // the landmark: the roomiest spot, an explored territory first
      const a = artFor(R.bio.landmark, R.bio, 0, 2), rect = R.reg.rect, spots = []; // landmarks stand twice the size
      for (let y = rect.y + a.h + 4; y < rect.y + rect.h - 4; y += 5) for (let x = rect.x + a.w; x < rect.x + rect.w - a.w; x += 5) spots.push([x, y]);
      const mid = [rect.x + rect.w / 2, rect.y + rect.h / 2];
      spots.sort((p, q) => Math.hypot(p[0] - mid[0], p[1] - mid[1]) - Math.hypot(q[0] - mid[0], q[1] - mid[1]));
      let done = false;
      for (const fogOk of [false, true]) {
        for (const [x, y] of spots) {
          const ti = terr[y * W + x];
          if (ti < 0 || (!fogOk && L.territories[ti].fog) || !fits(a, x, y, ti, true)) continue;
          place(a, x, y, R.bio.landmark, R.bio, ti, 4);
          done = true;
          break;
        }
        if (done) break;
      }
    }
    for (const R of regs) { // reeds round the lakes
      for (const lk of lakes) {
        if (!inRect(R.reg.rect, lk.x, lk.y)) continue;
        for (let k = 0; k < 10; k++) {
          const ang = r() * Math.PI * 2, x = Math.round(lk.x + Math.cos(ang) * lk.rx * 1.15), y = Math.round(lk.y + Math.sin(ang) * lk.ry * 1.15) + 1;
          const a = artFor("reed", R.bio, 0), ti = terr[clamp(y, 0, H - 1) * W + clamp(x, 0, W - 1)];
          if (ti >= 0 && fits(a, x, y, ti, false)) place(a, x, y, "reed", R.bio, ti, 0);
        }
      }
    }
    for (const R of regs) { // the biome's props, scattered on a jittered grid
      yield;
      const rect = R.reg.rect, sp = R.bio.spacing, total = R.bio.deco.reduce((a, d) => a + d[1], 0);
      for (let gy = rect.y - 10; gy < rect.y + rect.h + 14; gy += sp) {
        for (let gx = rect.x - 10; gx < rect.x + rect.w + 10; gx += sp) {
          const x = Math.round(gx + (r() - 0.5) * sp * 0.9), y = Math.round(gy + (r() - 0.5) * sp * 0.9);
          if (x < 2 || y < 2 || x >= W - 2 || y >= H - 2) continue;
          const ti = terr[y * W + x];
          if (ti < 0 || L.territories[ti].region !== R.reg.index) continue;
          let pick = r() * total, type = R.bio.deco[0][0];
          for (const [name, wgt] of R.bio.deco) { pick -= wgt; if (pick <= 0) { type = name; break; } }
          const a = artFor(type, R.bio, (r() * 6) | 0);
          if (fits(a, x, y, ti, type !== "bush" && type !== "rock")) place(a, x, y, type, R.bio, ti, type === "mountain" ? 0 : 1);
        }
      }
    }
    for (const isl of islands) { // a tree or two on each island
      const bio = regs.length ? regs[0].bio : BIOMES.craft;
      for (let k = 0; k < 2; k++) {
        const a = artFor(k ? "bush" : "pine", bio, 0), x = Math.round(isl.x + (k ? 4 : -2)), y = Math.round(isl.y + (k ? 3 : 2));
        if (free(x - 3, y - 2, 6, 3, SEA_BIT | TAKEN)) place(a, x, y, "isle", bio, -1, 0);
      }
    }
    props.sort((a, b) => a.foot - b.foot || a.x - b.x);
    yield;
    let drawn = 0;
    for (const p of props) {
      if (++drawn % 150 === 0) yield;
      g.drawImage(p.a.cv, p.x, p.y);
      const d = p.a.def, k = p.a.k, at = (pt, dx = 0, dy = 0) => [p.x + (pt[0] + p.a.pad) * k + dx, p.y + (pt[1] + p.a.pad) * k + dy];
      const ph = hash01(p.x, p.y, 20) * 10;
      if (d.smoke) { const [x, y] = at(d.smoke); anims.push({ kind: "smoke", x, y, ph, ti: p.ti, k }); }
      if (d.blink) { const [x, y] = at(d.blink, -1, -1); anims.push({ kind: "blink", x, y, ph, ti: p.ti, k }); }
      if (d.sparkle) { const [x, y] = at(d.sparkle, -1, -1); anims.push({ kind: "sparkle", x, y, ph, ti: p.ti, k }); }
      if (d.beam) { const [x, y] = at(d.beam, -1, 0); anims.push({ kind: "beam", x, y, ph, ti: p.ti, k }); }
    }

    const fogOf = yield* finish(g);
    // at night the windows are lit again, over the dark
    if (theme.dark) {
      for (const p of props) {
        const d = p.a.def;
        if (!d.glow || (p.ti >= 0 && fogOf[p.ti])) continue;
        d.rows.forEach((row, y) => {
          for (let x = 0; x < row.length; x++) {
            if (!d.glow.includes(row[x])) continue;
            const k = p.a.k, c = d.pal[row[x]].replace(/^[*=]/, ""), X = p.x + (x + p.a.pad) * k, Y = p.y + (y + p.a.pad) * k;
            g.fillStyle = rgba(c, 0.22);
            g.fillRect(X - 1, Y - 1, k + 2, k + 2);
            g.fillStyle = c;
            g.fillRect(X, Y, k, k);
          }
        });
      }
    }
    // ---- what moves: glints on the water, ships off the coast
    for (let k = 0; k < (W * H) / 1500; k++) {
      const x = (r() * W) | 0, y = (r() * H) | 0, o = occ[y * W + x];
      if ((o & SEA_BIT) || ((o & WATER) && !(o & ROAD))) glints.push({ x, y, ph: r() * 10, lake: !(o & SEA_BIT) });
    }
    for (let k = 0, made = 0; k < 200 && made < 6; k++) {
      const x = 14 + r() * (W - 28), y = 14 + r() * (H - 28);
      if (!free(x - 14, y - 14, 28, 22, ~SEA_BIT & 255)) continue;
      ships.push({ x: Math.round(x), y: Math.round(y), ph: r() * 6, dir: r() < 0.5 ? -1 : 1 });
      made++;
    }
    return { canvas: cv, k: 1, anims, glints, ships };
  }
  // run a painter in slices of `budget` ms between the page's other work; returns a cancel function
  function runSlices(gen, done, budget = 10) {
    let live = true;
    const slice = () => {
      if (!live) return;
      const end = performance.now() + budget;
      for (;;) {
        const r = gen.next();
        if (r.done) { live = false; done(r.value); return; }
        if (performance.now() >= end) break;
      }
      setTimeout(slice, 0);
    };
    setTimeout(slice, 0);
    return () => { live = false; };
  }
  // drain a painter at once (the quick preview)
  function runNow(gen) { for (;;) { const r = gen.next(); if (r.done) return r.value; } }

  // =====================================================================================
  // Mounted maps share one animation loop
  // =====================================================================================
  const maps = new Set();
  let raf = 0;
  function schedule() {
    if (raf || typeof requestAnimationFrame !== "function") return;
    raf = requestAnimationFrame(tick);
  }
  function tick(now) {
    raf = 0;
    let again = false;
    for (const m of [...maps]) if (m.step(now)) again = true;
    if (again) schedule();
  }
  if (typeof document !== "undefined") document.addEventListener("visibilitychange", () => { for (const m of maps) m.wake(); schedule(); });

  const KIND_DOT = { fix: "#e5484d", gotcha: "#7a7f96", decision: "#4a8fd8", learning: "#9a7ae0", pattern: "#5d7f8a", command: "#d9a05b" };
  const STATUS = { open: "bossAsleep", evidence: "bossAwake", done: "bossDefeated" };

  function mountMap(host, opts = {}) {
    const given = opts.labels || {};
    const labels = { ...LABELS, ...given, regions: { ...(given.regions || {}) }, modules: { ...(given.modules || {}) } };
    const onHover = typeof opts.onHover === "function" ? opts.onHover : () => {};
    const onPick = typeof opts.onPick === "function" ? opts.onPick : () => {};
    const reduced = !!opts.reducedMotion;
    const seed = Number.isFinite(opts.seed) ? opts.seed : 1;
    const data = opts.data || {};
    const L = layout(data, seed);
    const lessonOf = new Map((Array.isArray(data.lessons) ? data.lessons : []).map((l) => [String(l.id), l]));
    const regionOf = new Map((Array.isArray(data.regions) ? data.regions : []).map((r) => [r.id, r]));
    const moduleOf = new Map();
    for (const r of regionOf.values()) for (const m of r.modules || []) moduleOf.set(m.id, m);
    const bossOf = new Map((Array.isArray(data.bosses) ? data.bosses : []).map((b) => [String(b.id), b]));
    const agents = (Array.isArray(data.agents) ? data.agents : []).slice()
      .sort((a, b) => (b.level || 0) - (a.level || 0) || AGENT_ORDER.indexOf(a.agent) - AGENT_ORDER.indexOf(b.agent));
    let cleared = new Set((Array.isArray(data.cleared) ? data.cleared : []).map(String));
    const regionName = (r) => labels.regions[r.id] || r.name;
    const moduleName = (t) => labels.modules[t.id] || t.name;

    // encounters, drawn back to front; a grid for finding the one under the pointer
    const encs = L.lessons.map((p) => {
      const l = lessonOf.get(String(p.id)) || { id: p.id };
      return { ...p, lesson: l, kind: l.kind || "fix", stage: l.stage || "provisional", ph: (strHash(p.id) % 1000) / 1000, key: "lesson:" + p.id };
    }).sort((a, b) => a.y - b.y || a.x - b.x);
    const CELL = 32, grid = new Map();
    for (const e of encs) {
      const k = Math.floor(e.x / CELL) * 65536 + Math.floor(e.y / CELL);
      if (!grid.has(k)) grid.set(k, []);
      grid.get(k).push(e);
    }
    const bosses = L.bosses.map((b) => ({ ...b, boss: bossOf.get(String(b.id)) || b, key: "boss:" + b.id }));
    const terrStats = L.territories.map(() => ({ n: 0, done: 0 }));
    const regionStats = L.regions.map(() => ({ n: 0, done: 0 }));
    function recount() {
      terrStats.forEach((s) => { s.n = 0; s.done = 0; });
      regionStats.forEach((s) => { s.n = 0; s.done = 0; });
      for (const e of encs) {
        const t = L.territories[e.territory], c = cleared.has(String(e.id)) ? 1 : 0;
        terrStats[t.index].n++; terrStats[t.index].done += c;
        regionStats[t.region].n++; regionStats[t.region].done += c;
      }
    }
    recount();

    // ---- the DOM: the map's canvas, its buttons, the minimap, a line for screen readers
    const el = (tag, cls) => { const e = document.createElement(tag); if (cls) e.className = cls; return e; };
    const wrap = el("div", "adv-map");
    if (Number(opts.height) > 0) wrap.style.height = Math.round(Number(opts.height)) + "px";
    const canvas = el("canvas", "adv-canvas");
    canvas.tabIndex = 0;
    canvas.setAttribute("role", "application");
    canvas.setAttribute("aria-label", labels.map);
    const tools = el("div", "adv-tools");
    const button = (label, glyph, fn) => {
      const b = el("button", "adv-btn");
      b.type = "button";
      b.setAttribute("aria-label", label);
      b.title = label;
      const ic = el("canvas", "adv-ico");
      ic.setAttribute("aria-hidden", "true");
      b.appendChild(ic);
      b.addEventListener("click", () => { if (b.getAttribute("aria-disabled") !== "true") fn(); });
      tools.appendChild(b);
      return { b, ic, glyph };
    };
    const btns = [
      button(labels.zoomIn, "plus", () => zoomBy(1.6)),
      button(labels.zoomOut, "minus", () => zoomBy(1 / 1.6)),
      button(labels.home, "home", () => home()),
      button(labels.fit, "fit", () => fit()),
    ];
    const mini = el("canvas", "adv-mini");
    mini.setAttribute("aria-hidden", "true");
    const sr = el("div", "adv-sr");
    sr.setAttribute("aria-live", "polite");
    wrap.append(canvas, tools, mini, sr);
    if (!encs.length) { const e = el("div", "adv-empty"); e.textContent = labels.empty; wrap.appendChild(e); }
    host.appendChild(wrap);
    const ctx = canvas.getContext("2d"), mctx = mini.getContext("2d");

    // ---- state
    let theme = null, themeKey = "", world = null, miniBase = null, alive = true, visible = true, cancelPaint = null;
    let occluded = []; // the buttons' and the minimap's boxes, in canvas pixels: no label goes under them
    let cssW = 600, cssH = 400, dpr = 1, zMin = 0.3;
    const zMax = 6;
    const cam = { x: L.width / 2, y: L.height / 2, z: 2 };
    let goal = null, last = 0, animT = 0, frameNo = -1, dirty = true;
    let hover = null, hoverTerr = -1, focus = null, lastFocus = null, keyboard = false, ping = null;
    let drag = null;
    const pointers = new Map();
    let hits = []; // what was drawn this frame, for Tab

    function measure() {
      dpr = (root.devicePixelRatio || 1);
      cssW = Math.max(200, wrap.clientWidth || host.clientWidth || 640);
      cssH = Math.max(240, wrap.clientHeight || Number(opts.height) || host.clientHeight || 520);
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
      const mw = cssW < 520 ? 104 : 176, mh = Math.round((mw * L.height) / L.width);
      mini.style.width = mw + "px";
      mini.style.height = mh + "px";
      mini.width = Math.round(mw * dpr);
      mini.height = Math.round(mh * dpr);
      const box = (e) => ({ x: (e.offsetLeft - 6) * dpr, y: (e.offsetTop - 6) * dpr, w: (e.offsetWidth + 12) * dpr, h: (e.offsetHeight + 12) * dpr });
      occluded = [box(tools), box(mini)];
      zMin = Math.min(1, frame({ x: 0, y: 0, w: L.width, h: L.height }, 10).z);
      buildMini();
      Object.assign(cam, clampCam(cam));
      dirty = true;
    }
    // the theme: read the page's tokens; the land is painted again only when they really changed. A quick preview
    // (a pixel every 4) shows at once; the full painting follows in slices between the page's other work.
    function retheme(force) {
      if (!alive) return;
      if (!wrap.isConnected) { destroy(); return; }
      const t = readTheme(), key = JSON.stringify(t);
      if (!force && key === themeKey) return;
      theme = t;
      themeKey = key;
      drawIcons();
      if (cancelPaint) cancelPaint();
      world = runNow(paintSteps(L, theme, seed, 4));
      buildMini();
      cancelPaint = runSlices(paintSteps(L, theme, seed, 1), (w) => {
        cancelPaint = null;
        if (!alive) return;
        world = w;
        buildMini();
        invalidate();
      });
      invalidate();
    }
    function buildMini() {
      if (!world) return;
      miniBase = blank(mini.width, mini.height);
      const g = miniBase.getContext("2d");
      g.imageSmoothingEnabled = true;
      g.imageSmoothingQuality = "high";
      g.drawImage(world.canvas, 0, 0, mini.width, mini.height);
    }
    function drawIcons() {
      const shapes = {
        plus: ["...#...", "...#...", "...#...", "#######", "...#...", "...#...", "...#..."],
        minus: [".......", ".......", ".......", "#######", ".......", ".......", "......."],
        home: ["...#...", "..###..", ".#####.", "#######", ".#...#.", ".#.#.#.", ".#.#.#."],
        fit: ["##...##", "#.....#", ".......", ".......", ".......", "#.....#", "##...##"],
      };
      for (const { ic, glyph } of btns) {
        const rows = shapes[glyph], k = Math.max(2, Math.round(2 * dpr));
        ic.width = 7 * k; ic.height = 7 * k;
        ic.style.width = 14 + "px"; ic.style.height = 14 + "px";
        const g = ic.getContext("2d");
        g.clearRect(0, 0, ic.width, ic.height);
        g.fillStyle = theme.ink;
        rows.forEach((row, y) => { for (let x = 0; x < 7; x++) if (row[x] === "#") g.fillRect(x * k, y * k, k, k); });
      }
    }

    // ---- the camera: (x, y) the world point in the middle, z css pixels a world pixel
    // the middle of the view stays over the world (it may look out to sea, so an edge region can be centred)
    function clampCam(c) {
      return { x: clamp(c.x, 0, L.width), y: clamp(c.y, 0, L.height), z: clamp(c.z, zMin, zMax) };
    }
    // the camera that shows rectangle R whole and centred in the part of the view the buttons and the minimap leave
    // free: either a column down the right, or the buttons' column and a band along the bottom (the bigger wins)
    function frame(R, pad = 24) {
      const mw = (mini.offsetWidth || 0) + 18, mh = (mini.offsetHeight || 0) + 18, tw = (tools.offsetWidth || 0) + 18;
      const opts = [[mw, 0], [tw, mh]].map(([right, bottom]) => {
        const z = Math.min((cssW - right - 2 * pad) / R.w, (cssH - bottom - 2 * pad) / R.h);
        return { z, x: R.x + R.w / 2 + right / (2 * z), y: R.y + R.h / 2 + bottom / (2 * z) };
      });
      return opts[0].z >= opts[1].z ? opts[0] : opts[1];
    }
    function flyTo(x, y, z) {
      const g = clampCam({ x, y, z });
      if (reduced) { Object.assign(cam, g); goal = null; } else goal = g;
      invalidate();
    }
    function jumpTo(x, y, z) { Object.assign(cam, clampCam({ x, y, z })); goal = null; invalidate(); }
    function zoomBy(k, at) {
      const base = goal || cam, z = clamp(base.z * k, zMin, zMax);
      if (!at) { flyTo(base.x, base.y, z); return; }
      // keep the world point under `at` (css px from the canvas' top left) where it is
      const wx = base.x + (at.x - cssW / 2) / base.z, wy = base.y + (at.y - cssH / 2) / base.z;
      flyTo(wx - (at.x - cssW / 2) / z, wy - (at.y - cssH / 2) / z, z);
    }
    function home() {
      const p = L.party;
      if (p) flyTo(p.x, p.y - 16, clamp(cssW / 620, 1.6, 2.2));
      else fit();
    }
    function fit() { const f = frame({ x: 0, y: 0, w: L.width, h: L.height }, 10); flyTo(f.x, f.y, Math.max(zMin, f.z)); }
    function fitRect(R) { const f = frame(R); flyTo(f.x, f.y, clamp(f.z, zMin, zMax)); }
    // a region: whole and centred; on a small screen, where that would shrink its encounters to dots, its busiest
    // territory at a zoom where they read
    function focusArea(r) {
      const f = frame(r.rect);
      if (cssW >= 600 || f.z >= 1.1) { fitRect(r.rect); return; }
      const mine = L.territories.filter((t) => t.region === r.index).sort((a, b) => terrStats[b.index].n - terrStats[a.index].n || a.index - b.index);
      const t = mine[0], at = t ? t.hole || t.center : { x: r.rect.x + r.rect.w / 2, y: r.rect.y + r.rect.h / 2 };
      flyTo(at.x, at.y, 1.6);
    }
    const toWorld = (cx, cy) => ({ x: cam.x + (cx - cssW / 2) / cam.z, y: cam.y + (cy - cssH / 2) / cam.z });
    const toClient = (wx, wy) => {
      const rect = canvas.getBoundingClientRect();
      return { x: rect.left + cssW / 2 + (wx - cam.x) * cam.z, y: rect.top + cssH / 2 + (wy - cam.y) * cam.z };
    };

    // ---- art caches (world pixels: one canvas pixel per art pixel)
    const art = new Map();
    function monster(kind, stage, frame) {
      const key = `m|${kind}|${stage}|${frame}`;
      if (!art.has(key)) {
        let cv = null;
        try {
          const A = root.Scene && root.Scene.art;
          if (A) cv = A.monster(A.kinds[kind] || "glitch", stage, frame);
        } catch (e) { cv = null; }
        if (!cv) { // no Scene: a little blob of the kind's colour
          const c = KIND_DOT[kind] || "#e5484d";
          cv = paintArt(["..cccc..", ".cccccc.", "cwkccwkc", "cccccccc", "cccccccc", ".cccccc.", ".c.cc.c."], { c, w: "=#ffffff", k: "=#1b1d33" }, false).cv;
        }
        art.set(key, cv);
      }
      return art.get(key);
    }
    function fly(kind) {
      try { const A = root.Scene && root.Scene.art; return A ? A.fly(A.kinds[kind] || "glitch") : 0; } catch (e) { return 0; }
    }
    function bossImg(n, frame, felled) {
      const key = `b|${n}|${frame}|${felled ? 1 : 0}`;
      if (!art.has(key)) {
        let cv = null;
        try { const A = root.Scene && root.Scene.art; if (A) cv = A.boss(n, frame, { felled }); } catch (e) { cv = null; }
        if (!cv) cv = paintArt(["...rrrrrr...", ".rrrrrrrrrr.", "rrwkrrrrwkrr", "rrrrrrrrrrrr", "rrrrmmmmrrrr", "rrrrrrrrrrrr", ".rr.rr.rr.rr"],
          { r: felled ? "#8a8a96" : "#e05a5a", w: "=#ffffff", k: "=#1b1d33", m: "=#3b1f2e" }, false).cv;
        art.set(key, cv);
      }
      return art.get(key);
    }
    function agentImg(agent, tier, frame) {
      const key = `a|${agent}|${tier}|${frame}`;
      if (!art.has(key)) {
        let cv = blank(32, 32);
        try { if (root.Sprites) root.Sprites.draw(cv, { agent, tier, frame, scale: 1 }); else throw new Error("no sprites"); } catch (e) {
          const g = cv.getContext("2d");
          g.fillStyle = "#ffb45e"; g.fillRect(10, 8, 12, 20);
          g.fillStyle = "#1b1d33"; g.fillRect(13, 13, 2, 2); g.fillRect(18, 13, 2, 2);
        }
        art.set(key, cv);
      }
      return art.get(key);
    }
    function propImg(type, bio) {
      const key = `p|${type}|${bio.hue}`;
      if (!art.has(key)) { const d = decoVariant(type, bio, 0); art.set(key, paintArt(d.rows, d.pal, d.flat).cv); }
      return art.get(key);
    }

    // ---- one frame
    let S = 2, ox = 0, oy = 0;
    const X = (wx) => ox + wx * S, Y = (wy) => oy + wy * S;
    function rect(wx, wy, w, h, color) {
      ctx.fillStyle = color;
      const x0 = Math.round(X(wx)), y0 = Math.round(Y(wy));
      ctx.fillRect(x0, y0, Math.max(1, Math.round(X(wx + w)) - x0), Math.max(1, Math.round(Y(wy + h)) - y0));
    }
    // a canvas with its feet at (wx, wy), k world pixels a pixel
    function put(cv, wx, wy, k = 1) {
      const w = cv.width * k, h = cv.height * k, x0 = Math.round(X(wx - w / 2)), y0 = Math.round(Y(wy - h));
      ctx.drawImage(cv, x0, y0, Math.round(X(wx + w / 2)) - x0, Math.round(Y(wy)) - y0);
    }
    function text(str, x, y, { size = 11, weight = 600, color = theme.ink, align = "center", alpha = 1, max = 0, outline = true, italic = false } = {}) {
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.font = `${italic ? "italic " : ""}${weight} ${Math.round(size * dpr)}px ${FONT}`;
      ctx.textAlign = align;
      ctx.textBaseline = "middle";
      let s = String(str);
      if (max && ctx.measureText(s).width > max) {
        while (s.length > 1 && ctx.measureText(s + "…").width > max) s = s.slice(0, -1);
        s = s.trimEnd() + "…";
      }
      if (outline) {
        ctx.lineJoin = "round";
        ctx.lineWidth = Math.max(2, Math.round(3.2 * dpr));
        ctx.strokeStyle = theme.outline;
        ctx.strokeText(s, Math.round(x), Math.round(y));
      }
      ctx.fillStyle = color;
      ctx.fillText(s, Math.round(x), Math.round(y));
      const w = ctx.measureText(s).width;
      ctx.restore();
      return w;
    }
    function render() {
      if (!alive || !world) return;
      const t = reduced ? 0 : animT, frame = reduced ? 0 : Math.floor(t * 2.5);
      S = cam.z * dpr;
      const vw = canvas.width, vh = canvas.height;
      ox = vw / 2 - cam.x * S;
      oy = vh / 2 - cam.y * S;
      hits = [];
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.imageSmoothingEnabled = false;
      ctx.fillStyle = theme.sea.deep;
      ctx.fillRect(0, 0, vw, vh);
      // the chart's grid runs on past the painted world when zoomed out
      if (ox > 0 || oy > 0 || ox + L.width * S < vw || oy + L.height * S < vh) {
        ctx.fillStyle = theme.sea.grid;
        const dot = Math.max(1, Math.round(S)), gx0 = Math.floor(-ox / S / 48) * 48, gy0 = Math.floor(-oy / S / 48) * 48;
        for (let gx = gx0; X(gx) < vw; gx += 48) for (let y = 0; y < vh; y += 2 * dot) ctx.fillRect(Math.round(X(gx)), y, dot, dot);
        for (let gy = gy0; Y(gy) < vh; gy += 48) for (let x = 0; x < vw; x += 2 * dot) ctx.fillRect(x, Math.round(Y(gy)), dot, dot);
      }
      // the land, as painted
      const vx0 = -ox / S, vy0 = -oy / S, vx1 = (vw - ox) / S, vy1 = (vh - oy) / S;
      const sx0 = Math.max(0, Math.floor(vx0)), sy0 = Math.max(0, Math.floor(vy0));
      const sx1 = Math.min(L.width, Math.ceil(vx1)), sy1 = Math.min(L.height, Math.ceil(vy1));
      ctx.imageSmoothingEnabled = S < 1 || world.k < 1; // the preview is soft until the full painting lands
      if (sx1 > sx0 && sy1 > sy0) {
        const dx = Math.round(X(sx0)), dy = Math.round(Y(sy0)), k = world.k;
        ctx.drawImage(world.canvas, sx0 * k, sy0 * k, (sx1 - sx0) * k, (sy1 - sy0) * k, dx, dy, Math.round(X(sx1)) - dx, Math.round(Y(sy1)) - dy);
      }
      ctx.imageSmoothingEnabled = false;
      const seen = (x, y, m = 40) => x > vx0 - m && x < vx1 + m && y > vy0 - m && y < vy1 + m * 1.5;
      // water glints, ships, smoke and lights
      if (!reduced) {
        for (const gl of world.glints) {
          if (!seen(gl.x, gl.y, 4)) continue;
          const u = (t * 0.7 + gl.ph) % 3;
          if (u < 0.5) rect(gl.x - 1, gl.y, u < 0.25 ? 2 : 3, 1, rgba(theme.sea.glint, gl.lake ? 0.6 : 0.75));
        }
      }
      const bioShip = L.regions.length ? biomeOf(L.regions[0]) : BIOMES.craft;
      for (const sh of world.ships) {
        if (!seen(sh.x, sh.y, 20)) continue;
        const bob = reduced ? 0 : Math.round(Math.sin(t * 1.4 + sh.ph));
        const cv = propImg("ship", bioShip);
        rect(sh.x - 6, sh.y + 1, 12, 1, rgba(theme.sea.foam, 0.6));
        if (sh.dir < 0) { ctx.save(); ctx.translate(Math.round(X(sh.x)) * 2, 0); ctx.scale(-1, 1); put(cv, sh.x, sh.y + bob); ctx.restore(); }
        else put(cv, sh.x, sh.y + bob);
      }
      const fogged = (ti) => ti >= 0 && L.territories[ti] && L.territories[ti].fog;
      for (const an of world.anims) {
        if (!seen(an.x, an.y, 30) || fogged(an.ti)) continue;
        if (an.kind === "smoke" && !reduced) {
          for (let k = 0; k < 3; k++) {
            const u = (t * 0.32 + k / 3 + an.ph) % 1, x = an.x + Math.sin(u * 5 + an.ph) * 1.5 + u * 5, y = an.y - 1 - u * 15, s = 1 + Math.round(u * 2);
            rect(Math.round(x), Math.round(y), s, s, theme.dark ? rgba("#c8cbd8", 0.45 * (1 - u)) : rgba("#ffffff", 0.75 * (1 - u)));
          }
        } else if (an.kind === "blink") {
          const on = reduced || Math.floor(t * 1.6 + an.ph) % 2 === 0;
          if (on) { rect(an.x - 1, an.y - 1, 3, 3, rgba("#ff5a5a", 0.35)); rect(an.x, an.y, 1, 1, "#ff6b6b"); }
        } else if (an.kind === "sparkle" && !reduced) {
          const u = (t * 0.5 + an.ph) % 2.4;
          if (u < 0.36) {
            const r2 = u < 0.12 || u > 0.24 ? 1 : 2;
            for (let k = -r2; k <= r2; k++) { rect(an.x + k, an.y, 1, 1, "#ffffff"); rect(an.x, an.y + k, 1, 1, "#ffffff"); }
          }
        } else if (an.kind === "beam") {
          const ang = reduced ? 0.6 : t * 1.1;
          for (let k = 2; k < 30; k += 2) {
            const bx = an.x + Math.cos(ang) * k, by = an.y + Math.sin(ang) * k * 0.5;
            rect(Math.round(bx), Math.round(by), 2, 2, rgba("#fff3b0", 0.42 * (1 - k / 30)));
          }
        }
      }
      // the hovered territory: its outline
      if (hoverTerr >= 0) {
        const tt = L.territories[hoverTerr];
        ctx.save();
        ctx.beginPath();
        tt.poly.forEach(([x, y], k) => (k ? ctx.lineTo(X(x), Y(y)) : ctx.moveTo(X(x), Y(y))));
        ctx.closePath();
        ctx.fillStyle = rgba(theme.dark ? "#ffffff" : theme.accent, 0.07);
        ctx.fill();
        ctx.setLineDash([4 * dpr, 3 * dpr]);
        ctx.lineWidth = Math.max(1, Math.round(1.5 * dpr));
        ctx.strokeStyle = rgba(theme.dark ? "#ffffff" : theme.accent, 0.75);
        ctx.stroke();
        ctx.restore();
      }
      // the party's ring and the gates' bosses' auras go under everything that stands
      drawGroundMarks(t);
      // encounters, back to front
      const far = S < 0.5;
      ctx.imageSmoothingEnabled = S < 1;
      for (const e of encs) {
        if (!seen(e.x, e.y, 24)) continue;
        const done = cleared.has(String(e.id)), lift = hover === e.key || focus === e.key ? 1 : 0;
        if (far) {
          const c = done ? biomeOf(L.regions[L.territories[e.territory].region]).hue : KIND_DOT[e.kind] || "#e5484d";
          const s = Math.max(3, Math.round(6 * S)), x0 = Math.round(X(e.x)) - (s >> 1), y0 = Math.round(Y(e.y - 4)) - (s >> 1);
          ctx.fillStyle = "rgba(10, 12, 30, 0.6)";
          ctx.fillRect(x0 - 1, y0 - 1, s + 2, s + 2);
          ctx.fillStyle = c;
          ctx.fillRect(x0, y0, s, s);
          hits.push({ key: e.key, x: e.x, y: e.y, box: [e.x - 6, e.y - 10, 12, 12], item: () => lessonItem(e) });
          continue;
        }
        if (done) {
          const cv = propImg("flag", biomeOf(L.regions[L.territories[e.territory].region]));
          rect(e.x - 2, e.y, 6, 1, theme.shadow);
          put(cv, e.x + 2, e.y + 1 - lift);
          hits.push({ key: e.key, x: e.x, y: e.y, box: [e.x - 5, e.y - 12, 11, 13], item: () => lessonItem(e) });
        } else {
          const k = e.stage === "canonical" ? 1.5 : 1, fl = fly(e.kind);
          const fr = (frame + Math.floor(e.ph * 7)) % 2, cv = monster(e.kind, e.stage, fr);
          const bob = fl ? Math.round(Math.sin(t * 3 + e.ph * 6.28)) - Math.round(fl * 0.5) : 0;
          rect(e.x - 5 * k, e.y - 1, 10 * k, 1, theme.shadow);
          rect(e.x - 3 * k, e.y, 6 * k, 1, theme.shadow);
          if (lift) ring(e.x, e.y, 8 * k, theme.cta);
          put(cv, e.x, e.y + bob - lift, k);
          hits.push({ key: e.key, x: e.x, y: e.y, box: [e.x - 9 * k, e.y - 17 * k, 18 * k, 18 * k], item: () => lessonItem(e) });
        }
      }
      drawBosses(t, frame);
      drawParty(t, frame);
      drawLabels();
      drawFocus(t);
      drawMini();
      syncButtons();
    }
    // + and − say so when there is no further to go
    function syncButtons() {
      const z = (goal || cam).z;
      for (const [b, off] of [[btns[0].b, z >= zMax * 0.999], [btns[1].b, z <= zMin * 1.001]]) {
        if ((b.getAttribute("aria-disabled") === "true") !== off) b.setAttribute("aria-disabled", off ? "true" : "false");
      }
    }
    // a gold ellipse on the ground
    function ring(x, y, rx, color, ry = rx * 0.38) {
      for (let k = 0; k < 28; k++) {
        const a = (k / 28) * Math.PI * 2;
        rect(Math.round(x + Math.cos(a) * rx), Math.round(y + Math.sin(a) * ry), 1, 1, color);
      }
    }
    function drawGroundMarks(t) {
      if (L.party) {
        const p = L.party, pulse = reduced ? 0 : (t * 0.8) % 1;
        ring(p.x, p.y, 15, rgba(theme.cta, 0.9));
        if (!reduced) ring(p.x, p.y, 15 + pulse * 10, rgba(theme.cta, 0.6 * (1 - pulse)));
      }
      for (const b of bosses) {
        if (b.status !== "evidence") continue;
        const pulse = reduced ? 0.5 : (t * 0.6) % 1;
        ring(b.x, b.y, 18 + pulse * 6, rgba("#ff6b6b", 0.8 * (1 - pulse)));
        ring(b.x, b.y, 17, rgba(theme.cta, 0.8));
      }
    }
    function drawBosses(t, frame) {
      for (const b of bosses) {
        if (b.x < -ox / S - 60 || b.x > (canvas.width - ox) / S + 60) continue;
        const done = b.status === "done", awake = b.status === "evidence";
        const cv = bossImg(b.n || 1, awake && !reduced ? frame % 2 : 0, done);
        const lift = hover === b.key || focus === b.key ? 1 : 0;
        rect(b.x - 13, b.y - 1, 26, 2, theme.shadow);
        ctx.save();
        if (b.status === "open") ctx.globalAlpha = 0.82;
        put(cv, b.x, b.y + (done ? 3 : 0) - lift, 2);
        ctx.restore();
        const top = b.y - cv.height * 2;
        if (done) put(propImg("flag", biomeOf(L.regions[b.region])), b.x - 16, b.y + 1);
        else if (awake) { // awake: a "!" over its head
          const bounce = reduced ? 0 : Math.round(Math.abs(Math.sin(t * 4)) * -2);
          rect(b.x - 1, top - 10 + bounce, 3, 6, "#ff5a5a");
          rect(b.x - 1, top - 3 + bounce, 3, 2, "#ff5a5a");
        } else { // asleep: z's drifting up
          for (let k = 0; k < 2; k++) {
            const u = reduced ? k * 0.5 : (t * 0.4 + k * 0.5) % 1, zx = b.x + 10 + u * 6 + k * 2, zy = top + 2 - u * 12, s = k ? 4 : 3;
            const c = rgba(theme.dark ? "#e6ecff" : "#3a3f5a", reduced ? 0.8 : 1 - u);
            rect(zx, zy, s, 1, c); rect(zx + s - 1 - Math.floor(s / 2), zy + 1, 1, s - 2, c); rect(zx, zy + s - 1, s, 1, c);
          }
        }
        hits.push({ key: b.key, x: b.x, y: b.y, box: [b.x - 18, top, 36, b.y - top + 2], item: () => ({ type: "boss", boss: b.boss }) });
      }
    }
    function drawParty(t, frame) {
      const p = L.party;
      if (!p) return;
      const team = (agents.filter((a) => (a.level || 0) > 0).length ? agents.filter((a) => (a.level || 0) > 0) : agents).slice(0, 6);
      const lead = team.length ? team : [{ agent: "claude", tier: "novice" }];
      const spots = [[0, 0], [-15, -6], [15, -6], [-27, -11], [27, -11], [0, -13]];
      const order = lead.map((a, i) => ({ a, s: spots[i] })).sort((u, v) => u.s[1] - v.s[1]);
      const lift = hover === "party" || focus === "party" ? 1 : 0;
      for (const { a, s } of order) {
        const fr = reduced ? 0 : (frame + AGENT_ORDER.indexOf(a.agent)) % 4;
        put(agentImg(a.agent, a.tier || "novice", fr), p.x + s[0], p.y + s[1] + 1 - lift);
      }
      // a gold arrow over the leader's head
      const bob = reduced ? 0 : Math.round(Math.sin(t * 3.2) * 1.5), ay = p.y - 38 + bob;
      rect(p.x - 3, ay, 7, 1, theme.cta); rect(p.x - 2, ay + 1, 5, 1, theme.cta); rect(p.x - 1, ay + 2, 3, 1, theme.cta); rect(p.x, ay + 3, 1, 1, theme.cta);
      hits.push({ key: "party", x: p.x, y: p.y, box: [p.x - 30, p.y - 44, 60, 46], item: () => ({ type: "party", agents: agents.slice() }) });
    }
    function drawLabels() {
      const z = cam.z, boxes = occluded.slice(); // nothing goes under the buttons or the minimap
      const hit = (b) => boxes.some((q) => b.x < q.x + q.w && q.x < b.x + b.w && b.y < q.y + q.h && q.y < b.y + b.h);
      // a box that runs under a control slides left of it (and back inside the view)
      const slide = (b) => {
        b.x = clamp(b.x, 4 * dpr, canvas.width - b.w - 4 * dpr);
        for (const q of occluded) if (b.x < q.x + q.w && q.x < b.x + b.w && b.y < q.y + q.h && q.y < b.y + b.h) b.x = q.x - b.w - 2 * dpr;
        return b.x >= 0;
      };
      // the party and the bosses stand clear of every name
      const shield = (h) => { const [x, y, w, hh] = h.box; boxes.push({ x: X(x), y: Y(y) - 4 * dpr, w: w * S, h: hh * S + 4 * dpr }); };
      for (const h of hits) if (h.key === "party" || h.key.startsWith("boss:")) shield(h);
      // the regions' banners
      const fpx = clamp(Math.round(11 + 3 * z), 14, 20);
      for (const r of L.regions) {
        const bio = biomeOf(r), st = regionStats[r.index], title = fill(labels.regionTitle, { name: regionName(r) });
        const bx = X(r.banner.x), by = Y(r.banner.y);
        if (bx < -300 || bx > canvas.width + 300 || by < -60 || by > canvas.height + 60) continue;
        const size = bannerSize(title, fpx), b = { x: bx - size.w / 2, y: by - size.h / 2, w: size.w, h: size.h };
        if (!slide(b) || hit(b)) continue;
        banner(title, b.x + b.w / 2, by, fpx, bio.hue);
        boxes.push(b);
        if (z >= 0.9 && st.n) {
          ctx.font = `600 ${Math.round(10 * dpr)}px ${FONT}`;
          const line = fill(labels.regionStats, { cleared: st.done, lessons: st.n }), w = ctx.measureText(line).width + 8 * dpr;
          const sb = { x: b.x + b.w / 2 - w / 2, y: b.y + b.h + 1, w, h: 16 * dpr };
          if (slide(sb) && !hit(sb)) {
            text(line, sb.x + w / 2, sb.y + 8 * dpr, { size: 10, weight: 600, color: theme.dark ? "#ffd27a" : "#7a4a06" });
            boxes.push(sb);
          }
        }
      }
      // the bosses' tags: one panel under each gate, a line a boss
      if (z >= 1.1) for (const r of L.regions) {
        const mine = bosses.filter((b) => b.region === r.index);
        if (!mine.length) continue;
        const two = z >= 2;
        ctx.font = `600 ${Math.round(9.5 * dpr)}px ${FONT}`;
        const lines = mine.map((b) => {
          const tag = fill(labels.boss, { text: String(b.boss.text || "").replace(/[.。]\s*$/, "") });
          return { b, tag: two ? `${tag} · ${labels[STATUS[b.status] || "bossAsleep"]}` : tag };
        });
        const maxW = Math.min(300 * dpr, canvas.width - 24 * dpr), lh = 14 * dpr;
        const w = Math.min(maxW, Math.max(...lines.map((l) => ctx.measureText(l.tag).width))) + 14 * dpr, h = lines.length * lh + 8 * dpr;
        const top = Y(r.arch.y + 4), cx = X(r.arch.x);
        if (cx < -w || cx > canvas.width + w || top > canvas.height || top + h < 0) continue;
        // under the gate, stepping down past anything already there; else over the bosses' heads; else not at all (the
        // page's tooltip still names them)
        const above = Y(r.arch.y - 44) - h;
        const tries = [0, 1, 2, 3].map((k) => top + k * lh).concat([0, 1, 2].map((k) => above - k * lh));
        let pb = null;
        for (const y of tries) {
          const c = { x: cx - w / 2, y, w, h };
          if (slide(c) && !hit(c)) { pb = c; break; }
        }
        if (!pb) continue;
        ctx.save();
        ctx.fillStyle = theme.panel;
        ctx.fillRect(Math.round(pb.x), Math.round(pb.y), Math.round(w), Math.round(h));
        ctx.strokeStyle = rgba(biomeOf(r).hue, 0.9);
        ctx.lineWidth = Math.max(1, Math.round(dpr));
        ctx.strokeRect(Math.round(pb.x) + 0.5, Math.round(pb.y) + 0.5, Math.round(w) - 1, Math.round(h) - 1);
        ctx.restore();
        lines.forEach((l, k) => {
          const c = l.b.status === "evidence" ? (theme.dark ? "#ff9a9a" : "#b02a2a") : l.b.status === "done" ? theme.muted : theme.ink;
          text(l.tag, pb.x + 7 * dpr, pb.y + 4 * dpr + lh * (k + 0.5), { size: 9.5, weight: 600, color: c, align: "left", max: w - 14 * dpr, outline: false });
        });
        boxes.push(pb);
      }
      if (z < 1.05) return;
      // the territories' names (two lines when long), the busiest first; the party's territory names itself over the party
      const order = L.territories.slice().sort((a, b) => (terrStats[b.index].n - terrStats[a.index].n) || a.index - b.index);
      const lh = 12.5 * dpr;
      for (const tt of order) {
        const at = tt.hole || tt.center, partyHere = L.party && L.party.territory === tt.index, st = terrStats[tt.index];
        const cx0 = X(at.x);
        if (cx0 < -120 || cx0 > canvas.width + 120) continue;
        const xs = tt.poly.map((p) => p[0]), maxW = Math.max(96 * dpr, (Math.max(...xs) - Math.min(...xs)) * S - 6 * dpr);
        ctx.font = `600 ${Math.round(10.5 * dpr)}px ${FONT}`;
        const name = tt.fog ? labels.fog : moduleName(tt), rows = tt.fog ? [name] : wrapText(name, maxW);
        const w = Math.min(maxW, Math.max(...rows.map((x) => ctx.measureText(x).width)));
        if (tt.fog && w < ctx.measureText(name).width) continue; // a sliver of fog: no room for its word
        const prog = !tt.fog && z >= 1.6 && st.n, h = rows.length * lh + (prog ? 11 * dpr : 0) + 4 * dpr;
        const top = partyHere ? Y(L.party.y - 46) - h - 2 * dpr : Y(at.y - (tt.fog ? 0 : 5)) - lh / 2 - 2 * dpr;
        if (top < -40 || top > canvas.height + 40) continue;
        const b = { x: cx0 - w / 2 - 2, y: top, w: w + 4, h };
        if (!slide(b) || hit(b)) continue;
        boxes.push(b);
        const cx = b.x + b.w / 2;
        rows.forEach((row, k) => text(row, cx, top + 2 * dpr + lh * (k + 0.5), tt.fog ? { size: 10, weight: 500, color: theme.muted, italic: true, max: maxW }
          : { size: 10.5, weight: 600, color: theme.ink, max: maxW }));
        if (prog) {
          const all = st.done === st.n;
          text(fill(labels.progress, { cleared: st.done, lessons: st.n }), cx, top + 2 * dpr + rows.length * lh + 5 * dpr, { size: 9, weight: 600,
            color: all ? (theme.dark ? "#7dffb0" : "#0c7c50") : (theme.dark ? "#ffd27a" : "#7a4a06") });
        }
      }
    }
    // a name in at most two lines of `max` device pixels (ctx.font set), broken between words
    function wrapText(str, max) {
      if (ctx.measureText(str).width <= max) return [str];
      const words = String(str).split(/\s+/);
      let best = null;
      for (let k = 1; k < words.length; k++) {
        const a = words.slice(0, k).join(" "), b = words.slice(k).join(" "), wa = ctx.measureText(a).width, wb = ctx.measureText(b).width;
        const score = Math.max(wa, wb);
        if (wa <= max && (!best || score < best.score)) best = { rows: [a, b], score };
      }
      return best ? best.rows : [str];
    }
    // a banner's whole size, tails included
    function bannerSize(str, size) {
      ctx.save();
      ctx.font = `700 ${Math.round(size * dpr)}px ${FONT}`;
      const u = Math.max(1, Math.round(dpr * (size >= 16 ? 2 : 1.5))), w = Math.ceil(ctx.measureText(str).width) + 18 * dpr;
      ctx.restore();
      return { w: w + 16 * u, h: Math.round(size * dpr * 1.7) + 3 * u };
    }
    // a ribbon banner, pixel style, in device pixels
    function banner(str, cx, cy, size, hue) {
      ctx.save();
      ctx.font = `700 ${Math.round(size * dpr)}px ${FONT}`;
      const u = Math.max(1, Math.round(dpr * (size >= 16 ? 2 : 1.5)));
      const tw = Math.ceil(ctx.measureText(str).width), w = tw + 18 * dpr, h = Math.round(size * dpr * 1.7);
      const x = Math.round(cx - w / 2), y = Math.round(cy - h / 2), dark = mix(hue, "#101020", 0.45);
      // the tails behind, folded under
      ctx.fillStyle = dark;
      ctx.fillRect(x - 8 * u, y + 3 * u, 10 * u, h);
      ctx.fillRect(x + w - 2 * u, y + 3 * u, 10 * u, h);
      ctx.fillStyle = theme.page;
      ctx.fillRect(x - 8 * u, y + 3 * u + Math.round(h / 2) - u, 3 * u, 2 * u);
      ctx.fillRect(x + w + 5 * u, y + 3 * u + Math.round(h / 2) - u, 3 * u, 2 * u);
      // the face: a rim of the region's colour, the page's panel inside
      ctx.fillStyle = hue;
      ctx.fillRect(x, y + u, w, h - 2 * u);
      ctx.fillRect(x + u, y, w - 2 * u, h);
      ctx.fillStyle = theme.dark ? mix(hue, "#0b0f1e", 0.72) : mix(hue, "#ffffff", 0.86);
      ctx.fillRect(x + 2 * u, y + 2 * u, w - 4 * u, h - 4 * u);
      ctx.fillStyle = rgba("#ffffff", theme.dark ? 0.12 : 0.6);
      ctx.fillRect(x + 2 * u, y + 2 * u, w - 4 * u, u);
      ctx.fillStyle = theme.dark ? mix(hue, "#ffffff", 0.75) : mix(hue, "#0b0f1e", 0.6);
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(str, Math.round(cx), Math.round(y + h / 2 + dpr * 0.5));
      ctx.restore();
      return { x: x - 8 * u, y, w: w + 16 * u, h: h + 3 * u };
    }
    function drawFocus(t) {
      const key = focus && keyboard ? focus : null;
      const h = key && hits.find((x) => x.key === key);
      if (h) corners(h.box, Math.floor(t * 4) % 2 && !reduced ? theme.ink : theme.cta);
      if (ping) { // focusLesson: a ring that opens round the encounter
        const u = reduced ? 0.3 : ((performance.now() - ping.t0) / 1000) % 1.2 / 1.2;
        const hp = hits.find((x) => x.key === ping.key);
        if (hp) { ring(hp.x, hp.y - 6, 10 + u * 16, rgba(theme.cta, 1 - u), (10 + u * 16) * 0.8); corners(hp.box, theme.cta); }
        if (!reduced && performance.now() - ping.t0 > 6000) ping = null;
      }
    }
    function corners([x, y, w, h], c) {
      const k = 3;
      for (const [cx, cy, dx, dy] of [[x - 2, y - 2, 1, 1], [x + w + 1, y - 2, -1, 1], [x - 2, y + h + 1, 1, -1], [x + w + 1, y + h + 1, -1, -1]]) {
        rect(cx, cy, 1, 1, c);
        for (let i = 1; i < k; i++) { rect(cx + dx * i, cy, 1, 1, c); rect(cx, cy + dy * i, 1, 1, c); }
      }
    }
    function drawMini() {
      if (!miniBase) return;
      const k = mini.width / L.width;
      mctx.setTransform(1, 0, 0, 1, 0, 0);
      mctx.clearRect(0, 0, mini.width, mini.height);
      mctx.drawImage(miniBase, 0, 0);
      const hw = cssW / (2 * cam.z), hh = cssH / (2 * cam.z);
      const x0 = clamp((cam.x - hw) * k, 0, mini.width), y0 = clamp((cam.y - hh) * k, 0, mini.height);
      const x1 = clamp((cam.x + hw) * k, 0, mini.width), y1 = clamp((cam.y + hh) * k, 0, mini.height);
      const lw = Math.max(1, Math.round(1.5 * dpr));
      mctx.lineWidth = lw;
      mctx.strokeStyle = theme.dark ? "rgba(0, 0, 0, 0.6)" : "rgba(255, 255, 255, 0.85)";
      mctx.strokeRect(x0 + lw, y0 + lw, Math.max(2, x1 - x0 - 2 * lw), Math.max(2, y1 - y0 - 2 * lw));
      mctx.strokeStyle = theme.cta;
      mctx.strokeRect(x0 + lw / 2, y0 + lw / 2, Math.max(2, x1 - x0 - lw), Math.max(2, y1 - y0 - lw));
      const dot = (x, y, c, s) => { mctx.fillStyle = c; mctx.fillRect(Math.round(x * k - s / 2), Math.round(y * k - s / 2), s, s); };
      for (const b of bosses) if (b.status !== "done") dot(b.x, b.y - 8, b.status === "evidence" ? "#ff5a5a" : "#b06060", Math.round(3 * dpr));
      if (L.party) { dot(L.party.x, L.party.y - 8, "#ffffff", Math.round(5 * dpr)); dot(L.party.x, L.party.y - 8, theme.cta, Math.round(3 * dpr)); }
    }

    // ---- what is where
    function lessonItem(e) { return { type: "lesson", lesson: e.lesson, cleared: cleared.has(String(e.id)) }; }
    function territoryAt(wx, wy) {
      for (const r of L.regions) {
        if (!inRect(r.rect, wx, wy)) continue;
        for (const t of L.territories) if (t.region === r.index && depth(t.poly, wx, wy) >= 0) return t;
      }
      return null;
    }
    function hitAt(wx, wy) {
      // the party and the bosses stand in front; then the frontmost encounter under the point
      for (let i = hits.length - 1; i >= 0; i--) {
        const h = hits[i];
        if (h.key.startsWith("lesson:")) continue;
        const [x, y, w, hh] = h.box;
        if (wx >= x && wx < x + w && wy >= y && wy < y + hh) return h;
      }
      let best = null;
      const gx = Math.floor(wx / CELL), gy = Math.floor(wy / CELL);
      for (let i = -1; i <= 1; i++) for (let j = -1; j <= 2; j++) {
        for (const e of grid.get((gx + i) * 65536 + gy + j) || []) {
          const done = cleared.has(String(e.id)), k = e.stage === "canonical" && !done ? 1.5 : 1;
          const box = S < 0.5 ? [e.x - 6, e.y - 10, 12, 12] : done ? [e.x - 5, e.y - 12, 11, 13] : [e.x - 9 * k, e.y - 17 * k, 18 * k, 18 * k];
          if (wx >= box[0] && wx < box[0] + box[2] && wy >= box[1] && wy < box[1] + box[3] && (!best || e.y > best.e.y)) best = { e, box };
        }
      }
      if (best) return { key: best.e.key, x: best.e.x, y: best.e.y, box: best.box, item: () => lessonItem(best.e) };
      return null;
    }
    // a territory: its region, and its module with the map's own counts (total: encounters standing there; cleared:
    // of those; label: its name, or LABELS.fog under the fog)
    function regionItem(t) {
      const r = L.regions[t.region], st = terrStats[t.index], rs = regionStats[r.index];
      const region = { ...(regionOf.get(r.id) || { id: r.id }), name: regionName(r), total: rs.n, cleared: rs.done };
      const module = { ...(moduleOf.get(t.id) || { id: t.id }), name: moduleName(t), total: st.n, cleared: st.done, fog: !!t.fog,
        label: t.fog ? labels.fog : moduleName(t) };
      return { type: "region", region, module, fog: !!t.fog };
    }
    // where the page's tooltip goes: past the encounter's lower right corner, so it never covers what it names
    function tipPoint(h, at) {
      const [x, y, w, hh] = h.box, c = toClient(x + w, y + hh);
      return at ? { x: Math.max(at.x, c.x + 2), y: Math.max(at.y, c.y + 2) } : { x: c.x + 2, y: c.y + 2 };
    }
    const local = (e) => { const b = canvas.getBoundingClientRect(); return { x: e.clientX - b.left, y: e.clientY - b.top }; };
    function hoverAt(e) {
      const p = local(e), w = toWorld(p.x, p.y), h = hitAt(w.x, w.y);
      const was = hover, wasT = hoverTerr;
      if (h) {
        hover = h.key; hoverTerr = -1;
        canvas.classList.add("adv-pointer");
        onHover(h.item(), tipPoint(h, { x: e.clientX, y: e.clientY }));
      } else {
        hover = null;
        canvas.classList.remove("adv-pointer");
        const t = territoryAt(w.x, w.y);
        hoverTerr = t ? t.index : -1;
        onHover(t ? regionItem(t) : null, { x: e.clientX, y: e.clientY });
      }
      if (was !== hover || wasT !== hoverTerr) invalidate();
    }
    function clearHover() {
      if (hover || hoverTerr >= 0) invalidate();
      hover = null; hoverTerr = -1;
      canvas.classList.remove("adv-pointer");
      onHover(null, { x: 0, y: 0 });
    }

    // ---- pointer: drag to pan, pinch or wheel to zoom, tap to pick
    function onDown(e) {
      if (e.button != null && e.button > 0) return;
      canvas.focus({ preventScroll: true });
      keyboard = false;
      try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* not capturable */ }
      pointers.set(e.pointerId, local(e));
      if (pointers.size === 1) drag = { start: local(e), cam: { ...cam }, moved: false, pinch: null };
      else if (pointers.size === 2 && drag) {
        const [a, b] = [...pointers.values()];
        drag.pinch = { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, z: cam.z, mid: toWorld((a.x + b.x) / 2, (a.y + b.y) / 2) };
        drag.moved = true;
      }
      goal = null;
    }
    function onMove(e) {
      if (!pointers.has(e.pointerId)) { if (e.pointerType !== "touch") hoverAt(e); return; }
      pointers.set(e.pointerId, local(e));
      if (!drag) return;
      if (drag.pinch && pointers.size >= 2) {
        const [a, b] = [...pointers.values()], d = Math.hypot(a.x - b.x, a.y - b.y) || 1, m = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
        const z = clamp(drag.pinch.z * (d / drag.pinch.d), zMin, zMax);
        jumpTo(drag.pinch.mid.x - (m.x - cssW / 2) / z, drag.pinch.mid.y - (m.y - cssH / 2) / z, z);
        return;
      }
      const p = local(e), dx = p.x - drag.start.x, dy = p.y - drag.start.y;
      if (!drag.moved && Math.hypot(dx, dy) > 4) { drag.moved = true; canvas.classList.add("adv-dragging"); clearHover(); }
      if (drag.moved) jumpTo(drag.cam.x - dx / cam.z, drag.cam.y - dy / cam.z, cam.z);
    }
    function onUp(e) {
      if (!pointers.has(e.pointerId)) return;
      pointers.delete(e.pointerId);
      try { canvas.releasePointerCapture(e.pointerId); } catch (err) { /* released */ }
      if (pointers.size) { // one finger left after a pinch: carry on dragging from here
        const p = [...pointers.values()][0];
        drag = { start: p, cam: { ...cam }, moved: true, pinch: null };
        return;
      }
      const d = drag;
      drag = null;
      canvas.classList.remove("adv-dragging");
      if (d && !d.moved && e.type === "pointerup") {
        const p = local(e), w = toWorld(p.x, p.y), h = hitAt(w.x, w.y);
        if (h) { onHover(null, { x: 0, y: 0 }); onPick(h.item()); }
      }
    }
    function onWheel(e) {
      e.preventDefault();
      const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1;
      const k = Math.exp(-e.deltaY * unit * (e.ctrlKey ? 0.012 : 0.0018));
      const p = local(e), z = clamp(cam.z * k, zMin, zMax);
      const w = toWorld(p.x, p.y);
      jumpTo(w.x - (p.x - cssW / 2) / z, w.y - (p.y - cssH / 2) / z, z);
    }
    function onDbl(e) { e.preventDefault(); zoomBy(2, local(e)); }
    function onLeave() { if (!drag) clearHover(); }

    // ---- keyboard: arrows pan, + and − zoom, 0 back to the party, Tab through what is on screen, Enter picks
    function onKey(e) {
      const step = 0.18, z = (goal || cam).z, base = goal || cam;
      const pan = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] }[e.key];
      if (pan) {
        e.preventDefault();
        flyTo(base.x + (pan[0] * cssW * step) / z, base.y + (pan[1] * cssH * step) / z, z);
        return;
      }
      if (e.key === "+" || e.key === "=") { e.preventDefault(); zoomBy(1.5); return; }
      if (e.key === "-" || e.key === "_") { e.preventDefault(); zoomBy(1 / 1.5); return; }
      if (e.key === "0" || e.key === "Home") { e.preventDefault(); home(); return; }
      // N or ] steps to the next encounter on screen, P or [ to the one before; Tab is left alone (it leaves the map)
      const dir = e.key === "n" || e.key === "N" || e.key === "]" ? 1 : e.key === "p" || e.key === "P" || e.key === "[" ? -1 : 0;
      if (dir && !e.ctrlKey && !e.metaKey && !e.altKey) {
        const list = hits.filter((h) => { const sx = X(h.x), sy = Y(h.y); return sx >= 0 && sx <= canvas.width && sy >= 0 && sy <= canvas.height; })
          .sort((a, b) => Math.floor(a.y / 40) - Math.floor(b.y / 40) || a.x - b.x);
        if (!list.length) return;
        e.preventDefault();
        const from = list.findIndex((h) => h.key === (focus || lastFocus));
        const i = from < 0 ? (dir > 0 ? 0 : list.length - 1) : (from + dir + list.length) % list.length;
        keyboard = true;
        focus = lastFocus = list[i].key;
        const item = list[i].item();
        onHover(item, tipPoint(list[i]));
        sr.textContent = say(item);
        invalidate();
        return;
      }
      if ((e.key === "Enter" || e.key === " ") && focus) {
        const h = hits.find((x) => x.key === focus);
        if (h) { e.preventDefault(); onHover(null, { x: 0, y: 0 }); onPick(h.item()); }
        return;
      }
      if (e.key === "Escape" && focus) { lastFocus = focus; focus = null; keyboard = false; onHover(null, { x: 0, y: 0 }); invalidate(); }
    }
    function say(item) {
      if (!item) return "";
      if (item.type === "lesson") {
        const l = item.lesson || {};
        return fill(labels.lesson, { title: l.title || "", kind: l.kind || "", agent: l.agent || "" }) + (item.cleared ? ` (${labels.cleared})` : "");
      }
      if (item.type === "boss") return `${fill(labels.boss, { text: item.boss.text || "" })} (${labels[STATUS[item.boss.status] || "bossAsleep"]})`;
      if (item.type === "party") return labels.party;
      return "";
    }
    function onBlur() {
      wrap.classList.remove("adv-focus");
      if (keyboard) { focus = null; onHover(null, { x: 0, y: 0 }); }
      keyboard = false;
      invalidate();
    }
    // the map's focus ring is drawn over the canvas (adv-map::after), for keyboard focus only
    function onFocus() { let kb = true; try { kb = canvas.matches(":focus-visible"); } catch (err) { /* old browser */ } wrap.classList.toggle("adv-focus", kb); }

    // ---- the minimap: press or drag to move there
    let miniDrag = false;
    function miniAt(e) {
      const b = mini.getBoundingClientRect();
      return { x: ((e.clientX - b.left) / b.width) * L.width, y: ((e.clientY - b.top) / b.height) * L.height };
    }
    function onMiniDown(e) {
      e.preventDefault();
      miniDrag = true;
      try { mini.setPointerCapture(e.pointerId); } catch (err) { /* not capturable */ }
      const w = miniAt(e);
      flyTo(w.x, w.y, cam.z);
    }
    function onMiniMove(e) { if (miniDrag) { const w = miniAt(e); jumpTo(w.x, w.y, cam.z); } }
    function onMiniUp() { miniDrag = false; }

    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerup", onUp);
    canvas.addEventListener("pointercancel", onUp);
    canvas.addEventListener("pointerleave", onLeave);
    canvas.addEventListener("wheel", onWheel, { passive: false });
    canvas.addEventListener("dblclick", onDbl);
    canvas.addEventListener("keydown", onKey);
    canvas.addEventListener("blur", onBlur);
    canvas.addEventListener("focus", onFocus);
    mini.addEventListener("pointerdown", onMiniDown);
    mini.addEventListener("pointermove", onMiniMove);
    mini.addEventListener("pointerup", onMiniUp);
    mini.addEventListener("pointercancel", onMiniUp);

    // ---- the loop
    function invalidate() { dirty = true; schedule(); }
    const self = {
      step(now) {
        if (!alive) return false;
        if (!canvas.isConnected) { destroy(); return false; }
        if (!visible || document.hidden) { last = 0; return false; }
        const dt = last ? Math.min(0.1, (now - last) / 1000) : 0;
        last = now;
        if (goal) {
          const k = 1 - Math.exp(-dt * 8);
          cam.x += (goal.x - cam.x) * k;
          cam.y += (goal.y - cam.y) * k;
          cam.z = Math.exp(Math.log(cam.z) + (Math.log(goal.z) - Math.log(cam.z)) * k);
          if (Math.abs(goal.x - cam.x) * cam.z < 0.3 && Math.abs(goal.y - cam.y) * cam.z < 0.3 && Math.abs(goal.z - cam.z) < 0.002) { Object.assign(cam, goal); goal = null; }
          dirty = true;
        }
        if (!reduced) {
          animT += dt;
          const f = Math.floor(animT * 10);
          if (f !== frameNo) { frameNo = f; dirty = true; }
        }
        if (dirty) { dirty = false; render(); }
        return !!goal || !reduced || dirty;
      },
      wake() { last = 0; },
    };
    let io = null, ro = null, mo = null, scheme = null;
    const onScheme = () => retheme(false);
    function destroy() {
      if (!alive) return;
      alive = false;
      if (cancelPaint) { cancelPaint(); cancelPaint = null; }
      maps.delete(self);
      io && io.disconnect();
      ro && ro.disconnect();
      mo && mo.disconnect();
      scheme && scheme.removeEventListener && scheme.removeEventListener("change", onScheme);
      wrap.remove();
      art.clear();
      world = null;
    }
    measure();
    retheme(true);
    home();
    Object.assign(cam, goal || cam);
    goal = null;
    io = typeof IntersectionObserver === "function" ? new IntersectionObserver((entries) => {
      for (const en of entries) visible = en.isIntersecting;
      if (visible) { last = 0; invalidate(); }
    }) : null;
    io && io.observe(wrap);
    ro = typeof ResizeObserver === "function" ? new ResizeObserver(() => {
      if (!alive) return;
      if (wrap.clientWidth === cssW && (wrap.clientHeight || cssH) === cssH) return;
      measure();
      invalidate();
    }) : null;
    ro && ro.observe(wrap);
    mo = typeof MutationObserver === "function" ? new MutationObserver(onScheme) : null;
    mo && mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "class"] });
    scheme = typeof matchMedia === "function" ? matchMedia("(prefers-color-scheme: dark)") : null;
    scheme && scheme.addEventListener && scheme.addEventListener("change", onScheme);
    if (document.fonts && document.fonts.load) document.fonts.load(`600 11px ${FONT}`).then(() => invalidate(), () => {});
    maps.add(self);
    invalidate();

    return {
      destroy,
      focusLesson(id) {
        const e = encs.find((x) => String(x.id) === String(id));
        if (!e) return false;
        ping = { key: e.key, t0: performance.now() };
        flyTo(e.x, e.y - 8, Math.max(cam.z, 3));
        return true;
      },
      focusRegion(attrId) {
        const r = L.regions.find((x) => x.id === attrId);
        if (!r) return false;
        focusArea(r);
        return true;
      },
      setCleared(ids) {
        cleared = new Set((Array.isArray(ids) ? ids : []).map(String));
        recount();
        invalidate();
      },
      redraw() { retheme(true); },
    };
  }

  root.Adventure = Object.freeze({ mountMap, layout, LABELS });
})();
