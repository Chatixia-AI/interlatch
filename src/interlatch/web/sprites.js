/* Party sprites: the coding agents as 32×32 pixel-art toys in the Blueprint Cast's style (Chatixia Studio): chunky
   rounded bodies, big eyes, rosy cheeks, soft glossy colour. No images, no build step: a sprite is a stack of pixel maps
   (strings, one letter per pixel, "." for none). A map names parts only; the renderer shades each part in three tones,
   lit from the top left, gives glossy parts a highlight and edges the figure in a darker shade of its own colour, then
   paints it on a <canvas> with fillRect, so the page's Content-Security-Policy (no data: or blob: images) has nothing to
   object to. */
(() => {
  "use strict";

  const SIZE = 32, ICON = 16, FRAMES = 4;
  const AGENTS = ["claude", "codex", "copilot", "bob", "antigravity"];
  const TIERS = ["novice", "adept", "expert", "master"];
  const LOCKED = { fill: "#7f8999", rim: "#c3cad5", z: "#97a1b1" };
  const SHADOW = "rgba(16, 28, 64, 0.22)";

  // =====================================================================================
  // Colour. A palette value is a part's colour: "#rrggbb" shaded in three tones, "*#rrggbb" glossy (a highlight too),
  // "=#rrggbb" flat (eyes, cheeks, glows). Shadows lean toward the blueprint's navy.
  // =====================================================================================
  const rgbOf = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const hexOf = (rgb) => "#" + rgb.map((v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0")).join("");
  const mix = (a, b, u) => { const x = rgbOf(a), y = rgbOf(b); return hexOf(x.map((v, i) => v + (y[i] - v) * u)); };
  const toneCache = new Map();
  function tones(value) {
    if (!value) return null;
    if (!toneCache.has(value)) {
      const kind = value[0] === "=" ? "flat" : value[0] === "*" ? "gloss" : "shaded";
      const base = kind === "shaded" ? value : value.slice(1);
      toneCache.set(value, { kind, base, light: mix(base, "#ffffff", 0.26), dark: mix(base, "#1c2456", 0.3),
        rim: mix(base, "#131838", 0.55), spec: mix(base, "#ffffff", 0.75) });
    }
    return toneCache.get(value);
  }

  // =====================================================================================
  // Pieces: a pixel map placed at (x, y). `on` lists the frames it shows in (all when left out).
  // =====================================================================================
  const P = (x, y, rows, on) => ({ x, y, rows, on });
  const shows = (piece, frame) => !piece.on || piece.on.includes(frame);

  // letters into a grid; "_" cuts a pixel out of what is under it
  function paint(grid, n, piece) {
    piece.rows.forEach((row, dy) => {
      for (let dx = 0; dx < row.length; dx++) {
        const c = row[dx], x = piece.x + dx, y = piece.y + dy;
        if (c === "." || c === " " || x < 0 || y < 0 || x >= n || y >= n) continue;
        grid[y * n + x] = c === "_" ? null : c;
      }
    });
  }
  // rows [0, waist) move by dy (a breath, or a float); what moves down lands over the row below
  function shift(grid, n, waist, dy) {
    if (!dy) return grid;
    const out = grid.slice();
    for (let y = 0; y < waist; y++) for (let x = 0; x < n; x++) out[y * n + x] = null;
    for (let y = 0; y < waist; y++) {
      const ty = y + dy;
      if (ty < 0 || ty >= n) continue;
      for (let x = 0; x < n; x++) {
        const v = grid[y * n + x];
        if (ty < waist || v) out[ty * n + x] = v;
      }
    }
    return out;
  }
  // a walking step: one foot (stride 1 the left, 2 the right) lifts a pixel
  function lift(grid, n, stride) {
    const [x0, x1] = stride === 1 ? [6, 15] : [16, 25];
    for (let y = 24; y < n - 2; y++) for (let x = x0; x <= x1; x++) grid[y * n + x] = y + 1 < n - 2 ? grid[(y + 1) * n + x] : null;
    return grid;
  }
  const filled = (grid, n, x, y) => x >= 0 && y >= 0 && x < n && y < n && !!grid[y * n + x];

  // each part (the pixels of one letter that touch) is shaded across its own box: lit top left, dark bottom right,
  // and a glossy part gets a highlight near its top left
  function shade(grid, n, pal) {
    const out = new Array(n * n).fill(null), seen = new Uint8Array(n * n);
    for (let i = 0; i < n * n; i++) {
      if (!grid[i] || seen[i]) continue;
      const c = grid[i], t = tones(pal[c]), part = [i];
      seen[i] = 1;
      for (let k = 0; k < part.length; k++) {
        const p = part[k], x = p % n, y = (p - x) / n;
        for (const q of [x > 0 ? p - 1 : -1, x < n - 1 ? p + 1 : -1, p - n, p + n]) {
          if (q >= 0 && q < n * n && !seen[q] && grid[q] === c) { seen[q] = 1; part.push(q); }
        }
      }
      if (!t) continue;
      let x0 = n, y0 = n, x1 = 0, y1 = 0;
      for (const p of part) { const x = p % n, y = (p - x) / n; x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); }
      const w = x1 - x0 + 1, h = y1 - y0 + 1, cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
      for (const p of part) {
        const x = p % n, y = (p - x) / n;
        if (t.kind === "flat" || (w < 3 && h < 3)) { out[p] = t.base; continue; }
        // light on the upper left, a crescent of shadow along the lower right edge, as on a rounded toy
        const u = w > 1 ? (x - cx) / (w / 2 + 0.5) : 0, v = h > 1 ? (y - cy) / (h / 2 + 0.5) : 0;
        const s = 0.5 * u + 0.86 * v, r2 = u * u + v * v;
        out[p] = s < -0.42 && r2 < 0.9 ? t.light : s > 0.28 && r2 > 0.5 ? t.dark : t.base;
      }
      if (t.kind === "gloss" && w >= 4 && h >= 4) {
        const sx = x0 + Math.max(1, Math.round(w * 0.24)), sy = y0 + Math.max(1, Math.round(h * 0.2));
        if (grid[sy * n + sx] === c) out[sy * n + sx] = t.spec;
        if (w >= 9 && grid[sy * n + sx + 1] === c) out[sy * n + sx + 1] = t.spec;
        if (h >= 9 && grid[(sy + 1) * n + sx] === c) out[(sy + 1) * n + sx] = t.light;
      }
    }
    return out;
  }
  // the edge: every empty pixel that touches the figure side-on, in a darker shade of the part it touches
  function rims(grid, n, pal, flatColor) {
    const out = new Array(n * n).fill(null);
    for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
      if (grid[y * n + x]) continue;
      for (const [dx, dy] of [[0, 1], [1, 0], [-1, 0], [0, -1]]) {
        if (!filled(grid, n, x + dx, y + dy)) continue;
        const t = tones(pal[grid[(y + dy) * n + x + dx]]);
        out[y * n + x] = flatColor || (t ? t.rim : "#131838");
        break;
      }
    }
    return out;
  }
  // a glow that hugs the silhouette: a ring a pixel wide around the edged shape, and on a pulse a fainter one outside it
  function aura(shape, n, rgb, pulse) {
    const out = new Array(n * n).fill(null);
    const near = (x, y, r) => {
      for (let dy = -r; dy <= r; dy++) for (let dx = -r; dx <= r; dx++) if (filled(shape, n, x + dx, y + dy)) return true;
      return false;
    };
    for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
      if (shape[y * n + x]) continue;
      if (near(x, y, 1)) out[y * n + x] = `rgba(${rgb}, 0.6)`;
      else if (pulse && near(x, y, 2)) out[y * n + x] = `rgba(${rgb}, 0.25)`;
    }
    return out;
  }
  // a grid of colours becomes fillRect runs: [color, x, y, width]
  function runs(grid, n, ops, w = n) {
    const h = grid.length / w;
    for (let y = 0; y < h; y++) {
      let x = 0;
      while (x < w) {
        const c = grid[y * w + x];
        if (!c) { x++; continue; }
        let k = 1;
        while (x + k < w && grid[y * w + x + k] === c) k++;
        ops.push([c, x, y, k]);
        x += k;
      }
    }
    return ops;
  }
  function blit(canvas, n, scale, ops) {
    if (canvas.width !== n * scale) canvas.width = n * scale;
    if (canvas.height !== n * scale) canvas.height = n * scale;
    canvas.style.imageRendering = "pixelated";
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.globalAlpha = 1;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    for (const [c, x, y, w] of ops) {
      ctx.fillStyle = c;
      ctx.fillRect(x * scale, y * scale, w * scale, scale);
    }
  }
  // letters painted straight in a part's middle tone (glows, sparks, halos): no shading, no edge
  function paintColors(grid, n, piece, pal) {
    piece.rows.forEach((row, dy) => {
      for (let dx = 0; dx < row.length; dx++) {
        const c = row[dx], x = piece.x + dx, y = piece.y + dy, t = tones(pal[c]);
        if (c === "." || !t || x < 0 || y < 0 || x >= n || y >= n) continue;
        grid[y * n + x] = t.base;
      }
    });
  }

  // =====================================================================================
  // The Cast's face: big eyes (white, a dark pupil looking a little down), rosy cheeks, a small smile
  // =====================================================================================
  const BASE_PAL = { w: "=#ffffff", k: "=#1b1d33", c: "=#ff9db4", m: "=#3b1f2e" };
  // eyes 4×4 at (x, y) and (x + 8, y); cheeks under the outer corners; the mouth between
  const face = (x, y) => [
    P(x, y, [".ww......ww.", "wwkw....wwkw", "wkkw....wkkw", ".ww......ww."]),
    P(x - 1, y + 4, ["cc..........cc"]),
    P(x + 4, y + 4, ["m..m", ".mm."]),
  ];
  // eyes closed in a soft curve, in the colour of the face around them
  const closed = (x, y, skin) => P(x, y, [skin.repeat(4) + "...." + skin.repeat(4), skin.repeat(4) + "...." + skin.repeat(4),
    "k" + skin + skin + "k...." + "k" + skin + skin + "k", skin + "kk" + skin + "...." + skin + "kk" + skin]);
  // little arms (a bent limb, a round mitten) and rounded shoes
  const ARMS = (y) => [P(4, y, ["..aa", ".aa.", "aa..", "aa.."]), P(24, y, ["aa..", ".aa.", "..aa", "..aa"])];
  const SHOES = P(9, 27, [".ssss....ssss.", "ssssss..ssssss", ".sssss..sssss."]);

  // =====================================================================================
  // Characters
  // =====================================================================================

  // ---- claude: a terracotta egg of a sage; a cap and a wand, then the wizard's hat and staff, a beard, a starry tip
  const claudeBody = P(8, 12, [
    ".....bbbbbb.....",
    "...bbbbbbbbbb...",
    "..bbbbbbbbbbbb..",
    ".bbbbbbbbbbbbbb.",
    "bbbbbbbbbbbbbbbb",
    "bbbbbbbbbbbbbbbb",
    "bbbbbbbbbbbbbbbb",
    "bbbbbbbbbbbbbbbb",
    "bbbbbbbbbbbbbbbb",
    "bbbbbbbbbbbbbbbb",
    "bbbbbbbbbbbbbbbb",
    ".bbbbbbbbbbbbbb.",
    ".bbbbbbbbbbbbbb.",
    "..bbbbbbbbbbbb..",
    "....bbbbbbbb....",
  ]);
  const claudeCap = P(11, 4, ["....hh....", "...hhhh...", "...hhhhh..", "..hhhhhh..", "..hhhhhhh.", ".hhhhhhhhh", "hhhhhhhhhh",
    "hhhhhhhhhh", ".hhhhhhhh."]);
  const claudeWand = P(2, 14, [".g.", "gGg", ".g.", ".o.", ".o.", ".o.", ".o.", ".o.", ".o."]);
  const claudeHat = [
    P(5, 0, ["...............hhh....", ".............hhhh.....", "............hhhh......", "..........hhhhh.......",
      ".........hhhhhh.......", "........hhhhhhh.......", "........hhhhhhhh......", ".......hhhhhhhhh......",
      "......hhhhhhhhhhh.....", ".....hhhhhhhhhhhhh....", "....hhhhhhhhhhhhhhh..."]),
    P(5, 11, ["..HHHHHHHHHHHHHHHHHH..", "HHHHHHHHHHHHHHHHHHHHHH", ".HHHHHHHHHHHHHHHHHHHH."]),
  ];
  const claudeStaff = P(1, 5, [".oo.", "o..o", "o.oo", ".oo.", ".oo.", ".oo.", ".oo.", ".oo.", ".oo.", ".oo.", ".oo.", ".oo.",
    ".oo.", ".oo.", ".oo.", ".oo.", ".oo.", ".oo.", ".oo.", ".oo.", ".oo.", ".oo.", ".oo.", ".oo."]);
  const claudeTrim = [P(9, 10, ["yyyyyyyyyyyyyyy"]), P(14, 5, [".y.", "yyy", ".y."]), P(9, 24, ["yyyyyyyyyyyyyy"])];
  const claudeOrb = [P(0, 1, [".gg.", "gGGg", "gGGg", ".gg."]),
    P(0, 0, ["g...g", ".....", ".....", ".....", ".....", "g...g"], [1, 3])];
  const claudeStar = P(19, 0, ["..y..", ".yYy.", "yYYYy", ".yYy.", "..y.."]);
  const claude = {
    color: "#d97757",
    pal: { b: "*#e2825f", a: "#e2825f", h: "#a24a3a", H: "#8f3f31", y: "*#f6c445", Y: "=#fff1b0", o: "#a8703f",
      e: "*#f7f1e8", s: "#9a4a36", g: "=#ffd66b", G: "=#fff6d6" },
    waist: 23, blink: closed(10, 16, "b"),
    tiers: {
      novice: { body: [claudeBody, claudeCap, ...face(10, 16), ...ARMS(19), SHOES, claudeWand] },
      adept: { body: [claudeBody, ...claudeHat, ...face(10, 16), ...ARMS(19), SHOES, claudeStaff] },
      expert: { body: [claudeBody, ...claudeHat, ...face(10, 16), ...claudeTrim, ...ARMS(19), SHOES, claudeStaff],
        front: claudeOrb },
      master: { body: [claudeBody, ...claudeHat, ...face(10, 16), ...claudeTrim, ...ARMS(19), SHOES, claudeStaff,
        claudeStar], front: claudeOrb, aura: "255, 190, 110",
        sparks: [[27, 9], [29, 18], [26, 25], [6, 2], [30, 4]], spark: ["#fff6d6", "#ffd66b"] },
    },
  };

  // ---- codex: a teal robot tinkerer with a screen for a face; then a hood and a wrench, a glowing quill-blade, a drone
  const codexHead = [
    P(8, 6, ["..tttttttttttt..", ".tttttttttttttt.", "tttttttttttttttt", "tttnnnnnnnnnnttt", "ttnnnnnnnnnnnntt",
      "ttnnnnnnnnnnnntt", "ttnnnnnnnnnnnntt", "ttnnnnnnnnnnnntt", "ttnnnnnnnnnnnntt", "ttnnnnnnnnnnnntt",
      "tttnnnnnnnnnnttt", "tttttttttttttttt", ".tttttttttttttt."]),
    P(14, 1, [".oo.", "oooo", ".oo.", ".gg.", ".gg."]),
    P(6, 11, ["gg", "gg"]), P(24, 11, ["gg", "gg"]),
  ];
  const codexFace = [P(10, 10, [".ww......ww.", "wwkw....wwkw", "wkkw....wkkw", ".ww......ww."]), P(10, 14, ["cc........cc"]),
    P(14, 14, ["M..M", ".MM."])];
  const codexBody = [
    P(10, 19, ["uuuuuuuuuuuu", "uuuuuuuuuuuu", "lllllyylllll", "uuuuuuuuuuuu", "uuuuuuuuuuuu", "uuuuuuuuuuuu", ".uuuuuuuuuu."]),
    P(6, 19, ["..aa", ".aa.", "gg..", "gg.."]), P(22, 19, ["aa..", ".aa.", "..gg", "..gg"]),
    P(9, 26, [".ssss....ssss.", "ssssss..ssssss", ".sssss..sssss."]),
  ];
  const codexWrench = P(3, 14, ["e.e", "eEe", ".e.", ".e.", ".e.", ".e.", ".e.", ".e."]);
  const codexHood = P(6, 3, [".........dd.........", ".......dddddd.......", ".....dddddddddd.....", "...dddddddddddddd...",
    "..dddddddddddddddd..", ".dddddddddddddddddd.", "dddd............dddd", "dddd............dddd", "dddd............dddd",
    "dddd............dddd", "dddd............dddd", "dddd............dddd", "dddd............dddd", "dddd............dddd",
    "ddddd..........ddddd", "dddddddddddddddddddd"]);
  const codexCape = P(7, 18, [".DDDDDDDDDDDDDDDD.", "DDDDDDDDDDDDDDDDDD", "DDDDDDDDDDDDDDDDDD", "DDDDDDDDDDDDDDDDDD",
    "DDDDDDDDDDDDDDDDDD", "DDDDDDDDDDDDDDDDDD", "DDDDDDDDDDDDDDDDDD", "DDDDDDDDDDDDDDDDDD", "D.DD.DDDDDDDD.DD.D"]);
  const codexBigWrench = P(1, 9, ["ee.ee", "eE.Ee", "eEEEe", ".eEe.", "..e..", "..e..", "..e..", "..e..", "..e..", "..e..",
    "..e..", "..e..", "..E..", "..E.."]);
  const codexTrim = [P(9, 8, ["qqqqqqqqqqqqqq"]), P(9, 9, ["q", "q", "q", "q", "q", "q", "q", "q"]),
    P(22, 9, ["q", "q", "q", "q", "q", "q", "q", "q"]), P(7, 26, ["q.qq.qqqqqqqq.qq.q"])];
  const codexQuill = P(25, 9, ["...q", "..qQ", "..qQ", ".qQq", ".qQq", ".qQq", ".qQ.", ".Qq.", "eeee", ".l..", ".l.."]);
  const codexGlow = [P(24, 7, [".....Q", "......", "Q.....", "......"], [0, 2]), P(24, 7, ["......", "....Q.", "......", ".Q...."], [1, 3])];
  const drone = (y, on) => P(25, y, ["..o...", ".tttt.", "tnnnnt", "tnwwnt", ".tttt.", ".g..g."], on);
  const codex = {
    color: "#14b88c",
    pal: { t: "*#3cc9a6", u: "#3cc9a6", n: "=#17263d", M: "=#ffffff", g: "*#c9d4e0", o: "*#ff9a4a", l: "#8a5a3a",
      y: "*#f6c445", s: "#3d4f63", a: "#3cc9a6", d: "#5d6f86", D: "#46566b", e: "*#d5dde6", E: "#8e9aab",
      q: "=#7cf2cf", Q: "=#eafff8" },
    waist: 23, blink: P(10, 10, ["nnnn....nnnn", "nnnn....nnnn", "wnnw....wnnw", "nwwn....nwwn"]),
    tiers: {
      novice: { body: [...codexHead, ...codexFace, ...codexBody, codexWrench] },
      adept: { body: [codexCape, ...codexHead, ...codexFace, ...codexBody, codexHood, codexBigWrench] },
      expert: { body: [codexCape, ...codexHead, ...codexFace, ...codexBody, codexHood, ...codexTrim, codexBigWrench, codexQuill],
        front: codexGlow },
      master: { body: [codexCape, ...codexHead, ...codexFace, ...codexBody, codexHood, ...codexTrim, codexBigWrench, codexQuill,
        drone(0, [0, 1]), drone(1, [2, 3])], front: codexGlow, aura: "60, 230, 180",
        sparks: [[3, 3], [29, 17], [2, 24], [28, 26], [20, 0]], spark: ["#eafff8", "#3fe0b4"] },
    },
  };

  // ---- copilot: a violet ball in an aviator's cap and goggles; then a flying scarf and a compass, brass and a spyglass,
  // golden wings
  // a scarf tail 2 pixels deep: tops[i] is the row of column x + i, stripes[i] its letter (its shade is the capital)
  const ribbon = (x, tops, stripes, on) => {
    const y0 = Math.min(...tops), rows = [];
    for (let y = y0; y <= Math.max(...tops) + 1; y++) {
      rows.push(tops.map((t, i) => (y === t ? stripes[i] : y === t + 1 ? stripes[i].toUpperCase() : ".")).join(""));
    }
    return P(x, y0, rows, on);
  };
  const copilotBody = P(8, 10, [
    ".....vvvvvv.....",
    "...vvvvvvvvvv...",
    "..vvvvvvvvvvvv..",
    ".vvvvvvvvvvvvvv.",
    "vvvvvvvvvvvvvvvv",
    "vvvvvvvvvvvvvvvv",
    "vvvvvvvvvvvvvvvv",
    "vvvvvvvvvvvvvvvv",
    "vvvvvvvvvvvvvvvv",
    "vvvvvvvvvvvvvvvv",
    "vvvvvvvvvvvvvvvv",
    "vvvvvvvvvvvvvvvv",
    ".vvvvvvvvvvvvvv.",
    ".vvvvvvvvvvvvvv.",
    "..vvvvvvvvvvvv..",
    "....vvvvvvvv....",
  ]);
  const copilotCap = [
    P(7, 4, ["......bbbbbb......", "....bbbbbbbbbb....", "...bbbbbbbbbbbb...", "..bbbbbbbbbbbbbb..", ".bbbbbbbbbbbbbbbb.",
      ".bbbbbbbbbbbbbbbb.", "bbbb..........bbbb", "bbb............bbb", "bbb............bbb", ".bb............bb.",
      ".bb............bb."]),
    P(9, 6, ["qqqqqq..qqqqqq", "qggggq..qggggq", "qggggqqqqggggq", "qqqqqq..qqqqqq"]),
  ];
  const copilotScarf = P(8, 21, ["AAnnAAnnAAnnAAnn", ".AAnnAAnnAAnnAA."]);
  const copilotKnot = P(20, 23, ["nA", "An", "nA"]);
  const copilotTail = [
    ribbon(23, [21, 21, 20, 20, 20, 21, 21, 22], "aannaann", [0, 2]),
    ribbon(23, [21, 21, 21, 22, 22, 21, 20, 20], "aannaann", [1, 3]),
  ];
  const copilotCompass = [P(1, 20, [".jj.", "jgGj", "jGgj", ".jj."])];
  const copilotSpyglass = P(0, 19, ["jjJ......", "gjJBjjJ..", "GjJBjjJBj", "gjJBJJJBj", "JJJ......"]);
  const copilotWings = [P(1, 3, ["n......", "nnn....", ".nnnn..", "..NNnnj", "nnnnnnj", ".NNNnnj", "...NNj."]),
    P(24, 3, ["......n", "....nnn", "..nnnn.", "jnnNN..", "jnnnnnn", "jnnNNN.", ".jNN..."])];
  const copilotGlint = [P(10, 7, ["w......w"], [0, 2]), P(0, 20, ["G"], [1, 3])];
  const copilot = {
    color: "#8b6cf0",
    pal: { v: "*#a084f7", a: "#a084f7", b: "*#9a6844", q: "*#a7b2c4", g: "*#9fe6ff", G: "=#effcff", j: "*#e6b64f",
      J: "#a97a26", B: "#5d3a26", n: "*#f5f8ff", N: "#c8d4ea", A: "*#5cb8ff", s: "#6a4a8c" },
    waist: 23, blink: closed(10, 14, "v"),
    tiers: {
      novice: { body: [copilotBody, ...copilotCap, ...face(10, 14), copilotScarf, copilotKnot, ...ARMS(19), SHOES] },
      adept: { body: [copilotBody, ...copilotCap, ...face(10, 14), copilotScarf, ...copilotTail, ...ARMS(19), SHOES,
        ...copilotCompass] },
      expert: { pal: { q: "*#e6b64f" }, body: [copilotBody, ...copilotCap, ...face(10, 14), copilotScarf, ...copilotTail,
        ...ARMS(19), SHOES, copilotSpyglass], front: copilotGlint },
      master: { pal: { q: "*#e6b64f" }, body: [copilotBody, ...copilotCap, ...face(10, 14), copilotScarf, ...copilotTail,
        ...ARMS(19), SHOES, copilotSpyglass, ...copilotWings], front: copilotGlint, aura: "170, 150, 255",
        sparks: [[3, 14], [29, 12], [3, 26], [28, 27], [16, 0]], spark: ["#ffffff", "#9fe6ff"] },
    },
  };

  // ---- bob: a knight whose body is a blue shield, like the Cast's guard; a wooden sword, then helm and steel, a crest
  // and an emblem, the Cast's crown
  const bobBody = P(8, 11, [
    ".bbbbbbbbbbbbbb.",
    "bbbbbbbbbbbbbbbb",
    "bbbbbbbbbbbbbbbb",
    "bbbbbbbbbbbbbbbb",
    "bbbbbbbbbbbbbbbb",
    "bbbbbbbbbbbbbbbb",
    "bbbbbbbbbbbbbbbb",
    "bbbbbbbbbbbbbbbb",
    "bbbbbbbbbbbbbbbb",
    "bbbbbbbbbbbbbbbb",
    ".bbbbbbbbbbbbbb.",
    ".bbbbbbbbbbbbbb.",
    "..bbbbbbbbbbbb..",
    "...bbbbbbbbbb...",
    "....bbbbbbbb....",
    ".....bbbbbb.....",
    "......bbbb......",
  ]);
  const bobRim = P(8, 11, [".eeeeeeeeeeeeee.", "e..............e", "e..............e", "e..............e", "e..............e",
    "e..............e", "e..............e", "e..............e", "e..............e", "e..............e", ".e............e.",
    ".e............e.", "..e..........e..", "...e........e...", "....e......e....", ".....e....e.....", "......eeee......"]);
  const bobStick = P(1, 9, ["..o..", "..o..", "..o..", "..o..", "..o..", "..o..", "..o..", "..o..", "OOOOO", "..O..", "..O.."]);
  const bobHelm = [P(8, 3, [".....eeeeee.....", "...eeeeeeeeee...", "..eeeeeeeeeeee..", ".eeeeeeeeeeeeee.", "eeeeeeeeeeeeeeee",
    "eeeeeeeeeeeeeeee", "eeeeeeeeeeeeeeee"]), P(8, 10, ["EEEEEEEEEEEEEEEE", ".EEEEEEEEEEEEEE."])];
  const bobSword = P(1, 4, ["..W..", ".WWW.", ".WWW.", ".WWW.", ".WWW.", ".WWW.", ".WWW.", ".WWW.", ".WWW.", ".WWW.", ".WWW.",
    ".WWW.", "yyyyy", "..o..", "..o..", "..y.."]);
  const bobCrest = P(13, 0, ["..pppp", ".ppppp", "ppppp.", "pp...."]);
  const bobEmblem = P(14, 22, [".yy.", "yyyy", ".yy."]);
  const bobCrown = P(9, 0, ["j..j..jj..j..j", "y..y..yy..y..y", "yy.yyyyyyyy.yy", "yyyyyyyyyyyyyy", "yrryyggyyBByyy",
    "yyyyyyyyyyyyyy"]);
  const bobGleam = [P(3, 5, ["w"], [0]), P(2, 5, [".w.", "w.w", ".w."], [1]), P(21, 5, ["w"], [2])];
  const bob = {
    color: "#0f62fe",
    pal: { b: "*#3b78ff", a: "#3b78ff", e: "*#d3dce8", E: "#a9b4c4", W: "*#eef2f7", o: "#b07a4a", O: "#7a4e2c",
      y: "*#f6c445", j: "=#fff1b0", r: "*#e5484d", g: "*#3fcf7f", B: "*#4fb0ff", p: "*#8db8ff", s: "#24489c" },
    waist: 23, blink: closed(10, 14, "b"),
    tiers: {
      novice: { body: [bobBody, ...face(10, 14), ...ARMS(17), SHOES, bobStick] },
      adept: { body: [bobBody, bobRim, ...bobHelm, ...face(10, 14), ...ARMS(17), SHOES, bobSword] },
      expert: { body: [bobBody, bobRim, ...bobHelm, bobCrest, ...face(10, 14), bobEmblem, ...ARMS(17), SHOES, bobSword],
        front: bobGleam },
      master: { body: [bobBody, bobRim, ...bobHelm, bobCrown, ...face(10, 14), bobEmblem, ...ARMS(17), SHOES, bobSword],
        front: bobGleam, aura: "90, 150, 255",
        sparks: [[27, 4], [29, 15], [26, 26], [5, 26], [30, 9]], spark: ["#ffffff", "#78a9ff"] },
    },
  };

  // ---- antigravity: an amber orb of a monk, floating cross-legged in a saffron robe; colour beads, then the Cast's
  // floating diamonds in four colours, then a halo
  const agBody = P(8, 5, [
    ".....oooooo.....",
    "...oooooooooo...",
    "..oooooooooooo..",
    ".oooooooooooooo.",
    ".oooooooooooooo.",
    "oooooooooooooooo",
    "oooooooooooooooo",
    "oooooooooooooooo",
    "oooooooooooooooo",
    "oooooooooooooooo",
    "oooooooooooooooo",
    ".oooooooooooooo.",
    ".oooooooooooooo.",
    "..oooooooooooo..",
    "...oooooooooo...",
    ".....oooooo.....",
  ]);
  const agRobe = [P(8, 15, ["rr..............", "rrrr..........rr", ".rrrrr......rrr.", ".rrrrrrrrrrrrrr.", "..rrrrrrrrrrrr..",
    "...rrrrrrrrrr...", ".....rrrrrr....."]), P(9, 21, ["..RRRRRRRRRR..", "RRRRRRRRRRRRRR", ".aa.RRRRRR.aa."])];
  const agTrim = P(8, 15, ["yy..............", "..yy..........yy", "....yy......yy..", "......yyyyyy...."]);
  const agKnot = P(14, 2, [".hh.", "hhhh", ".hh."]);
  const agHands = P(13, 18, ["aa..aa", ".aaaa."]);
  const agFace = [closed(10, 9, "o"), P(9, 13, ["cc..........cc"]), P(14, 13, ["m..m", ".mm."])];
  const gem = (x, y, c, on) => P(x, y, [".d.", "ddd", ".d."].map((r) => r.replace(/d/g, c)), on);
  const agGems = [
    gem(26, 7, "B", [0, 1]), gem(26, 6, "B", [2, 3]),
    gem(3, 8, "C", [1, 2]), gem(3, 7, "C", [3, 0]),
    gem(2, 18, "D", [2, 3]), gem(2, 17, "D", [0, 1]),
    gem(27, 18, "E", [3, 0]), gem(27, 17, "E", [1, 2]),
  ];
  const agHalo = [P(9, 0, ["...HHHHHHHH...", ".HH........HH.", "...HHHHHHHH..."])];
  const antigravity = {
    color: "#e0a100", // Google's yellow, deepened: its red sat beside Claude's coral in charts, its green beside Codex
    pal: { o: "*#f5c542", a: "#f5c542", h: "*#5a3e2a", r: "#ef8a3c", R: "*#e8763a", B: "*#4285f4", C: "*#ea4335",
      D: "*#fbbc04", E: "*#34a853", H: "=#9ff0de", y: "*#f6c445" },
    float: [0, -1, -2, -1],
    blink: null,
    shadow: (f) => {
      const w = [14, 12, 10, 12][f];
      return P(16 - w / 2, 29, ["." + "x".repeat(w - 2) + ".", ".." + "x".repeat(w - 4) + ".."]);
    },
    tiers: {
      novice: { body: [agBody, agKnot, ...agFace, ...agRobe, agHands] },
      adept: { body: [agBody, agKnot, ...agFace, ...agRobe, agTrim, agHands, ...agGems.slice(0, 2)] },
      expert: { body: [agBody, agKnot, ...agFace, ...agRobe, agTrim, agHands, ...agGems] },
      master: { back: agHalo, body: [agBody, agKnot, ...agFace, ...agRobe, agTrim, agHands, ...agGems], aura: "255, 214, 110",
        sparks: [[5, 3], [26, 3], [4, 27], [27, 27], [24, 13]], spark: ["#ffffff", "#9ff0de"] },
    },
  };

  const CHARS = { claude, codex, copilot, bob, antigravity };

  // =====================================================================================
  // Composing a frame
  // =====================================================================================
  const BREATH = [0, 1, 0, 1];
  const cache = new Map();

  // twinkling sparks: each one rises a pixel a frame and pulses between a dot and a 5-pixel star
  function sparkPieces(points, colors, frame) {
    const out = [];
    points.forEach(([x, y], i) => {
      const phase = (frame + i) % FRAMES;
      const yy = y - phase;
      if (phase === 1) out.push({ x: x - 1, y: yy - 1, rows: [".a.", "aba", ".a."], pal: { a: "=" + colors[1], b: "=" + colors[0] } });
      else if (phase !== 3) out.push({ x, y: yy, rows: ["a"], pal: { a: "=" + colors[phase === 0 ? 1 : 0] } });
    });
    return out;
  }

  function compose(agent, tier, frame, blink, locked, stride = 0) {
    const key = [agent, tier, frame, blink ? 1 : 0, locked ? 1 : 0, stride].join("|");
    if (cache.has(key)) return cache.get(key);
    const ch = CHARS[agent];
    const spec = ch.tiers[tier] || ch.tiers.novice;
    const n = SIZE, pal = { ...BASE_PAL, ...ch.pal, ...spec.pal };
    const blank = () => new Array(n * n).fill(null);
    const letters = (pieces) => {
      const grid = blank();
      for (const piece of pieces || []) if (shows(piece, frame)) paint(grid, n, piece);
      return grid;
    };
    const colors = (pieces) => {
      const grid = blank();
      for (const piece of pieces || []) if (shows(piece, frame)) paintColors(grid, n, piece, pal);
      return grid;
    };
    // a breather's upper body sinks a pixel; a floater rises and falls whole
    const move = (grid) => shift(grid, n, ch.float ? n : ch.waist, (ch.float || BREATH)[frame]);
    let body = letters(spec.body);
    const blinkPiece = spec.blink !== undefined ? spec.blink : ch.blink;
    if (blink && blinkPiece && !locked) paint(body, n, blinkPiece);
    if (stride && !ch.float) body = lift(body, n, stride);
    body = move(body);
    const fill = locked ? body.map((c) => c && LOCKED.fill) : shade(body, n, pal);
    const line = rims(body, n, pal, locked ? LOCKED.rim : null);
    const shape = fill.map((c, i) => c || line[i]);
    const back = blank();
    paintColors(back, n, ch.shadow ? ch.shadow(frame) : P(9, 30, [".xxxxxxxxxxxx.", "..xxxxxxxxxx.."]), { x: "=#000000" });
    for (let i = 0; i < n * n; i++) if (back[i]) back[i] = SHADOW;
    if (spec.aura && !locked) aura(shape, n, spec.aura, frame % 2 === 1).forEach((c, i) => { if (c) back[i] = c; });
    if (!locked) move(colors(spec.back)).forEach((c, i) => { if (c && !shape[i]) back[i] = c; });
    const front = locked ? blank() : move(colors(spec.front));
    if (spec.sparks && !locked) for (const p of sparkPieces(spec.sparks, spec.spark, frame)) paintColors(front, n, p, p.pal);
    if (locked) for (const p of zzz(frame)) if (shows(p, frame)) paintColors(front, n, p, { z: "=" + LOCKED.z });
    if (locked) shape.forEach((c, i) => { if (c) front[i] = null; }); // the z's stay off the silhouette
    const ops = runs(back, n, []);
    runs(line, n, ops);
    runs(fill, n, ops);
    runs(front, n, ops);
    cache.set(key, ops);
    return ops;
  }
  // asleep: two z's drift beside the head
  function zzz(frame) {
    const small = ["zzz", "..z", ".z.", "zzz"];
    const big = ["zzzz", "..z.", ".z..", "zzzz"];
    return [P(24, 5 - (frame % 2), small), P(27, 1 + (frame % 2), big, [1, 2, 3])];
  }

  // any pixel map in the same style, for the Home scene's monsters and props: pieces [x, y, rows], a palette as above;
  // returns { cv, w, h, ox, oy } (logical size and offset, edge included) drawn at `scale`
  function art(pieces, pal, scale = 1, { flash = null, edge = true } = {}) {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const [x, y, rows] of pieces) {
      x0 = Math.min(x0, x); y0 = Math.min(y0, y);
      x1 = Math.max(x1, x + Math.max(...rows.map((r) => r.length))); y1 = Math.max(y1, y + rows.length);
    }
    const pad = edge ? 1 : 0, w = x1 - x0 + 2 * pad, h = y1 - y0 + 2 * pad, n = Math.max(w, h);
    const grid = new Array(n * n).fill(null);
    for (const [x, y, rows] of pieces) paint(grid, n, P(x - x0 + pad, y - y0 + pad, rows));
    const fullPal = { ...BASE_PAL, ...pal };
    const fill = flash ? grid.map((c) => c && flash) : shade(grid, n, fullPal);
    const line = edge ? rims(grid, n, fullPal, null) : [];
    const ops = runs(line.length ? line : new Array(n * n).fill(null), n, []);
    runs(fill, n, ops);
    const cv = document.createElement("canvas");
    cv.width = Math.max(1, w * scale);
    cv.height = Math.max(1, h * scale);
    const ctx = cv.getContext("2d");
    for (const [c, x, y, k] of ops) { ctx.fillStyle = c; ctx.fillRect(x * scale, y * scale, k * scale, scale); }
    return { cv, w, h, ox: x0 - pad, oy: y0 - pad };
  }

  // =====================================================================================
  // Icons: 16×16, outlined the same way
  // =====================================================================================
  const ICON_PAL = { k: "#1d2230", w: "#ffffff", e: "#d3dbe6", E: "#8d9aad", W: "#f4f7fb", o: "#b07a4a", O: "#7a4e2c",
    y: "#f6c445", Y: "#c48a1c", p: "#f7ecd4", P: "#d8c49a", q: "#9aa6b8", r: "#e5484d", R: "#a8282d",
    b: "#4f8ff7", B: "#2a5fc4", l: "#9cc2ff", g: "#3fcf7f", G: "#23874d", t: "#52cfe6", T: "#2a93b0", u: "#c9f3fb",
    v: "#a77bf3", V: "#6d47c4", m: "#f0a3ff", c: "#e8836a", C: "#b1543d", s: "#f6d2b4", S: "#dcab8a" };
  const SCROLL = [
    "................",
    "..PPPPPPPPPPP...",
    ".PpppppppppppP..",
    "..PPPPPPPPPPPp..",
    "...pqqqqqqqqp...",
    "...pppppppppp...",
    "...pqqqqqqpPp...",
    "...pppppppppp...",
    "...pqqqqqqqqp...",
    "...pppppppppp...",
    "...pqqqqpppPp...",
    "...pppppppppp...",
    "..PPPPPPPPPPPP..",
    ".PpppppppppppP..",
    "..PPPPPPPPPPP...",
    "................",
  ];
  const ICONS = {
    craft: [[0, 0, [
      "................",
      "................",
      "..WWWeeeeeeEE...",
      "..WeeeeeeeeEE...",
      "..WeeeeeeeeEE...",
      "..EEEEEEEEEEE...",
      "......oO........",
      "......oO........",
      "......oO........",
      "......oO........",
      "......oO........",
      "......oO........",
      "......oO........",
      "......OO........",
    ]]],
    data: [[0, 0, [
      "................",
      "....tttttttt....",
      "..ttuuuuuuuutt..",
      "..tuuuuuuuuuuT..",
      "..ttuuuuuuuuTT..",
      "..wttttttttttT..",
      "..TTTTTTTTTTTT..",
      "..wttttttttttT..",
      "..tttttttttttT..",
      "..TTTTTTTTTTTT..",
      "..wttttttttttT..",
      "..tttttttttttT..",
      "...ttttttttTT...",
      ".....tttttT.....",
    ]]],
    ops: [[0, 0, [
      "................",
      "......eeee......",
      "...e..eeee..e...",
      "..eeeeWWeeeeee..",
      "...eWeeeeeeeE...",
      "...eeeEEEEeEE...",
      ".eeeeE....EeEEE.",
      ".eeeeE....EeEEE.",
      ".eeeeE....EeEEE.",
      ".eeeeE....EeEEE.",
      "...eeeEEEEeEE...",
      "...eeeeeeeeEE...",
      "..eeeeeeeeEEEE..",
      "...E..EEEE..E...",
      "......EEEE......",
    ]]],
    guard: [[0, 0, [
      "................",
      "..eeeeeeeeeeee..",
      "..ellllllbbbbE..",
      "..ellllllbbbbE..",
      "..ellllwwbbbbE..",
      "..elllwwwwbbbE..",
      "..ellwwllwwbbE..",
      "..elwwllbbwwbE..",
      "...elllllbbbE...",
      "...elllllbbbE...",
      "....ellllbbE....",
      ".....elllbE.....",
      "......elbE......",
      ".......eE.......",
    ]]],
    arcana: [[0, 0, [
      "................",
      ".....vvvvvv.....",
      "...vvwwvvvvvv...",
      "..vvwvvvvvvvVV..",
      "..vvvvvmvvvvVV..",
      "..vvvvmmmvvVVV..",
      "..vvvvvmvvVVVV..",
      "...vvvvvVVVVV...",
      ".....VVVVVV.....",
      "....yyyyyyyy....",
      ".....yYYYYy.....",
      "....yyyyyyyY....",
      "...YYYYYYYYYY...",
    ]]],
    lore: [[0, 0, [
      "................",
      "................",
      "..ppppp..ppppp..",
      ".ppppppPPpppppp.",
      ".pqqqqpPPpqqqqp.",
      ".ppppppPPpppppp.",
      ".pqqqppPPpqqqqp.",
      ".ppppppPPpppppp.",
      ".pqqqqpPPpqqqpp.",
      ".ppppppPPpppppp.",
      ".cccccccCccccccc",
      "..cccccCCcccccC.",
    ]]],
    quest: [[0, 0, SCROLL], [8, 8, [".kkkk.", "krrrrk", "krRwrk", "krRRrk", ".krrk.", ".rkkr.", ".r..r."]]],
    "quest-done": [[0, 0, SCROLL],
      [7, 7, [".......k", "......kg", ".k...kgg", "kgk.kggk", "kggkggk.", ".kgggk..", "..kgk...", "...k...."]]],
    "quest-evidence": [[0, 0, SCROLL],
      [6, 6, [".kkkk...", "kuuwuk..", "kuwuuk..", "kuuuuk..", "kuuuuk..", ".kkkkO..", ".....OO.", "......OO", "......k."]]],
    star: [[0, 0, [
      "................",
      ".......yy.......",
      ".......yy.......",
      "......ywyy......",
      "......yyyY......",
      ".yyyyyyyyyyyyyY.",
      "..yyywyyyyyyYY..",
      "...yyyyyyyyYY...",
      "....yyyyyyYY....",
      "....yyyyyyYY....",
      "...yyyyYYyyYY...",
      "...yyyY..YyyY...",
      "..yyY......YyY..",
      "..yY........YY..",
    ]]],
    xp: [[0, 0, [
      "................",
      "................",
      "....gggggggg....",
      "...gwwgggggGg...",
      "..gwggggggggGG..",
      "..GGGGGGGGGGGG..",
      "...gggggggGGG...",
      "....ggggggGG....",
      ".....gggGGG.....",
      "......ggGG......",
      ".......gG.......",
    ]]],
    lock: [[0, 0, [
      "................",
      ".....eeeeee.....",
      "....eE....Ee....",
      "....eE....Ee....",
      "....eE....Ee....",
      "..yyyyyyyyyyyY..",
      "..ywyyyyyyyyYY..",
      "..yyyyyyyyyyYY..",
      "..yyyyykkyyyYY..",
      "..yyyyykkyyyYY..",
      "..yyyyyykyyyYY..",
      "..yyyyyyyyyyYY..",
      "..YYYYYYYYYYYY..",
    ]]],
    party: [[0, 0, [
      "................",
      "..cccc....tttt..",
      ".ccccccc.tttttT.",
      ".cssssss.ssssst.",
      ".skssks..skssks.",
      ".ssssss..ssssss.",
      "..ssbbbbbbbbss..",
      "...bbbbbbbbbB...",
      "...bbbbbbbbbB...",
      "...bssssssssB...",
      "...sskssssksS...",
      "...sssssssssS...",
      "....SSSSSSSS....",
    ]]],
    journey: [[0, 0, [
      "................",
      "..ppppPPPPpppp..",
      "..ppppPPPPprpr..",
      "..ppppPPPPpprp..",
      "..ppppPPPPrprp..",
      "..ppppPPrPpppp..",
      "..pppppPPPpppp..",
      "..pppprPPPpppp..",
      "..pppppPPPpppp..",
      "..ppprpPPPpppp..",
      "..ppppPPPPpppp..",
      "..prppPPPPpppp..",
      "..ppppPPPPpppp..",
      "..PPPPqqqqPPPP..",
    ]]],
  };
  const iconCache = new Map();
  const ICON_FLAT = Object.fromEntries(Object.entries(ICON_PAL).map(([k, v]) => [k, "=" + v]));
  function iconOps(name) {
    if (iconCache.has(name)) return iconCache.get(name);
    const n = ICON, grid = new Array(n * n).fill(null);
    for (const [x, y, rows] of ICONS[name] || []) paint(grid, n, P(x, y, rows));
    const ops = runs(rims(grid, n, ICON_FLAT, null), n, []);
    runs(shade(grid, n, ICON_FLAT), n, ops);
    iconCache.set(name, ops);
    return ops;
  }

  // =====================================================================================
  // Public API
  // =====================================================================================
  const drawn = new WeakMap(); // canvas → { n, color, paint() }: what it shows, so a sparkle can repaint it underneath
  const motionQuery = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  const reduced = () => !!(motionQuery && motionQuery.matches);
  const whole = (v, fallback) => Math.max(1, Math.round(Number(v) || fallback));

  // stride (0, 1, 2): a walking step for the Home scene, one foot lifted
  function draw(canvas, { agent, tier = "novice", frame = 0, scale = 4, locked = false, blink = false, stride = 0 } = {}) {
    if (!canvas) return;
    const sc = whole(scale, 4);
    const f = ((Math.round(Number(frame) || 0) % FRAMES) + FRAMES) % FRAMES;
    const st = stride === 1 || stride === 2 ? stride : 0;
    const ops = CHARS[agent] ? compose(agent, TIERS.includes(tier) ? tier : "novice", f, !!blink, !!locked, st) : [];
    const paintIt = () => blit(canvas, SIZE, sc, ops);
    drawn.set(canvas, { n: SIZE, color: locked ? LOCKED.rim : color(agent), paint: paintIt });
    paintIt();
  }

  function icon(canvas, name, scale = 2) {
    if (!canvas) return;
    const sc = whole(scale, 2), ops = iconOps(name);
    const paintIt = () => blit(canvas, ICON, sc, ops);
    drawn.set(canvas, { n: ICON, color: "#f6c445", paint: paintIt });
    paintIt();
  }

  function color(agent) { return CHARS[agent] ? CHARS[agent].color : "#8a94a3"; }

  // ---- the idle loop: one timer for every animated canvas. It ticks four times a second; a sprite's frame moves on
  // every other tick and it blinks for one tick in sixteen. Each canvas starts at its own phase, so a party doesn't
  // breathe in step.
  const live = new Map(); // canvas → { opts, phase, frame, blink, seen, waited }
  const TICK = 250;
  let timer = 0, ticks = 0, joined = 0;

  function animate(canvas, { agent, tier = "novice", scale = 4, locked = false } = {}) {
    if (!canvas) return () => {};
    const opts = { agent, tier, scale, locked };
    live.delete(canvas);
    draw(canvas, { ...opts, frame: 0 });
    if (reduced()) return () => {};
    live.set(canvas, { opts, phase: (joined++ * 5) % 16, frame: 0, blink: false, seen: canvas.isConnected, waited: 0 });
    if (!timer) timer = setInterval(tick, TICK);
    return () => { live.delete(canvas); };
  }
  function tick() {
    ticks++;
    const still = reduced();
    for (const [canvas, e] of live) {
      // gone from the page: stop. One that never joined it is given 5 s before it is dropped.
      if (canvas.isConnected) e.seen = true;
      else { if (e.seen || ++e.waited > 20) live.delete(canvas); continue; }
      if (document.hidden) continue;
      const t = ticks + e.phase;
      const frame = still ? 0 : Math.floor(t / 2) % FRAMES, blink = !still && t % 16 === 13;
      if (frame === e.frame && blink === e.blink) continue;
      e.frame = frame;
      e.blink = blink;
      draw(canvas, { ...e.opts, frame, blink });
    }
    if (!live.size) { clearInterval(timer); timer = 0; }
  }

  // ---- sparkle: a burst of pixel stars from the middle, painted over whatever the canvas shows, for 800 ms. One
  // animation frame loop serves every sparkling canvas; the canvas is repainted clean when it ends.
  const SPARKLE_MS = 800;
  const sparks = new Map(); // canvas → { t0, parts, base, n }
  let raf = 0;

  function sparkle(canvas) {
    if (!canvas || reduced() || typeof window.requestAnimationFrame !== "function") return;
    const rec = drawn.get(canvas);
    const n = rec ? rec.n : SIZE;
    let base = rec ? rec.paint : null;
    if (!base) {
      // not ours: keep what it shows and paint the burst over a copy of it
      try {
        const ctx = canvas.getContext("2d"), img = ctx.getImageData(0, 0, canvas.width, canvas.height);
        base = () => ctx.putImageData(img, 0, 0);
      } catch { return; }
    }
    const tint = rec ? rec.color : "#f6c445";
    const parts = [];
    const count = n === SIZE ? 14 : 9;
    for (let i = 0; i < count; i++) {
      const a = (i / count) * Math.PI * 2 + (i % 2 ? 0.2 : -0.1);
      parts.push({ dx: Math.cos(a), dy: Math.sin(a), reach: n * (i % 3 === 0 ? 0.48 : i % 3 === 1 ? 0.38 : 0.3),
        color: ["#ffffff", "#ffe27a", tint][i % 3], big: i % 3 === 0 });
    }
    sparks.set(canvas, { t0: performance.now(), parts, base, n });
    if (!raf) raf = window.requestAnimationFrame(burst);
  }
  function burst(now) {
    raf = 0;
    for (const [canvas, s] of sparks) {
      const t = (now - s.t0) / SPARKLE_MS;
      // repaint what is under the burst first: the sprite's current frame, or the copy
      const rec = drawn.get(canvas);
      (rec ? rec.paint : s.base)();
      if (t >= 1 || !canvas.isConnected) { sparks.delete(canvas); continue; }
      const ctx = canvas.getContext("2d");
      if (!ctx) continue;
      const sc = Math.max(1, Math.floor(canvas.width / s.n)), mid = s.n / 2;
      const step = Math.floor(t * 12) / 12; // a dozen positions in all: pixel-art motion, not a smooth glide
      const out = 1 - (1 - step) * (1 - step);
      const dot = (x, y, c) => { ctx.fillStyle = c; ctx.fillRect(Math.round(x) * sc, Math.round(y) * sc, sc, sc); };
      if (step < 0.34) { // a ring that opens first
        const r = 2 + step * s.n * 0.9;
        ctx.globalAlpha = 0.55;
        for (let i = 0; i < 24; i++) {
          const a = (i / 24) * Math.PI * 2;
          dot(mid - 0.5 + Math.cos(a) * r, mid - 0.5 + Math.sin(a) * r, "#ffffff");
        }
        ctx.globalAlpha = 1;
      }
      for (const p of s.parts) {
        if (step > 0.75 && (Math.round(step * 12) + (p.big ? 1 : 0)) % 2) continue; // twinkle out
        const x = mid - 0.5 + p.dx * p.reach * out, y = mid - 0.5 + p.dy * p.reach * out;
        dot(x, y, p.color);
        if (p.big && step < 0.75) { dot(x - 1, y, p.color); dot(x + 1, y, p.color); dot(x, y - 1, p.color); dot(x, y + 1, p.color); }
      }
    }
    if (sparks.size) raf = window.requestAnimationFrame(burst);
  }

  window.Sprites = Object.freeze({ SIZE, AGENTS: Object.freeze(AGENTS.slice()), TIERS: Object.freeze(TIERS.slice()),
    draw, animate, sparkle, icon, color, art });
})();
