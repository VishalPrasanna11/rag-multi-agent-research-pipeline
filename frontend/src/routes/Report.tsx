import * as Tabs from "@radix-ui/react-tabs";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bookmark, Download, Link2, Printer, ThumbsDown, ThumbsUp } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { Link, useParams } from "react-router";
import { toast } from "sonner";
import { Inline, Markdown, ReportCtx } from "../components/Markdown";
import { Badge, Button, Card, ErrorPanel, Skeleton, statusTone } from "../components/ui";
import { api, post } from "../lib/api";
import { fmtDate, fmtDateTime, fmtDay, modeLabel } from "../lib/format";
import type { Report } from "../lib/types";

export function ReportPage({ shared = false }: { shared?: boolean }) {
  const { reportId = "", token = "" } = useParams();
  const q = useQuery({
    queryKey: shared ? ["share", token] : ["report", reportId],
    queryFn: () => api<Report>(shared ? `/share/${token}` : `/reports/${reportId}`),
  });
  if (q.isLoading) return <div className="flex flex-col gap-4"><Skeleton className="h-10 w-64" /><Skeleton className="h-64" /><Skeleton className="h-96" /></div>;
  if (q.error) return <ErrorPanel error={q.error} onRetry={() => q.refetch()} />;
  return <ReportView report={q.data!} shared={shared} />;
}

export function ReportView({ report: r, shared }: { report: Report; shared: boolean }) {
  const [tab, setTab] = useState("report");
  const [highlight, setHighlight] = useState<string | null>(null);
  const openSource = useCallback((cid: string) => {
    setTab("sources");
    setHighlight(cid);
    setTimeout(() => { const el = document.getElementById(`src-${cid}`); el?.scrollIntoView({ block: "center" }); el?.focus(); }, 60);
  }, []);
  const ctx = useMemo(() => ({ facts: r.facts, citations: r.citations, openSource }), [r, openSource]);
  const b = r.brief;
  const ev = r.event;

  return (
    <ReportCtx.Provider value={ctx}>
      <article className="flex flex-col gap-6" aria-labelledby="report-title">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="text-sm text-muted">{modeLabel[r.mode]} · as of {fmtDate(r.as_of)} {shared && "· shared read-only view"}</p>
            <h1 id="report-title" className="text-2xl font-semibold">
              <Link to={`/company/${r.ticker}`} className="hover:underline">{r.ticker}</Link>
              {ev.company && <span className="ml-2 text-base font-normal text-muted">{ev.company}</span>}
            </h1>
            <div className="mt-2 flex flex-wrap gap-2">
              <Badge tone={statusTone(ev.date_status)}>Earnings {fmtDay(ev.date)} · {ev.time_of_day.toUpperCase()} · {ev.date_status}</Badge>
              <Badge tone={statusTone(r.review_status)}>Review: {r.review_status}</Badge>
              {r.fixture && <Badge tone="warn">Demo data (fixtures)</Badge>}
              {b && <Badge tone={statusTone(b.confidence.level)} title={b.confidence.explanation}>Confidence: {b.confidence.level}</Badge>}
            </div>
          </div>
          {!shared && <ReportActions r={r} />}
        </header>

        {r.notices?.map((n) => <p key={n} role="note" className="rounded-xl border border-brand bg-surface p-3 text-sm">{n}</p>)}
        {b && <DecisionBrief r={r} />}

        <Tabs.Root value={tab} onValueChange={setTab}>
          <Tabs.List aria-label="Report sections" className="no-print flex flex-wrap gap-1 border-b border-border">
            {[["report", "Full report"], ["timeline", "Agents"], ["critique", `Critique (${r.critique_history.length})`], ["sources", `Sources (${Object.values(r.citations).filter((c) => c.title !== "Not available").length})`], ...(!shared ? [["review", "Review & audit"]] : [])].map(([v, l]) => (
              <Tabs.Trigger key={v} value={v!} className="min-h-10 rounded-t-lg px-3 text-sm text-muted data-[state=active]:border-b-2 data-[state=active]:border-brand data-[state=active]:font-medium data-[state=active]:text-text">{l}</Tabs.Trigger>
            ))}
          </Tabs.List>
          <Tabs.Content value="report" className="mt-4 flex flex-col gap-4">
            {r.agents.flatMap((a) => a.sections.map((s) => (
              <Card key={`${a.agent}-${s.id}`} id={`sec-${s.id}`} title={s.title} actions={!shared && <SectionFeedback reportId={r.id} sectionId={s.id} />}>
                <Markdown text={s.markdown} />
              </Card>
            )))}
          </Tabs.Content>
          <Tabs.Content value="timeline" className="mt-4">
            <Card title="Agent timeline">
              <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table">
                <table className="w-full text-sm">
                  <thead className="text-left text-xs text-muted"><tr><th scope="col" className="py-2">Agent</th><th scope="col">Status</th><th scope="col" className="text-right">Time</th><th scope="col" className="text-right">Facts cited</th></tr></thead>
                  <tbody>{r.agents.map((a) => (
                    <tr key={a.agent} className="border-t border-border" style={{ height: "var(--row)" }}>
                      <th scope="row" className="text-left font-medium">{a.agent.replace("_", " ")}</th>
                      <td><Badge tone={statusTone(a.status === "ok" ? "completed" : a.status)}>{a.status}</Badge></td>
                      <td className="num text-right">{(a.timing_ms / 1000).toFixed(1)}s</td>
                      <td className="num text-right">{a.facts.length}</td>
                    </tr>))}</tbody>
                </table>
              </div>
              <p className="mt-3 text-sm text-muted">Model cost for this run: ${r.cost_usd.toFixed(3)}</p>
            </Card>
          </Tabs.Content>
          <Tabs.Content value="critique" className="mt-4">
            <Card title="Critique history">
              {!r.critique_history.length ? <p className="text-muted">No critique rounds recorded.</p> : (
                <ol className="flex flex-col gap-4">
                  {r.critique_history.map((c) => (
                    <li key={c.round}>
                      <p className="font-medium">Round {c.round} · <Badge tone={c.action === "accepted" ? "ok" : "warn"}>{c.action}</Badge></p>
                      {!c.issues.length ? <p className="text-sm text-muted">No issues found.</p> : (
                        <ul className="mt-1 list-disc pl-5 text-sm">
                          {c.issues.map((i, k) => <li key={k}><Badge tone={i.severity === "high" ? "danger" : "warn"}>{i.severity}</Badge> <span className="text-muted">{i.kind}{i.agent ? ` · ${i.agent}` : ""}:</span> {i.detail}</li>)}
                        </ul>
                      )}
                    </li>
                  ))}
                </ol>
              )}
            </Card>
          </Tabs.Content>
          <Tabs.Content value="sources" className="mt-4">
            <Card title="Sources">
              <ul className="flex flex-col gap-2 text-sm">
                {Object.values(r.citations).filter((c) => c.title !== "Not available").sort((a, b) => (a.source === "derived" ? 1 : 0) - (b.source === "derived" ? 1 : 0) || a.source.localeCompare(b.source)).map((c) => (
                  <li key={c.id} id={`src-${c.id}`} tabIndex={-1} className={`rounded-lg p-2 [overflow-wrap:anywhere] ${highlight === c.id ? "bg-surface-2 ring-2 ring-brand" : ""}`}>
                    <p className="font-medium">{c.url ? <a href={c.url} target="_blank" rel="noreferrer noopener" className="text-brand underline">{c.title}</a> : c.title}</p>
                    <p className="text-xs text-muted">{c.source} · as of {fmtDate(c.as_of)}{c.locator ? ` · ${c.locator.slice(0, 160)}` : ""}</p>
                    {c.license_note && <p className="text-xs italic text-muted">{c.license_note}</p>}
                  </li>
                ))}
              </ul>
            </Card>
          </Tabs.Content>
          {!shared && <Tabs.Content value="review" className="mt-4"><ReviewPanel r={r} /></Tabs.Content>}
        </Tabs.Root>
        <p className="rounded-lg border border-border p-3 text-xs text-muted">{r.disclaimer}</p>
      </article>
    </ReportCtx.Provider>
  );
}

function DecisionBrief({ r }: { r: Report }) {
  const b = r.brief!;
  return (
    <Card id="brief" title="Decision brief">
      <p className="text-lg font-semibold"><Inline text={b.headline} /></p>
      <div className="mt-4 grid gap-6 lg:grid-cols-2">
        <div>
          <h3 className="text-sm font-semibold text-muted">The setup</h3>
          <ul className="prose-note mt-1 list-disc pl-5">{b.setup.map((s, i) => <li key={i}><Inline text={s} /></li>)}</ul>
          <h3 className="mt-4 text-sm font-semibold text-muted">Confidence: {b.confidence.level}</h3>
          <p className="text-sm">{b.confidence.explanation}</p>
          {!!b.confidence.drivers.length && <ul className="mt-1 list-disc pl-5 text-sm text-muted">{b.confidence.drivers.map((d) => <li key={d}>{d}</li>)}</ul>}
        </div>
        <div>
          <h3 className="text-sm font-semibold text-muted">Scenarios</h3>
          <div className="mt-1 overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table">
            <table className="w-full text-sm">
              <caption className="sr-only">Bull, base and bear scenarios with base-rate probabilities</caption>
              <thead className="text-left text-xs text-muted"><tr><th scope="col" className="py-1 pr-2">Case</th><th scope="col" className="pr-2 text-right">Prob.</th><th scope="col" className="pr-2">Range</th><th scope="col">What has to be true</th></tr></thead>
              <tbody>
                {b.scenarios.map((s) => (
                  <tr key={s.name} className="border-t border-border align-top">
                    <th scope="row" className="py-2 pr-2 text-left capitalize">{s.name === "bull" ? "▲ " : s.name === "bear" ? "▼ " : "■ "}{s.name}</th>
                    <td className="num py-2 pr-2 text-right" title={`${s.probability_method}${s.base_rate_n ? `, n=${s.base_rate_n}` : ""}`}>{(s.probability * 100).toFixed(0)}%</td>
                    <td className="num whitespace-nowrap py-2 pr-2">{s.move_range_pct ? `${s.move_range_pct[0]}% to ${s.move_range_pct[1]}%` : "—"}</td>
                    <td className="py-2"><Inline text={s.narrative} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="mt-1 text-xs text-muted">
            Probabilities: {b.scenarios[0]?.probability_method === "base_rate" ? `this stock's past ${r.event.date_status === "reported" ? "post-earnings drift" : "day-1 reactions"}, shrunk toward 25/50/25 (n=${b.scenarios[0]?.base_rate_n})` : "heuristic"}.
            {b.validated ? " Supported by the back-test." : " Unvalidated: not yet supported by the back-test."}
          </p>
        </div>
      </div>
      <div className="mt-6 grid gap-6 md:grid-cols-3">
        <ListBlock title="What would change the view" items={b.what_would_change_view} />
        <ListBlock title="Questions for the call" items={b.questions_for_call} />
        <ListBlock title="Key risks" items={b.key_risks} />
      </div>
      <details className="mt-6">
        <summary className="cursor-pointer text-sm font-semibold">Unknowns and data gaps ({b.unknowns.length})</summary>
        <ul className="mt-2 list-disc pl-5 text-sm text-muted">{b.unknowns.map((u, i) => <li key={i}>{u}</li>)}</ul>
      </details>
      <details className="mt-2">
        <summary className="cursor-pointer text-sm font-semibold">Data freshness</summary>
        <ul className="mt-2 text-sm text-muted">{b.freshness.map((f) => <li key={f.source}>{f.source}: as of {fmtDateTime(f.as_of)}</li>)}</ul>
      </details>
    </Card>
  );
}

function ListBlock({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-muted">{title}</h3>
      <ul className="prose-note mt-1 list-disc pl-5 text-sm">{items.map((s, i) => <li key={i}><Inline text={s} /></li>)}</ul>
    </div>
  );
}

function ReportActions({ r }: { r: Report }) {
  const qc = useQueryClient();
  const save = useMutation({ mutationFn: () => post(`/reports/${r.id}/save?saved=${!r.saved}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["report", r.id] }) });
  const share = useMutation({
    mutationFn: () => post<{ url: string }>(`/reports/${r.id}/share`),
    onSuccess: async (s) => { const url = `${location.origin}${s.url}`; try { await navigator.clipboard.writeText(url); toast.success("Share link copied (read-only, expires in 30 days)"); } catch { toast(url); } },
  });
  return (
    <div className="no-print flex flex-wrap gap-2">
      <Button onClick={() => save.mutate()} aria-pressed={!!r.saved}><Bookmark size={16} aria-hidden /> {r.saved ? "Saved" : "Save"}</Button>
      <a href={`/api/v1/reports/${r.id}/export.md`} className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-border px-3 text-sm hover:bg-surface-2"><Download size={16} aria-hidden /> Markdown</a>
      <Button onClick={() => window.print()}><Printer size={16} aria-hidden /> PDF</Button>
      <Button onClick={() => share.mutate()}><Link2 size={16} aria-hidden /> Share</Button>
    </div>
  );
}

const REASONS = ["Wrong number", "Missing context", "Not useful", "Too long", "Unclear"];
function SectionFeedback({ reportId, sectionId }: { reportId: string; sectionId: string }) {
  const [sent, setSent] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);
  const send = useMutation({ mutationFn: (b: any) => post(`/reports/${reportId}/feedback`, b), onSuccess: (_d, b) => { setSent(b.rating); setAsking(false); toast.success("Thanks for the feedback"); } });
  if (sent) return <span className="text-xs text-muted">Feedback recorded</span>;
  return (
    <div className="no-print flex flex-wrap items-center gap-1">
      <Button variant="ghost" className="min-h-8" aria-label="Helpful" onClick={() => send.mutate({ rating: "up", section_id: sectionId })}><ThumbsUp size={14} aria-hidden /></Button>
      <Button variant="ghost" className="min-h-8" aria-label="Not helpful" onClick={() => setAsking((a) => !a)} aria-expanded={asking}><ThumbsDown size={14} aria-hidden /></Button>
      {asking && REASONS.map((x) => <Button key={x} className="min-h-7 text-xs" onClick={() => send.mutate({ rating: "down", reasons: [x], section_id: sectionId })}>{x}</Button>)}
    </div>
  );
}

function ReviewPanel({ r }: { r: Report }) {
  const qc = useQueryClient();
  const [notes, setNotes] = useState("");
  const audit = useQuery({ queryKey: ["audit", r.id], queryFn: () => api<any[]>(`/reports/${r.id}/audit`) });
  const review = useMutation({
    mutationFn: (decision: string) => post(`/reports/${r.id}/review`, { decision, notes: notes || undefined }),
    onSuccess: () => { setNotes(""); qc.invalidateQueries({ queryKey: ["report", r.id] }); qc.invalidateQueries({ queryKey: ["audit", r.id] }); toast.success("Review recorded"); },
  });
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Review">
        <p className="text-sm">Current status: <Badge tone={statusTone(r.review_status)}>{r.review_status}</Badge></p>
        <label className="mt-3 flex flex-col gap-1 text-sm">Notes <span className="text-xs text-muted">(required to reject)</span>
          <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} className="rounded-lg border border-border bg-surface px-3 py-2" maxLength={4000} />
        </label>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button variant="primary" onClick={() => review.mutate("approved")} disabled={review.isPending}>Approve</Button>
          <Button onClick={() => review.mutate("changes_requested")} disabled={review.isPending}>Request changes</Button>
          <Button variant="danger" onClick={() => review.mutate("rejected")} disabled={review.isPending || !notes.trim()}>Reject</Button>
        </div>
        {review.error && <div className="mt-3"><ErrorPanel error={review.error} /></div>}
        {!!r.reviews?.length && (
          <ul className="mt-4 flex flex-col gap-2 text-sm">
            {r.reviews.map((v, i) => <li key={i}><Badge tone={statusTone(v.decision)}>{v.decision}</Badge> {fmtDateTime(v.created_at)}{v.notes ? ` — ${v.notes}` : ""}</li>)}
          </ul>
        )}
      </Card>
      <Card title="Audit trail">
        {audit.isLoading ? <Skeleton className="h-24" /> : (
          <ol className="flex flex-col gap-2 text-sm">
            {audit.data?.map((a, i) => (
              <li key={i} className="border-l-2 border-border pl-3">
                <p className="font-medium">{a.action}</p>
                <p className="text-xs text-muted">{fmtDateTime(a.at)} · {a.actor.startsWith("anon:") ? "you (anonymous)" : a.actor}{a.detail?.notes ? ` · “${a.detail.notes}”` : ""}</p>
              </li>
            ))}
          </ol>
        )}
      </Card>
    </div>
  );
}
