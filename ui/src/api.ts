// The panel's typed client: the same API and error format as turno/server.py and turno/control.py.

export type Phase = "running" | "sleeping" | "outside_window" | "disabled" | "stopped" | "unknown";

export interface Usage {
  used: number;
  resets_at: number | null;
}

export interface EngineView {
  engine: string;
  label: string;
  config: string;
  enabled: boolean;
  pid: number | null;
  loop: { phase: Phase; project?: string | null; until?: number | null };
  limits: { known: boolean; primary: Usage; secondary: Usage };
  primary_cap: number | null;
  weekly_cap: number;
  window: { label: string; open: boolean; until: number } | null;
}

export interface Option {
  id: string;
  label: string;
}

export interface Decision {
  id: string;
  question: string;
  context?: string | null;
  options: Option[];
  default: string;
  deadline?: number | null;
  notified_at?: number | null;
}

export interface ProjectEngineState {
  status: "active" | "waiting" | "blocked" | "idle" | string;
  interrupted: boolean;
  blocked_until: number | null;
  weekly_cap: number | null;
  cost: unknown;
  retry_at: number | null;
}

export type BacklogState = "todo" | "spec" | "plan" | "doing" | "done";

export interface BacklogItem {
  title: string;
  state: BacklogState;
  tasks?: { done: number; total: number } | null;
}

export type SetupPhase = "requested" | "review" | "approved" | "done";

export interface ProjectView {
  name: string;
  engines: Record<string, ProjectEngineState>;
  claim: { engine: string; handoff_from?: string | null } | null;
  backlog: { error: string } | { current: BacklogItem | null; done: number; total: number; stale?: boolean };
  decisions: number;
  decisions_open: Decision[];
  paused: boolean;
  next: boolean;
  setup: SetupPhase | null;
  blocked_summary: string | null;
}

export interface LiveRun {
  run: string;
  unit_id: string | null;
  project: string;
  engine: string;
  started_at: string;
}

export interface Overview {
  engines: EngineView[];
  projects: ProjectView[];
  errors: { config: string; error: string }[];
  now: number;
  language: string;
  notify: { enabled: boolean; has_topic: boolean };
  version: string;
  update: { current: string; latest: string | null; newer: boolean };
  live: LiveRun[];
}

export interface FeedItem {
  n: number;
  kind: "message" | "command" | "command_done" | "file" | "role" | "wait" | "error" | "unknown";
  text?: string;
  role?: string;
  exit_code?: number | null;
  output?: string;
}

export interface Cost {
  primary: number;
  secondary: number;
}

export interface UnitRun {
  run?: string;
  engine: string;
  started_at: string;
  ended_at: string;
  outcome: string;
  feed?: FeedItem[] | null;
}

export interface Unit {
  unit_id: string;
  project: string;
  started_at: string;
  ended_at: string;
  engines: string[];
  outcome: string;
  closed: boolean;
  unit: string | null;
  summary: string | null;
  milestone: string | null;
  cost: Cost | null;
  duration: number;
  runs: UnitRun[];
}

export interface ProjectDetail {
  name: string;
  backlog: { error: string } | { items: BacklogItem[]; stale?: boolean };
  decisions: Decision[];
}

export interface SetupDetail {
  request: { goal: string; status: SetupPhase; feedback: string | null } | null;
  files: { path: string; size: number; text: string | null }[];
}

export interface Settings {
  order: string[];
  engines: Record<string, { primary_cap: number | null; weekly_cap: Record<string, number | null> }>;
}

export interface Schedule {
  start: string;
  end: string;
  start_margin_minutes: number;
}

export interface Role {
  model: string;
  effort: string;
}

export interface EngineConfig {
  enabled: boolean;
  autostart?: boolean;
  orchestrator_model: string;
  orchestrator_effort: string;
  primary_cap: number;
  schedule: Schedule | null;
  roles: Record<string, Role>;
  known_models: string[];
  cli: { found: boolean; path?: string; version?: string | null };
}

export interface ProjectConfig {
  name: string;
  path: string;
  docker_group: string | null;
  weekly_cap: Record<string, number>;
  checks: string[];
  removable: boolean;
  blocked_by: "running" | "interrupted" | "claimed" | null;
  setup: SetupPhase | null;
}

export interface ConfigView {
  projects: ProjectConfig[];
  engines: Record<string, EngineConfig>;
  notify: { enabled: boolean; server: string | null; decision_timeout_hours: number | null; has_topic: boolean };
  language: string;
  languages: string[];
  warnings: string[];
}

export interface Limits {
  primary?: { used_percent?: number; resets_at?: number } | null;
  secondary?: { used_percent?: number; resets_at?: number } | null;
}

export interface CheckResult {
  limits: Limits | null;
  error?: string;
}

/** An answer the server refused: `code` is its error code ("busy", "exists", ...), or "network". */
export class ApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export const enc = encodeURIComponent;

export async function getJSON<T>(url: string): Promise<T> {
  let r: Response;
  try {
    r = await fetch(url, { cache: "no-store" });
  } catch {
    throw new ApiError(0, "network", "");
  }
  if (!r.ok) {
    const data = await r.json().catch(() => null);
    throw new ApiError(r.status, (data && typeof data.error === "string" && data.error) || "http", `${url}: ${r.status}`);
  }
  return r.json() as Promise<T>;
}

/** A write request: JSON body and the `X-Turno: 1` guard header. Throws ApiError on any failure. */
export async function send<T = { ok: true }>(url: string, body?: unknown, method: "POST" | "PUT" | "DELETE" = "POST"): Promise<T> {
  let r: Response;
  try {
    r = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json", "X-Turno": "1" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network", "");
  }
  let data: unknown = null;
  try {
    data = await r.json();
  } catch {
    /* no body */
  }
  if (!r.ok) {
    const d = (data ?? {}) as { error?: unknown; message?: unknown };
    throw new ApiError(r.status, typeof d.error === "string" ? d.error : "http", typeof d.message === "string" ? d.message : "");
  }
  return data as T;
}
