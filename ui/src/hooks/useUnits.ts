import { useEffect, useMemo, useRef, useState } from "react";
import { getJSON, type Unit } from "@/api";
import { createSerialLoader } from "@/lib/loader";

export const PAGE = 30;

type Mode = "reset" | "refresh" | "more";

/**
 * The unit history, newest first, filtered by project when `project` is set. It reloads from scratch when
 * the project changes and refreshes the first page on every `tick` (an overview event), keeping the older
 * pages already loaded, as the old panel did. One request at a time; an answer for another project is
 * dropped, and what was asked for meanwhile runs afterwards for the project open now.
 */
export function useUnits(project: string | null, tick: number) {
  const [units, setUnits] = useState<Unit[]>([]);
  const [exhausted, setExhausted] = useState(false);
  const [error, setError] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const current = useRef<Unit[]>([]);
  const projectRef = useRef(project);
  projectRef.current = project;

  const loader = useMemo(
    () =>
      createSerialLoader<string | null, Mode>({
        getKey: () => projectRef.current,
        repeat: (mode) => mode !== "more",
        combine: (queued, mode) => (queued === "reset" || mode === "reset" ? "reset" : "refresh"),
        run: async (proj, mode, isCurrent) => {
          try {
            const params = new URLSearchParams({ limit: String(PAGE) });
            if (proj) params.set("project", proj);
            const have = current.current;
            if (mode === "more" && have.length) params.set("before", have[have.length - 1].started_at);
            const page = await getJSON<Unit[]>(`/api/units?${params}`);
            if (!isCurrent()) return;
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
            if (isCurrent()) setError(true);
          }
        },
      }),
    [],
  );

  useEffect(() => {
    current.current = [];
    setUnits([]);
    setLoaded(false);
    void loader.load("reset");
  }, [project, loader]);

  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    void loader.load("refresh");
  }, [tick, loader]);

  return { units, exhausted, error, loaded, more: () => loader.load("more") };
}
