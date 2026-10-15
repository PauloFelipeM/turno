import { useCallback, useEffect, useRef, useState } from "react";
import { enc, getJSON, type ProjectDetail, type Settings, type SetupDetail, type SetupPhase } from "@/api";

export interface DetailState {
  detail: ProjectDetail | null;
  settings: Settings | null;
  setup: SetupDetail | null;
  error: boolean;
}

/**
 * The project panel's data: GET /api/projects/<name>, /api/settings and /api/projects/<name>/setup.
 * It loads when the project changes and again when the loop moves the setup request to another phase
 * (`phase`, from the overview) — never on every event, and never two loads at once.
 */
export function useProjectDetail(name: string | null, phase: SetupPhase | null) {
  const [state, setState] = useState<DetailState>({ detail: null, settings: null, setup: null, error: false });
  const loadedFor = useRef<{ name: string; phase: SetupPhase | null } | null>(null);
  const busy = useRef(false);
  const again = useRef(false);

  const load = useCallback(async () => {
    if (!name) return;
    if (busy.current) {
      again.current = true;
      return;
    }
    busy.current = true;
    try {
      const detail = await getJSON<ProjectDetail>(`/api/projects/${enc(name)}`);
      const settings = await getJSON<Settings>("/api/settings").catch(() => null);
      const setup = await getJSON<SetupDetail>(`/api/projects/${enc(name)}/setup`).catch(() => null);
      // The phase this load was for: the request's own, else the overview's, so no reload loop follows.
      loadedFor.current = { name, phase: setup ? (setup.request ? setup.request.status : null) : phase };
      setState({ detail, settings, setup, error: false });
    } catch {
      loadedFor.current = { name, phase };
      setState((s) => ({ ...s, error: true }));
    } finally {
      busy.current = false;
      if (again.current) {
        again.current = false;
        void load();
      }
    }
  }, [name, phase]);

  useEffect(() => {
    setState({ detail: null, settings: null, setup: null, error: false });
    loadedFor.current = null;
  }, [name]);

  useEffect(() => {
    if (!name) return;
    const seen = loadedFor.current;
    if (!seen || seen.name !== name || seen.phase !== phase) void load();
  }, [name, phase, load]);

  return { ...state, reload: load };
}
