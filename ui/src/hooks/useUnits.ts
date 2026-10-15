import { useCallback, useEffect, useRef, useState } from "react";
import { getJSON, type Unit } from "@/api";

export const PAGE = 30;

/**
 * The unit history, newest first, filtered by project when `project` is set. It reloads from scratch when
 * the project changes and refreshes the first page on every `tick` (an overview event), keeping the older
 * pages already loaded, as the old panel did. One request at a time.
 */
export function useUnits(project: string | null, tick: number) {
  const [units, setUnits] = useState<Unit[]>([]);
  const [exhausted, setExhausted] = useState(false);
  const [error, setError] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const current = useRef<Unit[]>([]);
  const busy = useRef(false);
  const queued = useRef<"reset" | "refresh" | null>(null);

  const load = useCallback(
    async (mode: "reset" | "refresh" | "more") => {
      if (busy.current) {
        if (mode !== "more") queued.current = queued.current === "reset" || mode === "reset" ? "reset" : "refresh";
        return;
      }
      busy.current = true;
      try {
        const params = new URLSearchParams({ limit: String(PAGE) });
        if (project) params.set("project", project);
        const have = current.current;
        if (mode === "more" && have.length) params.set("before", have[have.length - 1].started_at);
        const page = await getJSON<Unit[]>(`/api/units?${params}`);
        let next: Unit[];
        let done: boolean | null = page.length < PAGE;
        if (mode === "more") next = have.concat(page);
        else if (mode === "refresh" && page.length && have.length) {
          const oldest = page[page.length - 1].started_at;
          const ids = new Set(page.map((u) => u.unit_id));
          const kept = have.filter((u) => u.started_at < oldest && !ids.has(u.unit_id));
          next = page.concat(kept);
          if (kept.length) done = null; // older pages stay as they were
        } else next = page;
        current.current = next;
        setUnits(next);
        if (done !== null) setExhausted(done);
        setError(false);
        setLoaded(true);
      } catch {
        setError(true);
      } finally {
        busy.current = false;
        const q = queued.current;
        queued.current = null;
        if (q) void load(q);
      }
    },
    [project],
  );

  useEffect(() => {
    current.current = [];
    setUnits([]);
    setLoaded(false);
    void load("reset");
  }, [load]);

  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    void load("refresh");
  }, [tick, load]);

  return { units, exhausted, error, loaded, more: () => load("more") };
}
