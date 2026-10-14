// turno panel: draws what the API returns. All data logic lives in the server.
import T from "./i18n/pt-BR.js";

const $ = (sel) => document.querySelector(sel);
const PAGE = 30;
const view = {
  overview: null, filter: null, feeds: new Map(), units: [], exhausted: false, liveEls: new Map(),
  confirm: null, // key of the confirmation row that is open: "engine:<name>" or "project:<name>"
  drafts: new Map(), // comment texts typed in the attention strip, kept across redraws
  attentionSig: null, // what the attention strip shows now, to redraw it only when that changes
  draft: null, // the settings dialog's working copy (see buildDraft)
};

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

// replaceChildren writes null as "null" and arrays as text: drop empty values and flatten first.
function fill(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter((k) => k != null && k !== false));
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

// Sends a write request; resolves to the parsed JSON and otherwise throws an Error with the text for the
// server's error code or, when there is none, the server's own message.
async function post(url, body, method = "POST") {
  let r;
  try {
    r = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json", "X-Turno": "1" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new Error(t("requestError"));
  }
  let data = null;
  try { data = await r.json(); } catch { /* no body */ }
  if (!r.ok) {
    const known = data && typeof data.error === "string" && Object.hasOwn(T.errors, data.error);
    throw new Error(known ? t(`errors.${data.error}`) : (data && data.message) || t("requestError"));
  }
  return data;
}

let toastTimer = null;
function toast(text, kind = "ok") {
  const el = $("#toast");
  el.textContent = text;
  el.className = `toast ${kind}`;
  el.hidden = false;
  try { el.hidePopover(); } catch { /* not open */ }
  try { el.showPopover(); } catch { /* already open, or no popover support */ } // the top layer keeps it above open dialogs
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.hidden = true;
    try { el.hidePopover(); } catch { /* already closed */ }
  }, 4000);
}

// Runs an action with its button disabled until the server answers; errors become a toast.
async function act(btn, fn, okText) {
  btn.disabled = true;
  try {
    await fn();
    if (okText) toast(okText);
    return true;
  } catch (e) {
    toast(e.message, "error");
    return false;
  } finally {
    btn.disabled = false;
  }
}

const enc = encodeURIComponent;

function actionBtn(label, onclick, attrs = {}) {
  return h("button", { type: "button", class: "icon-btn", ...attrs, onclick }, label);
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

function engineControls(e) {
  const running = e.loop.phase !== "stopped";
  const key = `engine:${e.engine}`;
  const name = engineName(e.engine);
  if (!running && !e.enabled) return h("span", { class: "muted" }, t("disabled"));
  if (!running) {
    return h("button", { type: "button", class: "btn", "data-action": `start:${e.engine}`, onclick: (ev) =>
      act(ev.currentTarget, () => post(`/api/engines/${enc(e.engine)}/start`), t("engineStarting", { engine: name })) }, t("start"));
  }
  if (view.confirm !== key) {
    return h("button", { type: "button", class: "btn", "data-action": `stop:${e.engine}`, onclick: () => { view.confirm = key; renderEngines(); } }, t("stop"));
  }
  const unit = e.loop.phase === "running" && e.loop.project ? t("stopConfirmUnit", { project: e.loop.project }) : "";
  return h("div", { class: "confirm", role: "group" },
    h("span", {}, t("stopConfirm", { engine: name, unit })),
    h("button", { type: "button", class: "btn danger", "data-action": `stop:${e.engine}`, onclick: async (ev) => {
      if (await act(ev.currentTarget, () => post(`/api/engines/${enc(e.engine)}/stop`), t("engineStopping", { engine: name }))) view.confirm = null;
      renderEngines();
    } }, t("stop")),
    h("button", { type: "button", class: "btn", onclick: () => { view.confirm = null; renderEngines(); } }, t("cancel")));
}

function renderEngines() {
  keepFocus("action", drawEngines);
}

function drawEngines() {
  const o = view.overview;
  fill($("#engines"),
    ...o.engines.map((e) => h("section", { class: `engine phase-${e.loop.phase}` },
      h("h2", {}, e.label, " ", h("span", { class: "phase" }, phaseText(e))),
      e.limits.known
        ? [meter(t("window5h"), e.limits.primary, e.primary_cap), meter(t("weekly"), e.limits.secondary, e.weekly_cap)]
        : h("p", { class: "muted" }, t("noLimits")),
      e.window ? h("p", { class: "muted small" }, e.window.label) : null,
      h("div", { class: "controls" }, engineControls(e)))),
    ...o.errors.map((err) => h("p", { class: "error" }, t("configError", { config: err.config }))));
}

// --- attention strip -----------------------------------------------------------------------

function attentionItems() {
  const items = [];
  for (const p of view.overview.projects) {
    for (const d of p.decisions_open || []) items.push({ kind: "decision", project: p.name, d });
    if (Object.values(p.engines).some((e) => e.status === "blocked")) items.push({ kind: "blocked", project: p.name, summary: p.blocked_summary });
  }
  return items;
}

function decisionItem(project, d) {
  const dkey = `${project}/${d.id}`;
  const comment = h("input", { type: "text", maxlength: "2000", class: "comment", "aria-label": t("attention.comment"), placeholder: t("attention.comment"),
    value: view.drafts.get(dkey) || "", oninput: (ev) => view.drafts.set(dkey, ev.target.value) });
  const buttons = d.options.map((o) => h("button", { type: "button", class: "btn", onclick: async (ev) => {
    const group = ev.currentTarget.closest("article").querySelectorAll("button");
    group.forEach((b) => { b.disabled = true; });
    const ok = await act(ev.currentTarget, () => post(`/api/decisions/${enc(project)}/${enc(d.id)}`, { option: o.id, comment: comment.value.trim() || undefined }), t("attention.answered"));
    if (ok) view.drafts.delete(dkey);
    else group.forEach((b) => { b.disabled = false; });
  } }, `${o.id.toUpperCase()}. ${o.label}`, o.id === d.default ? ` (${t("project.default")})` : ""));
  return h("article", { class: "attn-item" },
    h("p", {}, h("b", {}, project), " · ", d.question),
    d.context ? h("p", { class: "muted small" }, d.context) : null,
    d.deadline ? h("p", { class: "muted small" }, t("project.deadline", { when: when(d.deadline) })) : null,
    comment,
    h("div", { class: "btn-row" }, buttons));
}

function blockedItem(project, summary) {
  return h("article", { class: "attn-item" },
    h("p", {}, h("b", {}, project), " · ", t("attention.blocked")),
    h("p", { class: "muted small" }, summary || t("attention.noSummary")),
    h("div", { class: "btn-row" },
      h("button", { type: "button", class: "btn", onclick: (ev) =>
        act(ev.currentTarget, () => post(`/api/projects/${enc(project)}/reset`, {}), t("attention.reactivated", { project })) }, t("attention.reactivate"))));
}

// Redrawn only when its content changes, so a typed comment and the focus survive the events.
function renderAttention() {
  const items = attentionItems();
  const sig = JSON.stringify(items);
  if (sig === view.attentionSig) return;
  view.attentionSig = sig;
  const box = $("#attention");
  box.hidden = !items.length;
  const live = new Set(items.filter((i) => i.kind === "decision").map((i) => `${i.project}/${i.d.id}`));
  for (const k of [...view.drafts.keys()]) if (!live.has(k)) view.drafts.delete(k);
  fill(box, h("h2", {}, t("attention.title")),
    ...items.map((i) => (i.kind === "decision" ? decisionItem(i.project, i.d) : blockedItem(i.project, i.summary))));
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

// The wait after an error, a usage limit or a busy Docker, while it is still ahead.
const retryAt = (s) => (s && s.retry_at && s.retry_at > Date.now() / 1000 ? s.retry_at : null);

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
  keepFocus("project", () => keepFocus("action", drawProjects));
}

function projectActions(p, running) {
  const key = `project:${p.name}`;
  const label = (k) => t(`card.${k}`, { project: p.name });
  const btn = (k, glyph, path, okKey) => actionBtn(glyph, (ev) =>
    act(ev.currentTarget, () => post(`/api/projects/${enc(p.name)}/${path}`), t(`card.${okKey}`, { project: p.name })),
  { title: label(k), "aria-label": label(k), "data-action": `${path}:${p.name}` });
  if (view.confirm === key) {
    return h("div", { class: "confirm", role: "group" },
      h("span", {}, t("card.interruptConfirm", { project: p.name })),
      h("button", { type: "button", class: "btn danger", "data-action": `interrupt:${p.name}`, onclick: async (ev) => {
        if (await act(ev.currentTarget, () => post(`/api/projects/${enc(p.name)}/interrupt`), t("card.interrupted", { project: p.name }))) view.confirm = null;
        renderProjects();
      } }, t("card.interrupt")),
      h("button", { type: "button", class: "btn", onclick: () => { view.confirm = null; renderProjects(); } }, t("cancel")));
  }
  return h("div", { class: "card-actions" },
    p.paused ? btn("resume", "▶", "resume", "resumed") : btn("pause", "⏸", "pause", "paused"),
    p.next ? null : btn("next", "⏭", "next", "nextSet"),
    running ? actionBtn("⏹", () => { view.confirm = key; renderProjects(); },
      { title: label("interrupt"), "aria-label": label("interrupt"), "data-action": `interrupt:${p.name}` }) : null);
}

function drawProjects() {
  const running = new Set(view.overview.live.map((l) => l.project));
  if (view.confirm && view.confirm.startsWith("project:") && !running.has(view.confirm.slice(8))) view.confirm = null;
  fill($("#project-list"), ...view.overview.projects.map((p) => {
    const extra = [claimText(p.claim), p.next ? t("card.nextBadge") : null,
      p.decisions ? (p.decisions === 1 ? t("decisionsOne") : t("decisionsMany", { n: p.decisions })) : null];
    return h("div", { class: "card" + (running.has(p.name) ? " on" : "") + (view.filter === p.name ? " sel" : "") + (p.paused ? " paused" : "") },
      h("button", {
        type: "button",
        "data-project": p.name,
        class: "project",
        "aria-pressed": String(view.filter === p.name),
        onclick: () => selectProject(p.name),
      },
      h("span", { class: "row" }, h("b", {}, p.name), h("span", {}, p.paused ? h("span", { class: "chip paused-chip" }, t("card.pausedChip")) : null, statusChip(projectState(p)))),
      h("span", { class: "muted small" }, extra.filter(Boolean).join(" · ")),
      retryAt(projectState(p)) ? h("span", { class: "muted small" }, t("retry", { when: when(retryAt(projectState(p))) })) : null,
      progress(p.backlog)),
      projectActions(p, running.has(p.name)));
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
  await loadDetail(name);
}

// "Try now" in the project panel: only while some engine is waiting to retry this project.
function retryRow(name) {
  const p = view.overview && view.overview.projects.find((x) => x.name === name);
  const times = p ? Object.values(p.engines).map(retryAt).filter(Boolean) : [];
  if (!times.length) return null;
  const row = h("p", { class: "muted small" }, t("retry", { when: when(Math.min(...times)) }), " ",
    h("button", { type: "button", class: "btn", onclick: async (ev) => {
      const ok = await act(ev.currentTarget, () => post(`/api/projects/${enc(name)}/reset`, {}), t("project.retryRequested", { project: name }));
      if (ok) row.remove();
    } }, t("project.retryNow")));
  return row;
}

async function loadDetail(name) {
  const box = $("#project-detail");
  let d;
  let settings = null;
  try {
    d = await getJSON(`/api/projects/${encodeURIComponent(name)}`);
    settings = await getJSON("/api/settings").catch(() => null);
  } catch {
    if (view.filter === name) {
      box.hidden = false;
      fill(box, h("p", { class: "error" }, t("loadError")));
    }
    return;
  }
  if (view.filter !== name) return;
  box.hidden = false;
  fill(box,
    retryRow(name),
    h("h3", {}, `${t("project.backlog")} · ${name}`),
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
      h("p", { class: "muted small" }, x.deadline ? `${t("project.deadline", { when: when(x.deadline) })}. ` : "", t("project.answerByPhone")))),
    messageForm(name),
    settings ? capsForm(name, settings) : null);
}

function messageForm(name) {
  const text = h("textarea", { rows: "3", maxlength: "2000", "aria-label": t("message.title"), placeholder: t("message.placeholder") });
  return h("section", { class: "form" },
    h("h3", {}, t("message.title")),
    text,
    h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn", onclick: async (ev) => {
      if (!text.value.trim()) { toast(t("message.empty"), "error"); return; }
      if (await act(ev.currentTarget, () => post("/api/messages", { text: text.value, projects: [name] }), t("message.sent"))) text.value = "";
    } }, t("message.send"))));
}

function capInput(label, value, onChange) {
  return h("label", { class: "cap-field" }, h("span", {}, label),
    h("input", { type: "number", min: "0", max: "100", step: "any", value: value ?? "", oninput: (ev) => onChange(ev.target.value) }));
}

// Parses a cap field: a number from 0 to 100, or null.
function parseCap(v) {
  if (String(v).trim() === "") return null;
  const n = Number(v);
  return Number.isFinite(n) && n >= 0 && n <= 100 ? n : null;
}

function capsForm(name, settings) {
  const values = {};
  const engs = Object.keys(settings.engines);
  const fields = engs.map((e) => {
    values[e] = settings.engines[e].weekly_cap[name];
    return capInput(`${engineName(e)} (%)`, values[e], (v) => { values[e] = v; });
  });
  return h("section", { class: "form" },
    h("h3", {}, t("caps.title")),
    h("div", { class: "cap-row" }, fields),
    h("div", { class: "btn-row" }, h("button", { type: "button", class: "btn", onclick: (ev) => {
      const body = {};
      for (const e of engs) {
        if (String(values[e] ?? "").trim() === "") continue; // empty: leave that value alone
        const n = parseCap(values[e]);
        if (n == null) { toast(t("caps.invalid"), "error"); return; }
        if (n !== settings.engines[e].weekly_cap[name]) body[e] = n;
      }
      if (!Object.keys(body).length) { toast(t("caps.nothing")); return; }
      return act(ev.currentTarget, () => post("/api/settings", { weekly_cap: { [name]: body } }, "PUT"), t("caps.saved"));
    } }, t("caps.save"))));
}

// --- settings dialog -----------------------------------------------------------------------
// The dialog edits a working copy (view.draft) of GET /api/config. Saving sends only what changed.

const EFFORTS = ["low", "medium", "high", "xhigh"];
const ROLES = ["planner", "implementer", "reviewer"];
const clone = (x) => JSON.parse(JSON.stringify(x));

function engineDraft(e) {
  const s = e.schedule;
  return {
    enabled: !!e.enabled,
    orchestrator_model: e.orchestrator_model ?? "",
    orchestrator_effort: e.orchestrator_effort ?? "medium",
    roles: Object.fromEntries(ROLES.map((r) => [r, { model: e.roles?.[r]?.model ?? "", effort: e.roles?.[r]?.effort ?? "medium" }])),
    noWindow: !s,
    start: s ? s.start : "", end: s ? s.end : "", margin: s ? String(s.start_margin_minutes ?? 15) : "15",
    primary_cap: e.primary_cap ?? "",
  };
}

function buildDraft(cfg) {
  const d = {
    section: "projects", cfg, confirmRemove: null, checks: {},
    order: cfg.projects.map((p) => p.name),
    proj: Object.fromEntries(cfg.projects.map((p) => [p.name, {
      path: p.path, group: p.docker_group ?? "",
      caps: Object.fromEntries(Object.keys(cfg.engines).map((e) => [e, p.weekly_cap[e] ?? ""])),
    }])),
    eng: Object.fromEntries(Object.entries(cfg.engines).map(([k, e]) => [k, engineDraft(e)])),
    add: { name: "", path: "", group: "", caps: {} },
  };
  d.orig = [...d.order];
  d.origProj = clone(d.proj);
  d.origEng = clone(d.eng);
  return d;
}

// Rebuilds the draft from fresh server data but keeps what the owner has typed and not saved yet.
function mergeDraft(cfg) {
  const old = view.draft;
  const d = buildDraft(cfg);
  if (!old) return d;
  d.section = old.section;
  d.add = old.add;
  d.checks = old.checks;
  const names = new Set(d.order);
  for (const n of d.order) {
    if (old.proj[n]) { d.proj[n] = old.proj[n]; d.origProj[n] = old.origProj[n]; }
  }
  d.order = [...old.order.filter((n) => names.has(n)), ...d.order.filter((n) => !old.order.includes(n))];
  d.orig = [...old.orig.filter((n) => names.has(n)), ...d.orig.filter((n) => !old.orig.includes(n))];
  d.eng = old.eng;
  d.origEng = old.origEng;
  return d;
}

function renderSettings() {
  keepFocus("mv", () => keepFocus("sf", drawSettings));
}

function move(i, delta) {
  const o = view.draft.order;
  [o[i], o[i + delta]] = [o[i + delta], o[i]];
  renderSettings();
}

// The cap fields of one project/form: an empty field means "leave it alone". Returns {engine: n} or null if invalid.
function readCaps(raw, was) {
  const out = {};
  for (const [e, v] of Object.entries(raw)) {
    if (String(v ?? "").trim() === "") continue;
    const n = parseCap(v);
    if (n == null) return null;
    if (n !== was?.[e]) out[e] = n;
  }
  return out;
}

// The requests that save the changes: [{ run(), done() }] in order; null = an invalid field.
function saveSteps() {
  const d = view.draft;
  const steps = [];
  for (const n of d.order) {
    const cur = d.proj[n], was = d.origProj[n];
    const body = {};
    if (cur.path !== was.path) body.path = cur.path.trim();
    if (cur.group !== was.group) body.docker_group = cur.group.trim();
    const caps = readCaps(cur.caps, was.caps);
    if (!caps) return null;
    if (Object.keys(caps).length) body.weekly_cap = caps;
    if (Object.keys(body).length) steps.push({ url: `/api/projects/${enc(n)}`, body, done: () => { d.origProj[n] = clone(cur); } });
  }
  if (d.order.some((n, i) => n !== d.orig[i])) steps.push({ url: "/api/settings", body: { order: [...d.order] }, done: () => { d.orig = [...d.order]; } });
  for (const [k, cur] of Object.entries(d.eng)) {
    const was = d.origEng[k];
    const body = {};
    if (cur.enabled !== was.enabled) body.enabled = cur.enabled;
    if (cur.orchestrator_model !== was.orchestrator_model) body.orchestrator_model = cur.orchestrator_model.trim();
    if (cur.orchestrator_effort !== was.orchestrator_effort) body.orchestrator_effort = cur.orchestrator_effort;
    const roles = {};
    for (const r of ROLES) {
      const ch = {};
      if (cur.roles[r].model !== was.roles[r].model) {
        if (!cur.roles[r].model.trim()) return null;
        ch.model = cur.roles[r].model.trim();
      }
      if (cur.roles[r].effort !== was.roles[r].effort) ch.effort = cur.roles[r].effort;
      if (Object.keys(ch).length) roles[r] = ch;
    }
    if (Object.keys(roles).length) body.roles = roles;
    for (const f of ["primary_cap"]) {
      if (String(cur[f]).trim() === "") { if (String(was[f]).trim() !== "") return null; continue; }
      const n = parseCap(cur[f]);
      if (n == null) return null;
      if (n !== was[f]) body[f] = n;
    }
    if (cur.noWindow !== was.noWindow || (!cur.noWindow && (cur.start !== was.start || cur.end !== was.end || cur.margin !== was.margin))) {
      if (cur.noWindow) body.schedule = null;
      else {
        const margin = Number(cur.margin);
        if (!cur.start.trim() || !cur.end.trim() || !String(cur.margin).trim() || !Number.isInteger(margin) || margin < 0) return null;
        body.schedule = { start: cur.start.trim(), end: cur.end.trim(), start_margin_minutes: margin };
      }
    }
    if (Object.keys(body).length) steps.push({ url: `/api/engines/${enc(k)}`, body, done: () => { d.origEng[k] = clone(cur); } });
  }
  return steps;
}

async function saveSettings(steps) {
  const d = view.draft;
  for (const s of steps) {
    await post(s.url, s.body, "PUT");
    s.done();
  }
  view.draft = buildDraft(await getJSON("/api/config"));
  view.draft.section = d.section;
  view.draft.add = d.add;
  view.draft.checks = d.checks;
  if (view.filter) loadDetail(view.filter);
}

// Runs a settings request; on an error the working copy is redrawn as it stands (partial saves are kept).
function settingsAct(btn, fn, okText) {
  return act(btn, async () => {
    try { await fn(); } finally { renderSettings(); }
  }, okText);
}

function field(label, input, cls = "") {
  return h("label", { class: `field ${cls}`.trim() }, h("span", {}, label), input);
}

function textInput(value, onChange, attrs = {}) {
  return h("input", { type: "text", value: value ?? "", oninput: (ev) => onChange(ev.target.value), ...attrs });
}

function projectRow(n, i) {
  const d = view.draft;
  const p = d.cfg.projects.find((x) => x.name === n);
  const cur = d.proj[n];
  const why = p && !p.removable ? t(`settings.blocked.${p.blocked_by}`) : "";
  const confirming = d.confirmRemove === n;
  return h("li", {},
    h("span", { class: "order-name" }, h("b", {}, n)),
    field(t("settings.path"), textInput(cur.path, (v) => { cur.path = v; }, { "data-sf": `path:${n}` }), "wide"),
    h("div", { class: "cap-row" },
      field(t("settings.group"), textInput(cur.group, (v) => { cur.group = v; }, { "data-sf": `group:${n}` })),
      Object.keys(cur.caps).map((e) => capInput(`${engineName(e)} (%)`, cur.caps[e], (v) => { cur.caps[e] = v; }))),
    p && p.checks.length ? h("ul", { class: "warns" }, p.checks.map((c) => h("li", {}, t(`settings.checks.${c}`)))) : null,
    h("span", { class: "btn-row" },
      actionBtn("↑", () => move(i, -1), { disabled: i === 0, title: t("settings.up", { project: n }), "aria-label": t("settings.up", { project: n }), "data-mv": `up:${n}` }),
      actionBtn("↓", () => move(i, 1), { disabled: i === d.order.length - 1, title: t("settings.down", { project: n }), "aria-label": t("settings.down", { project: n }), "data-mv": `down:${n}` }),
      confirming
        ? h("span", { class: "confirm", role: "group" },
          h("span", {}, t("settings.removeConfirm", { project: n })),
          h("button", { type: "button", class: "btn danger", "data-sf": `remove-yes:${n}`, onclick: (ev) =>
            settingsAct(ev.currentTarget, async () => {
              d.confirmRemove = null;
              await post(`/api/projects/${enc(n)}`, undefined, "DELETE");
              view.draft = mergeDraft(await getJSON("/api/config"));
            }, t("settings.removed", { project: n })) }, t("settings.remove")),
          h("button", { type: "button", class: "btn", onclick: () => { d.confirmRemove = null; renderSettings(); } }, t("cancel")))
        : h("button", { type: "button", class: "btn", "data-sf": `remove:${n}`, disabled: !!why, title: why || null,
          onclick: () => { d.confirmRemove = n; renderSettings(); } }, t("settings.remove")),
      why ? h("span", { class: "muted small" }, why) : null));
}

function addForm() {
  const a = view.draft.add;
  const engs = Object.keys(view.draft.cfg.engines);
  return h("section", { class: "form" },
    h("h3", {}, t("settings.addTitle")),
    h("div", { class: "cap-row" },
      field(t("settings.name"), textInput(a.name, (v) => { a.name = v; }, { "data-sf": "add-name" })),
      field(t("settings.group"), textInput(a.group, (v) => { a.group = v; }, { "data-sf": "add-group" }))),
    field(t("settings.path"), textInput(a.path, (v) => { a.path = v; }, { "data-sf": "add-path" }), "wide"),
    h("div", { class: "cap-row" }, engs.map((e) => capInput(`${engineName(e)} (%)`, a.caps[e] ?? "", (v) => { a.caps[e] = v; }))),
    h("div", { class: "btn-row" },
      h("button", { type: "button", class: "btn", "data-sf": "add-go", onclick: (ev) => {
        if (!a.name.trim() || !a.path.trim()) { toast(t("settings.addMissing"), "error"); return; }
        if (!/^[A-Za-z0-9_-]+$/.test(a.name.trim())) { toast(t("settings.addBadName"), "error"); return; }
        if (!a.path.trim().startsWith("/")) { toast(t("settings.addBadPath"), "error"); return; }
        const caps = readCaps(a.caps);
        if (!caps) { toast(t("caps.invalid"), "error"); return; }
        const body = { name: a.name.trim(), path: a.path.trim() };
        if (a.group.trim()) body.docker_group = a.group.trim();
        if (Object.keys(caps).length) body.weekly_cap = caps;
        return settingsAct(ev.currentTarget, async () => {
          await post("/api/projects", body);
          view.draft = mergeDraft(await getJSON("/api/config"));
          view.draft.add = { name: "", path: "", group: "", caps: {} };
        }, t("settings.added", { project: body.name }));
      } }, t("settings.add"))));
}

function limitText(l) {
  const win = (label, w) => `${label} ${w ? pct(w.used_percent) : "-"}${w && w.resets_at ? ` (${t("reset", { when: when(w.resets_at) })})` : ""}`;
  return `${win(t("window5h"), l.primary)} · ${win(t("weekly"), l.secondary)}`;
}

// The quota check outcome: known server errors get a pt-BR text; others show the original as a technical detail.
function checkResult(k, res) {
  if (res.limits) return h("p", { class: "small", "data-check": k }, limitText(res.limits));
  const err = res.error || "";
  const known = Object.hasOwn(T.settings.checkErrors, err);
  return h("p", { class: "error small", "data-check": k },
    known ? t(`settings.checkErrors.${err}`) : t("settings.checkErrorOther"),
    known || !err ? null : [" ", h("span", { class: "muted tech" }, err)]);
}

function engineBlock(k) {
  const d = view.draft;
  const cur = d.eng[k];
  const cli = d.cfg.engines[k].cli || {};
  const res = d.checks[k];
  const effortSelect = (value, onChange, attrs) => {
    const efforts = EFFORTS.includes(value) ? EFFORTS : [...EFFORTS, value];
    return h("select", { onchange: (ev) => onChange(ev.target.value), ...attrs },
      efforts.map((x) => h("option", { value: x, selected: x === value }, x)));
  };
  const listId = `models-${k}`;
  const roleRow = (label, model, effort, key) => h("tr", {},
    h("th", { scope: "row" }, label),
    h("td", {}, textInput(model.value, model.set, { list: listId, "aria-label": `${label}: ${t("settings.model")}`, "data-sf": `model:${k}${key}` })),
    h("td", {}, effortSelect(effort.value, effort.set, { "aria-label": `${label}: ${t("settings.effort")}`, "data-sf": `effort:${k}${key}` })));
  const roleTable = h("table", { class: "role-table" },
    h("thead", {}, h("tr", {}, h("th", {}, t("settings.role")), h("th", {}, t("settings.model")), h("th", {}, t("settings.effort")))),
    h("tbody", {},
      roleRow(t("settings.roles.orchestrator"),
        { value: cur.orchestrator_model, set: (v) => { cur.orchestrator_model = v; } },
        { value: cur.orchestrator_effort, set: (v) => { cur.orchestrator_effort = v; } }, ""),
      ROLES.map((r) => roleRow(t(`settings.roles.${r}`),
        { value: cur.roles[r].model, set: (v) => { cur.roles[r].model = v; } },
        { value: cur.roles[r].effort, set: (v) => { cur.roles[r].effort = v; } }, `:${r}`))),
    h("datalist", { id: listId }, (d.cfg.engines[k].known_models || []).map((m) => h("option", { value: m }))));
  return h("section", { class: "engine-cfg" },
    h("h3", {}, engineName(k)),
    h("label", { class: "check" },
      h("input", { type: "checkbox", checked: cur.enabled, "data-sf": `enabled:${k}`, onchange: (ev) => { cur.enabled = ev.target.checked; } }),
      t("settings.enabled")),
    roleTable,
    h("div", { class: "cap-row" },
      capInput(t("settings.primaryCap"), cur.primary_cap, (v) => { cur.primary_cap = v; })),
    h("label", { class: "check" },
      h("input", { type: "checkbox", checked: cur.noWindow, "data-sf": `nowindow:${k}`, onchange: (ev) => { cur.noWindow = ev.target.checked; renderSettings(); } }),
      t("settings.noWindow")),
    cur.noWindow ? null : h("div", { class: "cap-row" },
      field(t("settings.start"), textInput(cur.start, (v) => { cur.start = v; }, { placeholder: "fri 19:00", "data-sf": `start:${k}` })),
      field(t("settings.end"), textInput(cur.end, (v) => { cur.end = v; }, { placeholder: "sun 03:00", "data-sf": `end:${k}` })),
      field(t("settings.margin"), h("input", { type: "number", min: "0", step: "1", value: cur.margin, "data-sf": `margin:${k}`, oninput: (ev) => { cur.margin = ev.target.value; } }))),
    h("p", { class: "muted small" }, cli.found ? t("settings.cliFound", { path: cli.path, version: cli.version || "?" }) : t("settings.cliMissing")),
    h("div", { class: "btn-row" },
      h("button", { type: "button", class: "btn", "data-sf": `check:${k}`, disabled: !!(res && res.loading), onclick: (ev) => {
        if (d.checks[k] && d.checks[k].loading) return;
        return act(ev.currentTarget, async () => {
          d.checks[k] = { loading: true };
          renderSettings();
          try { d.checks[k] = await post(`/api/engines/${enc(k)}/check`); } finally { if (d.checks[k].loading) delete d.checks[k]; renderSettings(); }
        });
      } }, res && res.loading ? t("settings.checking") : t("settings.check"))),
    res && !res.loading ? checkResult(k, res) : null);
}

function drawSettings() {
  const d = view.draft;
  const tab = (key, label) => h("button", { type: "button", class: "btn tab" + (d.section === key ? " on" : ""), "data-sf": `tab:${key}`,
    "aria-pressed": String(d.section === key), onclick: () => { d.section = key; renderSettings(); } }, label);
  fill($("#settings-body"),
    h("h2", {}, t("settings.title")),
    h("div", { class: "btn-row" }, tab("projects", t("settings.projects")), tab("engines", t("settings.engines"))),
    d.section === "projects"
      ? [h("p", { class: "muted small" }, t("settings.hint")),
        h("ol", { class: "order" }, d.order.map(projectRow)),
        addForm()]
      : Object.keys(d.eng).map(engineBlock),
    h("div", { class: "btn-row" },
      h("button", { type: "button", class: "btn", "data-sf": "save", onclick: (ev) => {
        const steps = saveSteps();
        if (!steps) { toast(t("settings.invalid"), "error"); return; }
        if (!steps.length) { toast(t("caps.nothing")); return; }
        return settingsAct(ev.currentTarget, () => saveSettings(steps), t("settings.saved"));
      } }, t("settings.save")),
      h("button", { type: "button", class: "btn", onclick: () => $("#settings").close() }, t("settings.close"))));
}

async function openSettings(ev) {
  await act(ev.currentTarget, async () => {
    view.draft = buildDraft(await getJSON("/api/config"));
    drawSettings();
    $("#settings").showModal();
  });
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
    fill(body, h("p", { class: "muted" }, t("live.none")),
      ...view.overview.engines.map((e) => h("p", { class: "muted small" }, `${e.label}: ${phaseText(e)}`)));
    return;
  }
  fill(body, ...runs.map((l) => {
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
    fill($("#history-list"), h("li", { class: "error" }, t("loadError")));
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
    fill(list, h("li", { class: "muted" }, t("history.empty")));
    return;
  }
  fill(list, ...view.units.map((u) => h("li", {},
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
    fill($("#unit-body"), h("p", { class: "error" }, t("loadError")));
    $("#unit").showModal();
    return;
  }
  fill($("#unit-body"),
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
    renderAttention();
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
$("#settings-open").textContent = t("settings.open");
$("#settings-open").addEventListener("click", openSettings);
$("#more").textContent = t("history.more");
$("#more").addEventListener("click", () => loadUnits("more"));
$("#unit-close").textContent = t("unit.close");
$("#offline").textContent = t("offline");
connect();
