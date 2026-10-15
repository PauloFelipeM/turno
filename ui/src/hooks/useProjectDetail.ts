import { useEffect, useMemo, useRef, useState } from "react";
import { enc, getJSON, type ProjectDetail, type Settings, type SetupDetail, type SetupPhase } from "@/api";
import { createSerialLoader } from "@/lib/loader";

export interface DetailState {
  detail: ProjectDetail | null;
  settings: Settings | null;
  setup: SetupDetail | null;
  error: boolean;
}

const EMPTY: DetailState = { detail: null, settings: null, setup: null, error: false };

/**
 * The project panel's data: GET /api/projects/<name>, /api/settings and /api/projects/<name>/setup.
 * It loads when the project changes and again when the loop moves the setup request to another phase
 * (`phase`, from the overview) — never on every event, and never two loads at once. An answer for a
 * project the owner has left is dropped, and the load that was waiting runs for the project open now.
 */
export function useProjectDetail(name: string | null, phase: SetupPhase | null) {
  const [state, setState] = useState<DetailState>(EMPTY);
  const loadedFor = useRef<{ name: string; phase: SetupPhase | null } | null>(null);
  const nameRef = useRef(name);
  const phaseRef = useRef(phase);
  nameRef.current = name;
  phaseRef.current = phase;

  const loader = useMemo(
    () =>
      createSerialLoader<string | null, "load">({
        getKey: () => nameRef.current,
        run: async (n, _mode, isCurrent) => {
          if (!n) return;
          try {
            const detail = await getJSON<ProjectDetail>(`/api/projects/${enc(n)}`);
            if (!isCurrent()) return;
            const settings = await getJSON<Settings>("/api/settings").catch(() => null);
            const setup = await getJSON<SetupDetail>(`/api/projects/${enc(n)}/setup`).catch(() => null);
            if (!isCurrent()) return;
            // The phase this load was for: the request's own, else the overview's, so no reload loop follows.
            loadedFor.current = { name: n, phase: setup ? (setup.request ? setup.request.status : null) : phaseRef.current };
            setState({ detail, settings, setup, error: false });
          } catch {
            if (!isCurrent()) return;
            loadedFor.current = { name: n, phase: phaseRef.current };
            setState((s) => ({ ...s, error: true }));
          }
        },
      }),
    [],
  );

  useEffect(() => {
    setState(EMPTY);
    loadedFor.current = null;
  }, [name]);

  useEffect(() => {
    if (!name) return;
    const seen = loadedFor.current;
    if (!seen || seen.name !== name || seen.phase !== phase) void loader.load("load");
  }, [name, phase, loader]);

  return { ...state, reload: () => void loader.load("load") };
}
