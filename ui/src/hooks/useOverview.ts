import { useEffect, useState } from "react";
import { getJSON, type FeedItem, type LiveRun, type Overview } from "@/api";

const MAX_ITEMS = 500;

/** Merges items by `n` (no repeats, in order), keeping the last MAX_ITEMS. */
export function mergeItems(old: FeedItem[], incoming: FeedItem[]): FeedItem[] {
  const seen = new Set(old.map((i) => i.n));
  const fresh = incoming.filter((i) => !seen.has(i.n));
  if (!fresh.length) return old;
  return old.concat(fresh).sort((a, b) => a.n - b.n).slice(-MAX_ITEMS);
}

export interface OverviewState {
  overview: Overview | null;
  /** Live feed per run id. */
  feeds: Record<string, FeedItem[]>;
  /** False while the event stream is down (EventSource reconnects by itself). */
  connected: boolean;
  /** Grows on every overview event: the history and the open project panel refresh on it. */
  tick: number;
}

/**
 * The `overview` and `feed` events of /api/events, read as the old panel did: an overview replaces the
 * state and drops the feeds of runs that ended; a run seen for the first time gets its feed so far from
 * /api/live, one request at a time.
 */
export function useOverview(): OverviewState {
  const [state, setState] = useState<OverviewState>({ overview: null, feeds: {}, connected: true, tick: 0 });

  useEffect(() => {
    let closed = false;
    let loading = false;
    let again = false; // an overview asked for a feed while a request was running
    const fetched = new Set<string>(); // runs whose feed so far came from /api/live
    const es = new EventSource("/api/events");

    const loadLive = async () => {
      if (loading) {
        again = true;
        return;
      }
      loading = true;
      try {
        const runs = await getJSON<(LiveRun & { items: FeedItem[] })[]>("/api/live");
        if (closed) return;
        for (const r of runs) fetched.add(r.run);
        setState((s) => {
          const feeds = { ...s.feeds };
          for (const r of runs) feeds[r.run] = mergeItems(feeds[r.run] ?? [], r.items);
          return { ...s, feeds };
        });
      } catch {
        /* a run without a feed is asked for again with the next overview, as the old panel did */
      } finally {
        loading = false;
        if (again && !closed) {
          again = false;
          void loadLive();
        }
      }
    };

    es.onopen = () => setState((s) => (s.connected ? s : { ...s, connected: true }));
    es.onerror = () => setState((s) => (s.connected ? { ...s, connected: false } : s));
    es.addEventListener("overview", (e) => {
      const overview = JSON.parse((e as MessageEvent<string>).data) as Overview;
      const runs = new Set(overview.live.map((l) => l.run));
      setState((s) => {
        const feeds: Record<string, FeedItem[]> = {};
        for (const [run, items] of Object.entries(s.feeds)) if (runs.has(run)) feeds[run] = items;
        return { overview, feeds, connected: true, tick: s.tick + 1 };
      });
      const fresh = [...runs].filter((r) => !fetched.has(r));
      if (fresh.length) void loadLive(); // marked as fetched only when /api/live answered
    });
    es.addEventListener("feed", (e) => {
      const d = JSON.parse((e as MessageEvent<string>).data) as { run: string; items: FeedItem[] };
      setState((s) => ({ ...s, feeds: { ...s.feeds, [d.run]: mergeItems(s.feeds[d.run] ?? [], d.items) } }));
    });

    return () => {
      closed = true;
      es.close();
    };
  }, []);

  return state;
}
