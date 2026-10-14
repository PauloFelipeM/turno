// turno panel: draws what the API returns. All data logic lives in the server.
import T from "./i18n/pt-BR.js";

const $ = (sel) => document.querySelector(sel);
const PAGE = 30;
const view = {
  overview: null, filter: null, feeds: new Map(), units: [], exhausted: false, liveEls: new Map(),
  confirm: null, // key of the confirmation row that is open: "engine:<name>" or "project:<name>"
  drafts: new Map(), // comment texts typed in the attention strip, kept across redraws
  attentionSig: null, // what the attention strip shows now, to redraw it only when that changes
  draft: null, // the settings dialog's working copy
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

function loadDraft(s) {
  view.draft = {
    order: [...s.order],
    orig: [...s.order],
    engines: Object.keys(s.engines),
    weekly: Object.fromEntries(s.order.map((n) => [n, Object.fromEntries(Object.entries(s.engines).map(([e, v]) => [e, v.weekly_cap[n] ?? ""]))])),
    primary: Object.fromEntries(Object.entries(s.engines).map(([e, v]) => [e, v.primary_cap ?? ""])),
  };
  view.draft.origWeekly = JSON.parse(JSON.stringify(view.draft.weekly));
  view.draft.origPrimary = { ...view.draft.primary };
}

function renderSettings() {
  keepFocus("mv", drawSettings);
}

function move(i, delta) {
  const o = view.draft.order;
  [o[i], o[i + delta]] = [o[i + delta], o[i]];
  renderSettings();
}

// Only what changed since the dialog was loaded; an empty field means "leave it alone". null = invalid field.
function saveBody() {
  const d = view.draft;
  const body = {};
  if (d.order.some((n, i) => n !== d.orig[i])) body.order = d.order;
  const weekly = {};
  const primary = {};
  const read = (raw, was, put) => {
    if (String(raw ?? "").trim() === "") return true;
    const v = parseCap(raw);
    if (v == null) return false;
    if (v !== was) put(v);
    return true;
  };
  for (const n of d.order) {
    for (const e of d.engines) {
      if (!read(d.weekly[n][e], d.origWeekly[n][e], (v) => { (weekly[n] ||= {})[e] = v; })) return null;
    }
  }
  for (const e of d.engines) {
    if (!read(d.primary[e], d.origPrimary[e], (v) => { primary[e] = v; })) return null;
  }
  if (Object.keys(weekly).length) body.weekly_cap = weekly;
  if (Object.keys(primary).length) body.primary_cap = primary;
  return body;
}

function drawSettings() {
  const d = view.draft;
  fill($("#settings-body"),
    h("h2", {}, t("settings.title")),
    h("p", { class: "muted small" }, t("settings.hint")),
    h("ol", { class: "order" }, d.order.map((n, i) => h("li", {},
      h("span", { class: "order-name" }, h("b", {}, n)),
      h("span", { class: "cap-row" }, d.engines.map((e) => capInput(`${engineName(e)} (%)`, d.weekly[n][e], (v) => { d.weekly[n][e] = v; }))),
      h("span", { class: "btn-row" },
        actionBtn("↑", () => move(i, -1), { disabled: i === 0, title: t("settings.up", { project: n }), "aria-label": t("settings.up", { project: n }), "data-mv": `up:${n}` }),
        actionBtn("↓", () => move(i, 1), { disabled: i === d.order.length - 1, title: t("settings.down", { project: n }), "aria-label": t("settings.down", { project: n }), "data-mv": `down:${n}` }))))),
    h("h3", {}, t("settings.primary")),
    h("div", { class: "cap-row" }, d.engines.map((e) => capInput(`${engineName(e)} (%)`, d.primary[e], (v) => { d.primary[e] = v; }))),
    h("div", { class: "btn-row" },
      h("button", { type: "button", class: "btn", onclick: (ev) => {
        const body = saveBody();
        if (!body) { toast(t("caps.invalid"), "error"); return; }
        if (!Object.keys(body).length) { toast(t("caps.nothing")); return; }
        return act(ev.currentTarget, async () => {
          loadDraft(await post("/api/settings", body, "PUT"));
          drawSettings();
          if (view.filter) loadDetail(view.filter);
        }, t("settings.saved"));
      } }, t("settings.save")),
      h("button", { type: "button", class: "btn", onclick: (ev) =>
        act(ev.currentTarget, async () => { loadDraft(await post("/api/settings", undefined, "DELETE")); drawSettings(); if (view.filter) loadDetail(view.filter); }, t("settings.resetDone")) }, t("settings.reset")),
      h("button", { type: "button", class: "btn", onclick: () => $("#settings").close() }, t("settings.close"))));
}

async function openSettings(ev) {
  await act(ev.currentTarget, async () => {
    loadDraft(await getJSON("/api/settings"));
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
