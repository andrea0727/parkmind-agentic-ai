import { createContext, useContext } from "react";
import type { Controller } from "./controller";
import type { Lang, LocFn, TFn } from "./i18n";

export interface Ctx {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: TFn;
  loc: LocFn;
  c: Controller;
}

export const AppCtx = createContext<Ctx | null>(null);

export function useApp(): Ctx {
  const v = useContext(AppCtx);
  if (!v) throw new Error("AppCtx missing");
  return v;
}
