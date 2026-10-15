import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { ApiError } from "@/api";
import en from "./en";
import ptBR from "./pt-BR";

// The languages of turno/config.py's LANGUAGES; an unknown one falls back to English.
export const LANGS = ["pt-BR", "en"];
export const DEFAULT_LANG = "en";

type Dict = { [key: string]: string | Dict };
const BUNDLES: Record<string, Dict> = { "pt-BR": ptBR, en };

export type Vars = Record<string, string | number | null | undefined>;
export type TFunction = (path: string, vars?: Vars) => string;

export function pickLanguage(lang: unknown): string {
  return typeof lang === "string" && LANGS.includes(lang) ? lang : DEFAULT_LANG;
}

export function lookup(dict: Dict, path: string): string | undefined {
  let node: string | Dict | undefined = dict;
  for (const k of path.split(".")) {
    if (node == null || typeof node === "string") return undefined;
    node = node[k];
  }
  return typeof node === "string" ? node : undefined;
}

/** A text in a given language, outside React (e.g. a toast right after the language was switched). */
export function textIn(lang: string, path: string, vars?: Vars): string {
  return translate(BUNDLES[pickLanguage(lang)], path, vars);
}

function translate(dict: Dict, path: string, vars: Vars = {}): string {
  const raw = lookup(dict, path) ?? path;
  return raw.replace(/\{(\w+)\}/g, (_, k: string) => String(vars[k] ?? ""));
}

interface I18n {
  lang: string;
  t: TFunction;
  /** Whether `path` has a text (an error code or a check message the server may send). */
  has: (path: string) => boolean;
  setLang: (lang: unknown) => void;
  /** The text of a failed request: the text of its error code, else the server's message, else a generic one. */
  errorText: (e: unknown) => string;
}

const Ctx = createContext<I18n | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<string>(DEFAULT_LANG);
  const setLang = useCallback((l: unknown) => setLangState(pickLanguage(l)), []);
  const value = useMemo<I18n>(() => {
    const dict = BUNDLES[lang];
    const t: TFunction = (path, vars) => translate(dict, path, vars);
    const has = (path: string) => lookup(dict, path) !== undefined;
    const errorText = (e: unknown) => {
      if (e instanceof ApiError) {
        if (has(`errors.${e.code}`)) return t(`errors.${e.code}`);
        if (e.message) return e.message;
      }
      return t("requestError");
    };
    return { lang, t, has, setLang, errorText };
  }, [lang, setLang]);
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useI18n(): I18n {
  const v = useContext(Ctx);
  if (!v) throw new Error("useI18n outside I18nProvider");
  return v;
}
