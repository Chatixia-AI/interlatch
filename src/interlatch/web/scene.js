/* Home's Journey strip: an idle game on one <canvas>. The six coding agents walk through a pixel landscape and fight
   the week's lessons, one monster per lesson; every name, number and date in it comes from GET /api/party (`recent`,
   `zone`, `journey`, `quests`, `agents`). The art is pixel maps, as in sprites.js: no images, styles only through the
   CSSOM, no eval. One timer serves every mounted strip, 30 frames a second, and stops when no strip is on screen or the
   page is hidden. */
(() => {
  "use strict";

  const FRAME_MS = 1000 / 30;
  const LABELS = { wave: "Lesson {n} of {total}", xp: "+{xp} XP · {skill}", levelUp: "{agent} reached level {n}!",
    skillUp: "{agent}: {skill} Lv {n}", boss: "Boss: {text}", bossHint: "Mark the quest done to defeat it",
    asleep: "Your party is asleep", quiet: "A quiet week: no new lessons",
    preview: "This week's lessons" };
  const AGENT_ORDER = ["claude", "codex", "copilot", "bob", "antigravity", "opencode"];
  const FONT = '"IBM Plex Mono", ui-monospace, "SF Mono", Menlo, Consolas, monospace';
  const fill = (tpl, vars) => String(tpl).replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const ease = (u) => 1 - (1 - clamp(u, 0, 1)) ** 2;

  // a seeded random stream, so a landscape is the same every time it is drawn
  function rng(seed) {
    let a = seed >>> 0;
    return () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function blank(w, h) {
    const c = document.createElement("canvas");
    c.width = Math.max(1, w);
    c.height = Math.max(1, h);
    return c;
  }

  // =====================================================================================
  // Colour, and the page's theme: the strip is drawn on the dashboard's own blueprint (its paper, grid and ink tokens),
  // navy in the dark theme, a whiteprint in the light one
  // =====================================================================================
  const rgbOf = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
  const hexOf = (rgb) => "#" + rgb.map((v) => clamp(Math.round(v), 0, 255).toString(16).padStart(2, "0")).join("");
  const mix = (a, b, u) => { const x = rgbOf(a), y = rgbOf(b); return hexOf(x.map((v, i) => v + (y[i] - v) * u)); };
  const glow = (hex, alpha) => { const [r, g, b] = rgbOf(hex); return `rgba(${r}, ${g}, ${b}, ${alpha})`; };
  const isHex = (v) => /^#[0-9a-f]{6}$/i.test(v);
  function themeDark() {
    const t = document.documentElement.getAttribute("data-theme");
    if (t === "dark" || t === "light") return t === "dark";
    return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;
  }
  // each zone's blueprint leans to a hue of its own: the paper and its lines, not the cast
  const BIOME_HUE = { meadow: null, craft: "#3a7be0", data: "#1fae96", ops: "#8a96a8", guard: "#d0605a", arcana: "#9a6ae0",
    lore: "#e8a040" };
  const BIOME_WEIGHT = { ops: 1.4, lore: 1.5 }; // a grey and an amber need more to show through the blue paper
  function readTheme(biome) {
    const dark = themeDark(), css = getComputedStyle(document.documentElement);
    const tok = (name, fallback) => { const v = css.getPropertyValue(name).trim(); return isHex(v) ? v : fallback; };
    const surface = tok("--surface", dark ? "#0e2c51" : "#ffffff"), surface2 = tok("--surface-2", dark ? "#10345e" : "#e9eff7");
    const grid = tok("--grid", dark ? "#24507f" : "#d0dceb"), base = tok("--baseline", dark ? "#4a74a6" : "#7f9cc0");
    const ink = tok("--ink", dark ? "#f3f8ff" : "#071629"), accent = tok("--accent-ink", dark ? "#8ccfff" : "#164c80");
    const cta = tok("--cta", "#ffb45e"), hue = BIOME_HUE[biome];
    const tint = (c, k) => (hue ? mix(c, hue, Math.min(0.9, k * (BIOME_WEIGHT[biome] || 1) * (dark ? 1 : 0.8))) : c);
    return { dark, ink, accent: tint(accent, 0.35), cta,
      wall: tint(dark ? mix(surface, "#05101f", 0.25) : surface2, 0.16), wallGrid: tint(dark ? grid : mix(grid, surface2, 0.2), 0.3),
      wallMajor: tint(dark ? mix(grid, base, 0.45) : mix(grid, base, 0.35), 0.35),
      floor: tint(dark ? mix(surface, "#1d4a80", 0.35) : mix(surface2, "#c7d8ee", 0.5), 0.2),
      floorLine: tint(dark ? mix(base, surface, 0.25) : base, 0.35),
      wire: glow(tint(accent, 0.45), dark ? 0.34 : 0.3), mark: dark ? "#ffb45e" : "#d08a20",
      panel: dark ? "rgba(6, 18, 36, 0.78)" : "rgba(255, 255, 255, 0.9)", panelRim: dark ? glow(base, 0.6) : glow(base, 0.55),
      panelInk: ink, shadow: dark ? "rgba(2, 8, 22, 0.42)" : "rgba(22, 52, 100, 0.18)" };
  }

  // the same blueprint with every colour turned to a grey of its own lightness (the signed-out preview)
  function greyTheme(theme) {
    const grey = (c) => {
      if (isHex(c)) { const [r, g, b] = rgbOf(c), v = r * 0.3 + g * 0.55 + b * 0.15; return hexOf([v, v, v + 4]); }
      const m = /^rgba\((\d+), (\d+), (\d+), ([\d.]+)\)$/.exec(c);
      if (!m) return c;
      const v = Math.round(m[1] * 0.3 + m[2] * 0.55 + m[3] * 0.15);
      return `rgba(${v}, ${v}, ${v}, ${m[4]})`;
    };
    return Object.fromEntries(Object.entries(theme).map(([k, v]) => [k, typeof v === "string" ? grey(v) : v]));
  }

  // the Cast's style for everything that isn't an agent: Sprites.art shades and edges a pixel map like the agents
  const cast = (pieces, pal, scale, flash) => window.Sprites.art(pieces, pal, scale, flash ? { flash } : {});
  // eyes for a creature that faces left (toward the party): two 4×4 eyes, pupils looking ahead
  const eyesL = (x, y, gap = 1) => [x, y, [".ww." + ".".repeat(gap) + ".ww.", "wkww" + ".".repeat(gap) + "wkww",
    "wkkw" + ".".repeat(gap) + "wkkw", ".ww." + ".".repeat(gap) + ".ww."]];
  const xEyes = (x, y, gap = 1) => [x, y, ["k..k" + ".".repeat(gap) + "k..k", ".kk." + ".".repeat(gap) + ".kk.",
    ".kk." + ".".repeat(gap) + ".kk.", "k..k" + ".".repeat(gap) + "k..k"]];

  // =====================================================================================
  // Monsters: the Cast's trouble-makers, one per lesson kind, facing left. Two frames each; an established lesson's
  // monster wears a gold badge, a canonical one is drawn twice the size.
  // =====================================================================================
  const MONSTERS = {
    // fix: Glitch, the bug
    glitch: {
      pal: { K: "*#2a2b3a", r: "*#f2666c", a: "=#2a2b3a", A: "*#ff7d7d", l: "=#2a2b3a" },
      frames: [
        [[0, 0, ["..A......A.....", ".AAA....AAA.....", "..a......a......", "...a....a.......", "....a..a..rrrr..",
          "..KKKKKKKrrrrrr.", ".KKKKKKKKKrrrrrr", "KKKKKKKKKKrrrrrr", "KKKKKKKKKKrrrrrr", "KKKKKKKKKKrrrrrr",
          "KKKKKKKKKKrrrrrr", ".KKKKKKKKrrrrrr.", "..KKKKKKKrrrrr..", ".l..l..l..l..l..", "l..l..l..l..l..."]],
          eyesL(1, 6), [0, 10, ["cc......cc"]], [4, 11, ["m..m"]]],
        [[0, 0, [".A.......A......", "AAA.....AAA.....", ".a.......a......", "..a.....a.......", "....a..a..rrrr..",
          "..KKKKKKKrrrrrr.", ".KKKKKKKKKrrrrrr", "KKKKKKKKKKrrrrrr", "KKKKKKKKKKrrrrrr", "KKKKKKKKKKrrrrrr",
          "KKKKKKKKKKrrrrrr", ".KKKKKKKKrrrrrr.", "..KKKKKKKrrrrr..", "..l..l..l..l..l.", ".l..l..l..l..l.."]],
          eyesL(1, 6), [0, 10, ["cc......cc"]], [4, 11, ["m..m"]]],
      ],
      chips: ["#f2666c", "#2a2b3a", "#ffffff"],
    },
    // gotcha: Mal's little pot, masked
    mal: {
      pal: { g: "*#8d93a8", b: "*#34364a", o: "*#ff6b6b", M: "=#1b1d2c", h: "#8d93a8" },
      frames: [
        [[0, 0, [".....oo.......", "....bbbbbb....", "...bbbbbbbb...", "..bbbbbbbbbb..", ".MMMMMMMMMMMM.", "hMMMMMMMMMMMMh",
          "hMMMMMMMMMMMMh", ".MMMMMMMMMMMM.", ".gggggggggggg.", ".gggggggggggg.", ".gggggggggggg.", "..gggggggggg..",
          "...gggggggg..."]], eyesL(2, 4, 1), [1, 9, ["cc........cc"]], [5, 9, ["m..m", ".mm."]]],
        [[0, 1, [".....oo.......", "....bbbbbb....", "...bbbbbbbb...", "..bbbbbbbbbb..", ".MMMMMMMMMMMM.", "hMMMMMMMMMMMMh",
          "hMMMMMMMMMMMMh", ".MMMMMMMMMMMM.", ".gggggggggggg.", ".gggggggggggg.", "..gggggggggg..", "...gggggggg..."]],
          eyesL(2, 5, 1), [1, 10, ["cc........cc"]], [5, 10, ["m..m", ".mm."]]],
      ],
      chips: ["#8d93a8", "#34364a", "#ff6b6b"],
    },
    // decision: a breaker switch
    breaker: {
      pal: { b: "*#c9d8ee", d: "=#1d2740", r: "*#ff6b6b", L: "*#9ce07a" },
      frames: [
        [[0, 0, ["....rrr.....", "....rrr.....", ".....d......", ".bbbbdbbbbb.", "bbbbbdbbbbbL", "bbbbbbbbbbbb", "bbbbbbbbbbbb",
          "bbbbbbbbbbbb", "bbbbbbbbbbbb", "bbbbbbbbbbbb", "bbbbbbbbbbbb", ".bbbbbbbbbb.", ".dd......dd."]], eyesL(1, 5, 1),
          [0, 9, ["cc.......cc"]], [4, 9, ["m..m", ".mm."]]],
        [[0, 0, [".......rrr..", ".......rrr..", "......d.....", ".bbbbdbbbbb.", "bbbbbdbbbbbL", "bbbbbbbbbbbb", "bbbbbbbbbbbb",
          "bbbbbbbbbbbb", "bbbbbbbbbbbb", "bbbbbbbbbbbb", "bbbbbbbbbbbb", ".bbbbbbbbbb.", ".dd......dd."]], eyesL(1, 5, 1),
          [0, 9, ["cc.......cc"]], [4, 9, ["m..m", ".mm."]]],
      ],
      chips: ["#c9d8ee", "#ff6b6b", "#1d2740"],
    },
    // learning: a little wisp of Lumi's kind, floating under a halo
    wisp: {
      fly: 8,
      pal: { o: "*#e4dcff", H: "=#9ff0de", d: "*#ffc8e0", D: "*#b6e3ff" },
      frames: [
        [[2, 0, ["..HHHHHH..", ".H......H.", "..HHHHHH.."]], [0, 4, ["...oooooo...", ".oooooooooo.", "oooooooooooo",
          "oooooooooooo", "oooooooooooo", "oooooooooooo", ".oooooooooo.", "...oooooo..."]], eyesL(1, 5, 2),
          [0, 9, ["cc........cc"]], [-3, 6, [".d.", "ddd", ".d."]]],
        [[2, 1, ["..HHHHHH..", ".H......H.", "..HHHHHH.."]], [0, 4, ["...oooooo...", ".oooooooooo.", "oooooooooooo",
          "oooooooooooo", "oooooooooooo", "oooooooooooo", ".oooooooooo.", "...oooooo..."]], eyesL(1, 5, 2),
          [0, 9, ["cc........cc"]], [13, 3, [".D.", "DDD", ".D."]]],
      ],
      chips: ["#e4dcff", "#9ff0de", "#ffc8e0"],
    },
    // pattern: a gears critter
    gears: {
      pal: { g: "*#b9c7da", h: "=#5d6b80", G: "*#d8e2ee" },
      frames: [
        [[0, 0, ["....gg..gg....", "....gggggg....", "..gggggggggg..", ".gggggggggggg.", "gggggggggggggg", ".gggggggggggg.",
          "gggggggggggggg", "gggggggggggggg", ".gggggggggggg.", "gggggggggggggg", ".gggggggggggg.", "..gggggggggg..",
          "....gggggg....", "....gg..gg...."]], eyesL(1, 4, 2), [1, 8, ["cc........cc"]], [5, 9, ["m..m", ".mm."]],
          [12, -3, [".GG.", "GGGG", ".GG."]]],
        [[0, 0, ["...gg....gg...", "...gggggggg...", "..gggggggggg..", "gggggggggggggg", ".gggggggggggg.", "gggggggggggggg",
          ".gggggggggggg.", ".gggggggggggg.", "gggggggggggggg", ".gggggggggggg.", "gggggggggggggg", "..gggggggggg..",
          "...gggggggg...", "...gg....gg..."]], eyesL(1, 4, 2), [1, 8, ["cc........cc"]], [5, 9, ["m..m", ".mm."]],
          [12, -3, ["G..G", ".GG.", "G..G"]]],
      ],
      chips: ["#b9c7da", "#5d6b80", "#ffffff"],
    },
    // command: a log scroll
    scroll: {
      pal: { p: "*#f4efe4", o: "*#d9a05b", b: "=#6cb6ff", G: "=#5fcf8a", r: "=#ff6b6b" },
      frames: [
        [[0, 0, ["oooooooooooooo", ".pppppppppppp.", ".pppppppppppp.", ".pppppppppppp.", ".pppppppppppp.", ".pppppppppppp.",
          ".pbbbbpppGGGp.", ".pppppppppppp.", ".prrrppppbbbp.", ".pppppppppppp.", "oooooooooooooo"]], eyesL(2, 1, 1),
          [1, 5, ["cc........cc"]], [5, 5, ["m..m"]]],
        [[0, 1, ["oooooooooooooo", ".pppppppppppp.", ".pppppppppppp.", ".pppppppppppp.", ".pppppppppppp.", ".pppppppppppp.",
          ".pGGGpppbbbbp.", ".pppppppppppp.", ".pbbbppprrrrp.", "oooooooooooooo"]], eyesL(2, 2, 1), [1, 6, ["cc........cc"]],
          [5, 6, ["m..m"]]],
      ],
      chips: ["#f4efe4", "#d9a05b", "#6cb6ff"],
    },
  };
  const BADGE = { pal: { y: "*#f6c445" }, piece: (x) => [x, -4, [".y.", "yyy", ".y."]] };
  const KIND_MONSTER = { fix: "glitch", gotcha: "mal", decision: "breaker", learning: "wisp", pattern: "gears", command: "scroll" };

  // bosses: a main quest's four milestones, as a giant Glitch, a giant Mal, a firewall with eyes, a giant breaker
  const FIREWALL = {
    pal: { r: "*#e07a55", R: "=#8e3f28", o: "=#ffb347", y: "=#fff1a0", f: "=#ff7a3c" },
    frames: [
      [[0, 0, ["..f...o...f.....", ".ffo.oyo.ffo..f.", ".foyoyyyofyo.fo.", "fooyyyyyooyyofyo"]],
        [0, 4, ["rrrRrrrrRrrrrRrr", "rrrRrrrrRrrrrRrr", "RRRRRRRRRRRRRRRR", "rRrrrrRrrrrRrrrr", "rRrrrrRrrrrRrrrr",
          "RRRRRRRRRRRRRRRR", "rrrRrrrrRrrrrRrr", "rrrRrrrrRrrrrRrr", "RRRRRRRRRRRRRRRR", "rRrrrrRrrrrRrrrr", "rRrrrrRrrrrRrrrr"]],
        eyesL(3, 6, 2), [2, 10, ["cc........cc"]], [6, 11, ["m..m", ".mm."]]],
      [[0, 0, [".f....o..f......", "ffo..oyo.fo...f.", "foyo.oyyofyo.fo.", "fooyyyyyooyyofyo"]],
        [0, 4, ["rrrRrrrrRrrrrRrr", "rrrRrrrrRrrrrRrr", "RRRRRRRRRRRRRRRR", "rRrrrrRrrrrRrrrr", "rRrrrrRrrrrRrrrr",
          "RRRRRRRRRRRRRRRR", "rrrRrrrrRrrrrRrr", "rrrRrrrrRrrrrRrr", "RRRRRRRRRRRRRRRR", "rRrrrrRrrrrRrrrr", "rRrrrrRrrrrRrrrr"]],
        eyesL(3, 6, 2), [2, 10, ["cc........cc"]], [6, 11, ["m..m", ".mm."]]],
    ],
  };
  const BOSSES = [MONSTERS.glitch, MONSTERS.mal, FIREWALL, MONSTERS.breaker];
  const FELLED = { k: "=#2b2638" };
  const DONE_FLAG = { pal: { p: "*#b9c7da", g: "*#7fd99a", B: "*#3b5f9e", w: "=#ffffff" },
    pieces: [[0, 0, ["pgggggg", "pgggggg", "pgg.www", "pgggggg", "p......", "p......", "p......", "p......", "p......", "p......",
      "p......", ".BBB..."]]] };

  // =====================================================================================
  // Biomes, one per attribute of the zone, each with props from the Cast (faceless): drawn in full near the path, and
  // as faint blueprint drawings further back
  // =====================================================================================
  const PROPS = {
    laptop: { pal: { s: "*#d5dde8", d: "=#1e2b44", b: "=#6cb6ff", g: "=#7fd99a", y: "=#f6c445", S: "*#aeb9c8" },
      rows: [".ssssssssssss.", ".sddddddddddds", ".sdbbbbddddd.s", ".sdddgggggdd.s", ".sdyyyddddddds", ".sddbbbbbddd.s",
        ".sssssssssssss", "SSSSSSSSSSSSSSS", ".SSSSSSSSSSSSS."] },
    gears: { pal: { g: "*#b9c7da", G: "*#a2b2c8", y: "*#f6c445" },
      rows: ["...g..g.........", "..gggggg........", ".gggggggg..G..G.", "gggyyyyggg.GGGG.", ".ggy..ygg.GGGGGG", "gggy..yggGGyyGG.",
        ".ggyyyyggGGy.yGG", "..gggggg.GGyyGG.", "...g..g...GGGG..", "...........G..G."] },
    plug: { pal: { m: "*#7fe0c8", s: "*#c9d4e0", y: "=#f6c445", c: "=#2c3b63" },
      rows: ["..s..s..", "..s..s..", ".mmmmmm.", "mmmmmmmm", "mmmyymmm", "mmmymmmm", "mmyymmmm", ".mmmmmm.", "...cc...",
        "...cc...", "....cc..", ".....ccc"] },
    database: { pal: { t: "*#6fe0c4", T: "*#8ff0d8", w: "=#ffffff" },
      rows: ["..TTTTTTTT..", ".TTTTTTTTTT.", "tttttttttttt", "twwwwwwwwwwt", "tttttttttttt", "tttttttttttt", "twwwwwwwwwwt",
        "tttttttttttt", "tttttttttttt", ".tttttttttt."] },
    bucket: { pal: { b: "*#a9d4f2", h: "=#9aa6b8", y: "*#f6d27a", g: "*#bfe8c9" },
      rows: ["..hhhhhhhh..", ".h........h.", "h..yy.gg...h", "bbbbbbbbbbbb", ".bbbbbbbbbb.", ".bhhhhhhhhb.", ".bbbbbbbbbb.",
        "..bbbbbbbb.."] },
    folder: { pal: { f: "*#f6d77a", p: "*#d9c8f2" },
      rows: ["ffff........", "ffffffffffff", "fppppppppppf", "fppppppppppf", "ffffffffffff", "ffffffffffff", "ffffffffffff"] },
    shards: { pal: { a: "*#e6dcff", b: "*#ffc8e0", c: "*#c9e8ff" },
      rows: ["...aaab...", ".aaaaabb..", "aaaaabbbb.", "cccccbbbbb", "ccccc.bbb.", ".ccc......"] },
    cloud: { pal: { w: "*#f4f7fb", b: "=#9fc6ff" },
      rows: ["....www.......", "..wwwwwww.....", ".wwwwwwwwwww..", "wwwwwwwwwwwwww", "wwwwwwwwwwwwww", ".wwwwwwwwwwww.",
        "...b...b..b..."] },
    rack: { pal: { n: "*#2c4170", s: "*#d5dde8", g: "=#7fd99a", y: "=#f6c445" },
      rows: ["nnnnnnnnnn", "nssssssssn", "nsgssssysn", "nnnnnnnnnn", "nssssssssn", "nsgssssssn", "nnnnnnnnnn", "nssssssssn",
        "nsysssssgn", "nnnnnnnnnn", "nssssssssn", "nnnnnnnnnn"] },
    container: { pal: { o: "*#f29a4a", O: "=#c06a22", d: "=#2c3b63", w: "=#ffffff" },
      rows: ["oooooooooooooo", "oOoOoOoOoOoOoo", "oOddddddOoOoOo", "oOdwdwwdOoOoOo", "oOoOoOoOoOoOoo", "oooooooooooooo"] },
    conveyor: { pal: { v: "*#cdbdf5", s: "*#c9d4e0", d: "=#3a4a6b", w: "*#f4f7fb" },
      rows: ["..ww...ww...ww..", "..ww...ww...ww..", "vvvvvvvvvvvvvvvv", "svvvvvvvvvvvvvvs", "dddddddddddddddd", ".d............d.",
        ".d............d."] },
    firewall: { pal: { r: "*#e07a55", R: "=#8e3f28", o: "=#ffb347", y: "=#fff1a0" },
      rows: ["..o...o....o..", ".oyo.oyo..oyo.", "ooyyooyyooyyoo", "rrrRrrrrRrrrrR", "RRRRRRRRRRRRRR", "rRrrrrRrrrrRrr",
        "RRRRRRRRRRRRRR", "rrrRrrrrRrrrrR", "RRRRRRRRRRRRRR"] },
    padlock: { pal: { s: "*#b9c7da", l: "*#ffa29a", k: "=#7a2730" },
      rows: ["..ssssss..", ".s......s.", ".s......s.", "llllllllll", "llllllllll", "llllkkllll", "lllllkllll",
        "llllllllll", ".llllllll."] },
    gateway: { pal: { m: "*#6fe0c4", n: "*#2c4170", g: "=#7fd99a", w: "=#ffffff" },
      rows: ["....gggggg....", "....gwwwwg....", "..mmmmmmmmmm..", ".mm........mm.", "mm..........mm", "mm..........mm",
        "mm..........mm", "nnnnnnnnnnnnnn"] },
    key: { pal: { y: "*#f6c445" },
      rows: [".yyyy.", "yy..yy", "yy..yy", ".yyyy.", "..yy..", "..yy..", "..yyy.", "..yy..", "..yyy."] },
    chip: { pal: { n: "*#2c4170", s: "*#c9d4e0", y: "=#f6c445", b: "=#8ccfff" },
      rows: [".y.y.y.y.y.", "nnnnnnnnnnn", "nsssssssssn", "nsbbbbbbbsn", "nsbsssssbsn", "nsbbbbbbbsn", "nsssssssssn",
        "nnnnnnnnnnn", ".y.y.y.y.y."] },
    orb: { pal: { o: "*#e4dcff", H: "=#9ff0de", n: "*#2c4170" },
      rows: ["..HHHHHH..", "..........", "...oooo...", ".oooooooo.", "oooooooooo", "oooooooooo", ".oooooooo.", "...oooo...",
        "..nnnnnn..", ".nnnnnnnn."] },
    sprout: { pal: { g: "*#7fd99a", w: "*#f4f7fb", p: "*#f4efe4", d: "=#8a5a3a" },
      rows: ["..w...w...", ".www.www..", "..g...g...", "..gg.gg...", "...ggg..w.", "....g..www", "....gggg..",
        "....g.....", "..pppppp..", "..pddddp..", "...pppp..."] },
    document: { pal: { p: "*#f4f7fb", v: "=#b79cff", b: "=#6cb6ff", g: "=#7fd99a", y: "=#f6c445" },
      rows: ["pppppppp..", "ppppppppp.", "pvpvpppppp", "pppppppppp", "pbbbbbbbpp", "pggggggppp", "pyyyyyyypp",
        "pbbbbbpppp", "pppppppppp", "pppppppppp"] },
    megaphone: { pal: { o: "*#f6a07a", w: "*#f4efe4", s: "=#9aa6b8" },
      rows: [".......oo...", "....oooww...", "oooooowwww.s", "oooooowwww..", "....oooww..s", ".......oo...", "..o.........",
        "..o........."] },
    flag: { pal: { p: "*#b9c7da", g: "*#9fe39a", d: "=#1e2b44", w: "=#ffffff", B: "*#3b5f9e" },
      rows: ["pggggggg", "pggggggg", "pggggdww", "pggggggg", "p.......", "p.......", "p.......", "p.......", "p.......",
        ".BBB...."] },
    globe: { pal: { b: "*#a9d4f2", w: "=#ffffff", y: "*#f6c445", n: "*#3b5f9e" },
      rows: ["..bbbbb.y.", ".bwbbwbb.y", "bbwbbwbbby", "bwwwwwwwby", "bbwbbwbbby", ".bwbbwbby.", "..bbbbby..", "....y.....",
        "..nnnnn..."] },
    clock: { pal: { y: "*#f6c445", w: "*#f7f3ea", k: "=#2c3b63", r: "=#ff6b6b" },
      rows: [".yy....yy.", "yyyyyyyyyy", "yywwwwwwyy", "ywwwwkwwwy", "ywwwwkwwwy", "ywwwwkkkwy", "ywwwwwwwwy", "yywwwwwwyy",
        ".yyyyyyyy.", ".y......y."] },
  };
  const BIOMES = {
    meadow: ["globe", "clock", "document", "flag"],
    craft: ["laptop", "gears", "plug", "laptop", "gears"],
    data: ["database", "bucket", "folder", "shards", "database"],
    ops: ["rack", "cloud", "container", "conveyor", "rack"],
    guard: ["firewall", "padlock", "gateway", "key", "padlock"],
    arcana: ["chip", "orb", "sprout", "orb", "chip"],
    lore: ["document", "scroll", "megaphone", "flag", "document"],
  };
  PROPS.scroll = { pal: MONSTERS.scroll.pal, rows: ["oooooooooooo", ".pppppppppp.", ".pbbbbppppp.", ".pppppppppp.", ".pGGGGGGppp.",
    ".pppppppppp.", ".prrrppbbbp.", ".pppppppppp.", "oooooooooooo"] };
  // dusk and night are lighting on the blueprint, not a sky
  const LIGHT = { day: null, dusk: ["rgba(255, 150, 90, 0.16)", "rgba(255, 150, 90, 0)"], night: ["rgba(4, 8, 28, 0.42)", "rgba(4, 8, 28, 0.2)"] };

  // ---- layer painters (logical pixels into a canvas at `scale` device pixels each)
  function px(ctx, scale, color, x, y, w = 1, h = 1) {
    ctx.fillStyle = color;
    ctx.fillRect(x * scale, y * scale, w * scale, h * scale);
  }
  // the wall: blueprint paper with a fine grid and a bolder one, a tile 32 wide
  function wallTile(theme, h, scale) {
    const cv = blank(32 * scale, h * scale), ctx = cv.getContext("2d");
    px(ctx, scale, theme.wall, 0, 0, 32, h);
    for (let x = 0; x < 32; x += 8) px(ctx, scale, x % 32 ? theme.wallGrid : theme.wallMajor, x, 0, 1, h);
    for (let y = h - 1; y >= 0; y -= 8) px(ctx, scale, (h - 1 - y) % 32 ? theme.wallGrid : theme.wallMajor, 0, y, 32, 1);
    return cv;
  }
  // the floor: paper in perspective; its rows close up toward the horizon (its converging lines are drawn per frame)
  function floorLayer(theme, W, top, L, scale) {
    const h = L - top, cv = blank(W * scale, h * scale), ctx = cv.getContext("2d");
    px(ctx, scale, theme.floor, 0, 0, W, h);
    px(ctx, scale, theme.wallMajor, 0, 0, W, 1);
    for (let k = 1, y = 2; y < h; k++, y += k) px(ctx, scale, theme.floorLine, 0, y, W, 1);
    return cv;
  }
  // props along the back of the floor: in full; or, further back, as a blueprint drawing (their outline only)
  function propsLayer(names, theme, period, L, base, scale, seed, wire) {
    const cv = blank(period * scale, L * scale), ctx = cv.getContext("2d"), r = rng(seed);
    let x = 6;
    for (let i = 0; x < period - 30; i++) {
      const prop = PROPS[names[i % names.length]], k = 2;
      const art = cast([[0, 0, prop.rows]], prop.pal, scale * k);
      const y = base - art.h * k + 1;
      if (wire) { // trace the edge of the prop's drawing
        const w = art.w * k, h = art.h * k, src = art.cv.getContext("2d").getImageData(0, 0, art.cv.width, art.cv.height).data;
        const on = (xx, yy) => xx >= 0 && yy >= 0 && xx < w && yy < h && src[((yy * scale) * art.cv.width + xx * scale) * 4 + 3] > 0;
        ctx.fillStyle = theme.wire;
        for (let yy = 0; yy < h; yy++) for (let xx = 0; xx < w; xx++) {
          if (on(xx, yy) && (!on(xx - 1, yy) || !on(xx + 1, yy) || !on(xx, yy - 1) || !on(xx, yy + 1))) ctx.fillRect((x + xx) * scale, (y + yy) * scale, scale, scale);
        }
      } else ctx.drawImage(art.cv, x * scale, y * scale);
      x += art.w * k + (wire ? 30 : 22) + Math.floor(r() * (wire ? 60 : 46));
    }
    return cv;
  }
  // gold registration marks on the floor, passing at the party's pace
  function marksLayer(theme, period, h, scale, seed) {
    const cv = blank(period * scale, h * scale), ctx = cv.getContext("2d"), r = rng(seed);
    for (let x = 10; x < period - 10; x += 40 + Math.floor(r() * 50)) {
      const y = 1 + Math.floor(r() * (h - 4));
      px(ctx, scale, glow(theme.mark, 0.55), x, y + 1, 3, 1);
      px(ctx, scale, glow(theme.mark, 0.55), x + 1, y, 1, 3);
    }
    return cv;
  }
  const CAMP = {
    logs: { pal: { b: "*#a8703f" }, rows: ["bb......bb", ".bbbbbbbb.", "..bbbbbb.."] },
    flames: [
      ["....y.....", "...yo.y...", "..yooy....", "..oyyoo...", ".orryyro..", ".orrrrro.."],
      [".....y....", "..y.oy....", "...yooy...", "..ooyyo...", ".orryyro..", ".orrrrro.."],
      ["...y......", "...oy..y..", "..yooy.o..", "..oyyoo...", ".orryyro..", ".orrrrro.."],
    ],
    flamePal: { y: "=#fff3a0", o: "=#ffb347", r: "=#f0603c" },
  };

  // =====================================================================================
  // The week as a timeline: walk to a monster, fight it, walk on; the bosses wait at the end
  // =====================================================================================
  const SPEED = 34;          // logical pixels a second while walking
  const STEP_IN = 0.24;      // the fighter steps up to the front
  const HIT = 0.44;          // one blow
  const POP = 0.55;          // the monster bursts, the fighter steps back
  const BOSS_HITS = 3, BOSS_HOLD = 2.4;
  const ENGAGE = 40;         // the monster stands this far ahead of the leader's spot
  const HITS = { provisional: 1, established: 2, canonical: 3 };

  function weekStart(party) {
    if (party.week && party.week.since) return party.week.since;
    const dates = (party.recent || []).map((l) => l.at).filter(Boolean).sort();
    if (dates.length) return dates[0];
    const d = new Date(Date.now() - 6 * 86400000);
    return d.toISOString().slice(0, 10);
  }
  function plan(party) {
    const agents = new Map((party.agents || []).map((a) => [a.agent, a]));
    const lessons = (party.recent || []).slice().sort((a, b) => String(a.at).localeCompare(String(b.at)) || a.id - b.id);
    const encounters = lessons.map((l) => {
      const hits = Math.max(1, Math.min(HITS[l.stage] || 1, Math.max(1, Number(l.xp) || 1)));
      const xp = Math.max(0, Number(l.xp) || 0);
      return { lesson: l, type: KIND_MONSTER[l.kind] || "glitch", stage: l.stage || "provisional", hits, xp,
        skill: (l.modules && l.modules[0] && l.modules[0].name) || "", agent: l.agent, events: [] };
    });
    const start = weekStart(party), byId = new Map(encounters.map((e) => [e.lesson.id, e]));
    for (const ev of (party.journey || []).slice().reverse()) {
      if ((ev.type !== "level" && ev.type !== "skill") || !ev.at || ev.at < start || !encounters.length) continue;
      let enc = ev.lesson && byId.get(ev.lesson.id);
      if (!enc) enc = encounters.filter((e) => e.lesson.at <= ev.at).pop() || encounters[encounters.length - 1];
      enc.events.push(ev);
    }
    const quests = (party.quests || []).filter((q) => q.main);
    const felled = quests.filter((q) => q.status === "done" && q.done_at && String(q.done_at).slice(0, 10) >= start);
    const standing = encounters.length ? quests.filter((q) => q.status === "evidence") : [];
    // where everything stands along the path, and the segments of time
    const segs = [], bosses = [];
    // a quest done this week lies by the path where the week reached the day it was done
    const felledAfter = new Map();
    for (const q of felled) {
      const day = String(q.done_at).slice(0, 10);
      let after = -1;
      encounters.forEach((e, i) => { if (String(e.lesson.at) <= day) after = i; });
      felledAfter.set(after, [...(felledAfter.get(after) || []), q]);
    }
    let x = ENGAGE + 44, cam = 0, t = 0;
    const layFelled = (after) => {
      for (const q of felledAfter.get(after) || []) { bosses.push({ quest: q, x: x + (after < 0 ? 0 : 40), felled: true }); x += 70; }
    };
    if (encounters.length) layFelled(-1);
    encounters.forEach((e, i) => {
      if (i) x += 64 + (e.stage === "canonical" || encounters[i - 1].stage === "canonical" ? 22 : 0);
      e.x = x;
      const walk = (x - ENGAGE - cam) / SPEED;
      segs.push({ type: "walk", t0: t, dur: walk, from: cam, to: x - ENGAGE });
      t += walk; cam = x - ENGAGE;
      e.t0 = t;
      e.kill = t + STEP_IN + (e.hits - 1) * HIT + 0.26;
      const dur = STEP_IN + e.hits * HIT + POP;
      segs.push({ type: "fight", t0: t, dur, enc: i });
      t += dur;
      layFelled(i);
    });
    for (const q of standing) {
      x += 120;
      const b = { quest: q, x, felled: false };
      bosses.push(b);
      const walk = (x - ENGAGE - 6 - cam) / SPEED;
      segs.push({ type: "walk", t0: t, dur: walk, from: cam, to: x - ENGAGE - 6 });
      t += walk; cam = x - ENGAGE - 6;
      b.t0 = t;
      const dur = STEP_IN + BOSS_HITS * HIT + BOSS_HOLD;
      b.end = t + dur;
      segs.push({ type: "boss", t0: t, dur, boss: b });
      t += dur;
    }
    if (encounters.length) {
      segs.push({ type: "walk", t0: t, dur: 1.2, from: cam, to: cam + 1.2 * SPEED });
      t += 1.2; cam += 1.2 * SPEED;
      segs.push({ type: "end", t0: t, dur: 0.9, cam });
      t += 0.9;
    }
    const order = AGENT_ORDER.slice().sort((a, b) => ((agents.get(b) || {}).level || 0) - ((agents.get(a) || {}).level || 0)
      || AGENT_ORDER.indexOf(a) - AGENT_ORDER.indexOf(b));
    return { agents, order, encounters, bosses, segs, total: t, start };
  }

  // =====================================================================================
  // A mounted strip
  // =====================================================================================
  const scenes = new Set();
  let timer = 0;

  // one timer for every strip: it ticks while some strip is on screen in a visible page, and not otherwise
  function kick() {
    if (timer) return;
    for (const s of scenes) if (s.running()) { timer = setTimeout(loop, FRAME_MS); return; }
  }
  function loop() {
    timer = 0;
    const now = performance.now();
    for (const s of [...scenes]) {
      if (!s.canvas.isConnected) { s.destroy(); continue; }
      if (s.running()) s.frame(now);
    }
    kick();
  }
  document.addEventListener("visibilitychange", kick);

  function mount(host, opts = {}) {
    const labels = { ...LABELS, ...(opts.labels || {}) };
    const onHover = typeof opts.onHover === "function" ? opts.onHover : () => {};
    const onPick = typeof opts.onPick === "function" ? opts.onPick : () => {};
    const cssH = Math.max(80, Number(opts.height) || 200);
    // `at` (seconds into the week's replay) and `time` ("day", "dusk", "night") pin a still frame: for previews and tests
    const pinned = Number.isFinite(opts.at) ? opts.at : null;
    const still = !!opts.reducedMotion || pinned != null;
    const canvas = document.createElement("canvas");
    canvas.tabIndex = 0;
    canvas.style.display = "block";
    canvas.style.width = "100%";
    canvas.style.height = cssH + "px";
    canvas.style.imageRendering = "pixelated";
    canvas.style.outline = "none";
    host.appendChild(canvas);
    const ctx = canvas.getContext("2d");

    let party = opts.party || {}, story = plan(party);
    let W = 0, L = 0, P = 1, dpr = 1, ground = 0, LX = 0, SP = 20;
    let time = "day", layers = null, visible = true, alive = true;
    let storyT = 0, animT = 0, lastNow = 0, hits = [], hover = null, focusKey = null, keyboard = false;
    let io = null, ro = null, mo = null, scheme = null, checked = Date.now();
    const agentX = new Map(); // where each agent was drawn this frame
    const banners = []; // level and skill banners of this frame, drawn over the HUD
    const art = new Map();
    const self = { canvas };

    function timeOfDay() {
      if (opts.time && opts.time in LIGHT) return opts.time;
      const h = new Date().getHours();
      return h >= 7 && h < 17 ? "day" : (h >= 17 && h < 20) || (h >= 5 && h < 7) ? "dusk" : "night";
    }
    // signed out with a preview: this week's lessons parade by, without a party; without one, the party sleeps
    function previewing() { return !!party && party.signed_in === false && !!party.preview && Array.isArray(party.preview.recent); }
    function asleep() { return !party || (party.signed_in === false && !previewing()); }
    function biome() {
      if (asleep()) return "meadow";
      const a = party.zone && party.zone.attr;
      return BIOMES[a] ? a : "meadow";
    }
    let theme = null;
    function measure() {
      dpr = window.devicePixelRatio || 1;
      const cssW = Math.max(120, host.clientWidth || canvas.clientWidth || 600);
      const S = cssH >= 140 ? 2 : 1;
      P = Math.max(1, Math.round(S * dpr));
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
      W = Math.ceil(canvas.width / P);
      L = Math.floor(canvas.height / P);
      ground = Math.round(L * 0.58); // the horizon: the wall above, the floor in perspective below
      SP = W >= 300 ? 22 : 18;
      LX = Math.max((AGENT_ORDER.length - 1) * SP + 6, Math.round(W * 0.32));
      time = asleep() ? "night" : timeOfDay();
      theme = readTheme(biome());
      const names = BIOMES[biome()], seed = 1 + Object.keys(BIOMES).indexOf(biome()) * 101;
      if (previewing()) { // signed out: the blueprint in plain greys, nothing on it but the lessons
        theme = greyTheme(theme);
        const none = blank(1, 1);
        layers = { wall: wallTile(theme, ground, P), far: none, near: none, floor: floorLayer(theme, W, ground, L, P), marks: none };
        art.clear();
        return;
      }
      layers = {
        wall: wallTile(theme, ground, P),
        far: propsLayer(names, theme, 420, L, ground - 2, P, seed + 1, true),
        near: propsLayer(names.slice().reverse(), theme, 300, L, ground + 3, P, seed + 2, false),
        floor: floorLayer(theme, W, ground, L, P),
        marks: marksLayer(theme, 200, L - ground - 2, P, seed + 3),
      };
      art.clear();
    }

    // ---- art, cached per device scale
    function sprite(agent, tier, frame, stride, locked, blink) {
      const key = `s|${agent}|${tier}|${frame}|${stride}|${locked ? 1 : 0}|${blink ? 1 : 0}`;
      if (!art.has(key)) {
        const c = blank(1, 1);
        window.Sprites.draw(c, { agent, tier, frame, stride, locked, blink, scale: P });
        art.set(key, c);
      }
      return art.get(key);
    }
    function monster(type, stage, frame, flash) {
      const key = `m|${type}|${stage}|${frame}|${flash ? 1 : 0}`;
      if (!art.has(key)) {
        const m = MONSTERS[type], pieces = m.frames[frame % 2].slice(), size = stage === "canonical" ? 3 : 2;
        const pal = { ...m.pal };
        if (stage !== "provisional") { pieces.push(BADGE.piece(5)); Object.assign(pal, BADGE.pal); }
        art.set(key, { ...cast(pieces, pal, P * size, flash ? "#ffffff" : null), size });
      }
      return art.get(key);
    }
    function boss(n, frame, flash, felled) {
      const key = `b|${n}|${frame}|${flash ? 1 : 0}|${felled ? 1 : 0}`;
      if (!art.has(key)) {
        const m = BOSSES[(Math.max(1, n || 1) - 1) % BOSSES.length];
        let pieces = m.frames[frame % 2];
        // a felled boss: greyed, X for eyes
        if (felled) pieces = pieces.map(([x, y, rows]) => (rows[1] && rows[1].startsWith("wkww") ? xEyes(x, y, rows[0].length - 8) : [x, y, rows]));
        const size = L >= 90 ? 4 : 3;
        const img = cast(pieces, felled ? { ...m.pal, ...FELLED } : m.pal, P * size, flash ? "#ffffff" : null);
        art.set(key, { ...(felled ? greyed(img) : img), size });
        if (felled) art.set(key + "|flag", cast(DONE_FLAG.pieces, DONE_FLAG.pal, P * 2));
      }
      return art.get(key);
    }
    // a copy of an image in greys (a quest's felled boss)
    function greyed(img) {
      const c = blank(img.cv.width, img.cv.height), g = c.getContext("2d");
      g.drawImage(img.cv, 0, 0);
      const d = g.getImageData(0, 0, c.width, c.height);
      for (let i = 0; i < d.data.length; i += 4) {
        const v = d.data[i] * 0.3 + d.data[i + 1] * 0.55 + d.data[i + 2] * 0.15;
        d.data[i] = v * 0.9 + 18; d.data[i + 1] = v * 0.92 + 20; d.data[i + 2] = v + 30;
      }
      g.putImageData(d, 0, 0);
      return { ...img, cv: c };
    }
    const put = (img, x, y) => ctx.drawImage(img, Math.round(x) * P, Math.round(y) * P);

    // ---- text: Plex Mono at device pixels, on the theme's panels so it reads over the blueprint
    function text(str, x, y, { size = 11, color = null, align = "left", panel = true, alpha = 1, weight = 600, max = 0 } = {}) {
      ctx.save();
      ctx.globalAlpha = alpha;
      ctx.font = `${weight} ${Math.round(size * dpr)}px ${FONT}`;
      ctx.textBaseline = "middle";
      let s = String(str);
      const limit = max * P;
      if (limit && ctx.measureText(s).width > limit) {
        while (s.length > 1 && ctx.measureText(s + "…").width > limit) s = s.slice(0, -1);
        s += "…";
      }
      const w = Math.ceil(ctx.measureText(s).width / P) + 6, h = Math.ceil((size * dpr * 1.25) / P) + 2;
      const left = clamp(align === "center" ? Math.round(x - w / 2) : align === "right" ? Math.round(x - w) : Math.round(x), 1, Math.max(1, W - w - 1));
      const top = Math.round(y - h / 2);
      if (panel) {
        ctx.fillStyle = theme.panelRim;
        ctx.fillRect((left + 1) * P, top * P, (w - 2) * P, h * P);
        ctx.fillRect(left * P, (top + 1) * P, w * P, (h - 2) * P);
        ctx.fillStyle = theme.panel;
        ctx.fillRect((left + 1) * P, (top + 1) * P, (w - 2) * P, (h - 2) * P);
      } else {
        ctx.lineWidth = Math.max(2, Math.round(3 * dpr));
        ctx.strokeStyle = theme.dark ? "rgba(6, 14, 32, 0.9)" : "rgba(255, 255, 255, 0.95)";
        ctx.lineJoin = "round";
        ctx.strokeText(s, (left + 3) * P, (top + h / 2) * P);
      }
      ctx.fillStyle = color || theme.panelInk;
      ctx.fillText(s, (left + 3) * P, (top + h / 2) * P);
      ctx.restore();
      return { x: left, y: top, w, h };
    }
    const agentName = (id) => ((story.agents.get(id) || {}).short || id.charAt(0).toUpperCase() + id.slice(1));
    // an agent's colour as text: lighter on the navy blueprint, deeper on the whiteprint
    const textColor = (id) => (theme.dark ? mix(window.Sprites.color(id), "#ffffff", 0.45) : mix(window.Sprites.color(id), "#0b1a33", 0.25));
    const gold = () => (theme.dark ? "#ffd27a" : "#9a5a08");

    // ---- where the story is at time t
    function at(t) {
      const segs = story.segs;
      if (!segs.length) return { seg: null, cam: t * SPEED, cur: 0 };
      const tt = ((t % story.total) + story.total) % story.total;
      let seg = segs[segs.length - 1];
      for (const s of segs) if (tt < s.t0 + s.dur) { seg = s; break; }
      const u = tt - seg.t0;
      let cam = 0, cur = story.encounters.length;
      if (seg.type === "walk") cam = seg.from + (seg.to - seg.from) * clamp(u / seg.dur, 0, 1);
      else if (seg.type === "fight") cam = story.encounters[seg.enc].x - ENGAGE;
      else if (seg.type === "boss") cam = seg.boss.x - ENGAGE - 6;
      else cam = seg.cam;
      if (seg.type === "fight") cur = seg.enc;
      else if (seg.type === "walk") cur = story.encounters.findIndex((e) => e.t0 >= seg.t0 + seg.dur - 1e-6);
      if (cur < 0) cur = story.encounters.length;
      return { seg, u, tt, cam, cur };
    }

    // ---- one frame
    function draw(t, a) {
      hits = [];
      ctx.imageSmoothingEnabled = false;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const sleeping = asleep(), preview = previewing(), lessons = preview ? previewLessons() : [];
      const quiet = !sleeping && (preview ? !lessons.length : !story.encounters.length);
      const st = sleeping ? { cam: 0, cur: 0, seg: null } : preview ? { cam: 0, cur: 0, seg: null }
        : quiet ? { cam: t * SPEED, cur: 0, seg: null } : at(t);
      const cam = st.cam;
      const sh = shake(st);
      ctx.save();
      ctx.translate(sh * P, 0);
      tile(layers.wall, 32, cam * 0.12, 0);
      tile(layers.far, 420, cam * 0.25, 0);
      put(layers.floor, 0, ground);
      floorLines(cam);
      tile(layers.near, 300, cam * 0.6, 0);
      tile(layers.marks, 200, cam, ground + 2);
      // dusk and night: lighting over the blueprint (the cast stays lit)
      const light = preview ? null : LIGHT[time];
      if (light) {
        const steps = 6;
        for (let i = 0; i < steps; i++) {
          ctx.fillStyle = i < steps / 2 ? light[0] : light[1];
          ctx.fillRect(0, Math.round((L * i) / steps) * P, canvas.width, Math.ceil(L / steps) * P);
        }
        if (time === "night") for (let i = 0; i < W / 18; i++) { // the grid's crossings glint at night
          const r = rng(i + 7), x = Math.floor(r() * W / 8) * 8, y = ground - 1 - Math.floor(r() * ground / 8) * 8;
          if ((Math.floor(a * 1.5) + i) % 5 === 0) px(ctx, P, glow(theme.accent, 0.8), x, y);
        }
      }
      if (sleeping) drawCamp(a);
      else if (preview) drawParade(t, a, lessons);
      else drawWorld(t, a, st, quiet);
      ctx.restore();
      // the week replays: a fade at the end of the wave and back in at its start
      const fade = st.seg && st.seg.type === "end" ? st.u / st.seg.dur : st.tt != null && st.tt < 0.5 && story.encounters.length ? 1 - st.tt / 0.5 : 0;
      if (fade > 0) { ctx.fillStyle = glow(theme.wall, clamp(fade, 0, 1) * 0.9); ctx.fillRect(0, 0, canvas.width, canvas.height); }
      drawHud(t, a, st, sleeping, quiet);
      for (const [ev, b] of banners.splice(0)) banner(ev, b);
      drawFocus(a);
    }
    // the floor's lines toward a vanishing point above the middle: they slide past at the party's pace
    function floorLines(cam) {
      const vx = W / 2, vy = ground - Math.round(L * 0.9), s = 20, off = ((cam % s) + s) % s;
      ctx.fillStyle = theme.floorLine;
      for (let xf = -Math.round(W * 0.9) - off; xf < W * 1.9; xf += s) {
        let runX = null, runY = 0;
        for (let y = ground + 1; y <= L; y++) {
          const x = Math.round(vx + ((xf - vx) * (y - vy)) / (L - vy));
          if (x !== runX) {
            if (runX != null && runX >= 0 && runX < W) ctx.fillRect(runX * P, runY * P, P, (y - runY) * P);
            runX = x; runY = y;
          }
        }
        if (runX != null && runX >= 0 && runX < W) ctx.fillRect(runX * P, runY * P, P, (L + 1 - runY) * P);
      }
    }
    function shake(st) {
      const seg = st.seg;
      if (!seg || still) return 0;
      if (seg.type === "fight") {
        const e = story.encounters[seg.enc], d = st.tt - e.kill;
        return d >= 0 && d < 0.16 ? (Math.floor(d * 50) % 2 ? 1 : -1) * (e.stage === "canonical" ? 2 : 1) : 0;
      }
      if (seg.type === "boss") {
        for (let k = 0; k < BOSS_HITS; k++) {
          const d = st.u - (STEP_IN + k * HIT + 0.15);
          if (d >= 0 && d < 0.1) return Math.floor(d * 60) % 2 ? 1 : -1;
        }
      }
      return 0;
    }
    function tile(img, period, offset, y) {
      let x = -(((Math.round(offset) % period) + period) % period);
      for (; x < W; x += period) put(img, x, y);
    }

    const feet = () => L - 7; // where a sprite's feet touch the floor, near its front edge
    function groundShadow(cx, w, at = feet()) {
      const x = Math.round(cx - w / 2);
      px(ctx, P, theme.shadow, x + 1, at - 1, w - 2, 1);
      px(ctx, P, theme.shadow, x + 2, at, w - 4, 1);
    }
    function agentAt(id, x, frame, stride, opts2 = {}) {
      const ag = story.agents.get(id) || {};
      const img = sprite(id, ag.tier || "novice", frame, stride, !!opts2.locked, !!opts2.blink);
      const y = feet() - 31 + (opts2.dy || 0);
      put(img, x, y);
      hits.push({ key: "agent:" + id, item: { type: "agent", agent: ag.agent ? ag : { agent: id } }, box: [x + 8, y + 4, 16, 26] });
    }

    // ---- the preview, signed out: the week's newest lessons as their monsters, greyed, standing in a row, each titled
    function previewLessons() {
      const recent = party.preview.recent.slice().sort((p, q) => String(q.at).localeCompare(String(p.at)) || q.id - p.id);
      return recent.slice(0, W < 300 ? 3 : 5);
    }
    function drawParade(t, a, lessons) {
      const n = lessons.length, fy = Math.round(L * 0.74);
      lessons.forEach((l, i) => {
        const type = KIND_MONSTER[l.kind] || "mal", mm = MONSTERS[type];
        const frame = still ? 0 : Math.floor(a * 1.2 + i * 0.7) % 2; // a gentle two-frame idle
        const key = `g|${type}|${frame}`;
        if (!art.has(key)) art.set(key, greyed(monster(type, "provisional", frame, false)));
        const img = art.get(key), w = img.w * img.size, h = img.h * img.size;
        const cx = Math.round((W * (i + 0.5)) / n), x = cx - Math.round(w / 2);
        const y = fy - h + 1 - (mm.fly ? Math.round(mm.fly * 0.6) : 0);
        groundShadow(cx, Math.round(w * 0.7), fy);
        put(img.cv, x, y);
        const title = String(l.title || "");
        text(title.length > 28 ? title.slice(0, 27).trimEnd() + "…" : title, cx, fy + 8, { size: 9, align: "center", panel: false,
          color: theme.panelInk, alpha: 0.85, max: Math.floor(W / n) - 4 });
        hits.push({ key: "lesson:" + l.id, item: { type: "lesson", lesson: l }, box: [x, y, w, fy + 12 - y] });
      });
    }

    function drawCamp(a) {
      const cx = Math.round(W / 2), fy = feet();
      // the glow, the logs, the flame
      for (const [r, al] of [[44, 0.07], [30, 0.09], [18, 0.12]]) {
        ctx.fillStyle = `rgba(255, 170, 80, ${al + Math.sin(a * 9) * 0.015})`;
        for (let y = -r; y <= r / 3; y++) {
          const w = Math.round(Math.sqrt(Math.max(0, r * r - y * y)) * 1.6);
          ctx.fillRect((cx - w) * P, (fy - 6 + y) * P, w * 2 * P, P);
        }
      }
      if (!art.has("logs")) {
        art.set("logs", cast([[0, 0, CAMP.logs.rows]], CAMP.logs.pal, P * 2));
        CAMP.flames.forEach((rows, i) => art.set("flame" + i, cast([[0, 0, rows]], CAMP.flamePal, P * 2)));
      }
      put(art.get("logs").cv, cx - 11, fy - 7);
      put(art.get("flame" + (Math.floor(a * 8) % 3)).cv, cx - 11, fy - 18);
      for (let i = 0; i < 7; i++) { // embers
        const u = (a * 0.5 + i / 7) % 1;
        if (u < 0.85) px(ctx, P, u < 0.4 ? "#ffd27a" : "#ff8a4a", cx - 1 + Math.round(Math.sin(i * 7 + a * 3) * 5), Math.round(fy - 18 - u * 30));
      }
      const k = Math.min(1, W / 240), spots = [-80, -54, 32, 56, 80].map((d) => Math.round(cx + d * k) - 16);
      AGENT_ORDER.forEach((id, i) => {
        const frame = Math.floor(a * 1.6 + i * 1.3) % 4;
        agentAt(id, spots[i], frame, 0, { locked: true });
      });
    }

    function drawWorld(t, a, st, quiet) {
      const order = story.order, seg = st.seg;
      const fighting = seg && (seg.type === "fight" || seg.type === "boss");
      const walking = quiet || (seg && seg.type === "walk") || (seg && seg.type === "end");
      const paused = isPaused();
      // bosses: felled ones lie by the path, standing ones wait at the end
      for (const bo of story.bosses) {
        const x = LX + bo.x - st.cam;
        if (x > W + 60 || x < -80) continue;
        // a boss the party couldn't fell steps back out of the way once its fight is over: it waits for the quest
        const gone = bo.end != null && st.tt != null ? st.tt - bo.end : -1;
        if (gone > 0.6) continue;
        if (gone > 0) {
          ctx.save();
          ctx.globalAlpha = 1 - gone / 0.6;
          ctx.translate(Math.round(gone * 30) * P, -Math.round(gone * 10) * P);
        }
        let flash = false, kx = 0;
        if (seg && seg.type === "boss" && seg.boss === bo) {
          for (let k = 0; k < BOSS_HITS; k++) {
            const imp = STEP_IN + k * HIT + 0.15, d = st.u - imp;
            if (d >= 0 && d < 0.07) flash = true;
            if (d >= 0 && d < 0.25) kx = Math.max(kx, 2 * (1 - d / 0.25));
          }
        }
        const fr = bo.felled ? 0 : Math.floor(a * 2.4) % 2, img = boss(bo.quest.n, fr, flash, bo.felled), k = img.size;
        const by = feet() - img.h * k + (bo.felled ? 6 : 2); // a felled one slumps into the floor
        groundShadow(x + (img.w * k) / 2, Math.round(img.w * k * 0.8));
        put(img.cv, x + kx, by);
        if (bo.felled) {
          const flag = art.get(`b|${bo.quest.n}|0|0|1|flag`);
          if (flag) put(flag.cv, x - 16, feet() - flag.h * 2 + 1);
        }
        const box = [x, by, img.w * k, img.h * k];
        hits.push({ key: "boss:" + bo.quest.id, item: { type: "boss", quest: bo.quest }, box });
        if (bo.felled) {
          for (let i = 0; i < 3; i++) { // dizzy stars over a felled boss
            const ang = a * 3 + (i * Math.PI * 2) / 3;
            px(ctx, P, "#f6c445", Math.round(x + (img.w * k) / 2 + Math.cos(ang) * 9), Math.round(by + 1 + Math.sin(ang) * 2));
          }
        }
        bo.box = box;
        if (gone > 0) {
          ctx.restore();
          bo.box = null;
          for (let i = 0; i < 8; i++) { // the puff it leaves in
            const ang = i * 0.8, rr = 6 + gone * 30;
            px(ctx, P, glow("#ffffff", 0.7 * (1 - gone / 0.6)), Math.round(x + 30 + Math.cos(ang) * rr), Math.round(feet() - 24 + Math.sin(ang) * rr * 0.5), 2, 2);
          }
        }
      }
      // monsters ahead, nearest last so it sits in front
      const encs = story.encounters;
      for (let j = encs.length - 1; j >= Math.max(0, st.cur); j--) {
        const e = encs[j], x0 = LX + e.x - st.cam;
        if (x0 > W + 10) continue;
        const local = seg && seg.type === "fight" && seg.enc === j ? st.u : -1;
        if (local >= 0 && st.tt >= e.kill) continue;
        let flash = false, kx = 0;
        for (let k = 0; local >= 0 && k < e.hits; k++) {
          const d = local - (STEP_IN + k * HIT + 0.15);
          if (d >= 0 && d < 0.07) flash = true;
          if (d >= 0 && d < 0.25) kx = Math.max(kx, 4 * (1 - d / 0.25));
        }
        const m = MONSTERS[e.type], img = monster(e.type, e.stage, Math.floor(a * 2.5 + j) % 2, flash);
        const hop = m.fly ? Math.round(Math.sin(a * 3 + j) * 2) - m.fly * img.size : 0;
        const x = x0 + kx, y = feet() - img.h * img.size + 1 + hop;
        groundShadow(x + (img.w * img.size) / 2, Math.round(img.w * img.size * (m.fly ? 0.45 : 0.7)));
        put(img.cv, x, y);
        hits.push({ key: "lesson:" + e.lesson.id, item: { type: "lesson", lesson: e.lesson }, box: [x, y, img.w * img.size, img.h * img.size] });
      }
      // the party: formation from the back; the one whose lesson it is steps up to fight
      const fighter = seg && seg.type === "fight" ? encs[seg.enc].agent : seg && seg.type === "boss" ? order[0] : null;
      const slots = order.map((id, i) => LX - i * SP);
      const drawOrder = order.map((id, i) => ({ id, i })).reverse();
      const front = LX + 10;
      let fighterPos = null;
      for (const { id, i } of drawOrder) {
        let x = slots[i], dy = 0, frame = Math.floor(a * 2 + i * 1.7) % 4, stride = 0;
        if (walking && !paused) {
          const step = Math.floor((t * SPEED) / 5 + i) % 2;
          stride = step + 1;
          frame = id === "antigravity" ? Math.floor(a * 2.5) % 4 : 0;
          dy = step ? -1 : 0;
        }
        if (id === fighter && fighting) {
          const u = st.u, back = seg.type === "fight" ? STEP_IN + encs[seg.enc].hits * HIT + 0.1 : seg.dur - 0.3;
          const step = u < STEP_IN ? ease(u / STEP_IN) : u > back ? 1 - ease((u - back) / 0.3) : 1;
          x = Math.round(slots[i] + (front - slots[i]) * step);
          const n = seg.type === "fight" ? encs[seg.enc].hits : BOSS_HITS;
          for (let k = 0; k < n; k++) {
            const d = u - (STEP_IN + k * HIT);
            if (d >= 0 && d < 0.22) x += Math.round(3 * Math.sin((d / 0.22) * Math.PI));
          }
          fighterPos = x;
        }
        const blink = !walking && Math.floor(a * 4 + i * 5) % 19 === 0;
        agentAt(id, x, frame, stride, { dy, blink });
        agentX.set(id, x);
      }
      if (fighting && fighterPos != null) drawBlows(st, fighter, fighterPos);
      drawAfter(t, st);
    }

    // projectiles from the casters, a slash from the fighters; numbers fly off each blow
    function drawBlows(st, fighter, fx) {
      const seg = st.seg, isBoss = seg.type === "boss";
      const n = isBoss ? BOSS_HITS : story.encounters[seg.enc].hits;
      const target = isBoss ? LX + seg.boss.x - st.cam + 8 : LX + story.encounters[seg.enc].x - st.cam + 3;
      const ty = feet() - (isBoss ? 30 : 14);
      const ranged = fighter === "claude" || fighter === "copilot" || fighter === "antigravity";
      const col = { claude: ["#ffd66b", "#ff9a3c"], copilot: ["#e9fbff", "#5cb8ff"], antigravity: ["#fbbc04", "#4285f4"],
        codex: ["#eafff8", "#14b88c"], bob: ["#ffffff", "#78a9ff"], opencode: ["#b8ffd9", "#d1497b"] }[fighter] || ["#ffffff", "#cccccc"];
      for (let k = 0; k < n; k++) {
        const d = st.u - (STEP_IN + k * HIT);
        if (ranged && d >= 0.03 && d < 0.15) { // a bolt with a glow and a trail
          const u = (d - 0.03) / 0.12, sx = fx + 22, x = Math.round(sx + (target - sx) * u), y = Math.round(ty - 3 - Math.sin(u * Math.PI) * 4);
          px(ctx, P, glow(col[1], 0.35), x - 2, y - 2, 6, 6);
          px(ctx, P, col[1], x - 6, y, 4, 2);
          px(ctx, P, col[1], x - 9, y + 1, 2, 1);
          px(ctx, P, col[0], x - 1, y - 1, 4, 4);
          px(ctx, P, "#ffffff", x, y, 2, 2);
        }
        if (!ranged && d >= 0.08 && d < 0.2) { // a slash: an arc of light across the target
          const u = (d - 0.08) / 0.12;
          for (let i = 0; i < 7; i++) {
            const ang = -1.2 + (i / 6) * 2.4 * u;
            px(ctx, P, i > 4 ? col[1] : col[0], Math.round(target + 2 + Math.cos(ang) * 7), Math.round(ty + Math.sin(ang) * 7));
          }
        }
        const imp = d - 0.15;
        if (imp >= 0 && imp < 0.14) { // impact: a star, then sparks flying off
          const r = imp < 0.05 ? 5 : 3, cx = target + 2;
          for (let i = -r; i <= r; i++) {
            const c = Math.abs(i) > r - 2 ? col[1] : "#ffffff";
            px(ctx, P, c, cx + i, ty); px(ctx, P, c, cx, ty + i);
          }
          if (imp < 0.05) for (const [dx, dy] of [[-2, -2], [2, -2], [-2, 2], [2, 2]]) px(ctx, P, "#fff7c2", cx + dx, ty + dy);
          for (let i = 0; i < 4; i++) {
            const ang = 0.6 + i * 1.5, dd = 4 + imp * 60;
            px(ctx, P, col[0], Math.round(cx + Math.cos(ang) * dd), Math.round(ty + Math.sin(ang) * dd * 0.8));
          }
        }
        if (imp >= 0 && imp < 0.75) { // the share of the lesson's XP this blow took, or a spark on a boss
          const rise = ease(imp / 0.75) * 12;
          if (isBoss) text("✦", target + 4, ty - 6 - rise, { size: 11, color: theme.dark ? "#c7a6ff" : "#6d47c4", panel: false, alpha: 1 - Math.max(0, imp - 0.5) / 0.25 });
          else {
            const e = story.encounters[seg.enc], share = Math.round((e.xp * (k + 1)) / e.hits) - Math.round((e.xp * k) / e.hits);
            text(String(share), target + 6, ty - 8 - rise, { size: 14, weight: 700, color: gold(), panel: false,
              alpha: 1 - Math.max(0, imp - 0.5) / 0.25 });
          }
        }
      }
    }

    // after a kill: the burst, "+XP · skill" over the fighter, and any level-up banners
    function drawAfter(t, st) {
      const tt = st.tt;
      if (tt == null) return;
      const encs = story.encounters;
      for (let j = Math.max(0, st.cur - 4); j < Math.min(encs.length, st.cur + 1); j++) {
        const e = encs[j], d = tt - e.kill;
        if (d < 0 || d > 4) continue;
        const mx = LX + e.x - st.cam + 12, my = feet() - 12;
        if (d < 0.6) { // pop: a white puff, then chips of the monster fly out and fall
          const r = rng(e.lesson.id || j), chips = MONSTERS[e.type].chips, big = e.stage === "canonical" ? 2 : 1;
          if (d < 0.3) {
            const u = d / 0.3, rad = (3 + u * 9) * big;
            ctx.save();
            ctx.globalAlpha = 1 - u;
            for (let i = 0; i < 7; i++) {
              const ang = i * 0.9 + 0.3, cx = mx + Math.cos(ang) * rad * 0.6, cy = my - 4 * big + Math.sin(ang) * rad * 0.45;
              const pr = Math.max(1, Math.round((3 - u * 2) * big));
              px(ctx, P, "#ffffff", Math.round(cx - pr), Math.round(cy - pr), pr * 2, pr * 2);
            }
            ctx.restore();
          }
          for (let i = 0; i < 14; i++) {
            const ang = r() * Math.PI * 2, sp = (18 + r() * 26) * big;
            const x = mx + Math.cos(ang) * sp * d, y = my - 4 + Math.sin(ang) * sp * d * 0.8 - 24 * d + 90 * d * d;
            if (d < 0.45 || i % 2) px(ctx, P, chips[i % 3], Math.round(x), Math.round(y), 2, 2);
          }
        }
        if (d < 1.7) {
          const rise = ease(d / 1.7) * 9;
          const label = e.skill ? fill(labels.xp, { xp: e.xp, skill: e.skill }) : `+${e.xp} XP`;
          text(label, LX + 26, feet() - 38 - rise, { size: 11, weight: 700, color: textColor(e.agent), align: "center", panel: true,
            alpha: d > 1.3 ? 1 - (d - 1.3) / 0.4 : 1, max: Math.max(60, W - 20) });
        }
        e.events.forEach((ev, k) => {
          const b = d - 0.25 - k * 1.4;
          if (b >= 0 && b < 2.3) { cheer(ev, b); banners.push([ev, b]); }
        });
      }
    }
    // the agent who levelled: a pillar of light (a level) or a ring of sparks (a skill) where it stands
    function cheer(ev, b) {
      const x = agentX.get(ev.agent);
      if (x == null || b > 1.2) return;
      const cx = x + 16, top = feet() - 34, isLevel = ev.type === "level";
      if (isLevel && b < 0.9) {
        const u = b / 0.9, w = Math.round(12 * (1 - u * 0.6));
        ctx.save();
        ctx.globalAlpha = 0.45 * (1 - u);
        px(ctx, P, "#fff1a8", cx - w, 0, w * 2, feet() + 2);
        ctx.globalAlpha = 0.7 * (1 - u);
        px(ctx, P, "#ffffff", cx - Math.ceil(w / 3), 0, Math.ceil(w / 3) * 2, feet() + 2);
        ctx.restore();
      }
      const n = isLevel ? 10 : 6, col = isLevel ? ["#f6c445", "#ffffff"] : [mix(window.Sprites.color(ev.agent), "#ffffff", 0.3), "#ffffff"];
      for (let i = 0; i < n; i++) {
        const ang = (i / n) * Math.PI * 2 + b * 2, rr = 8 + b * 16, y = top + 14 + Math.sin(ang) * rr * 0.6 - b * 10;
        const x2 = Math.round(cx + Math.cos(ang) * rr);
        if ((Math.floor(b * 12) + i) % 3) {
          px(ctx, P, col[i % 2], x2, Math.round(y));
          if (i % 2 === 0 && b < 0.6) { px(ctx, P, col[0], x2 - 1, Math.round(y)); px(ctx, P, col[0], x2 + 1, Math.round(y)); }
        }
      }
    }
    function banner(ev, b) {
      const who = agentName(ev.agent);
      const isLevel = ev.type === "level";
      const msg = isLevel ? fill(labels.levelUp, { agent: who, n: ev.level }) : fill(labels.skillUp, { agent: who, skill: ev.module_name || "", n: ev.level });
      const slide = b < 0.18 ? ease(b / 0.18) : 1, fade = b > 1.9 ? 1 - (b - 1.9) / 0.4 : 1;
      const y = Math.round((L < 90 ? -4 : 10) + 8 * slide); // a short strip: over the HUD row
      ctx.save();
      ctx.globalAlpha = fade;
      ctx.font = `700 ${Math.round((isLevel ? 13 : 11) * dpr)}px ${FONT}`;
      let s = msg;
      const maxW = (W - 24) * P;
      while (s.length > 4 && ctx.measureText(s).width > maxW - 20 * P) s = s.slice(0, -2) + "…";
      const w = Math.ceil(ctx.measureText(s).width / P) + 18, h = isLevel ? 15 : 12, x = Math.round(W / 2 - w / 2);
      const rimColor = isLevel ? theme.cta : mix(window.Sprites.color(ev.agent), "#ffffff", theme.dark ? 0.3 : 0);
      ctx.fillStyle = rimColor;
      ctx.fillRect((x + 1) * P, (y - 1) * P, (w - 2) * P, (h + 2) * P);
      ctx.fillRect(x * P, y * P, w * P, h * P);
      ctx.fillStyle = theme.dark ? (isLevel ? "#2a1c0c" : "#0b1a33") : "#ffffff";
      ctx.fillRect((x + 1) * P, (y + 1) * P, (w - 2) * P, (h - 2) * P);
      ctx.fillStyle = isLevel ? gold() : theme.panelInk;
      ctx.textBaseline = "middle";
      ctx.textAlign = "center";
      ctx.fillText(s, (x + w / 2) * P, (y + h / 2 + 0.5) * P);
      ctx.restore();
      if (isLevel) for (let i = 0; i < 10; i++) { // confetti
        const r = rng(i * 31 + (ev.level || 0)), u = b * 1.4, cx2 = x + r() * w, cy = y + h + u * (8 + r() * 14) - 4;
        if (u < 1.6) px(ctx, P, ["#f6c445", "#ffffff", "#e5484d", "#5cb8ff", "#7bd389"][i % 5], Math.round(cx2), Math.round(cy));
      }
    }

    function drawHud(t, a, st, sleeping, quiet) {
      const top = 8;
      if (sleeping) { text(labels.asleep, W / 2, top + 4, { size: 12, align: "center" }); return; }
      if (previewing()) {
        if (quiet) text(labels.quiet, W / 2, top + 16, { size: 11, align: "center" });
        else text(labels.preview, 4, top, { max: W - 12 });
        return;
      }
      if (story.encounters.length) {
        const n = Math.min(story.encounters.length, st.cur + 1);
        text(fill(labels.wave, { n, total: story.encounters.length }), W - 4, top, { align: "right", color: gold() });
      }
      if (quiet) text(labels.quiet, W / 2, top + 16, { size: 11, align: "center" });
      const seg = st.seg;
      // the next standing boss ahead: its quest, and how to beat it
      const next = story.bosses.find((bo) => !bo.felled && bo.x - st.cam >= -12);
      for (const bo of next ? [next] : []) {
        if (!bo.box) continue;
        const [bx, by, bw] = bo.box;
        if (bx > W || bx + bw < 0) continue;
        const near = seg && seg.type === "boss" && seg.boss === bo;
        const maxW = Math.min(W - 10, 240), cx = clamp(bx + bw / 2, maxW / 2 + 4, W - maxW / 2 - 4);
        text(fill(labels.boss, { text: bo.quest.text || "" }), cx, by - 9, { size: 10, align: "center", max: maxW - 8 });
        if (near && st.u > STEP_IN + BOSS_HITS * HIT - 0.2) {
          text(labels.bossHint, cx, by + 2 + Math.round(Math.sin(a * 4)), { size: 10, align: "center", color: gold(), max: W - 10 });
        }
      }
    }

    // ---- focus ring for keyboard users, and hit-testing
    function focusables() { return hits.filter((h) => h.box[0] + h.box[2] > 0 && h.box[0] < W).sort((p, q) => p.box[0] - q.box[0]); }
    function drawFocus(a) {
      if (!keyboard || !focusKey || document.activeElement !== canvas) return;
      const h = hits.find((x) => x.key === focusKey);
      if (!h) return;
      const [x, y, w, hh] = h.box.map(Math.round), c = Math.floor(a * 4) % 2 ? theme.panelInk : gold();
      for (const [cx, cy, dx, dy] of [[x - 2, y - 2, 1, 1], [x + w + 1, y - 2, -1, 1], [x - 2, y + hh + 1, 1, -1], [x + w + 1, y + hh + 1, -1, -1]]) {
        px(ctx, P, c, cx, cy, 1, 1); px(ctx, P, c, cx + dx, cy, 1, 1); px(ctx, P, c, cx, cy + dy, 1, 1);
      }
    }
    function isPaused() { return !!hover || (keyboard && !!focusKey && document.activeElement === canvas); }
    function itemAt(clientX, clientY) {
      const rect = canvas.getBoundingClientRect();
      const lx = ((clientX - rect.left) * (canvas.width / rect.width)) / P, ly = ((clientY - rect.top) * (canvas.height / rect.height)) / P;
      for (let i = hits.length - 1; i >= 0; i--) {
        const [x, y, w, h] = hits[i].box;
        if (lx >= x && lx < x + w && ly >= y && ly < y + h) return hits[i];
      }
      return null;
    }
    function clientPoint(h) {
      const rect = canvas.getBoundingClientRect(), k = rect.width / (canvas.width / P);
      return { x: rect.left + (h.box[0] + h.box[2] / 2) * k, y: rect.top + h.box[1] * k };
    }
    function onMove(e) {
      keyboard = false;
      const h = itemAt(e.clientX, e.clientY);
      hover = h ? h.key : null;
      canvas.style.cursor = h ? "pointer" : "";
      onHover(h ? h.item : null, { x: e.clientX, y: e.clientY });
      if (still) render();
    }
    function onLeave() { hover = null; canvas.style.cursor = ""; onHover(null, { x: 0, y: 0 }); }
    function onClick(e) { const h = itemAt(e.clientX, e.clientY); if (h) onPick(h.item); }
    function onKey(e) {
      const list = focusables();
      if (!list.length) return;
      if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
        e.preventDefault();
        keyboard = true;
        let i = list.findIndex((h) => h.key === focusKey);
        i = i < 0 ? (e.key === "ArrowRight" ? 0 : list.length - 1) : clamp(i + (e.key === "ArrowRight" ? 1 : -1), 0, list.length - 1);
        focusKey = list[i].key;
        onHover(list[i].item, clientPoint(list[i]));
        render();
      } else if ((e.key === "Enter" || e.key === " ") && focusKey) {
        const h = list.find((x) => x.key === focusKey);
        if (h) { e.preventDefault(); onPick(h.item); }
      } else if (e.key === "Escape") {
        focusKey = null;
        onHover(null, { x: 0, y: 0 });
        render();
      }
    }
    function onBlur() { if (keyboard) { focusKey = null; onHover(null, { x: 0, y: 0 }); } keyboard = false; render(); }
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerleave", onLeave);
    canvas.addEventListener("click", onClick);
    canvas.addEventListener("keydown", onKey);
    canvas.addEventListener("blur", onBlur);

    // ---- frames: a still one for reduced motion, else the shared loop
    function stillTime() {
      const f = story.segs.find((s) => s.type === "fight") || story.segs.find((s) => s.type === "boss");
      return f ? f.t0 + STEP_IN + 0.02 : 0;
    }
    function render() {
      if (!alive || !layers) return;
      if (still) draw(pinned != null ? pinned : stillTime(), pinned != null ? pinned : 0.25);
      else draw(storyT, animT);
    }
    self.running = () => alive && !still && visible && !document.hidden;
    self.frame = (now) => {
      const dt = lastNow ? Math.min(0.1, (now - lastNow) / 1000) : 0;
      lastNow = now;
      animT += dt;
      if (!isPaused()) storyT += dt;
      if (story.total && storyT > story.total * 50) storyT %= story.total;
      if (Date.now() - checked > 600000) { checked = Date.now(); const tod = asleep() ? "night" : timeOfDay(); if (tod !== time) measure(); }
      draw(storyT, animT);
    };
    self.destroy = () => {
      if (!alive) return;
      alive = false;
      scenes.delete(self);
      io && io.disconnect();
      ro && ro.disconnect();
      mo && mo.disconnect();
      scheme && scheme.removeEventListener && scheme.removeEventListener("change", self.retheme);
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerleave", onLeave);
      canvas.removeEventListener("click", onClick);
      canvas.removeEventListener("keydown", onKey);
      canvas.removeEventListener("blur", onBlur);
      canvas.remove();
      art.clear();
    };

    measure();
    io = typeof IntersectionObserver === "function" ? new IntersectionObserver((entries) => {
      for (const en of entries) visible = en.isIntersecting;
      if (!canvas.isConnected) { self.destroy(); return; }
      if (visible) { lastNow = 0; kick(); }
    }) : null;
    io && io.observe(canvas);
    let width = host.clientWidth;
    ro = typeof ResizeObserver === "function" ? new ResizeObserver(() => {
      if (!alive || host.clientWidth === width) return;
      width = host.clientWidth;
      measure();
      render();
    }) : null;
    ro && ro.observe(host);
    // the page's theme: the toggle sets data-theme on <html>; without it the system's scheme decides
    self.retheme = () => { if (alive) { measure(); render(); } };
    mo = typeof MutationObserver === "function" ? new MutationObserver(self.retheme) : null;
    mo && mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    scheme = typeof matchMedia === "function" ? matchMedia("(prefers-color-scheme: dark)") : null;
    scheme && scheme.addEventListener && scheme.addEventListener("change", self.retheme);
    if (document.fonts && document.fonts.load) document.fonts.load(`600 11px ${FONT}`).then(() => render(), () => {});
    scenes.add(self);
    render();
    kick();

    return {
      update(next) {
        if (!alive) return;
        const before = (party.recent || []).map((l) => l.id).join(), wasAsleep = asleep(), zone = JSON.stringify(party.zone || null);
        party = next || {};
        story = plan(party);
        if ((party.recent || []).map((l) => l.id).join() !== before) storyT = 0;
        if (wasAsleep !== asleep() || zone !== JSON.stringify(party.zone || null)) measure();
        render();
        kick();
      },
      destroy: self.destroy,
    };
  }

  // =====================================================================================
  // The arena: two parties face each other on the blueprint floor and a battle's log is replayed event by event
  // (knowledge/arena.py computes it; nothing here decides anything)
  // =====================================================================================
  const BATTLE_LABELS = { victory: "Victory!", defeat: "Defeat", round: "Round {n}", crit: "Crit!", ko: "KO",
    special: "{agent}: {name}", summary: "{winner} won in {n} rounds", vs: "{left} vs {right}", level: "Lv {n}",
    tiers: { novice: "Novice", adept: "Adept", expert: "Expert", master: "Master" } };
  const STEP_MAX = 0.35, STEP_MIN = 0.16, REPLAY_MAX = 50; // seconds an event, and the longest a replay may run
  const AGENT_NAMES = { claude: "Claude", codex: "Codex", copilot: "Copilot", bob: "Bob", antigravity: "Antigravity",
    opencode: "OpenCode" };

  function mountBattle(host, opts = {}) {
    const labels = { ...BATTLE_LABELS, ...(opts.labels || {}) };
    labels.tiers = { ...BATTLE_LABELS.tiers, ...((opts.labels && opts.labels.tiers) || {}) };
    const onEnd = typeof opts.onEnd === "function" ? opts.onEnd : () => {};
    const cssH = Math.max(120, Number(opts.height) || 240);
    // `at` (seconds into the replay) pins a still frame, for previews and tests; reduced motion shows the end
    const pinned = Number.isFinite(opts.at) ? opts.at : null;
    const still = !!opts.reducedMotion || pinned != null;
    const result = opts.result || { winner: "a", rounds: 0, log: [], units: {} };
    // the viewer's party is on the left; `leftSide` says which side of the log that is ("a" unless told)
    const leftSide = opts.leftSide === "b" ? "b" : "a", rightSide = leftSide === "a" ? "b" : "a";
    const sideName = { [leftSide]: (opts.left && opts.left.name) || "", [rightSide]: (opts.right && opts.right.name) || "" };
    const log = (Array.isArray(result.log) ? result.log : []).slice(0, 400);
    const units = Object.entries(result.units || {}).map(([key, u]) => ({ key, side: key.split(":")[0], agent: key.split(":")[1],
      max: Math.max(1, Number(u.hp) || 1), tier: u.tier || "novice", level: u.level || 1 }));
    const unitOf = new Map(units.map((u) => [u.key, u]));
    const step = clamp(REPLAY_MAX / Math.max(1, log.length), STEP_MIN, STEP_MAX);
    // hp after each event, and when each unit fell, worked out once
    const hpAfter = [], koAt = new Map();
    {
      const hp = new Map(units.map((u) => [u.key, u.max]));
      log.forEach((ev, i) => {
        if (ev.dst && unitOf.has(ev.dst) && Number.isFinite(ev.hp)) hp.set(ev.dst, Math.max(0, ev.hp));
        if (ev.t === "ko" && ev.dst) koAt.set(ev.dst, i);
        hpAfter.push(new Map(hp));
      });
    }
    const total = log.length * step;
    // champion against champion: one unit a side is staged as a duel, big, with an intro
    const duel = units.length === 2 && units[0].side !== units[1].side;
    const INTRO = duel ? 1.4 : 0;
    // where each unit really ended: a log cut at 400 events can stop before the fight does, so the result's final HP
    // (units[id].end_hp, or a map on the "end" event) wins over the log's last word
    const finalHp = new Map(hpAfter.length ? hpAfter[hpAfter.length - 1] : units.map((u) => [u.key, u.max]));
    {
      const endEv = log.find((ev) => ev.t === "end") || {};
      const endMap = [endEv.end_hp, endEv.units, endEv.hp].find((v) => v && typeof v === "object") || {};
      for (const u of units) {
        const own = (result.units || {})[u.key] || {}, e = endMap[u.key];
        const v = Number.isFinite(own.end_hp) ? own.end_hp : Number.isFinite(e) ? e : e && Number.isFinite(e.hp) ? e.hp : null;
        if (v != null) finalHp.set(u.key, clamp(v, 0, u.max));
      }
    }

    const canvas = document.createElement("canvas");
    canvas.style.display = "block";
    canvas.style.width = "100%";
    canvas.style.height = cssH + "px";
    canvas.style.imageRendering = "pixelated";
    canvas.setAttribute("role", "img");
    host.appendChild(canvas);
    const ctx = canvas.getContext("2d");
    let W = 0, L = 0, P = 1, dpr = 1, horizon = 0, theme = null, floor = null, wall = null, barW = 20;
    let t = 0, animT = 0, lastNow = 0, visible = true, alive = true, ended = false;
    const art = new Map(), slot = new Map();
    const self = { canvas };

    function measure() {
      dpr = window.devicePixelRatio || 1;
      const cssW = Math.max(160, host.clientWidth || canvas.clientWidth || 600);
      P = Math.max(1, Math.round((cssH >= 140 ? 2 : 1) * dpr));
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
      W = Math.ceil(canvas.width / P);
      L = Math.floor(canvas.height / P);
      horizon = Math.round(L * 0.42);
      theme = readTheme("guard");
      wall = wallTile(theme, horizon, P);
      floor = floorLayer(theme, W, horizon, L, P);
      // the floor's lines toward a vanishing point, and a gold ring in the middle: the arena (drawn once, nothing scrolls)
      const g = floor.getContext("2d"), vx = W / 2, vy = horizon - L, fh = L - horizon;
      g.fillStyle = theme.floorLine;
      for (let xf = -W; xf < 2 * W; xf += 20) {
        for (let y = 1; y <= fh; y++) {
          const x = Math.round(vx + ((xf - vx) * (horizon + y - vy)) / (L - vy));
          if (x >= 0 && x < W) g.fillRect(x * P, y * P, P, P);
        }
      }
      const cy = Math.round(fh * 0.55), rx = Math.min(W * 0.36, 150), ry = fh * 0.32;
      g.fillStyle = glow(theme.mark, 0.6);
      for (let i = 0; i < 160; i++) {
        const a = (i / 160) * Math.PI * 2;
        g.fillRect(Math.round(vx + Math.cos(a) * rx) * P, Math.round(cy + Math.sin(a) * ry) * P, P, P);
      }
      layout();
      art.clear();
    }
    // two rows a side: the three highest levels in front, the others behind; the right side mirrors the left
    function layout() {
      if (duel) { // the two champions big, facing each other in the middle of the floor
        const k = L >= 110 && W >= 300 ? 3 : 2, gap = W >= 300 ? Math.round(W * 0.05) : 6, cx = W / 2;
        for (const u of units) {
          const left = u.side === leftSide;
          slot.set(u.key, { x: Math.round(left ? cx - gap - 28 * k : cx + gap - 4 * k), feet: L - 3, flip: !left, row: 0, k });
        }
        return;
      }
      const cx = W / 2, gap = W >= 300 ? Math.round(W * 0.06) : 2;
      const room = W / 2 - gap - 2, stepX = Math.min(52, Math.max(16, Math.floor((room - 30) / 2.4)));
      barW = Math.min(20, stepX - 3);
      const front = L - 10, back = L - Math.round((L - horizon) * 0.5);
      for (const side of [leftSide, rightSide]) {
        const mine = units.filter((u) => u.side === side).sort((p, q) => q.level - p.level || AGENT_ORDER.indexOf(p.agent) - AGENT_ORDER.indexOf(q.agent));
        const dir = side === leftSide ? -1 : 1;
        mine.forEach((u, i) => {
          const row = i < 3 ? 0 : 1, k = row ? i - 3 + 0.5 : i;
          const off = gap + 16 + k * stepX;
          slot.set(u.key, { x: Math.round(cx + dir * off - 16), feet: row ? back : front, flip: side === rightSide, row, k: 1 });
          if (row) slot.get(u.key).x += dir * Math.round(stepX * 0.15); // the back row sits a little wider, so heads don't stack
        });
      }
    }

    // ---- art: a sprite (mirrored on the right), its white flash, its grey KO copy
    function sprite(u, frame, kind) {
      const s = slot.get(u.key), key = `${u.key}|${frame}|${kind}`;
      if (!art.has(key)) {
        const base = blank(1, 1);
        window.Sprites.draw(base, { agent: u.agent, tier: u.tier, frame, scale: P * s.k });
        const c = blank(base.width, base.height), g = c.getContext("2d");
        if (s.flip) { g.translate(c.width, 0); g.scale(-1, 1); }
        g.drawImage(base, 0, 0);
        g.setTransform(1, 0, 0, 1, 0, 0);
        if (kind === "flash") { g.globalCompositeOperation = "source-atop"; g.fillStyle = "#ffffff"; g.fillRect(0, 0, c.width, c.height); }
        if (kind === "ko") {
          const d = g.getImageData(0, 0, c.width, c.height);
          for (let i = 0; i < d.data.length; i += 4) {
            const v = d.data[i] * 0.3 + d.data[i + 1] * 0.55 + d.data[i + 2] * 0.15;
            d.data[i] = v * 0.8 + 30; d.data[i + 1] = v * 0.82 + 34; d.data[i + 2] = v * 0.9 + 46;
          }
          g.putImageData(d, 0, 0);
        }
        art.set(key, c);
      }
      return art.get(key);
    }
    const put = (img, x, y) => ctx.drawImage(img, Math.round(x) * P, Math.round(y) * P);
    function text(str, x, y, { size = 11, color = null, align = "center", panel = false, alpha = 1, weight = 700, max = 0 } = {}) {
      ctx.save();
      ctx.globalAlpha = clamp(alpha, 0, 1);
      ctx.font = `${weight} ${Math.round(size * dpr)}px ${FONT}`;
      ctx.textBaseline = "middle";
      let s = String(str);
      if (max && ctx.measureText(s).width > max * P) {
        while (s.length > 1 && ctx.measureText(s + "…").width > max * P) s = s.slice(0, -1);
        s += "…";
      }
      const w = Math.ceil(ctx.measureText(s).width / P) + 6, h = Math.ceil((size * dpr * 1.25) / P) + 2;
      const left = clamp(align === "center" ? Math.round(x - w / 2) : align === "right" ? Math.round(x - w) : Math.round(x), 1, Math.max(1, W - w - 1));
      const top = Math.round(y - h / 2);
      if (panel) {
        ctx.fillStyle = theme.panelRim;
        ctx.fillRect((left + 1) * P, top * P, (w - 2) * P, h * P);
        ctx.fillRect(left * P, (top + 1) * P, w * P, (h - 2) * P);
        ctx.fillStyle = theme.panel;
        ctx.fillRect((left + 1) * P, (top + 1) * P, (w - 2) * P, (h - 2) * P);
      } else {
        ctx.lineWidth = Math.max(2, Math.round(3 * dpr));
        ctx.strokeStyle = theme.dark ? "rgba(6, 14, 32, 0.9)" : "rgba(255, 255, 255, 0.95)";
        ctx.lineJoin = "round";
        ctx.strokeText(s, (left + 3) * P, (top + h / 2) * P);
      }
      ctx.fillStyle = color || theme.panelInk;
      ctx.fillText(s, (left + 3) * P, (top + h / 2) * P);
      ctx.restore();
      return { x: left, y: top, w, h };
    }
    const gold = () => (theme.dark ? "#ffd27a" : "#9a5a08");
    const green = () => (theme.dark ? "#7dffb0" : "#0c7c50");
    const red = () => (theme.dark ? "#ff8a8a" : "#c0392b");
    const name = (key) => { const u = unitOf.get(key); return u ? AGENT_NAMES[u.agent] || u.agent : ""; };
    function hpBar(x, y, w, frac, ghost) {
      px(ctx, P, theme.dark ? "rgba(4, 10, 24, 0.85)" : "rgba(20, 40, 70, 0.55)", x - 1, y - 1, w + 2, 4);
      if (ghost > frac) px(ctx, P, theme.dark ? "#ffe9b0" : "#f6d27a", x, y, Math.round(w * ghost), 2);
      const c = frac > 0.5 ? "#4fd88a" : frac > 0.25 ? "#f6c445" : "#ff6b6b";
      if (frac > 0) px(ctx, P, c, x, y, Math.max(1, Math.round(w * frac)), 2);
    }

    // ---- one frame at replay time tt (the duel's intro first, then the log)
    function draw(tt, a) {
      ctx.imageSmoothingEnabled = false;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      for (let x = 0; x < W; x += 32) put(wall, x, 0);
      const lt = tt - INTRO, intro = lt < 0;
      const i = log.length && !intro ? clamp(Math.floor(lt / step), 0, log.length - 1) : -1, u = lt - Math.max(0, i) * step;
      const done = lt >= total && !intro;
      const cur = i >= 0 ? log[i] : null, full = new Map(units.map((x) => [x.key, x.max]));
      const hpNow = done ? finalHp : i >= 0 ? hpAfter[i] : full;
      const hpBefore = i > 0 ? hpAfter[i - 1] : full;
      // a crit (or an Overdrive) shakes the floor in a duel
      let sh = 0;
      if (duel && cur && !done && !still && ((cur.t === "crit" && u >= 0.08 && u < 0.24) || (cur.t === "special" && u < 0.2))) sh = Math.floor(u * 60) % 2 ? 2 : -2;
      ctx.save();
      ctx.translate(sh * P, 0);
      put(floor, 0, horizon);
      // the blow being struck: the striker lunges at its target, which flashes and is knocked back
      const lunge = new Map(), flash = new Set(), knock = new Map();
      if (cur && !done && (cur.t === "hit" || cur.t === "crit") && slot.has(cur.src) && slot.has(cur.dst)) {
        const s = slot.get(cur.src), d = slot.get(cur.dst), k = u < 0.1 ? u / 0.1 : u < 0.28 ? 1 - (u - 0.1) / 0.18 : 0;
        lunge.set(cur.src, [Math.round((d.x - s.x) * 0.35 * k), Math.round((d.feet - s.feet) * 0.35 * k)]);
        if (u >= 0.08 && u < 0.16) flash.add(cur.dst);
        const push = (cur.t === "crit" ? 4 : 2) * (duel ? 2.5 : 1);
        if (u >= 0.08 && u < 0.3) knock.set(cur.dst, Math.round((d.flip ? 1 : -1) * push * (1 - (u - 0.08) / 0.22)));
      }
      if (cur && !done && cur.t === "special" && slot.has(cur.src) && u < 0.2) flash.add(cur.src);
      // back row first, then the front
      const order = units.slice().sort((p, q) => (slot.get(p.key).feet - slot.get(q.key).feet) || (slot.get(p.key).x - slot.get(q.key).x));
      for (const un of order) {
        const s = slot.get(un.key), k = s.k, ko = done ? finalHp.get(un.key) <= 0
          : koAt.has(un.key) && koAt.get(un.key) <= i && (koAt.get(un.key) < i || u > 0.15);
        const [lx, ly] = lunge.get(un.key) || [0, 0], kx = knock.get(un.key) || 0;
        const x = s.x + lx + kx, top = s.feet - 31 * k + ly;
        // a soft shadow on the floor
        px(ctx, P, theme.shadow, x + 8 * k, s.feet - 1, 16 * k, 1);
        px(ctx, P, theme.shadow, x + 9 * k, s.feet, 14 * k, 1);
        if (ko) { // greyed and sat down: sunk into the floor, cut at the feet
          ctx.save();
          ctx.beginPath();
          ctx.rect(0, 0, canvas.width, (s.feet + 1) * P);
          ctx.clip();
          put(sprite(un, 0, "ko"), x, top + 5 * k);
          ctx.restore();
          text(labels.ko, x + 16 * k, top + 6 * k, { size: duel ? 14 : 9, color: red(), alpha: 0.9 });
          continue;
        }
        const frame = still || intro ? 0 : Math.floor(a * 2 + AGENT_ORDER.indexOf(un.agent)) % 4;
        put(sprite(un, frame, flash.has(un.key) ? "flash" : "base"), x, top);
        if (duel) continue; // a duel's HP bars are the wide ones at the top
        // its HP bar, the lost part fading a moment later
        const now = hpNow.get(un.key), before = hpBefore.get(un.key), lag = done || u > 0.3 ? now : before;
        hpBar(x + 16 - Math.round(barW / 2), top - 2, barW, now / un.max, Math.max(now, lag) / un.max);
      }
      // what the event says: numbers, heals, specials
      if (cur && !done) drawEvent(cur, u, hpNow);
      ctx.restore();
      if (duel) drawDuelHud(cur, done, hpNow, hpBefore, u);
      else drawHud(cur, done, hpNow);
      if (intro) drawIntro(tt);
      if (done) drawEnd(lt - total, a);
    }
    function drawEvent(ev, u, hpNow) {
      const d = slot.get(ev.dst), s = slot.get(ev.src), big = duel ? 1.5 : 1;
      const at = (sl) => [sl.x + 16 * sl.k, sl.feet - 31 * sl.k]; // a unit's centre line and head
      if ((ev.t === "hit" || ev.t === "crit") && d && u >= 0.08) {
        const [cx, head] = at(d), rise = ease((u - 0.08) / 0.5) * 10 * big, crit = ev.t === "crit";
        const ny = Math.max(duel ? 34 : 14, head - 5 * big - rise);
        if (crit) text(labels.crit, cx, ny + 10 * big, { size: Math.round(9 * big), color: gold() });
        text(String(ev.n), cx, ny, { size: Math.round((crit ? 15 : 12) * big), color: crit ? gold() : theme.panelInk });
        if (u < 0.16) { // an impact star
          const cy = d.feet - 16 * d.k, r = Math.round((crit ? 5 : 3) * (duel ? 2 : 1));
          for (let k = -r; k <= r; k++) {
            const c = Math.abs(k) > r - 2 ? gold() : "#ffffff";
            px(ctx, P, c, cx + k, cy); px(ctx, P, c, cx, cy + k);
          }
          if (crit && duel) for (const [dx, dy] of [[-3, -3], [3, -3], [-3, 3], [3, 3]]) px(ctx, P, "#fff7c2", cx + dx, cy + dy, 2, 2);
        }
      }
      if (ev.t === "heal" && d) {
        const [cx, head] = at(d), rise = ease(u / 0.5) * 10 * big;
        text("+" + ev.n, cx, Math.max(duel ? 34 : 14, head - 5 * big - rise), { size: Math.round(12 * big), color: green() });
        for (let k = 0; k < (duel ? 10 : 6); k++) { // little green crosses rising
          const yy = d.feet - 6 - ((u * 40 + k * 5) % (26 * d.k)), xx = d.x + 6 * d.k + ((k * 7) % (20 * d.k));
          px(ctx, P, green(), xx, yy - 1, 1, 3); px(ctx, P, green(), xx - 1, yy, 3, 1);
        }
      }
      if (ev.t === "special" && s) {
        // a pillar of light on the one who used it
        const [cx] = at(s), w = Math.round(12 * s.k * (1 - (u / STEP_MAX) * 0.6));
        ctx.save();
        ctx.globalAlpha = 0.4 * (1 - u / STEP_MAX);
        px(ctx, P, theme.cta, cx - w, 0, w * 2, s.feet + 2);
        ctx.restore();
        if (d && Number.isFinite(ev.n) && ev.n > 0) {
          const [dx, head] = at(d);
          text((s.flip === d.flip ? "+" : "") + ev.n, dx, Math.max(duel ? 34 : 14, head - 7 * big), { size: Math.round(12 * big), color: s.flip === d.flip ? green() : gold() });
        }
      }
    }
    function lastSpecial(i) {
      for (let k = i; k >= 0 && k > i - 4; k--) if (log[k].t === "special") return [log[k], (i - k) * step];
      return null;
    }
    function drawHud(cur, done, hpNow) {
      // each side: its name and its party's health
      const team = (side) => {
        let h = 0, m = 0;
        for (const un of units) if (un.side === side) { h += hpNow.get(un.key) || 0; m += un.max; }
        return m ? h / m : 0;
      };
      const half = Math.floor(W / 2) - 10;
      const l = text(sideName[leftSide], 4, 8, { size: 11, align: "left", panel: true, weight: 600, max: half - 8 });
      const r = text(sideName[rightSide], W - 4, 8, { size: 11, align: "right", panel: true, weight: 600, max: half - 8 });
      hpBar(l.x + 1, l.y + l.h + 2, Math.min(half - 4, 60), team(leftSide), team(leftSide));
      hpBar(r.x + r.w - 1 - Math.min(half - 4, 60), r.y + r.h + 2, Math.min(half - 4, 60), team(rightSide), team(rightSide));
      if (cur && !done && W >= 300) text(fill(labels.round, { n: cur.r || 1 }), W / 2, 8, { size: 10, color: gold(), weight: 600 });
      const i = log.indexOf(cur), sp = !done && cur ? lastSpecial(i) : null;
      if (sp && sp[1] < 1.4) {
        const [ev] = sp, s = slot.get(ev.src), side = ev.src.split(":")[0];
        const msg = fill(labels.special, { agent: name(ev.src), name: ev.name || "" });
        const bw = text(msg, W / 2, 26, { size: 12, color: side === leftSide ? gold() : red(), panel: true });
        if (s) px(ctx, P, side === leftSide ? theme.cta : red(), bw.x + 1, bw.y + bw.h, bw.w - 2, 1);
      }
    }
    // a duel's HUD, as in a fighting game: each champion's owner and name, level and tier, and a wide HP bar
    // "owner · champion" within `max`: the owner's name is shortened first, so the champion's always shows
    function nameLine(owner, who, size, max) {
      const full = owner ? `${owner} · ${who}` : who;
      if (!owner) return full;
      ctx.save();
      ctx.font = `700 ${Math.round(size * dpr)}px ${FONT}`;
      const fits = (s) => ctx.measureText(s).width <= max * P;
      let o = owner;
      if (!fits(full)) while (o.length > 1 && !fits(`${o}… · ${who}`)) o = o.slice(0, -1);
      ctx.restore();
      return o === owner ? full : `${o}… · ${who}`;
    }
    function drawDuelHud(cur, done, hpNow, hpBefore, u) {
      const barWide = Math.max(40, Math.floor(W / 2) - 14), narrow = W < 300;
      for (const un of units) {
        const left = un.side === leftSide, x = left ? 4 : W - 4, align = left ? "left" : "right";
        const owner = sideName[un.side], who = name(un.key);
        // the name, then level and tier on the same row (toward the middle); the bar under them
        const room = Math.max(30, barWide - (narrow ? 0 : 76));
        const n = text(nameLine(owner, who, narrow ? 10 : 12, room - 6), x, 8, { size: narrow ? 10 : 12, align, panel: true, weight: 700, max: room });
        if (!narrow) {
          const tierColor = { master: gold(), expert: theme.accent, adept: green(), novice: theme.panelInk }[un.tier] || theme.panelInk;
          const lv = text(fill(labels.level, { n: un.level }), left ? n.x + n.w + 3 : n.x - 3, 8, { size: 11, align, panel: true, weight: 600 });
          text(labels.tiers[un.tier] || un.tier, left ? lv.x + lv.w + 3 : lv.x - 3, 8, { size: 11, align, panel: true, weight: 700, color: tierColor });
        }
        const now = hpNow.get(un.key), before = hpBefore.get(un.key), lag = done || u > 0.3 ? now : before;
        const bx = left ? 4 : W - 4 - barWide, by = n.y + n.h + 3, frac = now / un.max, ghost = Math.max(now, lag) / un.max;
        px(ctx, P, theme.dark ? "rgba(4, 10, 24, 0.85)" : "rgba(20, 40, 70, 0.55)", bx - 1, by - 1, barWide + 2, 6);
        const fillW = (f) => Math.max(f > 0 ? 1 : 0, Math.round(barWide * f));
        if (ghost > frac) px(ctx, P, theme.dark ? "#ffe9b0" : "#f6d27a", left ? bx : bx + barWide - fillW(ghost), by, fillW(ghost), 4);
        const c = frac > 0.5 ? "#4fd88a" : frac > 0.25 ? "#f6c445" : "#ff6b6b";
        if (frac > 0) {
          px(ctx, P, c, left ? bx : bx + barWide - fillW(frac), by, fillW(frac), 4);
          px(ctx, P, glow("#ffffff", 0.35), left ? bx : bx + barWide - fillW(frac), by, fillW(frac), 1);
        }
        if (narrow) { // no room on the name row: level and tier under the bar
          const tierColor = { master: gold(), expert: theme.accent, adept: green(), novice: theme.panelInk }[un.tier] || theme.panelInk;
          text(`${fill(labels.level, { n: un.level })} · ${labels.tiers[un.tier] || un.tier}`, left ? 4 : W - 4, by + 11, { size: 9, align, panel: true, weight: 600, color: tierColor });
        }
      }
      if (cur && !done && !narrow) text(fill(labels.round, { n: cur.r || 1 }), W / 2, 8, { size: 12, color: gold(), weight: 700, panel: true });
      // a special: a band across the whole arena with its name
      const i = log.indexOf(cur), sp = !done && cur ? lastSpecial(i) : null;
      if (sp && sp[1] < 1.2) {
        const [ev, since] = sp, side = ev.src.split(":")[0], mine = side === leftSide;
        const k = still ? 1 : ease(Math.min(1, (since + (cur === ev ? u : 0)) / 0.2)), y = Math.round(L * 0.42), h = 22;
        const w = Math.round(W * k), x = mine ? 0 : W - w;
        px(ctx, P, mine ? theme.cta : red(), x, y - 1, w, h + 2);
        px(ctx, P, theme.dark ? "#0b1a33" : "#ffffff", x, y + 1, w, h - 2);
        if (k > 0.6) text(fill(labels.special, { agent: name(ev.src), name: ev.name || "" }), W / 2, y + h / 2, { size: narrow ? 13 : 16, color: mine ? gold() : red() });
      }
    }
    // the duel's opening: "Claude vs Codex"
    function drawIntro(tt) {
      const [l, r] = [units.find((x) => x.side === leftSide), units.find((x) => x.side === rightSide)];
      const k = ease(tt / 0.35), fade = tt > INTRO - 0.3 ? (INTRO - tt) / 0.3 : 1;
      const y = Math.round(L * 0.42), h = 26;
      ctx.save();
      ctx.globalAlpha = clamp(fade, 0, 1);
      px(ctx, P, theme.dark ? "rgba(6, 14, 32, 0.8)" : "rgba(255, 255, 255, 0.88)", 0, y, W, h);
      px(ctx, P, theme.cta, 0, y, Math.round(W * k), 1);
      px(ctx, P, theme.cta, W - Math.round(W * k), y + h - 1, Math.round(W * k), 1);
      ctx.restore();
      if (fade > 0.1) text(fill(labels.vs, { left: l ? name(l.key) : "", right: r ? name(r.key) : "" }), W / 2, y + h / 2,
        { size: W < 300 ? 15 : 20, color: gold(), alpha: fade, max: W - 10 });
    }
    function drawEnd(since, a) {
      const won = result.winner === leftSide;
      const slide = still ? 1 : ease(since / 0.3), y = Math.round(L * 0.42 - 8 + 8 * slide);
      ctx.save();
      ctx.font = `700 ${Math.round(18 * dpr)}px ${FONT}`;
      const msg = won ? labels.victory : labels.defeat, w = Math.ceil(ctx.measureText(msg).width / P) + 24, h = 20, x = Math.round(W / 2 - w / 2);
      ctx.fillStyle = theme.dark ? `rgba(4, 10, 24, ${0.5 * slide})` : `rgba(255, 255, 255, ${0.55 * slide})`; // the stage steps back
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = won ? theme.cta : theme.panelRim;
      ctx.fillRect((x + 1) * P, (y - 1) * P, (w - 2) * P, (h + 2) * P);
      ctx.fillRect(x * P, y * P, w * P, h * P);
      ctx.fillStyle = theme.dark ? (won ? "#2a1c0c" : "#0b1a33") : "#ffffff";
      ctx.fillRect((x + 1) * P, (y + 1) * P, (w - 2) * P, (h - 2) * P);
      ctx.fillStyle = won ? gold() : theme.panelInk;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(msg, (x + w / 2) * P, (y + h / 2 + 0.5) * P);
      ctx.restore();
      if (won && !still) for (let k = 0; k < 14; k++) { // confetti
        const r = rng(k * 17 + 3), uu = Math.min(1.6, since * 0.8), cx = x + r() * w, cy = y + h + uu * (10 + r() * 30) - 6;
        px(ctx, P, ["#f6c445", "#ffffff", "#e5484d", "#5cb8ff", "#7bd389"][k % 5], Math.round(cx), Math.round(cy));
      }
      const winnerName = sideName[result.winner] || "";
      text(fill(labels.summary, { winner: winnerName, n: result.rounds || 0 }), W / 2, y + h + 9, { size: 10, panel: true, weight: 600, max: W - 10 });
    }

    function render() {
      if (!alive || !theme) return;
      draw(pinned != null ? pinned : still ? INTRO + total + 1 : t, pinned != null ? pinned : animT);
    }
    self.running = () => alive && !still && visible && !document.hidden && !ended;
    self.frame = (now) => {
      const dt = lastNow ? Math.min(0.1, (now - lastNow) / 1000) : 0;
      lastNow = now;
      animT += dt;
      t += dt;
      draw(t, animT);
      if (t >= INTRO + total + 2.2) { ended = true; onEnd(result); } // the result held; the timer may rest
    };
    let io = null, ro = null, mo = null, scheme = null;
    self.retheme = () => { if (alive) { measure(); render(); } };
    self.destroy = () => {
      if (!alive) return;
      alive = false;
      scenes.delete(self);
      io && io.disconnect();
      ro && ro.disconnect();
      mo && mo.disconnect();
      scheme && scheme.removeEventListener && scheme.removeEventListener("change", self.retheme);
      canvas.remove();
      art.clear();
    };
    measure();
    canvas.setAttribute("aria-label", fill(labels.summary, { winner: sideName[result.winner] || "", n: result.rounds || 0 }));
    io = typeof IntersectionObserver === "function" ? new IntersectionObserver((entries) => {
      for (const en of entries) visible = en.isIntersecting;
      if (!canvas.isConnected) { self.destroy(); return; }
      if (visible) { lastNow = 0; kick(); }
    }) : null;
    io && io.observe(canvas);
    let width = host.clientWidth;
    ro = typeof ResizeObserver === "function" ? new ResizeObserver(() => {
      if (!alive || host.clientWidth === width) return;
      width = host.clientWidth;
      measure();
      render();
    }) : null;
    ro && ro.observe(host);
    mo = typeof MutationObserver === "function" ? new MutationObserver(self.retheme) : null;
    mo && mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    scheme = typeof matchMedia === "function" ? matchMedia("(prefers-color-scheme: dark)") : null;
    scheme && scheme.addEventListener && scheme.addEventListener("change", self.retheme);
    if (document.fonts && document.fonts.load) document.fonts.load(`700 11px ${FONT}`).then(() => render(), () => {});
    scenes.add(self);
    render();
    if (still && pinned == null) onEnd(result);
    kick();
    return {
      replay() {
        if (!alive) return;
        t = 0; lastNow = 0; ended = false;
        if (still) { render(); return; }
        kick();
      },
      destroy: self.destroy,
    };
  }

  window.Scene = Object.freeze({ mount, mountBattle });
})();
