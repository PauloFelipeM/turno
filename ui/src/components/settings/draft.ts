// The settings dialog's working copy of GET /api/config, and the requests that save what changed.
// Ported from the old panel (buildDraft, mergeDraft, readCaps, saveSteps): only what changed is sent.

// Relative imports with the .ts extension: node runs this module as it is in tests/test_web_texts.py.
import { enc, type ConfigView, type EngineConfig } from "../../api.ts";
import { parseCap } from "../../lib/format.ts";
import { formatPoint, parsePoint, type WindowPoint } from "../../lib/schedule.ts";

export const EFFORTS = ["low", "medium", "high", "xhigh"];
export const ROLES = ["planner", "implementer", "reviewer"];
/** A new window starts as Claude's default: Friday 19:00 to Sunday 03:00. */
export const DEFAULT_START: WindowPoint = { day: "fri", time: "19:00" };
export const DEFAULT_END: WindowPoint = { day: "sun", time: "03:00" };

const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x)) as T;

export interface ProjectDraft {
  path: string;
  group: string;
  caps: Record<string, string>;
}

export interface EngineDraft {
  enabled: boolean;
  autostart: boolean;
  orchestrator_model: string;
  orchestrator_effort: string;
  roles: Record<string, { model: string; effort: string }>;
  noWindow: boolean;
  start: WindowPoint;
  end: WindowPoint;
  margin: string;
  primary_cap: string;
}

export interface GeneralDraft {
  language: string;
  enabled: boolean;
  server: string;
  timeout: string;
}

export interface AddDraft {
  name: string;
  path: string;
  group: string;
  caps: Record<string, string>;
}

export interface Draft {
  cfg: ConfigView;
  order: string[];
  orig: string[];
  proj: Record<string, ProjectDraft>;
  origProj: Record<string, ProjectDraft>;
  eng: Record<string, EngineDraft>;
  origEng: Record<string, EngineDraft>;
  gen: GeneralDraft;
  origGen: GeneralDraft;
}

export const emptyAdd = (): AddDraft => ({ name: "", path: "", group: "", caps: {} });

export function engineDraft(e: EngineConfig): EngineDraft {
  const s = e.schedule;
  return {
    enabled: !!e.enabled,
    autostart: e.autostart !== false,
    orchestrator_model: e.orchestrator_model ?? "",
    orchestrator_effort: e.orchestrator_effort ?? "medium",
    roles: Object.fromEntries(ROLES.map((r) => [r, { model: e.roles?.[r]?.model ?? "", effort: e.roles?.[r]?.effort ?? "medium" }])),
    noWindow: !s,
    start: (s && parsePoint(s.start)) || DEFAULT_START,
    end: (s && parsePoint(s.end)) || DEFAULT_END,
    margin: s ? String(s.start_margin_minutes ?? 15) : "15",
    primary_cap: e.primary_cap == null ? "" : String(e.primary_cap),
  };
}

export function generalDraft(cfg: ConfigView): GeneralDraft {
  const n = cfg.notify;
  return {
    language: cfg.language,
    enabled: n.enabled !== false,
    server: n.server ?? "",
    timeout: n.decision_timeout_hours == null ? "" : String(n.decision_timeout_hours),
  };
}

export function buildDraft(cfg: ConfigView): Draft {
  const order = cfg.projects.map((p) => p.name);
  const proj = Object.fromEntries(
    cfg.projects.map((p) => [
      p.name,
      {
        path: p.path,
        group: p.docker_group ?? "",
        caps: Object.fromEntries(Object.keys(cfg.engines).map((e) => [e, p.weekly_cap[e] == null ? "" : String(p.weekly_cap[e])])),
      },
    ]),
  );
  const eng = Object.fromEntries(Object.entries(cfg.engines).map(([k, e]) => [k, engineDraft(e)]));
  const gen = generalDraft(cfg);
  return { cfg, order, orig: [...order], proj, origProj: clone(proj), eng, origEng: clone(eng), gen, origGen: clone(gen) };
}

/** Rebuilds the draft from fresh server data but keeps what the owner typed and has not saved yet. */
export function mergeDraft(old: Draft | null, cfg: ConfigView): Draft {
  const d = buildDraft(cfg);
  if (!old) return d;
  const names = new Set(d.order);
  for (const n of d.order) {
    if (old.proj[n]) {
      d.proj[n] = old.proj[n];
      d.origProj[n] = old.origProj[n];
    }
  }
  d.order = [...old.order.filter((n) => names.has(n)), ...d.order.filter((n) => !old.order.includes(n))];
  d.orig = [...old.orig.filter((n) => names.has(n)), ...d.orig.filter((n) => !old.orig.includes(n))];
  d.eng = old.eng;
  d.origEng = old.origEng;
  d.gen = old.gen;
  d.origGen = old.origGen;
  return d;
}

/** The cap fields of one project or form: empty means "leave it alone". {engine: n}, or null if one is invalid. */
export function readCaps(raw: Record<string, string>, was?: Record<string, string>): Record<string, number> | null {
  const out: Record<string, number> = {};
  for (const [e, v] of Object.entries(raw)) {
    if (String(v ?? "").trim() === "") continue;
    const n = parseCap(v);
    if (n == null) return null;
    if (was === undefined || n !== parseCap(was[e])) out[e] = n;
  }
  return out;
}

export interface Step {
  url: string;
  body: Record<string, unknown>;
}

/** The PUT requests that save the changes, in order; null when a field is invalid. */
export function saveSteps(d: Draft): Step[] | null {
  const steps: Step[] = [];
  for (const n of d.order) {
    const cur = d.proj[n];
    const was = d.origProj[n];
    const body: Record<string, unknown> = {};
    if (cur.path !== was.path) body.path = cur.path.trim();
    if (cur.group !== was.group) body.docker_group = cur.group.trim();
    const caps = readCaps(cur.caps, was.caps);
    if (!caps) return null;
    if (Object.keys(caps).length) body.weekly_cap = caps;
    if (Object.keys(body).length) steps.push({ url: `/api/projects/${enc(n)}`, body });
  }
  if (d.order.some((n, i) => n !== d.orig[i])) steps.push({ url: "/api/settings", body: { order: [...d.order] } });
  for (const [k, cur] of Object.entries(d.eng)) {
    const was = d.origEng[k];
    const body: Record<string, unknown> = {};
    if (cur.enabled !== was.enabled) body.enabled = cur.enabled;
    if (cur.autostart !== was.autostart) body.autostart = cur.autostart;
    if (cur.orchestrator_model !== was.orchestrator_model) {
      if (!cur.orchestrator_model.trim()) return null;
      body.orchestrator_model = cur.orchestrator_model.trim();
    }
    if (cur.orchestrator_effort !== was.orchestrator_effort) body.orchestrator_effort = cur.orchestrator_effort;
    const roles: Record<string, Record<string, string>> = {};
    for (const r of ROLES) {
      const ch: Record<string, string> = {};
      if (cur.roles[r].model !== was.roles[r].model) {
        if (!cur.roles[r].model.trim()) return null;
        ch.model = cur.roles[r].model.trim();
      }
      if (cur.roles[r].effort !== was.roles[r].effort) ch.effort = cur.roles[r].effort;
      if (Object.keys(ch).length) roles[r] = ch;
    }
    if (Object.keys(roles).length) body.roles = roles;
    if (cur.primary_cap.trim() === "") {
      if (was.primary_cap.trim() !== "") return null;
    } else {
      const n = parseCap(cur.primary_cap);
      if (n == null) return null;
      if (n !== parseCap(was.primary_cap)) body.primary_cap = n;
    }
    const start = formatPoint(cur.start);
    const end = formatPoint(cur.end);
    const changed =
      cur.noWindow !== was.noWindow ||
      (!cur.noWindow && (start !== formatPoint(was.start) || end !== formatPoint(was.end) || cur.margin !== was.margin));
    if (changed) {
      if (cur.noWindow) body.schedule = null;
      else {
        const margin = Number(cur.margin);
        if (!start || !end || start === end || !cur.margin.trim() || !Number.isInteger(margin) || margin < 0) return null;
        body.schedule = { start, end, start_margin_minutes: margin };
      }
    }
    if (Object.keys(body).length) steps.push({ url: `/api/engines/${enc(k)}`, body });
  }
  const g = d.gen;
  const wg = d.origGen;
  const general: Record<string, unknown> = {};
  const notify: Record<string, unknown> = {};
  if (g.language !== wg.language) general.language = g.language;
  if (g.enabled !== wg.enabled) notify.enabled = g.enabled;
  if (g.server.trim() !== wg.server) {
    if (!/^https?:\/\/\S+$/.test(g.server.trim())) return null;
    notify.server = g.server.trim();
  }
  if (g.timeout.trim() !== wg.timeout) {
    const hours = Number(g.timeout);
    if (!g.timeout.trim() || !Number.isFinite(hours) || hours <= 0) return null;
    notify.decision_timeout_hours = hours;
  }
  if (Object.keys(notify).length) general.notify = notify;
  if (Object.keys(general).length) steps.push({ url: "/api/settings/general", body: general });
  return steps;
}
