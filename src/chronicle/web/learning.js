/* A complete topic library, grounded visual lessons and private browser-local work notes. No build step. */
"use strict";

const LearningPractice = (() => {
  const DAY = 86400000;
  const key = (k) => String(k.fingerprint || k.id);
  const topics = (k) => k.learning_topics || k.case?.topics || [];
  const KINDS = ["fix", "gotcha", "decision", "learning", "pattern"];
  // k.case is the lesson material the analysis found (analyze.case_of); an item analyzed before has none, and
  // nothing stands in for it: the lesson is its title and explanation.
  function prepare(k) { return k.case ? k : { ...k, case: {} }; }
  function version(k) {
    const { topics: _topics, visual: _visual, ...material } = k.case || {};
    const text = JSON.stringify([k.body, Object.keys(material).length ? material : null]);
    let hash = 2166136261;
    for (let i = 0; i < text.length; i++) hash = Math.imul(hash ^ text.charCodeAt(i), 16777619);
    return (hash >>> 0).toString(36);
  }
  function eligible(k) {
    k = prepare(k);
    return KINDS.includes(k.kind) && !!k.title && !!(k.body?.trim() || k.case.answer)
      && k.confidence !== "low" && k.stage !== "wip" && (!k.status || k.status === "active");
  }
  function record(k, old, outcome, now = Date.now()) {
    const previous = old && old.version === version(k) ? old : {};
    const recalls = outcome === "again" ? 0 : outcome === "recalled" ? Math.min((Number(previous.recalls) || 0) + 1, 4) : Number(previous.recalls) || 0;
    const days = outcome === "again" || outcome === "soon" ? 3 : outcome === "recalled" ? [7, 14, 30, 60][recalls - 1] : 7;
    return { version: version(k), seenAt: now, dueAt: now + days * DAY, recalls,
      exposures: (Number(previous.exposures) || 0) + (outcome === "read" ? 1 : 0),
      attempts: [...(Array.isArray(previous.attempts) ? previous.attempts : []),
        ...(outcome === "recalled" || outcome === "again" ? [{ at: now, outcome }] : [])].slice(-20) };
  }
  function state(k, history, now = Date.now()) {
    const r = history[key(k)];
    if (!r || r.version !== version(k) || !Number.isFinite(r.dueAt)) return "new";
    return r.dueAt <= now ? "due" : "read";
  }
  function select(items, history, now = Date.now(), interests = []) {
    const scores = new Map(interests.map((i) => [i.topic, Number(i.score) || 0]));
    const affinity = (k) => topics(k).reduce((score, topic) => score + (scores.get(topic) || 0), 0);
    // Keep navigation stable: reading changes badges, never ordering or access.
    return [...new Map(items.map(prepare).filter(eligible).map((k) => [key(k), k])).values()]
      .sort((a, b) => affinity(b) - affinity(a)
        || String(b.session_started || b.created_at || "").localeCompare(String(a.session_started || a.created_at || "")) || b.id - a.id);
  }
  function categories(items, interests = []) {
    const groups = new Map(), scores = new Map(interests.map((i) => [i.topic, Number(i.score) || 0]));
    for (const k of items) for (const id of new Set(topics(k).length ? topics(k) : ["other"])) {
      if (!groups.has(id)) groups.set(id, { id, items: [], score: scores.get(id) || 0 });
      groups.get(id).items.push(k);
    }
    return [...groups.values()].sort((a, b) => b.score - a.score || b.items.length - a.items.length || a.id.localeCompare(b.id));
  }
  function related(k, items) {
    k = prepare(k);
    const tags = new Set(k.tags || []);
    const shared = new Set(topics(k));
    return items.map(prepare).filter((x) => key(x) !== key(k) && eligible(x)).map((x) => ({ k: x,
      score: k.case.principle && x.case.principle === k.case.principle ? 100
        : (x.tags || []).filter((tag) => tags.has(tag)).length + topics(x).filter((topic) => shared.has(topic)).length * 2 }))
      .filter((x) => x.score >= 2).sort((a, b) => b.score - a.score).slice(0, 2).map((x) => x.k);
  }
  return { key, topics, prepare, version, eligible, record, state, select, categories, related };
})();

// A diagram only when the analysis drew one from the session (cases.visual_of). A lesson without one gets none: boxes
// made from the page's own headings, or a stock picture matched on a keyword, teach nothing.
function learningVisualData(k) {
  const v = k.case?.visual;
  if (!(v?.nodes?.length >= 2)) return null;
  return { ...v, branch: v.type === "relationship" && v.nodes.length === 3 && v.edges?.length === 2
    && v.edges.every((e) => e.from === v.nodes[0].id) };
}

let learningDiagramId = 0;
const learningDiagramObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver((entries) => {
  for (const { target } of entries) {
    if (!target.isConnected) learningDiagramObserver.unobserve(target);
    else target.learningDraw?.();
  }
});
function learningDiagram(k) {
  const visual = learningVisualData(k);
  if (!visual) return null;
  const marker = `learn-arrow-${++learningDiagramId}`;
  const svg = s("svg", { class: "learn-connections", "aria-hidden": "true" });
  const buttons = new Map(); // each part's box, by node id
  // every part says its role up front: nothing to click to find out
  const grid = h("div", { class: `learn-diagram-grid learn-layout-${visual.type}${visual.branch ? " learn-layout-branch" : ""}` },
    visual.nodes.map((node, i) => {
      const box = h("div", { class: "learn-node" },
        h("span", { class: "learn-node-symbol", "aria-hidden": "true" }, icon(visual.type === "comparison" ? "branch" : "component")),
        h("b", null, node.label), node.detail ? h("small", null, node.detail) : null,
        h("span", { class: "learn-node-order", "aria-hidden": "true" }, String(i + 1).padStart(2, "0")));
      buttons.set(node.id, box);
      return box;
    }));
  grid.style.setProperty("--learn-parts", visual.nodes.length);
  const canvas = h("div", { class: "learn-diagram-canvas" }, svg, grid);
  canvas.learningDraw = () => {
    const box = canvas.getBoundingClientRect();
    if (!box.width) return;
    svg.setAttribute("viewBox", `0 0 ${box.width} ${box.height}`);
    svg.replaceChildren(s("defs", null, s("marker", { id: marker, viewBox: "0 0 10 10", refX: 9, refY: 5,
      markerWidth: 6, markerHeight: 6, orient: "auto-start-reverse" }, s("path", { d: "M0 0 10 5 0 10z" }))));
    for (const edge of visual.edges || []) {
      const from = buttons.get(edge.from), to = buttons.get(edge.to);
      if (!from || !to) continue;
      const a = from.getBoundingClientRect(), b = to.getBoundingClientRect();
      const vertical = Math.abs(a.top - b.top) > 20;
      const forward = vertical ? b.top > a.top : b.left > a.left;
      const x1 = (vertical ? a.left + a.width / 2 : forward ? a.right : a.left) - box.left;
      const y1 = (vertical ? forward ? a.bottom : a.top : a.top + a.height / 2) - box.top;
      const x2 = (vertical ? b.left + b.width / 2 : forward ? b.left : b.right) - box.left;
      const y2 = (vertical ? forward ? b.top : b.bottom : b.top + b.height / 2) - box.top;
      const bend = vertical ? `C${x1} ${(y1+y2)/2},${x2} ${(y1+y2)/2},${x2} ${y2}`
        : `C${(x1+x2)/2} ${y1},${(x1+x2)/2} ${y2},${x2} ${y2}`;
      const label = s("text", { x: (x1+x2)/2, y: (y1+y2)/2 - 10, "text-anchor": "middle", class: "learn-edge-label" });
      const charWidth = /[^\x00-\xff]/.test(edge.label || "") ? 11 : 6;
      const chars = Math.max(4, Math.min(20, Math.floor((vertical ? box.width / (visual.branch ? 4 : 2)-8 : Math.abs(x2-x1)-8) / charWidth)));
      const lines = [], words = (edge.label || "").split(/\s+/).flatMap((word) => {
        const letters = Array.from(word), chunks = [];
        for (let i = 0; i < letters.length; i += chars) chunks.push(letters.slice(i, i+chars).join(""));
        return chunks;
      });
      let line = "";
      for (const word of words) {
        if (line && (line + " " + word).length > chars) { lines.push(line); line = ""; }
        line += (line ? " " : "") + word;
      }
      if (line) lines.push(line);
      lines.forEach((text, i) => append(label, [s("tspan", { x: (x1+x2)/2, dy: i ? 14 : -(lines.length-1)*7 }, text)]));
      append(svg, [s("path", { d: `M${x1} ${y1} ${bend}`, class: "learn-edge", "marker-end": `url(#${marker})` }),
        edge.label ? label : null]);
    }
  };
  learningDiagramObserver?.observe(canvas);
  const name = (id) => buttons.get(id)?.querySelector("b").textContent || id;
  return h("figure", { class: "learn-visual" },
    h("figcaption", null, h("div", null, icon("diagram"), h("b", null, visual.title || t("See how it works")))),
    canvas,
    h("ul", { class: "sr-only" }, (visual.edges || []).map((edge) => h("li", null,
      [name(edge.from), edge.label, name(edge.to)].filter(Boolean).join(" → ")))));
}

function learningTopicIcon(id) {
  return { system_design: "diagram", frontend: "page", backend: "service", api: "reference", data_modeling: "data",
    databases: "data", testing: "completed", security: "shield", devops: "platform", performance: "history",
    ai_ml: "sparkles", mobile: "devices", languages: "branch" }[id] || "learning";
}

function learningTopicLabel(id) {
  return { system_design: t("System design"), frontend: t("Frontend"), backend: t("Backend"), api: t("APIs"),
    data_modeling: t("Data modeling"), databases: t("Databases"), testing: t("Testing"), security: t("Security"),
    devops: t("DevOps and cloud"), performance: t("Performance"), ai_ml: t("AI and machine learning"),
    mobile: t("Mobile development"), languages: t("Languages and concurrency"), other: t("Other lessons") }[id] || id;
}

function learningInterests(interests) {
  return h("details", { class: "learn-interests", "aria-label": t("Your learning interests") },
    h("summary", null, t("Picked up from your questions"), h("span", null, t("Why these topics?"))),
    interests.length ? [
      h("p", { class: "muted" }, t("Your recent questions and requests for explanations shape these suggestions. They aren't a skill rating.")),
      interests.map((i) => h("div", { class: "learn-interest-source" }, h("b", null, learningTopicLabel(i.topic)),
        i.examples.map((x) => h("p", null, h("q", null, x.question), " ",
          h("a", { href: `#/session/${encodeURIComponent(x.session_id)}` }, t("Source"))))))]
      : h("p", { class: "muted" }, t("As you ask about concepts and tradeoffs, your interests will appear here automatically.")));
}

const learningMemory = new Map();
function learningStore() {
  const storageKey = `interlatch.learning.v1:${ME?.viewer?.id || "local"}`;
  let data = learningMemory.get(storageKey), available = true;
  if (!data) {
    try { data = JSON.parse(localStorage.getItem(storageKey) || "null"); } catch { available = false; }
    if (!data || typeof data !== "object" || Array.isArray(data)) data = {};
    for (const name of ["history", "notes"]) if (!data[name] || typeof data[name] !== "object" || Array.isArray(data[name])) data[name] = {};
    learningMemory.set(storageKey, data);
  }
  const save = () => {
    try { localStorage.setItem(storageKey, JSON.stringify(data)); return true; }
    catch { toast(t("Browser storage is unavailable. Learning history and notes last for this tab."), 6000); return false; }
  };
  return { data, save, available };
}

function learningSource(k) {
  return h("details", { class: "learn-source" }, h("summary", null, t("Source and lesson trust")),
    h("div", { class: "learn-source-body" },
      k.session_id ? h("a", { href: `#/session/${encodeURIComponent(k.session_id)}` }, k.session_title || t("session"))
        : k.source === "team" ? teamTag(k) : null,
      k.who ? whoTag(k.who, k.who_key, "#/knowledge/all") : null,
      h("div", { class: "learn-meta" }, k.project_name || t("global"), fmtDate(k.session_started || k.created_at),
        stageTag(k.stage, confirmCount(k), k.stage_reason), confidenceMeter(k.confidence)),
      k.evidence ? mdEl(k.evidence) : null));
}

function learningNote(k) {
  return [k.title, ...(k.case.scene ? ["", t("The scene"), k.case.scene] : []), "", t("Explanation"), k.body || k.case.answer,
    ...(k.case.checklist?.length ? ["", t("Checks for next time"), ...k.case.checklist.map((x) => `- ${x}`)] : []),
    ...(k.case.principle ? ["", t("Principle"), k.case.principle] : []), "", t("Source"),
    ...(k.session_id ? [`${location.origin}${location.pathname}#/session/${encodeURIComponent(k.session_id)}`] : [])].join("\n");
}

async function learningView(params) {
  setCrumbs([sectionLink("knowledge"), [t("Learn from your work")]]);
  const store = learningStore(), project = params.project || "", topic = params.topic || "", notesMode = params.view === "notes";
  let query = params.q || "";
  const href = (changes = {}) => `#/learn?${new URLSearchParams({ project, topic, q: query, ...changes })}`;
  const profile = await api("/api/learning/interests", { project });
  const interests = profile.interests || [];
  const fetched = [];
  let page;
  do {
    page = await api("/api/knowledge", { lessons: "1", project, limit: 1000, offset: fetched.length });
    fetched.push(...page.items);
  } while (page.items.length === 1000);
  const items = fetched.map(LearningPractice.prepare).filter(LearningPractice.eligible);
  const ordered = LearningPractice.select(items, store.data.history, Date.now(), interests);
  const groups = LearningPractice.categories(ordered, interests);
  const selected = topic ? groups.find((g) => g.id === topic)?.items || [] : ordered;
  const lesson = params.lesson ? ordered.find((k) => LearningPractice.key(k) === params.lesson) : null;
  const panel = h("div", { class: "learn-content" + (!lesson ? " learn-library-content" : "") });
  // A direct lesson link remains readable even when its category or search changes.
  const sequence = lesson && !selected.includes(lesson) ? ordered : selected;
  const index = lesson ? sequence.indexOf(lesson) : -1;
  function finish(k, outcome, advance = true) {
    const key = LearningPractice.key(k);
    store.data.history[key] = LearningPractice.record(k, store.data.history[key], outcome);
    store.save();
    if (advance) {
      const next = sequence[index + 1];
      go(next ? href({ lesson: LearningPractice.key(next) }) : href());
      return;
    }
    if (outcome === "soon") toast(t("Ready to revisit in three days. You can open it anytime."));
    draw();
    panel.querySelector(".learn-actions .btn:not(:disabled)")?.focus();
  }
  function draw() {
    if (notesMode) { panel.replaceChildren(learningNotes(selected, store)); return; }
    if (!lesson) {
      const needle = query.trim().toLocaleLowerCase();
      const matches = selected.filter((k) => !needle || [k.title, k.body, k.case.answer, k.case.principle, ...(k.tags || [])]
        .filter(Boolean).join(" ").toLocaleLowerCase().includes(needle));
      panel.replaceChildren(learningLibrary(matches, store, interests, href, topic, !!needle));
      return;
    }
    panel.replaceChildren(h("a", { class: "learn-back", href: href() }, t("Back to lesson lists")),
      learningCase(lesson, { store, items, interests, revisiting: LearningPractice.state(lesson, store.data.history) === "due",
        index, total: sequence.length, finish }));
  }
  const filter = h("select", { "aria-label": t("Project"), onchange: (e) => go(href({ view: notesMode ? "notes" : "library", project: e.target.value })) },
    await projectOptions(project));
  const topicFilter = h("select", { "aria-label": t("Learning topic"), onchange: (e) => go(href({ view: notesMode ? "notes" : "library", topic: e.target.value })) },
    h("option", { value: "", selected: !topic }, t("All learning topics")),
    [...new Set([...groups.map((g) => g.id), ...interests.map((i) => i.topic), ...(topic ? [topic] : [])])]
      .map((id) => h("option", { value: id, selected: topic === id }, learningTopicLabel(id))));
  const search = h("input", { class: "input learn-search", type: "search", value: query, placeholder: t("Search your lessons…"),
    "aria-label": t("Search your lessons"), oninput: (e) => {
      query = e.target.value;
      setParams({ project, topic, q: query });
      draw();
    } });
  draw();
  return h("div", { class: "learning-page" },
    h("div", { class: "page-head" }, h("div", null, h("h1", null, t("Learn from your work")),
      h("div", { class: "sub" }, t("Your work, organized into lessons. Explore any category, anytime."))),
      h("a", { class: "btn", href: `#/knowledge/all?${new URLSearchParams({ project })}` }, t("All knowledge"))),
    h("div", { class: "learn-toolbar" },
      h("nav", { class: "learn-tabs", "aria-label": t("Learning views") },
        h("a", { href: href(), class: notesMode ? "" : "on", "aria-current": notesMode ? null : "page" }, t("Lesson library")),
        h("a", { href: href({ view: "notes" }), class: notesMode ? "on" : "", "aria-current": notesMode ? "page" : null }, t("Saved work notes"))),
      h("div", { class: "learn-filters" }, filter, topicFilter)),
    !notesMode && !lesson ? [
      h("nav", { class: "learn-category-nav", "aria-label": t("Lesson categories") },
        groups.map((g) => h("a", { class: "learn-category-tile" + (topic === g.id ? " on" : ""), href: href({ topic: g.id }),
          "aria-current": topic === g.id ? "page" : null },
          h("span", { class: "learn-category-symbol", "aria-hidden": "true" }, icon(learningTopicIcon(g.id))),
          h("span", null, h("b", null, learningTopicLabel(g.id)), h("small", null, tn(g.items.length, "{n} lesson", "{n} lessons"))),
          g.score > 0 ? h("span", { class: "learn-interest-dot", title: t("From your questions"), "aria-label": t("From your questions") }) : null))),
      learningInterests(interests), search] : null,
    !store.available ? h("p", { class: "warn-line" }, t("Browser storage is unavailable. Learning history and notes last for this tab.")) : null,
    panel, h("p", { class: "learn-private muted" }, t("Reading history and work notes stay in this browser.")));
}

function learningLibrary(items, store, interests, href, topic, searching) {
  const groups = LearningPractice.categories(items, interests);
  if (!items.length) return h("section", { class: "card empty" }, h("h2", null, searching ? t("No matching lessons") : t("No lessons yet")),
    h("p", null, searching ? t("Try another search or browse all categories.") : t("Lessons appear automatically as your sessions are analyzed.")),
    h("a", { class: "btn", href: href({ topic: "", q: "" }) }, t("All categories")));
  const stateLabel = (k) => ({ new: t("New"), read: t("Read"), due: t("Ready to revisit") })[LearningPractice.state(k, store.data.history)];
  const row = (k, id) => h("li", null,
    h("a", { class: "learn-lesson-row", href: href({ topic: id, lesson: LearningPractice.key(k) }) },
      h("span", { class: "learn-lesson-copy" }, h("b", { html: codeSpans(k.title) }),
        h("span", { class: "learn-lesson-preview" }, (k.case.principle || k.body || k.case.answer).replace(/[#*`]/g, "")),
        h("span", { class: "learn-lesson-meta" }, k.project_name || t("global"),
          h("span", { class: "tag learn-state-" + LearningPractice.state(k, store.data.history) }, stateLabel(k)),
          store.data.notes[LearningPractice.key(k)] ? h("span", { class: "tag" }, icon("completed"), t("Saved note")) : null)),
      icon("arrow")));
  return h("div", null,
    h("div", { class: "learn-library-heading" }, h("h2", null, topic ? learningTopicLabel(topic) : t("All lessons by category")),
      h("span", { class: "muted" }, tn(items.length, "{n} lesson", "{n} lessons")),
      topic ? h("a", { href: href({ topic: "" }) }, t("All categories")) : null),
    !topic ? h("p", { class: "learn-library-hint muted" }, t("Lessons can belong to more than one category. Your interests put relevant categories first.")) : null,
    h("div", { class: "learn-category-grid" + (topic ? " learn-category-single" : "") },
      (topic ? [{ id: topic, items }] : groups).map((g) => h("section", { class: "card learn-category", "aria-label": learningTopicLabel(g.id) },
        h("div", { class: "learn-category-heading" }, icon(learningTopicIcon(g.id)), h("h3", null, learningTopicLabel(g.id)),
          h("span", { class: "tag" }, String(g.items.length)),
          !topic ? h("a", { href: href({ topic: g.id }), "aria-label": t("Browse {topic}", { topic: learningTopicLabel(g.id) }) }, t("Browse")) : null),
        h("ul", { class: "learn-lesson-list" }, g.items.map((k) => row(k, g.id)))))));
}

function learningCase(k, { store, items, interests = [], revisiting, index, total, finish }) {
  const c = k.case, key = LearningPractice.key(k), article = h("article", { class: "card learn-case" });
  const interest = interests.find((i) => LearningPractice.topics(k).includes(i.topic));
  const reason = revisiting ? t("Suggested because this lesson is ready to revisit.")
    : interest ? t("Suggested because you asked about {topic}.", { topic: learningTopicLabel(interest.topic) }) : null;
  const leads = (c.ruled_out || []).filter((x) => x.lead);
  const evidence = c.clues?.length ? h("details", { class: "learn-evidence" }, h("summary", null, t("Look at the evidence")),
    h("ol", { class: "learn-clues" }, c.clues.map((x) => h("li", null, mdEl(x))))) : null;
  const saveNote = h("button", { type: "button", class: "btn", disabled: !!store.data.notes[key], onclick: () => {
    store.data.notes[key] ||= { text: learningNote(k).slice(0, 20000), savedAt: Date.now(), version: LearningPractice.version(k) };
    const saved = store.save();
    saveNote.disabled = true;
    saveNote.textContent = t("Work note saved");
    toast(saved ? t("Work note saved in this browser") : t("Work note kept for this tab"));
  } }, store.data.notes[key] ? t("Work note saved") : t("Save work note"));
  // Each part appears once, in the order it happened: the scene, the question and its answer (a case file, as in All
  // knowledge, told at once), the explanation, the leads that turned out wrong, then what carries over to other work.
  append(article, [h("div", { class: "learn-meta" },
    h("span", null, t("Lesson {n} of {total}", { n: index + 1, total })),
    h("span", null, k.project_name || t("global"))),
    reason ? h("p", { class: "learn-reason muted" }, reason) : null,
    h("div", { class: "learn-topics" }, LearningPractice.topics(k).map((id) => h("span", { class: "tag" },
      icon(learningTopicIcon(id)), learningTopicLabel(id)))),
    h("section", { class: "learn-explanation learn-ready" }, h("h2", { tabindex: "-1", html: codeSpans(k.title) }),
      c.scene && c.question && c.answer ? [caseScene(c),
        h("div", { class: "learn-qa" }, h("div", { class: "kcase-q", html: codeSpans(c.question) }),
          h("div", { class: "learn-answer" }, icon("completed"), h("span", { html: codeSpans(c.answer) })))] : null,
      learningDiagram(k),
      mdEl(k.body || c.answer, "learn-prose"),
      leads.length ? h("div", { class: "kcase-block" }, h("span", { class: "kcase-label" }, t("Ruled out")), caseLeads(leads)) : null,
      c.principle ? h("div", { class: "learn-principle" }, icon("learning"),
        h("div", null, h("h3", null, t("The principle to keep")), mdEl(c.principle))) : null,
      c.checklist?.length ? h("div", { class: "learn-apply" }, h("h3", null, t("Use it in your next task")),
        h("ol", { class: "learn-checks" }, c.checklist.map((x) => h("li", null, mdEl(x))))) : null),
    h("div", { class: "learn-actions" },
      h("button", { type: "button", class: "btn primary", disabled: LearningPractice.state(k, store.data.history) === "read",
        onclick: () => finish(k, "read", false) }, LearningPractice.state(k, store.data.history) === "read" ? t("Read") : t("Mark as read")),
      h("button", { type: "button", class: "btn", onclick: () => finish(k, "read") }, index + 1 < total ? t("Next lesson") : t("Back to lesson lists")), saveNote,
      h("button", { type: "button", class: "link-btn", onclick: () => finish(k, "soon", false) }, t("Revisit sooner"))),
    learningComparison(k, items), evidence, learningSource(k)]);
  return article;
}

function learningComparison(k, items) {
  const matches = LearningPractice.related(k, items);
  if (!matches.length) return null;
  return h("details", { class: "learn-comparison" }, h("summary", null, t("Explore related ideas")),
    h("p", { class: "muted" }, t("Related by shared topics. Compare the causes before assuming the same fix applies.")),
    matches.map((other) => h("section", { class: "learn-related" },
      h("h3", null, other.title), mdEl(other.body || other.case.answer),
      other.case.principle ? mdEl(other.case.principle) : null, learningSource(other))));
}

function learningNotes(items, store) {
  const notes = items.filter((k) => store.data.notes[LearningPractice.key(k)]?.text)
    .sort((a, b) => store.data.notes[LearningPractice.key(b)].savedAt - store.data.notes[LearningPractice.key(a)].savedAt);
  if (!notes.length) return h("section", { class: "card empty" }, h("h2", null, t("No saved work notes yet")),
    h("p", null, t("After a case, keep a small checklist or reasoning habit for your next task.")));
  const list = h("div", { class: "learn-notes" }, notes.map((k) => {
    const key = LearningPractice.key(k), note = store.data.notes[key];
    const preview = h("div", { class: "learn-prose" }, mdEl(note.text));
    const text = h("textarea", { class: "input learn-draft", rows: "6", "aria-label": t("Work note") }, note.text);
    const edit = h("button", { type: "button", class: "btn", onclick: () => {
      editor.hidden = false; edit.hidden = true; text.focus();
    } }, t("Edit work note"));
    const editor = h("div", { class: "learn-note-editor", hidden: true }, text,
      h("div", { class: "learn-actions" }, h("button", { type: "button", class: "btn primary", onclick: () => {
        if (!text.value.trim()) { text.focus(); return; }
        const updated = { text: text.value.trim().slice(0, 20000), savedAt: Date.now(), version: LearningPractice.version(k) };
        store.data.notes[key] = updated;
        const saved = store.save();
        preview.replaceChildren(mdEl(updated.text));
        editor.hidden = true; edit.hidden = false;
        toast(saved ? t("Work note saved in this browser") : t("Work note kept for this tab"));
      } }, t("Save work note")), h("button", { type: "button", class: "btn", onclick: () => {
        text.value = store.data.notes[key].text; editor.hidden = true; edit.hidden = false;
      } }, t("Cancel"))));
    const row = h("article", { class: "card" }, h("h2", { html: codeSpans(k.title) }),
      h("div", { class: "learn-meta" }, k.project_name || t("global"), fmtDate(new Date(note.savedAt).toISOString())),
      note.version !== LearningPractice.version(k) ? h("p", { class: "warn-line" }, t("The source lesson changed. Review this work note before using it.")) : null,
      preview, editor, h("div", { class: "learn-actions" }, edit,
        h("button", { type: "button", class: "btn", onclick: (e) => copyText(store.data.notes[key].text, e.currentTarget) }, t("Copy")),
        h("button", { type: "button", class: "link-btn", onclick: () => {
          delete store.data.notes[key]; store.save();
          list.replaceWith(learningNotes(items, store));
        } }, t("Remove work note"))),
      learningSource(k));
    return row;
  }));
  return list;
}

// The Knowledge page's Learn band (learning.glance): the newest lesson worth reading with its principle, a case file
// to answer in place (All knowledge's card, asking first), the topic to read next and what is ready to revisit. Each
// part shows only when it has something; with nothing at all, the band is a link into the library.
function learningGlance(g) {
  const store = learningStore();
  const lessonHref = (k) => `#/learn?${new URLSearchParams({ lesson: LearningPractice.key(k) })}`;
  const tile = (cls, label, iconName, ...body) => h("div", { class: `learn-glance-tile ${cls}` },
    h("span", { class: "learn-glance-label" }, icon(iconName), label), ...body);
  const latest = g?.latest, c = latest?.case || {};
  const open = askFirst() ? (g?.cases || []).find((k) => !caseAnswers()[k.id]) : null;
  const topic = g?.topic;
  const due = Object.values(store.data.history).filter((r) => Number.isFinite(r?.dueAt) && r.dueAt <= Date.now()).length;
  const tiles = [
    latest ? tile("", t("Latest lesson"), "learning",
      h("a", { class: "learn-glance-title", href: lessonHref(latest), html: codeSpans(latest.title) }),
      c.principle ? h("p", { class: "learn-glance-principle" }, c.principle)
        : c.question ? h("p", { class: "learn-glance-note", html: codeSpans(c.question) }) : null) : null,
    topic ? tile("", topic.asked ? t("From your questions") : t("Your top topic"), learningTopicIcon(topic.id),
      h("a", { class: "learn-glance-title", href: `#/learn?${new URLSearchParams({ topic: topic.id })}` },
        learningTopicLabel(topic.id), h("span", { class: "learn-glance-count" }, tn(topic.count, "{n} lesson", "{n} lessons"))),
      h("ul", { class: "learn-glance-list" }, topic.lessons.map((k) =>
        h("li", null, h("a", { href: lessonHref(k), html: codeSpans(k.title) }))))) : null,
    open ? tile("learn-glance-case", t("A case to solve"), "reviews", knowledgeCard(open, { compact: true, ask: true })) : null,
  ].filter(Boolean);
  return h("section", { class: "card learn-glance" },
    h("div", { class: "learn-glance-head" },
      h("a", { href: "#/learn" }, icon("learning"), h("h2", null, t("Learn from your work"))),
      h("a", { class: "learn-glance-all", href: "#/learn" }, t("All lessons"), icon("arrow"))),
    tiles.length ? h("div", { class: "learn-glance-grid" + (open ? " with-case" : "") }, tiles)
      : h("p", { class: "muted" }, t("Explore every lesson by category, with diagrams and ready-to-use explanations.")),
    due ? h("a", { class: "learn-glance-due", href: "#/learn" }, icon("again"),
      tn(due, "{n} lesson ready to revisit", "{n} lessons ready to revisit")) : null);
}
