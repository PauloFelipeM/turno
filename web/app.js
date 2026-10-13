// Painel do Turno: desenha o que a API devolve. Toda a lógica de dados fica no servidor.
import T from "./i18n/pt-BR.js";

const $ = (sel) => document.querySelector(sel);
const PAGE = 30;
const view = { overview: null, filter: null, feeds: new Map(), units: [], exhausted: false };

function t(path, vars = {}) {
  const raw = path.split(".").reduce((o, k) => (o == null ? o : o[k]), T);
  return String(raw ?? path).replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? ""));
}

// Monta elementos sem innerHTML: todo texto vindo da API entra como texto.
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

// --- motores ---------------------------------------------------------------------------------

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

// --- projetos --------------------------------------------------------------------------------

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
    tasks ? h("span", { class: "bar" }, h("i", { style: `width:${(100 * tasks.done) / tasks.total}%` })) : null,
    h("span", { class: "muted small" }, t("backlog.items", { done: b.done, total: b.total }), b.stale ? ` · ${t("backlog.stale")}` : ""),
  ];
}

function renderProjects() {
  const running = new Set(view.overview.live.map((l) => l.project));
  $("#project-list").replaceChildren(...view.overview.projects.map((p) => {
    const extra = [claimText(p.claim), p.decisions ? (p.decisions === 1 ? t("decisionsOne") : t("decisionsMany", { n: p.decisions })) : null];
    return h("button", {
      type: "button",
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
  await loadUnits(true);
  const box = $("#project-detail");
  if (!view.filter) {
    box.hidden = true;
    return;
  }
  const d = await getJSON(`/api/projects/${encodeURIComponent(name)}`);
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

// --- ao vivo ---------------------------------------------------------------------------------

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
  if (!runs.length) {
    body.replaceChildren(h("p", { class: "muted" }, t("live.none")),
      ...view.overview.engines.map((e) => h("p", { class: "muted small" }, `${e.label}: ${phaseText(e)}`)));
    return;
  }
  body.replaceChildren(...runs.map((l) => {
    const items = view.feeds.get(l.run) || [];
    const step = currentStep(items);
    const list = h("ol", { class: "feed", "aria-live": "polite" }, items.slice(-200).map(feedItem));
    requestAnimationFrame(() => { list.scrollTop = list.scrollHeight; });
    return h("article", { class: "run" },
      h("h3", {}, h("span", { class: "dot", "aria-hidden": "true" }), `${l.project} · ${engineName(l.engine)}`,
        step ? h("span", { class: "chip" }, step) : null, h("span", { class: "muted small num" }, clock(l.started_at))),
      list);
  }));
}

function addItems(run, incoming) {
  const items = view.feeds.get(run) || [];
  const last = items.length ? items[items.length - 1].n : -1;
  view.feeds.set(run, items.concat(incoming.filter((i) => i.n > last)).slice(-500));
}

async function loadLive() {
  for (const r of await getJSON("/api/live")) view.feeds.set(r.run, r.items.slice(-500));
}

// --- histórico -------------------------------------------------------------------------------

async function loadUnits(reset = false) {
  const params = new URLSearchParams({ limit: String(PAGE) });
  if (view.filter) params.set("project", view.filter);
  if (!reset && view.units.length) params.set("before", view.units[view.units.length - 1].started_at);
  const page = await getJSON(`/api/units?${params}`);
  view.units = reset ? page : view.units.concat(page);
  view.exhausted = page.length < PAGE;
  renderHistory();
}

function renderHistory() {
  const list = $("#history-list");
  $("#more").hidden = view.exhausted || !view.units.length;
  if (!view.units.length) {
    list.replaceChildren(h("li", { class: "muted" }, t("history.empty")));
    return;
  }
  list.replaceChildren(...view.units.map((u) => h("li", {},
    h("button", { type: "button", class: "unit", onclick: () => openUnit(u.unit_id) },
      h("span", { class: "num muted" }, clock(u.started_at)),
      h("b", {}, u.project),
      h("span", { class: `chip oc-${u.outcome}` }, t(`outcome.${u.outcome}`)),
      h("span", { class: "grow" }, u.unit || ""),
      h("span", { class: "muted small" }, `${u.engines.map(engineName).join(" → ")} · ${duration(u.duration)}`),
      u.cost ? h("span", { class: "chip num" }, pct(u.cost.secondary)) : null))));
}

async function openUnit(id) {
  const u = await getJSON(`/api/units/${encodeURIComponent(id)}`);
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

// --- canal de eventos ------------------------------------------------------------------------

function connect() {
  const es = new EventSource("/api/events"); // reconecta sozinho se o servidor cair
  es.onopen = () => { $("#offline").hidden = true; };
  es.onerror = () => { $("#offline").hidden = false; };
  es.addEventListener("overview", async (e) => {
    view.overview = JSON.parse(e.data);
    const runs = new Set(view.overview.live.map((l) => l.run));
    for (const run of [...view.feeds.keys()]) if (!runs.has(run)) view.feeds.delete(run);
    if ([...runs].some((run) => !view.feeds.has(run))) await loadLive();
    renderEngines();
    renderProjects();
    renderLive();
    await loadUnits(true);
    document.body.classList.remove("loading");
  });
  es.addEventListener("feed", (e) => {
    const d = JSON.parse(e.data);
    addItems(d.run, d.items);
    if (view.overview) renderLive();
  });
}

$("#live-h").textContent = t("live.title");
$("#projects-h").textContent = t("projects.title");
$("#history-h").textContent = t("history.title");
$("#more").textContent = t("history.more");
$("#more").addEventListener("click", () => loadUnits(false));
$("#unit-close").textContent = t("unit.close");
$("#offline").textContent = t("offline");
connect();
