// turno panel: draws what the API returns. All data logic lives in the server.
import T from "./i18n/pt-BR.js";

const $ = (sel) => document.querySelector(sel);
const PAGE = 30;
const view = { overview: null, filter: null, feeds: new Map(), units: [], exhausted: false, liveEls: new Map() };

function t(path, vars = {}) {
  const raw = path.split(".").reduce((o, k) => (o == null ? o : o[k]), T);
  return String(raw ?? path).replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ""));
}

// Builds elements without innerHTML: every text from the API goes in as text.
function h(tag, attrs = {}, ...kids) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid != null && kid !== false) node.append(kid instanceof Node ? kid : String(kid));
  }
  return node;
}

const engineName = (e) => T.engine[e] || e;
const when = (ts) => (ts ? new Date(ts * 1000).toLocaleString("pt-BR", { weekday: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "-");
const clock = (iso) => (iso ? new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "-");
const pct = (n) => `${Number(n || 0).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`;
const duration = (s) => (s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min` : `${Math.max(1, Math.round(s / 60))} min`);

async function getJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

// Keeps and restores keyboard focus when the list is redrawn.
function keepFocus(attr, render) {
  const key = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset[attr] : null;
  render();
  if (key == null) return;
  for (const el of document.querySelectorAll(`[data-${attr}]`)) {
    if (el.dataset[attr] === key) { el.focus(); break; }
  }
}

// --- engines -------------------------------------------------------------------------------

function phaseText(e) {
  const p = e.loop || {};
  return t(`phase.${p.phase || "unknown"}`, { project: p.project || "", until: when(p.until) });
}

function meter(label, w, cap) {
  const used = Math.min(100, w.used || 0);
  return h("div", { class: "meter-row" },
    h("span", {}, label),
    h("span", { class: "meter" + (cap && used >= cap ? " hot" : ""), role: "img", "aria-label": `${label}: ${pct(w.used)}` },
      h("i", { style: `width:${used}%` }),
      cap ? h("b", { class: "cap", style: `left:${Math.min(cap, 100)}%`, title: t("cap", { cap }) }) : null),
    h("span", { class: "num" }, pct(w.used)),
    h("span", { class: "muted" }, w.resets_at ? t("reset", { when: when(w.resets_at) }) : ""));
}

function renderEngines() {
  const o = view.overview;
  $("#engines").replaceChildren(
    ...o.engines.map((e) => h("section", { class: `engine phase-${e.loop.phase}` },
      h("h2", {}, e.label, " ", h("span", { class: "phase" }, phaseText(e))),
      e.limits.known
        ? [meter(t("window5h"), e.limits.primary, e.primary_cap), meter(t("weekly"), e.limits.secondary, e.weekly_cap)]
        : h("p", { class: "muted" }, t("noLimits")),
      e.window ? h("p", { class: "muted small" }, e.window.label) : null)),
    ...o.errors.map((err) => h("p", { class: "error" }, t("configError", { config: err.config }))));
}

// --- projects ------------------------------------------------------------------------------

function projectState(p) {
  const c = p.claim;
  const name = c && c.engine !== "any" && p.engines[c.engine] ? c.engine : Object.keys(p.engines)[0];
  return p.engines[name] || {};
}

function statusChip(s) {
  const key = s.interrupted ? "interrupted" : s.status || "active";
  return h("span", { class: `chip st-${key}` }, t(`status.${key}`, { until: when(s.blocked_until) }));
}

function claimText(c) {
  if (!c) return null;
  return c.engine === "any" ? t("baton", { engine: engineName(c.handoff_from) }) : t("claim", { engine: engineName(c.engine) });
}

function progress(b) {
  if (b.error) return h("span", { class: "muted small" }, t(`backlog.${b.error}`));
  const cur = b.current;
  const tasks = cur && cur.tasks;
  return [
    cur ? h("span", {}, cur.title, " · ", h("span", { class: "muted" }, tasks ? `${tasks.done}/${tasks.total}` : t(`backlog.state.${cur.state}`))) : null,
    tasks ? h("span", { class: "bar" }, h("i", { style: `width:${tasks.total ? (100 * tasks.done) / tasks.total : 0}%` })) : null,
    h("span", { class: "muted small" }, t("backlog.items", { done: b.done, total: b.total }), b.stale ? ` · ${t("backlog.stale")}` : ""),
  ];
}

function renderProjects() {
  keepFocus("project", drawProjects);
}

function drawProjects() {
  const running = new Set(view.overview.live.map((l) => l.project));
  $("#project-list").replaceChildren(...view.overview.projects.map((p) => {
    const extra = [claimText(p.claim), p.decisions ? (p.decisions === 1 ? t("decisionsOne") : t("decisionsMany", { n: p.decisions })) : null];
    return h("button", {
      type: "button",
      "data-project": p.name,
      class: "project" + (running.has(p.name) ? " on" : "") + (view.filter === p.name ? " sel" : ""),
      "aria-pressed": String(view.filter === p.name),
      onclick: () => selectProject(p.name),
    },
    h("span", { class: "row" }, h("b", {}, p.name), statusChip(projectState(p))),
    h("span", { class: "muted small" }, extra.filter(Boolean).join(" · ")),
    progress(p.backlog));
  }));
}

async function selectProject(name) {
  view.filter = view.filter === name ? null : name;
  renderProjects();
  renderLive();
  const box = $("#project-detail");
  box.hidden = true;
  await loadUnits("reset");
  if (!view.filter) return;
  let d;
  try {
    d = await getJSON(`/api/projects/${encodeURIComponent(name)}`);
  } catch {
    if (view.filter === name) {
      box.hidden = false;
      box.replaceChildren(h("p", { class: "error" }, t("loadError")));
    }
    return;
  }
  if (view.filter !== name) return;
  box.hidden = false;
  box.replaceChildren(
    h("h3", {}, t("project.backlog")),
    d.backlog.error
      ? h("p", { class: "muted" }, t(`backlog.${d.backlog.error}`))
      : h("ol", { class: "backlog" }, d.backlog.items.map((i) => h("li", { class: `bl-${i.state}` },
          h("span", {}, i.title),
          h("span", { class: "chip" }, i.tasks ? `${i.tasks.done}/${i.tasks.total}` : t(`backlog.state.${i.state}`))))),
    d.decisions.length ? h("h3", {}, t("project.decisions")) : null,
    d.decisions.map((x) => h("article", { class: "decision" },
      h("p", {}, h("b", {}, x.question)),
      x.context ? h("p", { class: "muted small" }, x.context) : null,
      h("ul", {}, x.options.map((o) => h("li", {}, `${o.id.toUpperCase()}. ${o.label}`, o.id === x.default ? ` (${t("project.default")})` : ""))),
      h("p", { class: "muted small" }, x.deadline ? `${t("project.deadline", { when: when(x.deadline) })}. ` : "", t("project.answerByPhone")))));
}

// --- live ----------------------------------------------------------------------------------

function feedItem(i) {
  switch (i.kind) {
    case "message": return h("li", { class: "msg" }, i.text);
    case "command": return h("li", { class: "cmd" }, "$ ", i.text);
    case "command_done":
      return h("li", { class: "done" + (i.exit_code ? " bad" : "") },
        h("details", {}, h("summary", {}, "↳ ", t("live.exit", { code: i.exit_code ?? "?" })), h("pre", {}, i.output || "")));
    case "file": return h("li", { class: "file" }, "✎ ", i.text);
    case "role": return h("li", { class: "role" }, "→ ", i.role || "", i.text ? `: ${i.text}` : "");
    case "wait": return h("li", { class: "wait muted" }, "… ", t("live.wait"));
    case "error": return h("li", { class: "err" }, "! ", i.text);
    default: return h("li", { class: "muted" }, t("live.unknown", { text: i.text }));
  }
}

function currentStep(items) {
  for (let k = items.length - 1; k >= 0; k -= 1) {
    if (items[k].kind === "role") return items[k].role;
    if (items[k].kind === "wait") return t("live.wait");
  }
  return null;
}

function renderLive() {
  const runs = view.overview.live.filter((l) => !view.filter || l.project === view.filter);
  const body = $("#live-body");
  view.liveEls = new Map();
  if (!runs.length) {
    body.replaceChildren(h("p", { class: "muted" }, t("live.none")),
      ...view.overview.engines.map((e) => h("p", { class: "muted small" }, `${e.label}: ${phaseText(e)}`)));
    return;
  }
  body.replaceChildren(...runs.map((l) => {
    const items = view.feeds.get(l.run) || [];
    const step = currentStep(items);
    const chip = h("span", { class: "chip", hidden: !step }, step || "");
    const list = h("ol", { class: "feed", "aria-live": "polite" }, items.slice(-200).map(feedItem));
    requestAnimationFrame(() => { list.scrollTop = list.scrollHeight; });
    const article = h("article", { class: "run" },
      h("h3", {}, h("span", { class: "dot", "aria-hidden": "true" }), `${l.project} · ${engineName(l.engine)}`,
        chip, h("span", { class: "muted small num" }, clock(l.started_at))),
      list);
    view.liveEls.set(l.run, { list, chip });
    return article;
  }));
}

// Merges items by n (no repeats, in order); returns only the new ones.
function mergeItems(run, incoming) {
  const items = view.feeds.get(run) || [];
  const seen = new Set(items.map((i) => i.n));
  const fresh = incoming.filter((i) => !seen.has(i.n));
  view.feeds.set(run, items.concat(fresh).sort((a, b) => a.n - b.n).slice(-500));
  return fresh.sort((a, b) => a.n - b.n);
}

function addItems(run, incoming) {
  const fresh = mergeItems(run, incoming);
  const els = view.liveEls.get(run);
  if (!els) return false;
  if (!fresh.length) return true;
  const { list, chip } = els;
  const atEnd = list.scrollHeight - list.scrollTop - list.clientHeight <= 24;
  list.append(...fresh.map(feedItem));
  while (list.children.length > 200) list.firstChild.remove();
  if (atEnd) list.scrollTop = list.scrollHeight;
  const step = currentStep(view.feeds.get(run));
  chip.textContent = step || "";
  chip.hidden = !step;
  return true;
}

async function loadLive() {
  for (const r of await getJSON("/api/live")) mergeItems(r.run, r.items);
}

// --- history -------------------------------------------------------------------------------

// mode: "reset" (filter changed), "refresh" (event: first page only, keeps those already loaded), "more".
async function loadUnits(mode = "reset") {
  const params = new URLSearchParams({ limit: String(PAGE) });
  if (view.filter) params.set("project", view.filter);
  if (mode === "more" && view.units.length) params.set("before", view.units[view.units.length - 1].started_at);
  let page;
  try {
    page = await getJSON(`/api/units?${params}`);
  } catch {
    $("#history-list").replaceChildren(h("li", { class: "error" }, t("loadError")));
    return;
  }
  if (mode === "more") {
    view.units = view.units.concat(page);
    view.exhausted = page.length < PAGE;
  } else if (mode === "refresh" && page.length && view.units.length) {
    const oldest = page[page.length - 1].started_at;
    const ids = new Set(page.map((u) => u.unit_id));
    const kept = view.units.filter((u) => u.started_at < oldest && !ids.has(u.unit_id));
    view.units = page.concat(kept);
    if (!kept.length) view.exhausted = page.length < PAGE;
  } else {
    view.units = page;
    view.exhausted = page.length < PAGE;
  }
  renderHistory();
}

function renderHistory() {
  keepFocus("unit", drawHistory);
}

function drawHistory() {
  const list = $("#history-list");
  $("#more").hidden = view.exhausted || !view.units.length;
  if (!view.units.length) {
    list.replaceChildren(h("li", { class: "muted" }, t("history.empty")));
    return;
  }
  list.replaceChildren(...view.units.map((u) => h("li", {},
    h("button", { type: "button", class: "unit", "data-unit": u.unit_id, onclick: () => openUnit(u.unit_id) },
      h("span", { class: "num muted" }, clock(u.started_at)),
      h("b", {}, u.project),
      h("span", { class: `chip oc-${u.outcome}` }, t(`outcome.${u.outcome}`)),
      h("span", { class: "grow" }, u.unit || ""),
      h("span", { class: "muted small" }, `${u.engines.map(engineName).join(" → ")} · ${duration(u.duration)}`),
      u.cost ? h("span", { class: "chip num" }, pct(u.cost.secondary)) : null))));
}

async function openUnit(id) {
  let u;
  try {
    u = await getJSON(`/api/units/${encodeURIComponent(id)}`);
  } catch {
    $("#unit-body").replaceChildren(h("p", { class: "error" }, t("loadError")));
    $("#unit").showModal();
    return;
  }
  $("#unit-body").replaceChildren(
    h("h2", {}, `${u.project} · ${u.unit || t(`outcome.${u.outcome}`)}`),
    u.milestone ? h("p", {}, h("b", {}, `${t("unit.milestone")}: `), u.milestone) : null,
    u.summary ? h("p", {}, u.summary) : null,
    u.cost ? h("p", { class: "muted" }, t("unit.cost", { primary: pct(u.cost.primary), secondary: pct(u.cost.secondary) })) : null,
    h("h3", {}, t("unit.runs")),
    u.runs.map((r) => h("section", { class: "unit-run" },
      h("p", { class: "small" }, h("span", { class: "num" }, `${clock(r.started_at)} → ${clock(r.ended_at)}`),
        ` · ${engineName(r.engine)} · `, h("span", { class: `chip oc-${r.outcome}` }, t(`outcome.${r.outcome}`))),
      r.feed ? h("ol", { class: "feed" }, r.feed.map(feedItem)) : h("p", { class: "muted small" }, t("unit.feedGone")))));
  $("#unit").showModal();
}

// --- event stream --------------------------------------------------------------------------

function connect() {
  const es = new EventSource("/api/events"); // reconecta sozinho se o servidor cair
  es.onopen = () => { $("#offline").hidden = true; };
  es.onerror = () => { $("#offline").hidden = false; };
  es.addEventListener("overview", async (e) => {
    view.overview = JSON.parse(e.data);
    const runs = new Set(view.overview.live.map((l) => l.run));
    for (const run of [...view.feeds.keys()]) if (!runs.has(run)) view.feeds.delete(run);
    renderEngines();
    renderProjects();
    renderLive();
    document.body.classList.remove("loading");
    if ([...runs].some((run) => !view.feeds.has(run))) {
      try {
        await loadLive();
        renderLive();
      } catch { /* the steps arrive with the next events */ }
    }
    await loadUnits("refresh");
  });
  es.addEventListener("feed", (e) => {
    const d = JSON.parse(e.data);
    if (!addItems(d.run, d.items) && view.overview) renderLive();
  });
}

$("#live-h").textContent = t("live.title");
$("#projects-h").textContent = t("projects.title");
$("#history-h").textContent = t("history.title");
$("#more").textContent = t("history.more");
$("#more").addEventListener("click", () => loadUnits("more"));
$("#unit-close").textContent = t("unit.close");
$("#offline").textContent = t("offline");
connect();
