/* Party sprites: the coding agents as 32×32 pixel-art characters, and the page's 16×16 icons. No images, no build step:
   every sprite is a stack of pixel maps (strings, one palette letter per pixel, "." for none) painted on a <canvas> with
   fillRect, so the page's Content-Security-Policy (no data: or blob: images) has nothing to object to. */
(() => {
  "use strict";

  const SIZE = 32, ICON = 16, FRAMES = 4;
  const AGENTS = ["claude", "codex", "copilot", "bob", "antigravity"];
  const TIERS = ["novice", "adept", "expert", "master"];
  const LOCKED = { fill: "#7f8999", rim: "#c3cad5", z: "#97a1b1" };
  const SHADOW = "rgba(9, 17, 30, 0.2)";

  // =====================================================================================
  // Pieces: a pixel map placed at (x, y). `on` lists the frames it shows in (all when left out).
  // =====================================================================================
  const P = (x, y, rows, on) => ({ x, y, rows, on });
  const shows = (piece, frame) => !piece.on || piece.on.includes(frame);

  function paint(grid, n, piece, pal) {
    piece.rows.forEach((row, dy) => {
      for (let dx = 0; dx < row.length; dx++) {
        const c = row[dx], x = piece.x + dx, y = piece.y + dy;
        if (c === "." || c === " " || x < 0 || y < 0 || x >= n || y >= n) continue;
        if (c === "_") grid[y * n + x] = null; // "_" cuts a pixel out of what is under it
        else if (pal[c]) grid[y * n + x] = pal[c];
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
  const filled = (grid, n, x, y) => x >= 0 && y >= 0 && x < n && y < n && !!grid[y * n + x];
  // the 1-px outline: every empty pixel that touches the shape side-on (corners stay open, as hand-drawn outlines do)
  function outline(grid, n, color) {
    const out = new Array(n * n).fill(null);
    for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
      if (grid[y * n + x]) continue;
      const touches = filled(grid, n, x - 1, y) || filled(grid, n, x + 1, y) || filled(grid, n, x, y - 1) || filled(grid, n, x, y + 1);
      if (touches) out[y * n + x] = color;
    }
    return out;
  }
  // a glow that hugs the silhouette: a ring a pixel wide around the outlined shape, and on a pulse a fainter one outside it
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
  // a grid becomes fillRect runs: [color, x, y, width]
  function runs(grid, n, ops) {
    for (let y = 0; y < n; y++) {
      let x = 0;
      while (x < n) {
        const c = grid[y * n + x];
        if (!c) { x++; continue; }
        let w = 1;
        while (x + w < n && grid[y * n + x + w] === c) w++;
        ops.push([c, x, y, w]);
        x += w;
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

  // =====================================================================================
  // Shared parts. Palette letters every character uses: k line and outline, s/S skin, c blush, m mouth, w white.
  // =====================================================================================
  const HEAD = P(10, 7, [
    "..ssssssss..",
    ".ssssssssss.",
    "sssssssssssS",
    "sssssssssssS",
    "sssssssssssS",
    "sssssssssssS",
    "sswksssswksS",
    "sskksssskksS",
    "sccssssssccS",
    "sssssmmssssS",
    ".SSSSSSSSSS.",
  ]);
  const BLINK = P(12, 13, ["ss....ss", "kk....kk"]);
  // a ring of radius r around (cx, cy) a pixel wide, one palette letter per quarter: top-left, top-right, bottom-right,
  // bottom-left
  function ring(cx, cy, r, letters) {
    const x0 = Math.floor(cx - r - 1), y0 = Math.floor(cy - r - 1), rows = [];
    for (let y = y0; y <= cy + r + 1; y++) {
      let row = "";
      for (let x = x0; x <= cx + r + 1; x++) {
        const dx = x + 0.5 - cx, dy = y + 0.5 - cy;
        row += Math.abs(Math.hypot(dx, dy) - r) <= 0.5 ? letters[dy < 0 ? (dx < 0 ? 0 : 1) : (dx < 0 ? 3 : 2)] : ".";
      }
      rows.push(row);
    }
    return P(x0, y0, rows);
  }
  const BASE_PAL = { w: "#ffffff" };

  // =====================================================================================
  // Characters
  // =====================================================================================

  // ---- claude: a sage in terracotta robes; the apprentice grows a beard, a hat and a staff whose tip glows
  const claudeHair = P(9, 5, [
    "....hhhhhh....",
    "..hhhhhhhhhh..",
    ".hhhhHhhhhhhH.",
    "hhhhHhhhhHhhHH",
    "hhhHhhhhHhhhHH",
    "hhhhhhhhhhhhHH",
    "hhh.hhhh...hHH",
    "hh..hh.....hHH",
    "hh..........HH",
    "hh..........HH",
    ".h..........H.",
  ]);
  const claudeCap = P(11, 1, [
    "....rq....",
    "...rRRq...",
    "...rRRq...",
    "..rRRRRq..",
    ".rRRRRRRq.",
    "qqqqqqqqqq",
  ]);
  const claudeRobe = P(8, 18, [
    "..rRRRRqqRRRRq..",
    ".rRRRRRqqRRRRRq.",
    ".rRRRRRRRRRRRRq.",
    "rRRqRRRRRRRRqRRq",
    "rRRqnnnnnnnnqRRq",
    "qqqrRRRRRRRRqqqq",
    ".ssrRRRRRRRRqss.",
    "..rRRRRRRRRRRq..",
    "..rRRRRRRRRRRq..",
    ".rRRRRRRRRRRRRq.",
    ".qqqqqqqqqqqqqq.",
    "...BBB....BBB...",
  ]);
  const claudeWand = P(6, 16, [
    ".G.",
    "GgG",
    ".b.",
    ".b.",
    ".b.",
    ".b.",
    ".b.",
    ".b.",
    "sss",
    "sss",
    ".B.",
  ]);
  const claudeStaff = P(4, 4, [
    "..bb..",
    ".bBBb.",
    "bB..Bb",
    "bB..bB",
    ".bB.bB",
    "...bB.",
    "...bB.",
    "...bB.",
    "...bB.",
    "...bB.",
    "...bB.",
    "...bB.",
    "...bB.",
    "...bB.",
    "...bB.",
    "...bB.",
    "...bB.",
    "...bB.",
    "...bB.",
    "..sss.",
    "..sss.",
    "...bB.",
    "...bB.",
    "...bB.",
    "...bB.",
    "...BB.",
  ]);
  const claudeHat = P(7, 0, [
    "...........RRq....",
    ".........rRRq.....",
    ".......rRRRq......",
    "......rRRRRq......",
    "......rRRRRq......",
    ".....rRRRRRRq.....",
    "....rRRRRRRRRq....",
    "...rRRRRRRRRRRq...",
    ".rRRRRRRRRRRRRRRq.",
    "qqqqqqqqqqqqqqqqqq",
  ]);
  const claudeMantle = P(9, 18, [
    ".nnnnnnnnnnnN.",
    "nnnnnnnnnnnnNN",
    "yyyyyyyyyyyyyy",
  ]);
  const claudeBeard = P(10, 15, [
    "h..........H",
    "hh.hh..hh.HH",
    "hhhhhhhhhhHH",
    ".hhhhhhhhhH.",
    "..hhhhhhHH..",
    "....hhhH....",
  ]);
  const claudeLongBeard = P(10, 15, [
    "h..........H",
    "hh.hh..hh.HH",
    "hhhhhhhhhhHH",
    "hhhhhhhhhhHH",
    ".hhhhhhhhhH.",
    ".hhhhhhhhHH.",
    "..hhhhhhHH..",
    "..hhhhhhH...",
    "...hhhhH....",
    "....hhH.....",
  ]);
  const claudeTrim = [
    P(8, 28, [".yyyyyyyyyyyyyy."]),
    P(8, 23, ["yyy.........yyyy"]),
    P(10, 7, ["yyyyyyyyyyyy"]),
    P(14, 3, [".y.", "yYy", ".y."]),
  ];
  const claudeStar = P(18, 0, ["..y..", ".yGy.", "yGGGy", ".yYy.", "..y.."]);
  const claudeOrb = [
    P(4, 1, ["..gg..", ".gGGg.", "gGGGGg", "gGGGGg", ".gGGg."]),
    P(3, 0, ["..g..g..", "........", "g......g", "........", "........", "g......g", "..g..g.."], [1, 3]),
  ];
  const claude = {
    color: "#d97757",
    pal: { k: "#3d1e18", s: "#f8d8bf", S: "#e6b293", c: "#f3a08f", m: "#b04f45",
      h: "#f3f0ea", H: "#c5bfb3", R: "#d97757", r: "#f0a283", q: "#a5503b",
      n: "#f6e7c8", N: "#d8c19a", y: "#f2c14e", Y: "#c38a1f", b: "#9a6542", B: "#5f3a25",
      g: "#ffd66b", G: "#fff6d6" },
    waist: 22,
    tiers: {
      novice: { body: [HEAD, claudeHair, claudeCap, claudeRobe, claudeWand] },
      adept: { body: [HEAD, claudeHair, claudeRobe, claudeMantle, claudeBeard, claudeHat, claudeStaff] },
      expert: { body: [HEAD, claudeHair, claudeRobe, ...claudeTrim.slice(0, 2), claudeMantle, claudeLongBeard, claudeHat,
        ...claudeTrim.slice(2), claudeStaff], front: claudeOrb },
      master: { body: [HEAD, claudeHair, claudeRobe, ...claudeTrim.slice(0, 2), claudeMantle, claudeLongBeard, claudeHat,
        ...claudeTrim.slice(2), claudeStaff, claudeStar], front: claudeOrb, aura: "255, 190, 110",
        sparks: [[2, 12], [27, 6], [26, 20], [3, 24], [28, 13]], spark: ["#fff6d6", "#ffd66b"] },
    },
  };

  // ---- codex: a hooded tinkerer in slate and teal; the wrench gets bigger, then a glowing quill-blade and a drone
  const codexHair = P(9, 3, [
    ".....h...h....",
    "..h.hh..hh.h..",
    "..hhhhhhhhhhh.",
    ".hhhhhhhhhhhhh",
    ".hhhhiihhhhhhH",
    "hhhiihhhhhhhHH",
    "hhhhhhhhhhhhHH",
    "hhhhhhhhhhhhHH",
    "hhh.hhh.hh.hHH",
    "hh..h...h...HH",
    "h...........HH",
    "h............H",
  ]);
  const codexBody = P(9, 18, [
    "..Ttttttttu...",
    ".lddddtuddddD.",
    "llddddtuddddDD",
    "llddddtuddddDD",
    "ldbbbbebbbbbDD",
    "ssdddddddddDss",
    "..pppP..pppP..",
    "..pppP..pppP..",
    "..pppP..pppP..",
    "..BBBB..BBBB..",
    "..BBBB..BBBB..",
    ".BBBBB..BBBBB.",
  ]);
  const codexWrench = P(7, 16, ["e.e", "eEe", ".e.", ".e.", ".e.", ".e.", ".e.", ".ss", ".E."]);
  const codexBigWrench = P(5, 10, [
    "ee.ee",
    "eE.Ee",
    "eEEEe",
    ".eEe.",
    "..eE.",
    "..eE.",
    "..eE.",
    "..eE.",
    "..eE.",
    "..eE.",
    "..eE.",
    "..eE.",
    "..eE.",
    "..sss",
    "..eE.",
    "..EE.",
  ]);
  const codexCloak = P(7, 18, [
    "..lddddddddddddD..",
    ".llddddddddddddDD.",
    ".llddddddddddddDD.",
    ".llddddddddddddDD.",
    "llddddddddddddddDD",
    "llddddddddddddddDD",
    "lduuuuuuuuuuuuuuDD",
    "lduuuuuuuuuuuuuuDD",
    "lduuuuuuuuuuuuuuDD",
    "lduuuuuuuuuuuuuuDD",
    "ld.u.uuuuuuuu.u.dD",
  ]);
  const codexHood = P(8, 3, [
    ".......dD.......",
    "......lddD......",
    ".....lddddD.....",
    "...llddddddDD...",
    "..lddddddddddD..",
    ".lddddddddddddD.",
    ".lddDDDDDDDDddD.",
    "ldd..........ddD",
    "ldd..........ddD",
    "ldd..........ddD",
    "ldd..........ddD",
    "ldd..........ddD",
    "ldd..........ddD",
    "ldd..........ddD",
    "ldd..........ddD",
    "lldddddddddddddD",
    "llddddddddddddDD",
  ]);
  const codexShade = P(11, 10, ["SSSSSSSSSS", "SSSSSSSSSS"]);
  const codexMask = P(11, 15, ["TTTTTTTTTT", "tttttttttt", ".tttuuttt."]);
  const codexTrim = [P(11, 9, ["tttttttttt"]), P(8, 3, [".......tt......."]), P(7, 28, ["tt.t.tttttttt.t.tt"]),
    P(8, 19, ["t", "t", "t", "t", "t", "t", "t", "t", "t"]), P(23, 19, ["t", "t", "t", "t", "t", "t", "t", "t", "t"])];
  const codexEyes = P(12, 13, ["T.....T"]);
  const codexQuill = P(20, 11, [
    "....T",
    "...TG",
    "...TG",
    "..TGt",
    "..TGt",
    "..TGt",
    "..TG.",
    "..Gt.",
    ".eeee",
    "..b..",
    "..b..",
    ".sss.",
  ]);
  const codexQuillGlow = [P(19, 9, [".....G", "......", "......", ".G....", "......"], [0, 2]),
    P(19, 9, ["......", "....G.", "......", "......", "G....."], [1, 3])];
  const drone = (y, on) => P(24, y, ["...T..", "..ee..", ".eeeeE", "eGTTeE", "eeeeEE", ".EEEE."], on);
  const codexDrone = [drone(2, [0, 1]), drone(3, [2, 3])];
  const codexThrust = [P(26, 8, ["t.t"], [0, 1]), P(26, 9, [".T."], [2, 3])];
  const codex = {
    color: "#14b88c",
    pal: { k: "#111a22", s: "#efc4a0", S: "#cf9a77", c: "#f0a08a", m: "#9c4b3e",
      h: "#323b4b", H: "#222a35", i: "#56657e", d: "#4b5b70", D: "#34404f", l: "#6d8098",
      t: "#14b88c", T: "#7cf2cf", u: "#0d7a60", G: "#eafff8",
      p: "#2e3846", P: "#232b36", b: "#8a5a3a", B: "#4f3424", e: "#d5dde6", E: "#8e9aab" },
    waist: 23,
    tiers: {
      novice: { body: [HEAD, codexHair, codexBody, codexWrench] },
      adept: { body: [codexCloak, HEAD, codexHood, codexShade, codexBody, codexMask, codexBigWrench] },
      expert: { body: [codexCloak, HEAD, codexHood, codexShade, codexBody, codexMask, ...codexTrim, codexEyes, codexBigWrench,
        codexQuill], front: codexQuillGlow },
      master: { body: [codexCloak, HEAD, codexHood, codexShade, codexBody, codexMask, ...codexTrim, codexEyes, codexBigWrench,
        codexQuill, ...codexDrone], front: [...codexQuillGlow, ...codexThrust], aura: "60, 230, 180",
        sparks: [[3, 9], [28, 15], [2, 22], [29, 26], [14, 0]], spark: ["#eafff8", "#3fe0b4"] },
    },
  };

  // ---- copilot: a sky navigator in a violet flight suit; cap and goggles, then the jacket, spyglass and golden wings
  // a scarf tail 2 pixels deep: tops[i] is the row of column x + i, stripes[i] its colour (its shade is the capital)
  const ribbon = (x, tops, stripes, on) => {
    const y0 = Math.min(...tops), rows = [];
    for (let y = y0; y <= Math.max(...tops) + 1; y++) {
      rows.push(tops.map((t, i) => y === t ? stripes[i] : y === t + 1 ? stripes[i].toUpperCase() : ".").join(""));
    }
    return P(x, y0, rows, on);
  };
  const copilotCap = P(9, 5, [
    "....LLLLLL....",
    "..LLbbbbbbBB..",
    ".LbbbbbBbbbbB.",
    "LbbbbbbBbbbbBB",
    "bqqqqqbbqqqqqB",
    "bqgGgqqqqgGgqB",
    "bqgggqbbqgggqB",
    "fqqqqqffqqqqqf",
    "ff..........ff",
    "bf..........fB",
    "bf..........fB",
    "bb..........BB",
    ".b..........B.",
  ]);
  const copilotBody = P(9, 18, [
    "..aannaannaA..",
    ".uvvvvvvvvvvV.",
    "uuvvvvvVvvvvVV",
    "uuvvvvvVvvvvVV",
    "uvbbbbjjbbbbVV",
    "ssvvvvvvvvvVss",
    "..vvvV..vvvV..",
    "..vvvV..vvvV..",
    "..vvvV..vvvV..",
    "..BBBB..BBBB..",
    "..BBBB..BBBB..",
    ".BBBBB..BBBBB.",
  ]);
  const copilotTail = P(19, 19, ["na", "an", "NA"]);
  const copilotJacket = P(8, 18, [
    ".ffff......ffff.",
    "fffff......fffff",
    ".Lbbb......bbbB.",
    ".Lbbb......bbbB.",
    ".LbbB......BbbB.",
    ".ssBBBBBBBBBBss.",
  ]);
  const copilotScarf = [
    ribbon(20, [18, 18, 17, 17, 17, 18, 18, 19, 19], "aannaanna", [0, 2]),
    ribbon(20, [18, 18, 18, 19, 19, 18, 17, 17, 18], "aannaanna", [1, 3]),
  ];
  const copilotCompass = [P(6, 21, [".jj.", "jgGj", "jGgj", ".jj."]), P(9, 23, ["ss"])];
  const copilotSpyglass = P(1, 19, [
    "jjJ.......",
    "gjJBjjJ...",
    "GjJBjjJBjs",
    "gjJBJJJBJs",
    "JJJ.......",
  ]);
  const copilotTrim = [P(12, 19, ["j......j"]), P(12, 20, ["j......j"]), P(12, 21, ["j......j"]), P(12, 22, ["j......j"])];
  const copilotGlint = [P(11, 10, ["w......w"], [0, 2]), P(1, 20, ["G"], [1, 3]), P(0, 19, [".G.", "G.G", ".G."], [1])];
  const wing = (x, rows) => P(x, 5, rows);
  const copilotWings = [
    wing(2, ["n......", "nnn....", ".nnnn..", "..NNnnj", "nnnnnnj", ".NNNnnj", "...NNj."]),
    wing(23, ["......n", "....nnn", "..nnnn.", "jnnNN..", "jnnnnnn", "jnnNNN.", ".jNN..."]),
  ];
  const copilot = {
    color: "#8b6cf0",
    pal: { k: "#1e1735", s: "#f6cfae", S: "#dca184", c: "#f4a094", m: "#a5504a",
      L: "#a8774f", b: "#7a4e33", B: "#523221", f: "#f3e6cc", j: "#e6b64f", J: "#a97a26", q: "#8e9aab",
      g: "#9fe6ff", G: "#effcff", v: "#9a7ff5", V: "#6447cc", u: "#c1b3ff", r: "#4d3699",
      a: "#4fb0ff", A: "#2a7fd4", n: "#f5f8ff", N: "#c4d1e8" },
    waist: 23,
    tiers: {
      novice: { body: [HEAD, copilotCap, copilotBody, copilotTail] },
      adept: { body: [HEAD, copilotCap, copilotBody, copilotJacket, copilotTail, ...copilotScarf, ...copilotCompass] },
      expert: { pal: { q: "#e6b64f" }, body: [HEAD, copilotCap, copilotBody, copilotJacket, ...copilotTrim, copilotTail,
        ...copilotScarf, copilotSpyglass], front: copilotGlint },
      master: { pal: { q: "#e6b64f" }, body: [HEAD, copilotCap, copilotBody, copilotJacket, ...copilotTrim, copilotTail,
        ...copilotScarf, copilotSpyglass, ...copilotWings], front: copilotGlint, aura: "170, 150, 255",
        sparks: [[2, 15], [29, 12], [3, 25], [28, 26], [15, 1]], spark: ["#ffffff", "#9fe6ff"] },
    },
  };

  // ---- bob: a knight in blue plate; the squire's buckler becomes a tower shield, then a crest and a crown
  const bobHair = P(9, 5, [
    "....hhhhhh....",
    "..hhhhhhhhhh..",
    ".hhhhhhhhhhhH.",
    "hhhhhhhhhhhhHH",
    "hhhhhhhhhhhhHH",
    "hhhhhhhhhhhhHH",
    "hhhhhhhhhhhhHH",
    "hh.hhh.hhhh.HH",
    "h............H",
  ]);
  const bobBody = P(9, 18, [
    "..lbbbbbbbbB..",
    ".llbbbbbbbbBB.",
    "llbbbbbbbbbbBB",
    "llbbbbbbbbbbBB",
    "lbLLLLyyLLLLBB",
    "ssbbbbbbbbbBss",
    "..pppP..pppP..",
    "..pppP..pppP..",
    "..pppP..pppP..",
    "..MMMM..MMMM..",
    "..MMMM..MMMM..",
    ".MMMMM..MMMMM.",
  ]);
  const bobBuckler = P(20, 19, [".eeee.", "eooooe", "eoeEoe", "eoEEoe", "eooooe", ".eeee."]);
  const bobStick = P(7, 15, [".o..", ".oO.", ".oO.", ".oO.", ".oO.", ".oO.", "OOOO", ".M..", ".sss", ".O.."]);
  const bobHelm = P(9, 4, [
    "....eeeeee....",
    "..eWeeeeeeee..",
    ".eWeeeeeeeeeE.",
    "eWeeeeeeeeeeEE",
    "eeeeeeeeeeeeEE",
    "eeeeeeeeeeeeEE",
    "EEEEEEEEEEEEEE",
    "ee....ee....EE",
    "ee....ee....EE",
    "ee....ee....EE",
    "ee..........EE",
    "ee..........EE",
    ".e..........E.",
  ]);
  const bobPlate = P(11, 18, ["WeeeeeeeeE", "lbbbbbbbbB", "lbbbllbbbB", "lbbbbbbbbB"]);
  const bobSword = P(4, 9, [
    "..W....",
    ".WeE...",
    ".WeE...",
    ".WeE...",
    ".WeE...",
    ".WeE...",
    ".WeE...",
    ".WeE...",
    ".WeE...",
    ".WeE...",
    ".WeE...",
    "yyyyy..",
    "..M....",
    "..M....",
    "..sssss",
    "..y....",
  ]);
  const bobShield = P(19, 13, [
    ".eeeeeee.",
    "eWllllllE",
    "elbbbbbBE",
    "elbbbbbBE",
    "elbbbbbBE",
    "elbbbbbBE",
    "elbbbbbBE",
    "elbbbbbBE",
    "elbbbbbBE",
    "elbbbbbBE",
    "elbbbbbBE",
    "elbbbbbBE",
    ".elbbbBE.",
    "..eEEEE..",
  ]);
  const bobEmblem = P(21, 16, ["..W..", ".WeW.", "WeEeW", ".WeW.", "..W.."]);
  const bobPauldrons = [P(8, 17, [".lll", "lbbb", "lbbB", ".BB."]), P(20, 17, ["bbl.", "bbbB", "BbBB", ".BB."])];
  const bobGreaves = [P(11, 24, ["eeeE..eeeE", "WeeE..WeeE", "eeeE..eeeE"])];
  const bobCrest = P(9, 9, ["llllllllllllll"]);
  const bobCrown = [P(11, 1, ["y..y..y..y", "yyyyyyyyyy", "yYyyYYyyYy"]), P(19, 0, ["..lll", ".lbbb", "lbbB."])];
  const bobGleam = [P(6, 9, ["w"], [0]), P(5, 9, [".w.", "w.w", ".w."], [1]), P(23, 18, ["w"], [2])];
  const bob = {
    color: "#0f62fe",
    pal: { k: "#0b1633", s: "#f1c7a3", S: "#d69c78", c: "#f2a08e", m: "#a24c42",
      h: "#7a5133", H: "#553722", b: "#0f62fe", B: "#0043ce", l: "#78a9ff", D: "#002d9c",
      e: "#dde1e6", E: "#a2a9b0", W: "#f8fafc", o: "#b07a4a", O: "#7a4e2c", L: "#7d5137",
      M: "#553522", p: "#3d4a63", P: "#2b3548", y: "#f1c21b", Y: "#b28600" },
    waist: 23,
    tiers: {
      novice: { body: [HEAD, bobHair, bobBody, bobStick, bobBuckler] },
      adept: { body: [HEAD, bobHelm, bobBody, bobPlate, bobSword, bobShield] },
      expert: { body: [HEAD, bobHelm, bobCrest, bobBody, bobPlate, ...bobGreaves, ...bobPauldrons, bobSword, bobShield, bobEmblem],
        front: bobGleam },
      master: { body: [HEAD, bobHelm, bobCrest, ...bobCrown, bobBody, bobPlate, ...bobGreaves, ...bobPauldrons, bobSword,
        bobShield, bobEmblem], front: bobGleam, aura: "90, 150, 255",
        sparks: [[3, 5], [29, 4], [2, 26], [30, 30], [16, 0]], spark: ["#ffffff", "#78a9ff"] },
    },
  };

  // ---- antigravity: a monk floating cross-legged; colour trims, then orbs that float with it, then a halo
  const agHead = [P(9, 12, ["s", "s"]), P(22, 12, ["S", "S"]), HEAD, P(12, 13, ["ss....ss", "kk....kk"]),
    P(14, 3, [".hh.", "hhhh", "hhhh", ".hh."]), P(10, 7, ["..hhhhhhhh..", ".hhhhhhhhhh.", "hhhhhhhhhhhH"]), P(10, 10, ["bbbbbbbbbbbB"])];
  const agBody = P(6, 18, [
    "....nnnnNssNnnnN....",
    "...nnnnnnNssNnnnNN..",
    "..nnnnnnnnNNnnnnnNN.",
    "..nnNnnnnnnnnnnNnNN.",
    "..nnNbbbbbbbbbbNnNN.",
    ".nnnNnnnssssnnnNnnNN",
    "nnnnnnnnSSSSnnnnnNNN",
    "nnnnnnssnnnnssnnnNNM",
    "NnnnnnnnnMMnnnnnnNNM",
    ".NNNNNNNNNNNNNNNNMM.",
  ]);
  const agSash = P(11, 22, ["bbbrrryyyggg"]);
  const agHem = P(7, 27, ["bbbbrrrryyyygggg"]);
  const agCollar = P(10, 18, ["eeeEssEeeeeE"]);
  const agCuffs = [P(8, 23, ["bb"]), P(22, 23, ["gg"])];
  const orb = (x, y, c, on) => P(x, y, [".a.", "awa", ".a."].map((r) => r.replace(/a/g, c)), on);
  const agOrbs = [
    orb(26, 12, "b", [0, 1]), orb(26, 11, "b", [2, 3]),
    orb(3, 13, "r", [1, 2]), orb(3, 12, "r", [3, 0]),
    orb(1, 22, "y", [2, 3]), orb(1, 21, "y", [0, 1]),
    orb(28, 21, "g", [3, 0]), orb(28, 20, "g", [1, 2]),
  ];
  const agHalo = [ring(16, 12.5, 9, "bryg")];
  const antigravity = {
    color: "#e0a100", // Google's yellow, deepened: its red sat beside Claude's coral in charts, its green beside Codex
    pal: { k: "#23273a", s: "#f3cfab", S: "#d9a27c", c: "#f4a39a", m: "#a85048", h: "#3b3446", H: "#2a2433",
      n: "#f8f9fc", N: "#d4d9e5", M: "#a8b1c4", e: "#c9d2e3", E: "#8e99ad",
      b: "#4285f4", B: "#2a64c9", r: "#ea4335", y: "#fbbc04", g: "#34a853" },
    float: [0, -1, -2, -1],
    blink: null,
    // the higher it floats, the smaller its shadow
    shadow: (f) => {
      const w = [12, 10, 8, 10][f];
      return P(16 - w / 2, 30, ["." + "x".repeat(w - 2) + ".", ".." + "x".repeat(w - 4) + ".."]);
    },
    tiers: {
      novice: { body: [...agHead, agBody] },
      adept: { body: [...agHead, agBody, agSash, ...agCuffs, ...agOrbs.slice(0, 2)] },
      expert: { body: [...agHead, agBody, agSash, ...agCuffs, agHem, agCollar, ...agOrbs] },
      master: { back: agHalo, body: [...agHead, agBody, agSash, ...agCuffs, agHem, agCollar, ...agOrbs],
        aura: "140, 190, 255", sparks: [[5, 6], [26, 5], [4, 28], [27, 28], [24, 17]], spark: ["#ffffff", "#fbbc04"] },
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
      if (phase === 1) out.push({ x: x - 1, y: yy - 1, rows: [".a.", "aba", ".a."], pal: { a: colors[1], b: colors[0] } });
      else if (phase !== 3) out.push({ x, y: yy, rows: ["a"], pal: { a: colors[phase === 0 ? 1 : 0] } });
    });
    return out;
  }

  function compose(agent, tier, frame, blink, locked) {
    const key = [agent, tier, frame, blink ? 1 : 0, locked ? 1 : 0].join("|");
    if (cache.has(key)) return cache.get(key);
    const ch = CHARS[agent];
    const spec = ch.tiers[tier] || ch.tiers.novice;
    const n = SIZE, pal = { ...BASE_PAL, ...ch.pal, ...spec.pal };
    const blank = () => new Array(n * n).fill(null);
    const layer = (pieces) => {
      const grid = blank();
      for (const piece of pieces || []) if (shows(piece, frame)) paint(grid, n, piece, pal);
      return grid;
    };
    // a breather's upper body sinks a pixel; a floater rises and falls whole
    const move = (grid) => shift(grid, n, ch.float ? n : ch.waist, (ch.float || BREATH)[frame]);
    let body = layer(spec.body);
    const blinkPiece = spec.blink !== undefined ? spec.blink : ch.blink !== undefined ? ch.blink : BLINK;
    if (blink && blinkPiece && !locked) paint(body, n, blinkPiece, pal);
    body = move(body);
    if (locked) body = body.map((c) => c && LOCKED.fill);
    const line = outline(body, n, locked ? LOCKED.rim : pal.k);
    const shape = body.map((c, i) => c || line[i]);
    const back = blank();
    paint(back, n, ch.shadow ? ch.shadow(frame) : P(10, 30, [".xxxxxxxxxx.", "..xxxxxxxx.."]), { x: SHADOW });
    if (spec.aura && !locked) aura(shape, n, spec.aura, frame % 2 === 1).forEach((c, i) => { if (c) back[i] = c; });
    if (!locked) move(layer(spec.back)).forEach((c, i) => { if (c && !shape[i]) back[i] = c; });
    const front = locked ? blank() : move(layer(spec.front));
    if (spec.sparks && !locked) for (const p of sparkPieces(spec.sparks, spec.spark, frame)) paint(front, n, p, p.pal);
    if (locked) for (const p of zzz(frame)) if (shows(p, frame)) paint(front, n, p, { z: LOCKED.z });
    if (locked) shape.forEach((c, i) => { if (c) front[i] = null; }); // the z's stay off the silhouette
    const ops = runs(back, n, []);
    runs(line, n, ops);
    runs(body, n, ops);
    runs(front, n, ops);
    cache.set(key, ops);
    return ops;
  }
  // asleep: two z's drift beside the head
  function zzz(frame) {
    const small = ["zzz", "..z", ".z.", "zzz"];
    const big = ["zzzz", "..z.", ".z..", "zzzz"];
    return [P(23, 5 - (frame % 2), small), P(26, 1 + (frame % 2), big, [1, 2, 3])];
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
  function iconOps(name) {
    if (iconCache.has(name)) return iconCache.get(name);
    const n = ICON, grid = new Array(n * n).fill(null);
    for (const [x, y, rows] of ICONS[name] || []) paint(grid, n, P(x, y, rows), ICON_PAL);
    const ops = runs(outline(grid, n, ICON_PAL.k), n, []);
    runs(grid, n, ops);
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

  function draw(canvas, { agent, tier = "novice", frame = 0, scale = 4, locked = false, blink = false } = {}) {
    if (!canvas) return;
    const sc = whole(scale, 4);
    const f = ((Math.round(Number(frame) || 0) % FRAMES) + FRAMES) % FRAMES;
    const ops = CHARS[agent] ? compose(agent, TIERS.includes(tier) ? tier : "novice", f, !!blink, !!locked) : [];
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
    draw, animate, sparkle, icon, color });
})();
