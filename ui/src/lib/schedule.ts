// The engine window as turno/schedule.py reads it: "<day> <H>:<MM>", day in mon..sun, e.g. "fri 19:00".

export const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
export type Day = (typeof DAYS)[number];

export interface WindowPoint {
  day: Day;
  /** "HH:MM", the value of an <input type="time">. */
  time: string;
}

const SPEC = /^\s*(mon|tue|wed|thu|fri|sat|sun)\s+(\d{1,2}):(\d{2})\s*$/i;

/** Parses a value the way schedule.parse accepts it; null when it does not. */
export function parsePoint(spec: string | null | undefined): WindowPoint | null {
  const m = SPEC.exec(String(spec ?? ""));
  if (!m) return null;
  const h = Number(m[2]);
  const min = Number(m[3]);
  if (h > 23 || min > 59) return null;
  return { day: m[1].toLowerCase() as Day, time: `${String(h).padStart(2, "0")}:${m[3]}` };
}

/** The text sent to the API: "fri 19:00". Null when the time is empty or not HH:MM. */
export function formatPoint(p: { day: Day; time: string }): string | null {
  const m = /^(\d{2}):(\d{2})$/.exec(p.time);
  if (!m || Number(m[1]) > 23 || Number(m[2]) > 59) return null;
  return `${p.day} ${m[1]}:${m[2]}`;
}
