import type { Fact } from "./types";

const NY = "America/New_York";

export function fmtValue(value: number | string | null, unit: string): string {
  if (value === null || value === undefined) return "n/a";
  if (typeof value === "string") return value;
  const v = value;
  const abs = Math.abs(v);
  switch (unit) {
    case "USD": return abs >= 1e9 ? `$${(v / 1e9).toFixed(2)}B` : abs >= 1e6 ? `$${(v / 1e6).toFixed(1)}M` : `$${v.toLocaleString()}`;
    case "USD/share": return `$${v.toFixed(2)}`;
    case "%": return `${v >= 0 ? "+" : "−"}${Math.abs(v).toFixed(1)}%`;
    case "pct": return `${v.toFixed(1)}%`;
    case "x": return `${v.toFixed(1)}x`;
    case "days": return `${v.toFixed(0)} days`;
    case "count": return v.toLocaleString();
    case "prob": return `${(v * 100).toFixed(0)}%`;
    default: return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
}

export const fmtDate = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleDateString("en-US", { timeZone: NY, year: "numeric", month: "short", day: "numeric" }) : "—";
export const fmtDateTime = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString("en-US", { timeZone: NY, month: "short", day: "numeric", hour: "numeric", minute: "2-digit", timeZoneName: "short" }) : "—";
export const fmtDay = (d?: string | null) => (d ? fmtDate(`${d}T12:00:00Z`) : "—");

export function signClass(v: number | null | undefined) {
  if (v === null || v === undefined || Number.isNaN(v)) return "";
  return v > 0 ? "text-up" : v < 0 ? "text-down" : "";
}
export function arrow(v: number | null | undefined) {
  if (v === null || v === undefined) return "";
  return v > 0 ? "▲" : v < 0 ? "▼" : "■";
}
export function signed(v: number | null | undefined, digits = 1) {
  if (v === null || v === undefined) return "n/a";
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(digits)}%`;
}

export function factTooltip(f: Fact, sourceTitle?: string): string {
  const parts = [`${f.label}: ${fmtValue(f.value, f.unit)}`];
  if (f.status === "unavailable") parts.push(`Not available — needs ${f.needs}`);
  if (sourceTitle) parts.push(`Source: ${sourceTitle}`);
  parts.push(`As of ${fmtDate(f.as_of)}`);
  if (f.formula) parts.push(`Computed: ${f.formula}`);
  return parts.join(" · ");
}

export const modeLabel: Record<string, string> = {
  full_cycle: "Full cycle", pre_only: "Pre-earnings", post_only: "Post-earnings", question: "Question",
};
