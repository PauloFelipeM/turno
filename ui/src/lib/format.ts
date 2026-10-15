// Dates, numbers and durations in the panel's language (the same formats as the old panel).

export const when = (locale: string, ts: number | null | undefined) =>
  ts ? new Date(ts * 1000).toLocaleString(locale, { weekday: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" }) : "-";

export const clock = (locale: string, iso: string | null | undefined) =>
  iso ? new Date(iso).toLocaleString(locale, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "-";

export const pct = (locale: string, n: number | null | undefined) =>
  `${Number(n || 0).toLocaleString(locale, { maximumFractionDigits: 1 })}%`;

export const duration = (s: number) =>
  s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min` : `${Math.max(1, Math.round(s / 60))} min`;

/** The wait after an error, a usage limit or a busy Docker, while it is still ahead (seconds since the epoch). */
export const retryAt = (s: { retry_at?: number | null } | undefined) =>
  s && s.retry_at && s.retry_at > Date.now() / 1000 ? s.retry_at : null;

/** A cap field: a number from 0 to 100, or null (empty or invalid). */
export function parseCap(v: unknown): number | null {
  if (String(v ?? "").trim() === "") return null;
  const n = Number(v);
  return Number.isFinite(n) && n >= 0 && n <= 100 ? n : null;
}
