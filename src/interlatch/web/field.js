/* Innerlatch's region fields: one region of the Adventure as a pixel field you walk around in, seen from above at an
   angle. The region's roadmap modules are its zones, cut apart by lines of trees and joined by dirt paths, with the
   region's town in the middle. Your party walks where you click or tap (or with the arrow keys and WASD), the leader
   first and the others following. In each zone a few of its uncleared lessons wander as monsters: walk into one and the
   page opens its battle; walk into the town's gate and it opens the town. The monsters are the lessons worth studying:
   each zone's open ones with study material first, never the agents' own friction (study.py), more of them in the
   main quest's zones. Open: where the person studies (data.studying), not yet understood (a win without a good answer
   leaves it out there) or back for another look (data.back: understood a while ago, first in its zone, marked ↻);
   elsewhere, not yet cleared. A lesson fought today (data.today) rests until tomorrow: trying again right after its
   answer was shown would be copying, not remembering. Each zone shows how many of its lessons the person understood,
   out of what makes it ready for its gate.

   The art is the Adventure's (adventure.js: Adventure.art, its biomes, props and town), the agents' (sprites.js) and the
   monsters' (scene.js: Scene.art): pixel maps, no images, styles only through the CSSOM, no eval. The ground is painted
   once into a canvas one pixel a world pixel; each frame draws the part on screen, scaled, then everything that stands
   (trees, rocks, houses, the town, signs, monsters, the party) back to front by where its feet are.

   window.Field = {
     mountField(host, opts) -> { destroy(), redraw() }
       opts.data           the /api/party/map payload
       opts.region         the region's attr id ("craft")
       opts.labels         overrides for LABELS below (the page passes Japanese)
       opts.reducedMotion  no wandering, no bobbing, no camera easing
       opts.start          {x, y}: where the party stands, in world pixels (back from a battle); the spawn otherwise
       opts.hero           {agent: Sprites.heroKey(look), short, level, tier}: the person's hero, who leads the party
                           (its companions follow); none: the agents alone
       opts.onEncounter(lesson)   the party walked into a monster (once; the page opens its battle); a job's own
                                  monster (data.lessons with `prep`, its id a string) wears ♜
       opts.onTown(region)        the party walked into the town's gate
       opts.onGate(zone)          the party walked into a zone's open gate (data.gates: {module id: rank}; gates.py)
       opts.onMove({x, y})        where the party stands now (now and then, for the page to keep)
       opts.onHover(item, at)     item = {type: "monster", lesson, back} | {type: "town", name} | {type: "sign", zone} | null
                                  (a sign with an open gate: zone.gate, the rank it gives);
                                  at = {x, y} in client pixels
     layout(data, regionId, seed = 1) -> the field, pure and deterministic (no DOM): see layout() below
     path(field, from, to) -> [[x, y], …] tiles from one walkable tile to another (A*), [] when there is no way
     LABELS                the English defaults
   } */
(() => {
  "use strict";

  /* Labels, English. {placeholders} are filled in.
       field       the field's accessible name: how to move and what walking into things does   {region}
       zoneLeft    under a zone's name: its lessons still to battle    {n}
       zoneClear   under a zone's name when all its lessons are cleared
       zoneFog     a zone with no lessons yet
       zoneYou     how many of the zone's lessons the person understood, of what makes it ready    {n} {of}
       zoneGoal    a zone of the main quest's track
       zoneGate    under a zone's name while its gate is open    {n}
       zoneJob     a zone the active job goal asks for (data.job.modules; jobs.py)
       level       a party member's level    {n}
       hint        how to move, in the corner
       empty       over the field when the region has no lessons at all */
  const LABELS = Object.freeze({
    field: "{region}: a field to explore. Click or tap where to walk, or use the arrow keys or WASD. Walk into a monster to battle it, or into the town's gate to shop.",
    zoneLeft: "{n} to battle here",
    zoneClear: "All cleared here",
    zoneFog: "Unexplored: no lessons here yet",
    zoneYou: "You understood {n}/{of}",
    zoneGoal: "Main quest",
    zoneGate: "Rank {n} gate open",
    zoneJob: "Job goal",
    level: "Lv {n}",
    hint: "Click to walk · arrow keys or WASD",
    empty: "No lessons in this region yet: nothing to battle here.",
  });

  const root = typeof window !== "undefined" ? window : globalThis;
  const fill = (tpl, vars) => String(tpl).replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const AGENT_ORDER = ["claude", "codex", "copilot", "bob", "antigravity", "opencode"];

  const T = 16;                 // world pixels a tile
  const ZONE_TILES = 240;       // tiles of field a zone
  const EDGE = 3;               // the forest band round the field, in tiles
  const PER_ZONE = [2, 4];      // monsters out in a zone at once (a main quest zone: more), its best uncleared lessons
  const PER_JOB = 5;            // in a zone the active job asks for: its own monsters with the lessons
  const CLEAR_R = 4;            // the clearing round a zone's sign, in tiles
  const TOWN_R = 5;             // the plaza round the town
  const TOWN_K = 3;             // the town's art, world pixels a pixel
  const SPEED = 5.2;            // the party, tiles a second
  const WANDER = 1.1, CHASE = 2.4, NOTICE = 3.5, TOUCH = 0.85, LEASH = 3; // the monsters, in tiles (a second)
  const TRAIL = 15;             // world pixels between party members

  // ground kinds
  const GRASS = 0, PATH = 1, WATER = 2, CLEAR = 3;

  // =====================================================================================
  // Pure helpers: seeded random numbers, hashes, value noise
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
  function strHash(str) {
    let h = 2166136261;
    for (const ch of String(str)) h = Math.imul(h ^ ch.charCodeAt(0), 16777619) >>> 0;
    return h >>> 0;
  }
  const hash2 = (x, y, s) => { let h = Math.imul(x | 0, 374761393) + Math.imul(y | 0, 668265263) + Math.imul(s | 0, 2147483647); h = Math.imul(h ^ (h >>> 13), 1274126177); return ((h ^ (h >>> 16)) >>> 0) / 4294967296; };
  // smooth value noise, 0..1, cells `size` wide
  function noise(x, y, size, s) {
    const fx = x / size, fy = y / size, x0 = Math.floor(fx), y0 = Math.floor(fy), u = fx - x0, v = fy - y0;
    const su = u * u * (3 - 2 * u), sv = v * v * (3 - 2 * v);
    const a = hash2(x0, y0, s), b = hash2(x0 + 1, y0, s), c = hash2(x0, y0 + 1, s), d = hash2(x0 + 1, y0 + 1, s);
    return a + (b - a) * su + (c - a) * sv + (a - b - c + d) * su * sv;
  }

  // =====================================================================================
  // The field, pure: zones, ground, what stands where, where the monsters live
  //   {cols, rows, ground: Uint8Array (GRASS/PATH/WATER/CLEAR), zoneOf: Int16Array (-1: the town's plaza and the
  //    band), block: Uint8Array (1: nothing walks there), zones: [{index, id, name, lessons, open, site, sign}],
  //    props: [{type, x, y, v}] (tiles, feet at the tile's bottom middle), town: {x, y, gate}, spawn: {x, y},
  //    monsters: [{id, zone, x, y}] (home tiles), region: {id, name, town}}
  // =====================================================================================
  function layout(data, regionId, seed = 1) {
    const regions = Array.isArray(data && data.regions) ? data.regions : [];
    const region = regions.find((r) => r.id === regionId) || regions[0] || { id: regionId, name: regionId, modules: [] };
    const mods = Array.isArray(region.modules) ? region.modules : [];
    const towns = (data && data.towns) || {};
    const town = (Array.isArray(towns) ? towns.find((t) => t.attr === region.id) : towns[region.id]) || null;
    const cleared = new Set((Array.isArray(data && data.cleared) ? data.cleared : []).map(String));
    const R = rng((strHash(region.id) ^ Math.imul(seed, 2654435761)) >>> 0);

    const nz = Math.max(1, mods.length);
    const area = Math.max(8, nz) * ZONE_TILES;
    const cols = Math.round(Math.sqrt(area * 4 / 3)) + 2 * EDGE, rows = Math.round(area / (cols - 2 * EDGE)) + 2 * EDGE;
    const N = cols * rows, at = (x, y) => y * cols + x, inside = (x, y) => x >= 0 && y >= 0 && x < cols && y < rows;
    const cx = Math.floor(cols / 2), cy = Math.floor(rows / 2);

    // the zones' sites: spread round the town (which keeps the middle), then relaxed so the zones come out even
    const sites = mods.map((m, i) => {
      const a = (i / nz) * Math.PI * 2 + R() * 0.8, d = 0.45 + R() * 0.4;
      return { x: cx + Math.cos(a) * d * (cols / 2 - EDGE - 3), y: cy + Math.sin(a) * d * (rows / 2 - EDGE - 3) };
    });
    const zoneOf = new Int16Array(N).fill(-1);
    const assign = () => {
      for (let y = EDGE; y < rows - EDGE; y++) for (let x = EDGE; x < cols - EDGE; x++) {
        if (Math.hypot(x - cx, y - cy) <= TOWN_R) { zoneOf[at(x, y)] = -1; continue; }
        let best = -1, bd = Infinity;
        sites.forEach((s, i) => { const d = (x - s.x) ** 2 + (y - s.y) ** 2; if (d < bd) { bd = d; best = i; } });
        zoneOf[at(x, y)] = best;
      }
    };
    for (let it = 0; it < 4; it++) {
      assign();
      const sum = sites.map(() => [0, 0, 0]);
      for (let y = 0; y < rows; y++) for (let x = 0; x < cols; x++) { const z = zoneOf[at(x, y)]; if (z >= 0) { sum[z][0] += x; sum[z][1] += y; sum[z][2]++; } }
      sites.forEach((s, i) => {
        if (!sum[i][2]) return;
        s.x = sum[i][0] / sum[i][2]; s.y = sum[i][1] / sum[i][2];
        const d = Math.hypot(s.x - cx, s.y - cy), min = TOWN_R + CLEAR_R + 3; // never on the plaza
        if (d < min) { s.x = cx + ((s.x - cx) / (d || 1)) * min; s.y = cy + ((s.y - cy) / (d || 1)) * min; }
        s.x = clamp(s.x, EDGE + CLEAR_R + 1, cols - EDGE - CLEAR_R - 2); s.y = clamp(s.y, EDGE + CLEAR_R + 2, rows - EDGE - CLEAR_R - 2);
      });
    }
    assign();
    sites.forEach((s) => { s.x = Math.round(s.x); s.y = Math.round(s.y); });

    const ground = new Uint8Array(N), block = new Uint8Array(N);
    // the plaza and the clearings
    const disc = (ox, oy, r, kind) => {
      for (let y = Math.floor(oy - r); y <= oy + r; y++) for (let x = Math.floor(ox - r); x <= ox + r; x++) {
        if (inside(x, y) && (x - ox) ** 2 + ((y - oy) * 1.15) ** 2 <= r * r && ground[at(x, y)] === GRASS) ground[at(x, y)] = kind;
      }
    };
    disc(cx, cy, TOWN_R, CLEAR);
    sites.forEach((s) => disc(s.x, s.y, CLEAR_R, CLEAR));
    // paths: the town and the sites joined in a tree (the shortest links first), plus a few loops
    const nodes = [{ x: cx, y: cy + 2 }, ...sites];
    const links = [], inTree = new Set([0]);
    while (inTree.size < nodes.length) {
      let best = null;
      for (const i of inTree) nodes.forEach((n, j) => {
        if (inTree.has(j)) return;
        const d = Math.hypot(n.x - nodes[i].x, n.y - nodes[i].y);
        if (!best || d < best.d) best = { i, j, d };
      });
      links.push(best);
      inTree.add(best.j);
    }
    const linked = new Set(links.map((l) => `${Math.min(l.i, l.j)}-${Math.max(l.i, l.j)}`));
    const extra = [];
    nodes.forEach((n, i) => nodes.forEach((m, j) => {
      if (j <= i || linked.has(`${i}-${j}`)) return;
      extra.push({ i, j, d: Math.hypot(n.x - m.x, n.y - m.y) });
    }));
    extra.sort((p, q) => p.d - q.d).slice(0, Math.max(1, Math.round(nz / 4))).forEach((l) => links.push(l));
    for (const l of links) {
      const a = nodes[l.i], b = nodes[l.j], len = Math.hypot(b.x - a.x, b.y - a.y), steps = Math.ceil(len * 3);
      const nx = -(b.y - a.y) / (len || 1), ny = (b.x - a.x) / (len || 1), amp = 1 + R() * 1.6, ph = R() * 6.28, waves = 1 + Math.floor(R() * 2);
      for (let k = 0; k <= steps; k++) {
        const u = k / steps, w = Math.sin(u * Math.PI) * Math.sin(u * Math.PI * waves + ph) * amp;
        const px = a.x + (b.x - a.x) * u + nx * w, py = a.y + (b.y - a.y) * u + ny * w;
        for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
          const x = Math.round(px + dx * 0.55), y = Math.round(py + dy * 0.55);
          if (inside(x, y) && x >= EDGE && y >= EDGE && x < cols - EDGE && y < rows - EDGE) ground[at(x, y)] = PATH;
        }
      }
    }
    // ponds: in a couple of zones, off the paths and clearings
    const ponds = Math.max(1, Math.round(nz / 5));
    for (let p = 0, tries = 0; p < ponds && tries < 200; tries++) {
      const z = Math.floor(R() * nz), s = sites[z];
      if (!s) break;
      const a = R() * 6.28, d = 4 + R() * 3, px = Math.round(s.x + Math.cos(a) * d), py = Math.round(s.y + Math.sin(a) * d * 0.8);
      const rx = 1.8 + R() * 1.6, ry = 1.3 + R() * 1;
      let ok = true;
      for (let y = Math.floor(py - ry - 1); y <= py + ry + 1 && ok; y++) for (let x = Math.floor(px - rx - 1); x <= px + rx + 1; x++) {
        if (!inside(x, y) || x < EDGE + 1 || y < EDGE + 1 || x >= cols - EDGE - 1 || y >= rows - EDGE - 1 || ground[at(x, y)] !== GRASS) { ok = false; break; }
      }
      if (!ok) continue;
      for (let y = Math.floor(py - ry); y <= py + ry; y++) for (let x = Math.floor(px - rx); x <= px + rx; x++) {
        if (((x - px) / rx) ** 2 + ((y - py) / ry) ** 2 <= 1) { ground[at(x, y)] = WATER; block[at(x, y)] = 1; }
      }
      p++;
    }

    // what stands: the town, its houses, a sign in each clearing, then trees, bushes and rocks
    const props = [];
    const free = (x, y) => inside(x, y) && !block[at(x, y)] && ground[at(x, y)] === GRASS;
    const stand = (type, x, y, v = 0) => { props.push({ type, x, y, v }); block[at(x, y)] = 1; };
    const townSpot = { x: cx, y: cy - 1 };
    for (let y = townSpot.y - 1; y <= townSpot.y; y++) for (let x = townSpot.x - 3; x <= townSpot.x + 2; x++) block[at(x, y)] = 1;
    const gate = { x: cx, y: cy + 1 };
    sites.forEach((s) => { // the zone's sign stands in its clearing, the nearest spot to its heart off the paths
      for (let r = 1; r <= CLEAR_R && !s.sign; r++) {
        for (const [dx, dy] of [[r, -r], [-r, -r], [r, r], [-r, r], [r, 0], [-r, 0], [0, -r], [0, r]]) {
          const x = s.x + dx, y = s.y + dy;
          if (inside(x, y) && ground[at(x, y)] === CLEAR && !block[at(x, y)] && zoneOf[at(x, y)] === sites.indexOf(s)) {
            stand("sign", x, y); s.sign = { x, y }; break;
          }
        }
      }
      if (!s.sign) s.sign = { x: s.x, y: s.y };
    });
    [[-4, 3], [4, 3], [-4, -3], [4, -2]].forEach(([dx, dy], k) => {
      const x = cx + dx, y = cy + dy;
      if (inside(x, y) && ground[at(x, y)] !== PATH && !block[at(x, y)]) { stand("house", x, y, k); if (inside(x - 1, y)) block[at(x - 1, y)] = 1; }
    });
    const near = (x, y, d) => { // the distance to another zone's tile (up to d), or d + 1
      const z = zoneOf[at(x, y)];
      for (let r = 1; r <= d; r++) for (let dy = -r; dy <= r; dy++) for (let dx = -r; dx <= r; dx++) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r || !inside(x + dx, y + dy)) continue;
        const o = zoneOf[at(x + dx, y + dy)];
        if (o !== z && o >= 0 && z >= 0) return r;
      }
      return d + 1;
    };
    for (let y = 0; y < rows; y++) for (let x = 0; x < cols; x++) {
      if (!free(x, y)) continue;
      const band = Math.min(x, y, cols - 1 - x, rows - 1 - y);
      const h = hash2(x, y, 11), clump = noise(x, y, 5, 7);
      let p;
      if (band < EDGE) p = band < EDGE - 1 ? 0.97 : 0.75;
      else {
        const b = near(x, y, 2);
        p = b === 1 ? 0.44 : b === 2 ? 0.12 : 0.025; // a line of trees where zones meet; groves here and there
        if (clump > 0.74) p += 0.38;
        if (Math.hypot(x - cx, y - cy) < TOWN_R + 2) p *= 0.3;
      }
      if (h < p) stand(hash2(x, y, 3) < 0.26 ? "pine" : "tree", x, y, Math.floor(hash2(x, y, 5) * 3));
      else if (band >= EDGE && h < p + 0.025) stand("bush", x, y, Math.floor(hash2(x, y, 9) * 2));
      else if (band >= EDGE && h < p + 0.035) stand("rock", x, y);
    }
    // whatever the party can't reach from its spawn becomes forest too: every open tile is reachable
    const spawn = { x: gate.x, y: gate.y + 2 };
    block[at(spawn.x, spawn.y)] = 0;
    const seen = new Uint8Array(N), queue = [at(spawn.x, spawn.y)];
    seen[queue[0]] = 1;
    while (queue.length) {
      const i = queue.pop(), x = i % cols, y = (i / cols) | 0;
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const nx = x + dx, ny = y + dy;
        if (!inside(nx, ny)) continue;
        const j = at(nx, ny);
        if (!seen[j] && !block[j]) { seen[j] = 1; queue.push(j); }
      }
    }
    for (let i = 0; i < N; i++) {
      if (block[i] || seen[i]) continue;
      const x = i % cols, y = (i / cols) | 0;
      if (ground[i] === WATER) continue;
      stand("tree", x, y, Math.floor(hash2(x, y, 5) * 3));
    }

    // the zones, with their lessons; the monsters: each zone's newest uncleared lessons, at home somewhere it can be
    // reached, off the paths, not too near the spawn or each other
    const zoneIndex = new Map(mods.map((m, i) => [m.id, i]));
    const byZone = mods.map(() => []);
    for (const l of Array.isArray(data && data.lessons) ? data.lessons : []) {
      const z = zoneIndex.get((l.modules || [])[0]);
      if (z !== undefined) byZone[z].push(l);
    }
    const goal = new Set((data && data.goal && Array.isArray(data.goal.modules)) ? data.goal.modules : []);
    const job = new Set((data && data.job && Array.isArray(data.job.modules)) ? data.job.modules : []);
    const ready = (data && data.readiness) || {};
    const known = new Set((Array.isArray(data && data.understood) ? data.understood : []).map(String));
    const back = new Set((Array.isArray(data && data.back) ? data.back : []).map(String));
    const today = new Set((Array.isArray(data && data.today) ? data.today : []).map(String));
    const studyable = (l) => !l.friction && !today.has(String(l.id))
      && (data && data.studying ? !known.has(String(l.id)) || back.has(String(l.id)) : !cleared.has(String(l.id)));
    const zones = mods.map((m, i) => ({ index: i, id: m.id, name: m.name || m.id, lessons: byZone[i].length,
      open: byZone[i].filter(studyable).length, goal: goal.has(m.id), job: job.has(m.id), you: Number(ready[m.id]) || 0,
      gate: Math.max(0, Math.round(Number(((data && data.gates) || {})[m.id]) || 0)),
      site: { x: sites[i].x, y: sites[i].y }, sign: sites[i].sign }));
    const monsters = [];
    zones.forEach((z) => {
      const homes = [];
      for (let y = 0; y < rows; y++) for (let x = 0; x < cols; x++) {
        const i = at(x, y);
        if (zoneOf[i] !== z.index || block[i] || !seen[i] || ground[i] === PATH) continue;
        const d = Math.hypot(x - z.site.x, y - z.site.y);
        if (d < 2 || d > 9 || Math.hypot(x - spawn.x, y - spawn.y) < 6) continue;
        homes.push({ x, y });
      }
      // those back for another look first, then the lessons worth studying (study material), the newest first; the
      // agents' friction never
      // (and the job's own monsters, data.lessons with `prep`: jobprep.py, right after those coming back)
      const rank = (l) => (back.has(String(l.id)) ? 3 : 0) + (l.prep ? 2 : 0) + (l.study ? 1 : 0);
      const newer = (a, b) => (typeof a.id === "number" && typeof b.id === "number" ? b.id - a.id : String(a.id).localeCompare(String(b.id)));
      const want = byZone[z.index].filter(studyable).sort((a, b) => rank(b) - rank(a) || newer(a, b))
        .slice(0, z.job ? PER_JOB : PER_ZONE[z.goal ? 1 : 0]);
      const taken = [];
      for (const l of want) {
        const start = strHash("home" + l.id) % Math.max(1, homes.length);
        for (let k = 0; k < homes.length; k++) {
          const h = homes[(start + k * 7) % homes.length];
          if (taken.every((t) => Math.hypot(t.x - h.x, t.y - h.y) >= 2.5)) {
            taken.push(h);
            monsters.push({ id: l.id, hid: typeof l.id === "number" ? l.id : strHash(l.id), zone: z.index, x: h.x, y: h.y,
              back: back.has(String(l.id)), prep: !!l.prep });
            break;
          }
        }
      }
    });
    props.sort((a, b) => a.y - b.y || a.x - b.x);
    return { cols, rows, ground, zoneOf, block, reach: seen, zones, props, monsters, spawn, gate,
      town: { x: townSpot.x, y: townSpot.y, gate, name: (town && town.name) || region.name },
      region: { id: region.id, name: region.name || region.id } };
  }

  // A* over the walkable tiles, eight ways (no corner cutting); [] when `to` can't be reached
  function path(F, from, to) {
    const { cols, rows, block } = F, at = (x, y) => y * cols + x;
    const ok = (x, y) => x >= 0 && y >= 0 && x < cols && y < rows && !block[at(x, y)];
    if (!ok(from.x, from.y) || !ok(to.x, to.y)) return [];
    const start = at(from.x, from.y), goal = at(to.x, to.y);
    const g = new Float64Array(cols * rows).fill(Infinity), came = new Int32Array(cols * rows).fill(-1);
    const h = (i) => { const x = i % cols, y = (i / cols) | 0, dx = Math.abs(x - to.x), dy = Math.abs(y - to.y); return Math.max(dx, dy) + 0.414 * Math.min(dx, dy); };
    const open = [[h(start), start]];
    g[start] = 0;
    const done = new Uint8Array(cols * rows);
    while (open.length) {
      let bi = 0;
      for (let k = 1; k < open.length; k++) if (open[k][0] < open[bi][0]) bi = k;
      const [, i] = open[bi];
      open[bi] = open[open.length - 1];
      open.pop();
      if (done[i]) continue;
      done[i] = 1;
      if (i === goal) break;
      const x = i % cols, y = (i / cols) | 0;
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        if (!dx && !dy) continue;
        const nx = x + dx, ny = y + dy;
        if (!ok(nx, ny) || (dx && dy && (!ok(x + dx, y) || !ok(x, y + dy)))) continue;
        const j = at(nx, ny), cost = g[i] + (dx && dy ? 1.414 : 1);
        if (cost < g[j]) { g[j] = cost; came[j] = i; open.push([cost + h(j), j]); }
      }
    }
    if (!Number.isFinite(g[goal])) return [];
    const out = [];
    for (let i = goal; i !== -1; i = came[i]) out.push([i % cols, (i / cols) | 0]);
    return out.reverse();
  }

  // =====================================================================================
  // Art: the field's own props in the Cast's style, the rest from adventure.js, sprites.js and scene.js
  // =====================================================================================
  const FIELD_ART = {
    tree: { k: 2, rows: [
      "......LLLLLL........", "....LLLLLLLLLL......", "...LLLLLLLLLLLll....", "..LLLLLLLLLLLLlll...", ".LLLLLLLLLLLLllllll.",
      ".LLLLLLLLLLLllllll..", "LLLLLLLLLLllllllllll", "LLLLLLLLLlllllllllll", "lLLLLLLllllllllllllL", "llllllllllllllllllll",
      ".lllllllllllllllllll", ".llllllllllllllllll.", "..llllllllllllllll..", "....lllllltllllll...", ".......tttt.........",
      ".......tttt.........", "......tttttt........"], pal: { L: "#69b04f", l: "#4c9445", t: "#7a5232" } },
    sign: { k: 2, rows: ["wwwwwwww", "wkkwwkkw", "wwwwwwww", "...pp...", "...pp...", "...pp..."], pal: { w: "#c9a06a", k: "#8a6038", p: "#7a5232" } },
    // a zone's open gate, where its sign stood: a stone arch with a gold light inside (gates.py)
    gate: { k: 2, rows: ["....ssssss....", "..ssssssssss..", ".sssggggggsss.", ".ssgg....ggss.", "ssg........gss",
      "ssg........gss", "ss..........ss", "ss..........ss", "ss..........ss", "ss..........ss", "SS..........SS",
      "SS..........SS"], pal: { s: "#a7a2b6", S: "#7d7891", g: "=#ffd66b" } },
  };
  const LEAVES = { craft: ["#69b04f", "#4c9445"], data: ["#6fbf86", "#4f9a6a"], ops: ["#6a9a6c", "#4a7a52"],
    guard: ["#8aa65a", "#6a8a44"], arcana: ["#9a86d8", "#7a64c0"], lore: ["#e0a040", "#c8702e"] };
  const GRASS_TONES = { craft: ["#78b356", "#6da84f", "#86bf62", "#8fc86a"], data: ["#7cc28c", "#6db57d", "#8acd98", "#94d4a0"],
    ops: ["#a8a596", "#9c998a", "#b4b1a2", "#bdbaa9"], guard: ["#c8aa78", "#bc9e6c", "#d3b584", "#dcbf8e"],
    arcana: ["#a898d4", "#9a8ac8", "#b4a6dc", "#bdb0e2"], lore: ["#8aa868", "#7e9c5c", "#96b474", "#a0bc7e"] };
  const DIRT = ["#c9a26c", "#bd9561", "#d4b07c", "#a77d4f"];
  const WATERS = ["#4a86c8", "#5a98d8", "#8cc8ec", "#d6eef8"];

  // =====================================================================================
  // The screen
  // =====================================================================================
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function blank(w, h) {
    const c = document.createElement("canvas");
    c.width = Math.max(1, Math.ceil(w));
    c.height = Math.max(1, Math.ceil(h));
    return c;
  }
  const hexRgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));

  function mountField(host, opts = {}) {
    const labels = { ...LABELS, ...(opts.labels || {}) };
    const lab = (k, vars) => fill(labels[k] != null ? labels[k] : LABELS[k], vars || {});
    const call = (k, ...args) => { if (typeof opts[k] === "function") { try { return opts[k](...args); } catch (e) { setTimeout(() => { throw e; }); } } return undefined; };
    const reduced = !!opts.reducedMotion;
    const data = opts.data || {};
    const F = layout(data, opts.region, 1);
    const gateAt = new Set(F.zones.filter((z) => z.gate).map((z) => `${z.sign.x},${z.sign.y}`)); // signs that are gates now
    const A = root.Adventure && root.Adventure.art, SP = root.Sprites, SA = root.Scene && root.Scene.art;
    const bio = (A && A.BIOMES[F.region.id]) || { hue: "#3a7be0", deco: [] };
    const lessonOf = new Map((Array.isArray(data.lessons) ? data.lessons : []).map((l) => [String(l.id), l]));
    const agents = (Array.isArray(data.agents) ? data.agents : []).slice()
      .sort((a, b) => (b.level || 0) - (a.level || 0) || AGENT_ORDER.indexOf(a.agent) - AGENT_ORDER.indexOf(b.agent));
    const lead0 = opts.hero && opts.hero.agent ? [opts.hero] : []; // you first, then your strongest companions
    const team = [...lead0, ...(agents.filter((a) => (a.level || 0) > 0).length ? agents.filter((a) => (a.level || 0) > 0) : agents)].slice(0, 4);
    if (!team.length) team.push({ agent: "claude", tier: "novice", level: 1, short: "Claude" });

    // ---- the DOM: the canvas and the HUD over it (the party, the zone, the minimap, the hint)
    const wrap = el("div", "fd");
    const canvas = el("canvas", "fd-canvas");
    canvas.tabIndex = 0;
    canvas.setAttribute("role", "application");
    canvas.setAttribute("aria-label", lab("field", { region: F.region.name }));
    const hudParty = el("div", "fd-party");
    for (const a of team) {
      const row = el("div", "fd-member"), pic = blank(32, 32);
      pic.className = "fd-face";
      pic.setAttribute("aria-hidden", "true");
      try { if (SP) SP.draw(pic, { agent: a.agent, tier: a.tier || "novice", frame: 0, scale: 1 }); } catch (e) { /* initials below */ }
      const txt = el("div", "fd-member-txt");
      txt.append(el("b", null, a.short || a.name || a.agent), el("span", null, lab("level", { n: a.level || 0 })));
      row.append(pic, txt);
      hudParty.append(row);
    }
    const hudZone = el("div", "fd-zone");
    hudZone.setAttribute("aria-live", "polite");
    const zoneName = el("b"), zoneSub = el("span");
    hudZone.append(zoneName, zoneSub);
    const mini = el("canvas", "fd-mini");
    mini.setAttribute("aria-hidden", "true");
    const hint = el("div", "fd-hint", lab("hint"));
    wrap.append(canvas, hudParty, hudZone, mini, hint);
    if (!F.zones.some((z) => z.lessons)) wrap.append(el("div", "fd-empty", lab("empty")));
    host.appendChild(wrap);
    const ctx = canvas.getContext("2d"), mctx = mini.getContext("2d");

    // ---- art caches
    const art = new Map();
    function shaded(key, rows, pal, k) {
      if (!art.has(key)) {
        let cv = null;
        try { if (A) cv = A.paintArt(rows, pal, false, k).cv; } catch (e) { cv = null; }
        if (!cv) { // flat, without the Cast's shading
          const w = Math.max(...rows.map((r) => r.length)), c = blank(w * k, rows.length * k), g = c.getContext("2d");
          rows.forEach((row, y) => { for (let x = 0; x < row.length; x++) { const v = pal[row[x]]; if (v) { g.fillStyle = v.replace(/^[*=]/, ""); g.fillRect(x * k, y * k, k, k); } } });
          cv = c;
        }
        art.set(key, cv);
      }
      return art.get(key);
    }
    function propImg(p) {
      if (p.type === "tree") {
        const [L, l] = F.region.id === "lore" && p.v === 2 ? ["#d0603a", "#a84a2e"] : LEAVES[F.region.id] || LEAVES.craft;
        return shaded(`tree|${p.v}`, FIELD_ART.tree.rows, { ...FIELD_ART.tree.pal, L: p.v === 1 ? mixHex(L, "#ffffff", 0.08) : L, l }, FIELD_ART.tree.k);
      }
      if (p.type === "sign" && gateAt.has(`${p.x},${p.y}`)) return shaded("gate", FIELD_ART.gate.rows, FIELD_ART.gate.pal, FIELD_ART.gate.k);
      if (p.type === "sign") return shaded("sign", FIELD_ART.sign.rows, FIELD_ART.sign.pal, FIELD_ART.sign.k);
      if (A && A.DECO[p.type]) {
        const d = A.decoVariant(p.type === "house" ? "house" : p.type, bio, p.v);
        const k = p.type === "house" ? 4 : p.type === "pine" ? 3 : 3;
        return shaded(`${p.type}|${p.v}`, d.rows, d.pal, k);
      }
      return shaded("rock", [".rr..", "rrrr.", "rrrrr"], { r: "#9b968d" }, 3);
    }
    function townImg() {
      if (!art.has("town")) {
        let cv = null;
        try { if (A) cv = A.paintArt(A.TOWN_ROWS, A.townPal(F.region, bio), false, TOWN_K).cv; } catch (e) { cv = null; }
        art.set("town", cv || shaded("town-flat", ["wwwwwww", "wwdddww", "wwdddww"], { w: "#efe3c8", d: "#6a4428" }, 8));
      }
      return art.get("town");
    }
    function agentImg(agent, tier, frame) {
      const key = `a|${agent}|${tier}|${frame}`;
      if (!art.has(key)) {
        const cv = blank(32, 32);
        try { if (SP) SP.draw(cv, { agent, tier, frame, scale: 1 }); else throw new Error("no sprites"); } catch (e) {
          const g = cv.getContext("2d");
          g.fillStyle = "#ffb45e"; g.fillRect(10, 8, 12, 20);
          g.fillStyle = "#1b1d33"; g.fillRect(13, 13, 2, 2); g.fillRect(18, 13, 2, 2);
        }
        art.set(key, cv);
      }
      return art.get(key);
    }
    function monsterImg(kind, stage, frame) {
      const key = `m|${kind}|${stage}|${frame}`;
      if (!art.has(key)) {
        let cv = null;
        try { if (SA) cv = SA.monster(SA.kinds[kind] || "glitch", stage, frame); } catch (e) { cv = null; }
        art.set(key, cv || shaded("blob", ["..cccc..", ".cccccc.", "cwkccwkc", "cccccccc", "cccccccc", ".cccccc."], { c: "#e5484d", w: "=#ffffff", k: "=#1b1d33" }, 2));
      }
      return art.get(key);
    }
    function mixHex(a, b, u) { const x = hexRgb(a), y = hexRgb(b); return "#" + x.map((v, i) => Math.round(v + (y[i] - v) * u).toString(16).padStart(2, "0")).join(""); }

    // ---- the ground, painted once (one canvas pixel a world pixel): grass in patches, dirt paths with soft edges,
    // ponds with a bright rim, flowers and tufts
    const W = F.cols * T, H = F.rows * T;
    let groundCv = null;
    function paintGround() {
      const cv = blank(W, H), g = cv.getContext("2d"), img = g.createImageData(W, H), px = img.data;
      const grass = (GRASS_TONES[F.region.id] || GRASS_TONES.craft).map(hexRgb), dirt = DIRT.map(hexRgb), water = WATERS.map(hexRgb);
      const kindAt = (x, y) => { const tx = Math.floor(x / T), ty = Math.floor(y / T); return tx < 0 || ty < 0 || tx >= F.cols || ty >= F.rows ? GRASS : F.ground[ty * F.cols + tx]; };
      for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
        const k = kindAt(x, y), o = (y * W + x) * 4;
        let c;
        if (k === WATER) {
          let rim = false; // two pixels of light where the pond meets the bank
          for (const [dx, dy] of [[2, 0], [-2, 0], [0, 2], [0, -2]]) if (kindAt(x + dx, y + dy) !== WATER) rim = true;
          c = rim ? water[2] : noise(x, y, 6, 3) > 0.62 ? water[1] : water[0];
          if (!rim && hash2(x >> 1, y >> 1, 4) > 0.985) c = water[3];
        } else if (k === PATH) {
          let edge = 0;
          for (const [dx, dy] of [[3, 0], [-3, 0], [0, 3], [0, -3]]) if (kindAt(x + dx, y + dy) !== PATH) edge++;
          const n = noise(x, y, 4, 9), h = hash2(x >> 1, y >> 1, 6);
          c = edge && hash2(x, y, 8) < 0.55 ? (kindAt(x + 3, y) === WATER ? dirt[3] : grass[0]) : h > 0.93 ? dirt[3] : n > 0.6 ? dirt[2] : n < 0.35 ? dirt[1] : dirt[0];
        } else {
          const n = noise(x, y, 18, 1), m = noise(x, y, 5, 2), h = hash2(x >> 1, y >> 1, 3);
          c = n > 0.62 ? (m > 0.5 ? grass[3] : grass[2]) : n < 0.35 ? grass[1] : m > 0.66 ? grass[2] : grass[0];
          if (h > 0.97) c = grass[1];
          if (k === CLEAR && noise(x, y, 9, 5) > 0.7) c = grass[3];
        }
        px[o] = c[0]; px[o + 1] = c[1]; px[o + 2] = c[2]; px[o + 3] = 255;
      }
      g.putImageData(img, 0, 0);
      // tufts and flowers on open grass
      const flower = bio.flower || "#ffd66b";
      for (let ty = 0; ty < F.rows; ty++) for (let tx = 0; tx < F.cols; tx++) {
        const i = ty * F.cols + tx;
        if (F.ground[i] !== GRASS && F.ground[i] !== CLEAR) continue;
        if (F.block[i]) continue;
        const h = hash2(tx, ty, 21);
        const bx = tx * T + 3 + Math.floor(hash2(tx, ty, 22) * 9), by = ty * T + 4 + Math.floor(hash2(tx, ty, 23) * 9);
        if (h < 0.22) { // a tuft
          g.fillStyle = mixHex((GRASS_TONES[F.region.id] || GRASS_TONES.craft)[1], "#1c3a20", 0.25);
          g.fillRect(bx, by, 1, 2); g.fillRect(bx + 2, by - 1, 1, 3); g.fillRect(bx + 4, by, 1, 2);
        } else if (h < 0.3) { // flowers
          g.fillStyle = flower; g.fillRect(bx, by, 2, 2); g.fillRect(bx + 4, by + 3, 2, 2);
          g.fillStyle = "#ffffff"; g.fillRect(bx + 1, by + 1, 1, 1);
        }
      }
      groundCv = cv;
    }

    // ---- the minimap: the field in a corner, a pixel a tile, then the party, the monsters and the view
    let miniBase = null;
    function paintMini() {
      const cv = blank(F.cols, F.rows), g = cv.getContext("2d");
      const grass = (GRASS_TONES[F.region.id] || GRASS_TONES.craft)[0], leaf = (LEAVES[F.region.id] || LEAVES.craft)[1];
      for (let y = 0; y < F.rows; y++) for (let x = 0; x < F.cols; x++) {
        const i = y * F.cols + x, k = F.ground[i];
        g.fillStyle = k === WATER ? WATERS[1] : k === PATH ? DIRT[2] : F.block[i] ? leaf : grass;
        g.fillRect(x, y, 1, 1);
      }
      g.fillStyle = "#f6e3b0";
      g.fillRect(F.town.x - 3, F.town.y - 2, 6, 3);
      miniBase = cv;
    }

    // ---- the party and the monsters
    const lead = { x: 0, y: 0, face: 1, moving: false };
    const startTile = opts.start && Number.isFinite(opts.start.x) && Number.isFinite(opts.start.y) ? opts.start : null;
    const spawnAt = (x, y) => { lead.x = x; lead.y = y; };
    if (startTile && walkablePx(startTile.x, startTile.y)) spawnAt(startTile.x, startTile.y);
    else spawnAt(F.spawn.x * T + T / 2, F.spawn.y * T + T / 2);
    const startPos = { x: lead.x, y: lead.y };
    let armed = false; // touching starts nothing until the party has moved a little (back from a battle, by the monster)
    // the others start in a line behind the leader (down the screen), as if they had just walked up
    const trail = [];
    for (let k = TRAIL * team.length; k > 0; k--) trail.push({ x: lead.x, y: lead.y + k });
    trail.push({ x: lead.x, y: lead.y });
    const members = team.map((a, i) => ({ a, x: lead.x, y: lead.y + TRAIL * i, face: 1 }));
    const monsters = F.monsters.map((m) => {
      const l = lessonOf.get(String(m.id)) || { id: m.id, kind: "fix", stage: "provisional", title: "" };
      return { ...m, lesson: l, px: m.x * T + T / 2, py: m.y * T + T / 2, tx: m.x * T + T / 2, ty: m.y * T + T / 2,
        wait: hash2(m.x, m.y, 30) * 2, face: -1, ph: hash2(m.x, m.y, 31), alert: false };
    });
    let route = [], target = null, hover = null, busy = false;
    const keys = new Set();

    function walkablePx(x, y) {
      const tx = Math.floor(x / T), ty = Math.floor(y / T);
      return tx >= 0 && ty >= 0 && tx < F.cols && ty < F.rows && !F.block[ty * F.cols + tx];
    }
    function tileOf(x, y) { return { x: clamp(Math.floor(x / T), 0, F.cols - 1), y: clamp(Math.floor(y / T), 0, F.rows - 1) }; }
    // the nearest walkable tile to one (a click on a tree walks to the tree)
    function nearestOpen(t) {
      for (let r = 0; r < 6; r++) for (let dy = -r; dy <= r; dy++) for (let dx = -r; dx <= r; dx++) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
        const x = t.x + dx, y = t.y + dy;
        if (x >= 0 && y >= 0 && x < F.cols && y < F.rows && !F.block[y * F.cols + x]) return { x, y };
      }
      return null;
    }
    function walkTo(tile) {
      const to = nearestOpen(tile);
      if (!to) return;
      const steps = path(F, tileOf(lead.x, lead.y), to);
      route = steps.slice(1).map(([x, y]) => ({ x: x * T + T / 2, y: y * T + T / 2 }));
      target = route.length ? to : null;
    }

    // ---- the camera: on the leader, inside the field; an integer number of device pixels a world pixel
    let cssW = 640, cssH = 480, dpr = 1, S = 4;
    const cam = { x: lead.x, y: lead.y };
    function measure() {
      dpr = root.devicePixelRatio || 1;
      cssW = Math.max(240, wrap.clientWidth || host.clientWidth || 640);
      cssH = Math.max(240, wrap.clientHeight || host.clientHeight || 480);
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
      canvas.style.width = cssW + "px";
      canvas.style.height = cssH + "px";
      S = clamp(Math.round(Math.min(canvas.height / (T * 15), canvas.width / (T * 17))), 2, 8);
      const mw = Math.round(clamp(cssW * 0.2, 96, 170));
      mini.width = Math.round(mw * dpr);
      mini.height = Math.round(mw * (F.rows / F.cols) * dpr);
      mini.style.width = mw + "px";
      mini.style.height = Math.round(mw * (F.rows / F.cols)) + "px";
    }
    function clampCam() {
      const hw = canvas.width / (2 * S), hh = canvas.height / (2 * S);
      cam.x = W <= 2 * hw ? W / 2 : clamp(cam.x, hw, W - hw);
      cam.y = H <= 2 * hh ? H / 2 : clamp(cam.y, hh, H - hh);
    }
    let ox = 0, oy = 0;
    const X = (wx) => Math.round(ox + wx * S), Y = (wy) => Math.round(oy + wy * S);
    function put(cv, wx, wy, k = 1, flip = false) { // feet at (wx, wy)
      const w = cv.width * k * S, h = cv.height * k * S, x0 = X(wx) - Math.round(w / 2), y0 = Y(wy) - Math.round(h);
      if (flip) { ctx.save(); ctx.translate(x0 + w, y0); ctx.scale(-1, 1); ctx.drawImage(cv, 0, 0, w, h); ctx.restore(); }
      else ctx.drawImage(cv, x0, y0, w, h);
    }
    function shadow(wx, wy, rx) {
      ctx.fillStyle = "rgba(24, 40, 30, 0.28)";
      const r = Math.max(1, Math.round(rx * S)), h = Math.max(1, Math.round(rx * S * 0.4));
      ctx.beginPath();
      ctx.ellipse(X(wx), Y(wy), r, h, 0, 0, Math.PI * 2);
      ctx.fill();
    }

    // ---- each frame: move, then draw
    let last = 0, t = 0, raf = 0, alive = true, visible = true, lastSaved = 0;
    function step(dt) {
      // the leader: held keys, else the route
      let vx = 0, vy = 0;
      if (keys.has("ArrowLeft") || keys.has("a")) vx -= 1;
      if (keys.has("ArrowRight") || keys.has("d")) vx += 1;
      if (keys.has("ArrowUp") || keys.has("w")) vy -= 1;
      if (keys.has("ArrowDown") || keys.has("s")) vy += 1;
      const sp = SPEED * T * dt;
      lead.moving = false;
      if (vx || vy) {
        route = []; target = null;
        const n = Math.hypot(vx, vy), mx = (vx / n) * sp, my = (vy / n) * sp;
        if (walkablePx(lead.x + mx + Math.sign(mx) * 4, lead.y)) lead.x += mx; // slide along what's in the way
        if (walkablePx(lead.x, lead.y + my + Math.sign(my) * 2)) lead.y += my;
        if (mx) lead.face = Math.sign(mx);
        lead.moving = true;
      } else if (route.length) {
        let left = sp;
        while (left > 0 && route.length) {
          const p = route[0], dx = p.x - lead.x, dy = p.y - lead.y, d = Math.hypot(dx, dy);
          if (d <= left) { lead.x = p.x; lead.y = p.y; route.shift(); left -= d; } else { lead.x += (dx / d) * left; lead.y += (dy / d) * left; left = 0; }
          if (Math.abs(dx) > 0.5) lead.face = Math.sign(dx);
        }
        lead.moving = true;
        if (!route.length) target = null;
      }
      if (!armed && Math.hypot(lead.x - startPos.x, lead.y - startPos.y) > T * 1.2) armed = true;
      // the others follow along the leader's trail
      const head = trail[trail.length - 1];
      if (Math.hypot(lead.x - head.x, lead.y - head.y) >= 1) { trail.push({ x: lead.x, y: lead.y }); if (trail.length > 400) trail.shift(); }
      members[0].x = lead.x; members[0].y = lead.y; members[0].face = lead.face;
      for (let i = 1; i < members.length; i++) {
        let want = TRAIL * i, k = trail.length - 1, px = lead.x, py = lead.y;
        while (k > 0 && want > 0) {
          const a = trail[k], b = trail[k - 1], d = Math.hypot(a.x - b.x, a.y - b.y);
          if (d >= want) { px = a.x + ((b.x - a.x) * want) / d; py = a.y + ((b.y - a.y) * want) / d; want = 0; break; }
          want -= d; k--; px = b.x; py = b.y;
        }
        const m = members[i];
        if (Math.abs(px - m.x) > 0.3) m.face = Math.sign(px - m.x);
        m.x = px; m.y = py;
      }
      // the monsters: wander near home, come at the party when it's close, start the battle on touch
      for (const m of monsters) {
        const d = Math.hypot(lead.x - m.px, lead.y - m.py);
        m.alert = d < NOTICE * T;
        if (armed && !busy && d < TOUCH * T) {
          busy = true; route = []; target = null; keys.clear();
          call("onEncounter", m.lesson);
          return;
        }
        if (reduced) continue;
        const home = { x: m.x * T + T / 2, y: m.y * T + T / 2 };
        let goal = null, speed = WANDER * T;
        if (m.alert && Math.hypot(lead.x - home.x, lead.y - home.y) < (LEASH + NOTICE) * T) { goal = { x: lead.x, y: lead.y }; speed = CHASE * T; }
        else {
          if (m.wait > 0) { m.wait -= dt; continue; }
          if (Math.hypot(m.tx - m.px, m.ty - m.py) < 1) { // a new spot near home
            const a = hash2(Math.floor(t * 10), m.hid, 33) * 6.28, r = hash2(m.hid, Math.floor(t * 10), 34) * LEASH * T;
            const nx = home.x + Math.cos(a) * r, ny = home.y + Math.sin(a) * r * 0.7;
            if (walkablePx(nx, ny)) { m.tx = nx; m.ty = ny; }
            m.wait = 0.8 + hash2(m.hid, Math.floor(t), 35) * 2.2;
            continue;
          }
          goal = { x: m.tx, y: m.ty };
        }
        const dx = goal.x - m.px, dy = goal.y - m.py, dd = Math.hypot(dx, dy);
        if (dd > 1) {
          const s = Math.min(dd, speed * dt), nx = m.px + (dx / dd) * s, ny = m.py + (dy / dd) * s;
          if (walkablePx(nx, ny)) { m.px = nx; m.py = ny; } else { m.tx = m.px; m.ty = m.py; }
          if (Math.abs(dx) > 0.5) m.face = Math.sign(dx);
        }
      }
      if (t - lastSaved > 0.6) { lastSaved = t; call("onMove", { x: Math.round(lead.x), y: Math.round(lead.y) }); }
    }

    let zoneShown = -2;
    function syncZone() {
      const tl = tileOf(lead.x, lead.y), z = F.zoneOf[tl.y * F.cols + tl.x];
      if (z === zoneShown) return;
      zoneShown = z;
      const zone = F.zones[z];
      if (!zone) { zoneName.textContent = F.town.name; zoneSub.textContent = F.region.name; return; }
      zoneName.textContent = (zone.job ? "♜ " : zone.goal ? "★ " : "") + zone.name;
      const of = Number((opts.data || {}).ready_at) || 5;
      zoneSub.textContent = !zone.lessons ? lab("zoneFog") : [zone.gate ? lab("zoneGate", { n: zone.gate }) : null, zone.job ? lab("zoneJob") : null, zone.goal ? lab("zoneGoal") : null,
        zone.open ? lab("zoneLeft", { n: zone.open }) : lab("zoneClear"), lab("zoneYou", { n: Math.min(zone.you, of), of })].filter(Boolean).join(" · ");
      hudZone.classList.toggle("fd-goal", !!zone.goal);
    }

    function render() {
      if (!groundCv) return;
      const ease = reduced ? 1 : 0.18;
      cam.x += (lead.x - cam.x) * ease;
      cam.y += (lead.y - 8 - cam.y) * ease;
      clampCam();
      ox = canvas.width / 2 - cam.x * S;
      oy = canvas.height / 2 - cam.y * S;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.imageSmoothingEnabled = false;
      ctx.fillStyle = mixHex((LEAVES[F.region.id] || LEAVES.craft)[1], "#0c1a12", 0.4);
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      const vx0 = -ox / S, vy0 = -oy / S, vx1 = (canvas.width - ox) / S, vy1 = (canvas.height - oy) / S;
      const sx0 = Math.max(0, Math.floor(vx0)), sy0 = Math.max(0, Math.floor(vy0)), sx1 = Math.min(W, Math.ceil(vx1)), sy1 = Math.min(H, Math.ceil(vy1));
      if (sx1 > sx0 && sy1 > sy0) ctx.drawImage(groundCv, sx0, sy0, sx1 - sx0, sy1 - sy0, X(sx0), Y(sy0), (sx1 - sx0) * S, (sy1 - sy0) * S);
      // where the route ends: a ring on the ground
      if (target) {
        const gx = target.x * T + T / 2, gy = target.y * T + T / 2, u = reduced ? 0.5 : (t * 1.6) % 1;
        ctx.strokeStyle = `rgba(255, 220, 140, ${0.9 - u * 0.6})`;
        ctx.lineWidth = Math.max(1, S);
        ctx.beginPath();
        ctx.ellipse(X(gx), Y(gy + 3), (4 + u * 3) * S, (2 + u * 1.2) * S, 0, 0, Math.PI * 2);
        ctx.stroke();
      }
      // everything that stands, back to front by its feet
      const seen = (x, y, m) => x > vx0 - m && x < vx1 + m && y > vy0 - 6 && y < vy1 + m;
      const items = [];
      for (const p of F.props) {
        const fx = p.x * T + T / 2, fy = p.y * T + T - 2;
        if (seen(fx, fy, 48)) items.push({ y: fy, draw: () => drawProp(p, fx, fy) });
      }
      const tx = F.town.x * T, ty = F.town.y * T + T;
      if (seen(tx, ty, 120)) items.push({ y: ty, draw: () => put(townImg(), tx, ty + 2) });
      for (const m of monsters) if (seen(m.px, m.py, 40)) items.push({ y: m.py + 4, draw: () => drawMonster(m) });
      members.forEach((m, i) => items.push({ y: m.y + 4 - i * 0.01, draw: () => drawMember(m, i) }));
      items.sort((a, b) => a.y - b.y);
      for (const it of items) it.draw();
      // the zones' names over their signs
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      ctx.font = `700 ${Math.round(11 * dpr)}px "IBM Plex Mono", ui-monospace, monospace`;
      for (const z of F.zones) {
        const sx = z.sign.x * T + T / 2, sy = z.sign.y * T - 10;
        if (!seen(sx, sy, 80)) continue;
        const txt = (z.gate ? "⚔ " : z.job ? "♜ " : z.goal ? "★ " : "") + z.name, w = ctx.measureText(txt).width + 12 * dpr, h = 17 * dpr, bx = X(sx) - w / 2, by = Y(sy) - h;
        ctx.fillStyle = "rgba(42, 30, 18, 0.78)";
        ctx.fillRect(Math.round(bx), Math.round(by), Math.round(w), Math.round(h));
        ctx.fillStyle = z.gate || z.job || z.goal ? "#ffd27a" : z.open ? "#ffe2a0" : "#d9e6d0";
        ctx.fillText(txt, X(sx), Y(sy) - 3 * dpr);
      }
      if (themeDark()) { ctx.fillStyle = "rgba(14, 22, 56, 0.22)"; ctx.fillRect(0, 0, canvas.width, canvas.height); }
      drawMini();
      syncZone();
    }
    function drawProp(p, fx, fy) {
      const cv = propImg(p);
      if (p.type === "tree") shadow(fx, fy, 10);
      else if (p.type !== "sign") shadow(fx, fy, 6);
      // a crown in front of the party, or of a monster, lets it show through
      const hides = (x, y) => fy > y + 2 && fy - cv.height < y && Math.abs(fx - x) < cv.width / 2 + 6;
      const over = (p.type === "tree" || p.type === "pine" || p.type === "house")
        && (members.some((m) => hides(m.x, m.y)) || monsters.some((m) => hides(m.px, m.py)));
      if (over) ctx.globalAlpha = 0.5;
      put(cv, fx, fy + (p.type === "house" ? 2 : 0));
      ctx.globalAlpha = 1;
    }
    function drawMonster(m) {
      const frame = reduced ? 0 : Math.floor(t * 2.5 + m.ph * 7) % 2;
      const cv = monsterImg(m.lesson.kind || "fix", m.lesson.stage || "provisional", frame);
      const bob = reduced ? 0 : Math.round(Math.sin(t * 4 + m.ph * 6.28) * 0.8);
      shadow(m.px, m.py + 2, 7);
      if (hover === m) {
        ctx.strokeStyle = "rgba(255, 210, 120, 0.95)";
        ctx.lineWidth = Math.max(1, Math.round(S * 0.75));
        ctx.beginPath();
        ctx.ellipse(X(m.px), Y(m.py + 2), 10 * S, 4 * S, 0, 0, Math.PI * 2);
        ctx.stroke();
      }
      if (m.back) { // back for another look: a ring of light under it, ↻ over it
        ctx.strokeStyle = "rgba(140, 220, 255, 0.9)";
        ctx.lineWidth = Math.max(1, Math.round(S * 0.6));
        ctx.beginPath();
        ctx.ellipse(X(m.px), Y(m.py + 2), 9 * S, 3.5 * S, 0, 0, Math.PI * 2);
        ctx.stroke();
      }
      put(cv, m.px, m.py + 3 + bob, 1, m.face > 0); // the Cast's monsters face left
      if (m.prep && !m.back && !m.alert) { // the job's own: its banner over it
        ctx.font = `700 ${Math.round(9 * S)}px "IBM Plex Mono", ui-monospace, monospace`;
        ctx.textAlign = "center"; ctx.textBaseline = "bottom";
        ctx.lineWidth = Math.max(2, S); ctx.strokeStyle = "rgba(10, 20, 40, 0.8)";
        ctx.strokeText("♜", X(m.px), Y(m.py - cv.height - 1));
        ctx.fillStyle = "#ffd27a"; ctx.fillText("♜", X(m.px), Y(m.py - cv.height - 1));
      }
      if (m.back && !m.alert) {
        ctx.font = `700 ${Math.round(9 * S)}px "IBM Plex Mono", ui-monospace, monospace`;
        ctx.textAlign = "center"; ctx.textBaseline = "bottom";
        ctx.lineWidth = Math.max(2, S); ctx.strokeStyle = "rgba(10, 20, 40, 0.8)";
        ctx.strokeText("↻", X(m.px), Y(m.py - cv.height - 1));
        ctx.fillStyle = "#8cdcff";
        ctx.fillText("↻", X(m.px), Y(m.py - cv.height - 1));
      }
      if (m.alert) { // a "!" over its head
        const top = m.py - cv.height - 4 + (reduced ? 0 : Math.round(Math.abs(Math.sin(t * 6)) * -2));
        ctx.fillStyle = "#ff5a5a";
        ctx.fillRect(X(m.px - 1), Y(top - 6), 3 * S, 5 * S);
        ctx.fillRect(X(m.px - 1), Y(top + 1), 3 * S, 2 * S);
      }
    }
    function drawMember(m, i) {
      const frame = reduced ? 0 : (Math.floor(t * 4) + i) % 4;
      const hop = !reduced && lead.moving ? Math.round(Math.abs(Math.sin(t * 11 + i * 1.3)) * -2) : 0;
      shadow(m.x, m.y + 2, 7);
      put(agentImg(m.a.agent, m.a.tier || "novice", frame), m.x, m.y + 4 + hop, 1, m.face < 0);
      if (i === 0) { // a gold arrow over the leader
        const ay = m.y - 34 + (reduced ? 0 : Math.round(Math.sin(t * 3.2) * 1.5));
        ctx.fillStyle = "#ffb45e";
        for (let k = 0; k < 4; k++) ctx.fillRect(X(m.x - 3 + k), Y(ay + k), (7 - 2 * k) * S, S);
      }
    }
    function drawMini() {
      if (!miniBase) return;
      const k = mini.width / F.cols;
      mctx.setTransform(1, 0, 0, 1, 0, 0);
      mctx.imageSmoothingEnabled = false;
      mctx.clearRect(0, 0, mini.width, mini.height);
      mctx.drawImage(miniBase, 0, 0, mini.width, mini.height);
      const dot = (x, y, c, s) => { mctx.fillStyle = c; mctx.fillRect(Math.round((x / T) * k - s / 2), Math.round((y / T) * k - s / 2), s, s); };
      const s = Math.max(2, Math.round(2.5 * dpr));
      for (const m of monsters) dot(m.px, m.py, "#e5484d", s);
      dot(lead.x, lead.y, "#ffffff", s + 2);
      dot(lead.x, lead.y, "#ffb45e", s);
      const hw = canvas.width / (2 * S), hh = canvas.height / (2 * S);
      mctx.strokeStyle = "rgba(255, 255, 255, 0.85)";
      mctx.lineWidth = Math.max(1, dpr);
      mctx.strokeRect(((cam.x - hw) / T) * k, ((cam.y - hh) / T) * k, ((2 * hw) / T) * k, ((2 * hh) / T) * k);
    }
    function themeDark() {
      const th = document.documentElement.getAttribute("data-theme");
      if (th === "dark" || th === "light") return th === "dark";
      return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;
    }

    function loop(now) {
      raf = 0;
      if (!alive) return;
      const dt = last ? Math.min(0.05, (now - last) / 1000) : 0;
      last = now;
      t += dt;
      if (!busy) step(dt);
      render();
      if (visible && !document.hidden) raf = requestAnimationFrame(loop);
    }
    function wake() { if (alive && !raf) { last = 0; raf = requestAnimationFrame(loop); } }

    // ---- input: click or tap to walk (a monster: walk into it; the town: to its gate), keys to steer
    function worldAt(e) {
      const r = canvas.getBoundingClientRect();
      return { x: ((e.clientX - r.left) * dpr - ox) / S, y: ((e.clientY - r.top) * dpr - oy) / S };
    }
    function thingAt(w) {
      let best = null;
      for (const m of monsters) {
        const d = Math.hypot(w.x - m.px, w.y - (m.py - 8));
        if (d < 14 && (!best || d < best.d)) best = { d, m };
      }
      if (best) return { type: "monster", m: best.m };
      if (Math.abs(w.x - F.town.x * T) < 48 && w.y > F.town.y * T - 36 && w.y < F.town.y * T + T + 4) return { type: "town" };
      for (const z of F.zones) if (Math.abs(w.x - (z.sign.x * T + T / 2)) < 10 && Math.abs(w.y - (z.sign.y * T + 6)) < 12) return { type: "sign", zone: z };
      return null;
    }
    function onDown(e) {
      if (busy || (e.button != null && e.button !== 0)) return;
      canvas.focus({ preventScroll: true });
      const w = worldAt(e), th = thingAt(w);
      if (th && th.type === "monster") walkTo(tileOf(th.m.px, th.m.py));
      else if (th && th.type === "town") walkTo(F.gate);
      else if (th && th.type === "sign" && th.zone.gate) walkTo(nearestOpen(th.zone.sign));
      else walkTo(tileOf(w.x, w.y));
      wake();
    }
    function onMove(e) {
      const th = thingAt(worldAt(e)), was = hover;
      hover = th && th.type === "monster" ? th.m : null;
      canvas.style.cursor = th ? "pointer" : "";
      const item = !th ? null : th.type === "monster" ? { type: "monster", lesson: th.m.lesson, back: !!th.m.back } : th.type === "town" ? { type: "town", name: F.town.name }
        : { type: "sign", zone: th.zone };
      if (item || was) call("onHover", item, { x: e.clientX, y: e.clientY });
    }
    function onLeave() { hover = null; call("onHover", null, { x: 0, y: 0 }); }
    const KEYS = { ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight", ArrowUp: "ArrowUp", ArrowDown: "ArrowDown", a: "a", d: "d", w: "w", s: "s", A: "a", D: "d", W: "w", S: "s" };
    // the keys steer the party wherever the focus is while the field shows, except while typing (the search box)
    const typing = (t) => !!t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName || ""));
    function onKey(e) {
      const k = KEYS[e.key];
      if (!k) return;
      if (e.type === "keyup") { keys.delete(k); return; } // a key let go is always let go
      if (!alive || !wrap.isConnected || busy || e.metaKey || e.ctrlKey || e.altKey || typing(e.target)) return;
      e.preventDefault();
      keys.add(k);
      wake();
    }
    function onBlur() { keys.clear(); }
    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerleave", onLeave);
    document.addEventListener("keydown", onKey);
    document.addEventListener("keyup", onKey);
    window.addEventListener("blur", onBlur);
    // the town: walking into its gate opens it
    const townCheck = setInterval(() => {
      if (busy || !armed) return;
      const gx = F.gate.x * T + T / 2, gy = F.gate.y * T + T / 2;
      if (Math.hypot(lead.x - gx, lead.y - gy) < T * 0.8) { busy = true; route = []; keys.clear(); call("onTown", F.region); return; }
      for (const z of F.zones) { // a zone's open gate: walking up to it opens it
        if (!z.gate || Math.hypot(lead.x - (z.sign.x * T + T / 2), lead.y - (z.sign.y * T + T / 2)) > T * 1.15) continue;
        busy = true; route = []; keys.clear(); call("onGate", z); return;
      }
    }, 120);

    const io = typeof IntersectionObserver === "function" ? new IntersectionObserver((en) => { for (const x of en) visible = x.isIntersecting; if (visible) wake(); }) : null;
    io && io.observe(wrap);
    const ro = typeof ResizeObserver === "function" ? new ResizeObserver(() => { if (alive) { measure(); wake(); } }) : null;
    ro && ro.observe(wrap);
    const onVis = () => { if (!document.hidden) wake(); };
    document.addEventListener("visibilitychange", onVis);

    measure();
    paintGround();
    paintMini();
    cam.x = lead.x; cam.y = lead.y - 8;
    wake();

    function destroy() {
      alive = false;
      if (raf) cancelAnimationFrame(raf);
      clearInterval(townCheck);
      io && io.disconnect();
      ro && ro.disconnect();
      document.removeEventListener("visibilitychange", onVis);
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("keyup", onKey);
      window.removeEventListener("blur", onBlur);
      wrap.remove();
      art.clear();
    }
    return { destroy, redraw() { measure(); wake(); } };
  }

  root.Field = Object.freeze({ mountField, layout, path, LABELS });
})();
