import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
import type { Meta } from "./types";

export const useMeta = () => useQuery({ queryKey: ["meta"], queryFn: () => api<Meta>("/meta"), staleTime: 300_000 });

export function applyTheme(theme: string) {
  const dark = theme === "dark" || (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  try { localStorage.setItem("ei-theme", theme); } catch { /* storage unavailable */ }
}
export function applyDensity(d: string) {
  document.documentElement.dataset.density = d;
  try { localStorage.setItem("ei-density", d); } catch { /* storage unavailable */ }
}
export const storedTheme = () => { try { return localStorage.getItem("ei-theme") || "system"; } catch { return "system"; } };
