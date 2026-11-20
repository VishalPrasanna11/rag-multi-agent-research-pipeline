import * as Tooltip from "@radix-ui/react-tooltip";
import { Fragment, createContext, useContext, type ReactNode } from "react";
import { factTooltip, fmtDate } from "../lib/format";
import type { Citation, Fact } from "../lib/types";

interface Ctx { facts: Record<string, Fact>; citations: Record<string, Citation>; openSource?: (citationId: string, factKey?: string) => void }
export const ReportCtx = createContext<Ctx>({ facts: {}, citations: {} });

const TOKEN = /\[\[fact:([a-z0-9_.-]+)\|([^\]]*)\]\]/g;

/** A cited number: keyboard-focusable; hover/focus shows source + as-of; click/Enter opens the Sources panel. */
export function FactChip({ factKey, shown }: { factKey: string; shown: string }) {
  const { facts, citations, openSource } = useContext(ReportCtx);
  const f = facts[factKey];
  if (!f) return <span>{shown}</span>;
  const c = citations[f.citation_id];
  const na = f.status !== "ok";
  return (
    <Tooltip.Root delayDuration={150}>
      <Tooltip.Trigger asChild>
        <button type="button" className={na ? "fact fact-na" : "fact"} aria-label={factTooltip(f, c?.title)}
          onClick={() => openSource?.(f.citation_id, factKey)} data-fact={factKey}>
          {shown}
        </button>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content side="top" sideOffset={6} className="z-50 max-w-xs rounded-lg border border-border bg-surface p-3 text-xs shadow-lg">
          <p className="font-semibold">{f.label}</p>
          <p className="tabular mt-1 text-sm">{na ? `Not available — needs ${f.needs}` : shown}</p>
          {c && <p className="mt-1 text-muted">Source: {c.title}</p>}
          <p className="text-muted">As of {fmtDate(f.as_of)}{f.period ? ` · ${f.period}` : ""}</p>
          {f.formula && <p className="mt-1 text-muted">Computed: {f.formula}</p>}
          {c?.license_note && <p className="mt-1 text-muted italic">{c.license_note}</p>}
          <Tooltip.Arrow className="fill-[var(--border)]" />
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

function inline(text: string, keyBase: string): ReactNode[] {
  // fact tokens first, then **bold**, *italic*, [link](url)
  const out: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  TOKEN.lastIndex = 0;
  let i = 0;
  while ((m = TOKEN.exec(text))) {
    if (m.index > last) out.push(...emph(text.slice(last, m.index), `${keyBase}-t${i}`));
    out.push(<FactChip key={`${keyBase}-f${i}`} factKey={m[1]!} shown={m[2]!} />);
    last = m.index + m[0].length;
    i++;
  }
  if (last < text.length) out.push(...emph(text.slice(last), `${keyBase}-t${i}`));
  return out;
}

function emph(s: string, k: string): ReactNode[] {
  const parts = s.split(/(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|\[[^\]]+\]\(https?:\/\/[^)\s]+\))/g);
  return parts.map((p, j) => {
    if (p.startsWith("**") && p.endsWith("**")) return <strong key={`${k}${j}`}>{p.slice(2, -2)}</strong>;
    if (p.startsWith("*") && p.endsWith("*") && p.length > 2) return <em key={`${k}${j}`}>{p.slice(1, -1)}</em>;
    const link = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(p);
    if (link) return <a key={`${k}${j}`} href={link[2]} target="_blank" rel="noreferrer noopener" className="text-brand underline">{link[1]}</a>;
    return <Fragment key={`${k}${j}`}>{p}</Fragment>;
  });
}

/** Minimal, safe markdown (no HTML injection): paragraphs, bullet/numbered lists, emphasis, links, fact tokens. */
export function Markdown({ text, className }: { text: string; className?: string }) {
  const blocks: ReactNode[] = [];
  const lines = text.split("\n");
  let list: { ordered: boolean; items: string[] } | null = null;
  let para: string[] = [];
  const flushPara = () => { if (para.length) { blocks.push(<p key={`p${blocks.length}`}>{inline(para.join(" "), `p${blocks.length}`)}</p>); para = []; } };
  const flushList = () => {
    if (list) {
      const items = list.items.map((it, j) => <li key={j}>{inline(it, `l${blocks.length}-${j}`)}</li>);
      blocks.push(list.ordered ? <ol key={`o${blocks.length}`}>{items}</ol> : <ul key={`u${blocks.length}`}>{items}</ul>);
      list = null;
    }
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    const b = /^\s*[-*•]\s+(.*)$/.exec(line);
    const n = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (b || n) {
      flushPara();
      const ordered = !!n;
      if (!list || list.ordered !== ordered) { flushList(); list = { ordered, items: [] }; }
      list.items.push((b ?? n)![1]!);
    } else if (!line.trim()) { flushPara(); flushList(); }
    else if (/^#{1,6}\s/.test(line)) { flushPara(); flushList(); blocks.push(<h3 key={`h${blocks.length}`} className="mt-3 font-semibold">{inline(line.replace(/^#+\s/, ""), `h${blocks.length}`)}</h3>); }
    else { flushList(); para.push(line.trim()); }
  }
  flushPara(); flushList();
  return <div className={className ?? "prose-note"}>{blocks}</div>;
}

export function Inline({ text }: { text: string }) {
  return <>{inline(text, "i")}</>;
}
