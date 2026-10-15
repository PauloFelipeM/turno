import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type ThemeChoice = "light" | "dark" | "system";
const KEY = "turno.theme";

function stored(): ThemeChoice {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

function systemDark() {
  return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

interface Theme {
  choice: ThemeChoice;
  resolved: "light" | "dark";
  setChoice: (c: ThemeChoice) => void;
}

const Ctx = createContext<Theme | null>(null);

/** Applies the `dark` class on <html> before the first paint (called from main.tsx). */
export function applyStoredTheme() {
  const c = stored();
  document.documentElement.classList.toggle("dark", c === "dark" || (c === "system" && systemDark()));
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoiceState] = useState<ThemeChoice>(stored);
  const [dark, setDark] = useState(systemDark);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const on = () => setDark(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  const resolved: "light" | "dark" = choice === "system" ? (dark ? "dark" : "light") : choice;
  useEffect(() => {
    document.documentElement.classList.toggle("dark", resolved === "dark");
  }, [resolved]);
  const value = useMemo<Theme>(
    () => ({
      choice,
      resolved,
      setChoice: (c) => {
        setChoiceState(c);
        try {
          if (c === "system") localStorage.removeItem(KEY);
          else localStorage.setItem(KEY, c);
        } catch {
          /* private window: the choice lasts for this page only */
        }
      },
    }),
    [choice, resolved],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTheme(): Theme {
  const v = useContext(Ctx);
  if (!v) throw new Error("useTheme outside ThemeProvider");
  return v;
}
