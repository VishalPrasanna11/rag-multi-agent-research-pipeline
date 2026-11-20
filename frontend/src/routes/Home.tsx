import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarArrowDown, Play, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import { toast } from "sonner";
import { Badge, Button, Card, Empty, ErrorPanel, Skeleton, statusTone } from "../components/ui";
import { api, HttpError, post } from "../lib/api";
import { fmtDate, fmtDay, modeLabel } from "../lib/format";
import type { Mode } from "../lib/types";

interface WItem { ticker: string; auto_run: boolean; next_event?: { date?: string; time?: string | null; status?: string;
  days_until?: number; eps_est?: number | null; last_reported?: string } | null }
interface RSummary { id: string; ticker: string; mode: Mode; review_status: string; created_at: string; headline: string;
  fixture: boolean; confidence?: string | null; event?: { date?: string; date_status?: string } }

export function Home() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const wl = useQuery({ queryKey: ["watchlist"], queryFn: () => api<WItem[]>("/watchlist") });
  const reports = useQuery({ queryKey: ["reports", "recent"], queryFn: () => api<RSummary[]>("/reports?limit=8") });
  const [ticker, setTicker] = useState("");
  const add = useMutation({
    mutationFn: (t: string) => post("/watchlist", { ticker: t }),
    onSuccess: () => { setTicker(""); qc.invalidateQueries({ queryKey: ["watchlist"] }); },
    onError: (e) => toast.error(e instanceof HttpError ? e.error.message : "Could not add ticker"),
  });
  const remove = useMutation({
    mutationFn: (t: string) => api(`/watchlist/${t}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["watchlist"] }),
  });
  const sorted = [...(wl.data ?? [])].sort((a, b) => (a.next_event?.date ?? "9999").localeCompare(b.next_event?.date ?? "9999"));

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
      <div className="flex flex-col gap-6">
        <div>
          <h1 className="text-2xl font-semibold">Watchlist</h1>
          <p className="text-muted">Upcoming earnings for the tickers you follow. Run a grounded brief in one click.</p>
        </div>
        <Card id="watchlist" title="Upcoming earnings" actions={
          <a href="/api/v1/calendar.ics" className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-border px-3 text-sm hover:bg-surface-2">
            <CalendarArrowDown size={16} aria-hidden /> Export .ics
          </a>}>
          <form className="mb-4 flex gap-2" onSubmit={(e) => { e.preventDefault(); if (ticker.trim()) add.mutate(ticker.trim()); }}>
            <label htmlFor="add-ticker" className="sr-only">Ticker to add</label>
            <input id="add-ticker" value={ticker} onChange={(e) => setTicker(e.target.value.toUpperCase())} placeholder="Add ticker, e.g. NVDA"
              className="min-h-9 w-full max-w-xs rounded-lg border border-border bg-surface px-3" maxLength={10} autoComplete="off" />
            <Button type="submit" disabled={add.isPending}><Plus size={16} aria-hidden /> Add</Button>
          </form>
          {wl.isLoading ? <div className="flex flex-col gap-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-10" />)}</div>
            : wl.error ? <ErrorPanel error={wl.error} onRetry={() => wl.refetch()} />
            : !sorted.length ? <Empty>Your watchlist is empty. Add a ticker to see its next earnings date, or run research directly from the command palette (⌘K).</Empty>
            : (
              <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table">
                <table className="w-full text-sm">
                  <caption className="sr-only">Watchlist with next earnings dates</caption>
                  <thead className="text-left text-xs text-muted">
                    <tr><th scope="col" className="py-2 pr-3">Ticker</th><th scope="col" className="pr-3">Next earnings</th><th scope="col" className="pr-3">Status</th><th scope="col" className="pr-3 text-right">In</th><th scope="col" className="pr-3 text-right">EPS est.</th><th scope="col"><span className="sr-only">Actions</span></th></tr>
                  </thead>
                  <tbody>
                    {sorted.map((w) => {
                      const ev = w.next_event;
                      return (
                        <tr key={w.ticker} className="border-t border-border" style={{ height: "var(--row)" }}>
                          <th scope="row" className="pr-3 text-left font-semibold"><Link to={`/company/${w.ticker}`} className="hover:underline">{w.ticker}</Link></th>
                          <td className="pr-3">{ev?.date ? <>{fmtDay(ev.date)} <span className="text-muted">{(ev.time ?? "time tbc").toUpperCase()}</span></> : <span className="text-muted">No date found</span>}</td>
                          <td className="pr-3">{ev?.date && <Badge tone={statusTone(ev.status)} title="Estimated until the company confirms">{ev.status}</Badge>}</td>
                          <td className="num pr-3 text-right">{ev?.days_until !== undefined ? `${ev.days_until}d` : "—"}</td>
                          <td className="num pr-3 text-right">{ev?.eps_est != null ? `$${ev.eps_est.toFixed(2)}` : "—"}</td>
                          <td className="whitespace-nowrap text-right">
                            <Button variant="primary" className="min-h-8" onClick={() => navigate(`/new?ticker=${w.ticker}&mode=pre_only`)} aria-label={`Run pre-earnings brief for ${w.ticker}`}>
                              <Play size={14} aria-hidden /> Brief
                            </Button>
                            <Button variant="ghost" className="ml-1 min-h-8" onClick={() => remove.mutate(w.ticker)} aria-label={`Remove ${w.ticker}`}>
                              <Trash2 size={14} aria-hidden />
                            </Button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
        </Card>
      </div>
      <aside className="flex flex-col gap-6">
        <NewRunCard />
        <Card id="recent" title="Recent reports" actions={<Link to="/history" className="text-sm text-brand underline">All</Link>}>
          {reports.isLoading ? <Skeleton className="h-24" /> : !reports.data?.length ? <p className="text-muted">No reports yet.</p> : (
            <ul className="flex flex-col gap-3">
              {reports.data.map((r) => (
                <li key={r.id}>
                  <Link to={`/reports/${r.id}`} className="block rounded-lg p-2 hover:bg-surface-2">
                    <div className="flex items-center justify-between gap-2"><span className="font-semibold">{r.ticker}</span><Badge tone={statusTone(r.review_status)}>{r.review_status}</Badge></div>
                    <p className="line-clamp-2 text-sm">{r.headline || modeLabel[r.mode]}</p>
                    <p className="text-xs text-muted">{modeLabel[r.mode]} · {fmtDate(r.created_at)}{r.fixture ? " · demo data" : ""}</p>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </aside>
    </div>
  );
}

export function NewRunCard({ initialTicker = "", initialMode = "pre_only" as Mode }) {
  const navigate = useNavigate();
  const [ticker, setTicker] = useState(initialTicker);
  const [mode, setMode] = useState<Mode>(initialMode);
  const [query, setQuery] = useState("");
  const [asOf, setAsOf] = useState("");
  const [transcript, setTranscript] = useState("");
  const run = useMutation({
    mutationFn: () => post<{ run_id: string }>("/runs", { ticker, mode, query: query || undefined, as_of: asOf || undefined,
      transcript_text: transcript || undefined }),
    onSuccess: (r) => navigate(`/run/${r.run_id}`),
  });
  return (
    <Card id="new-run" title="New research">
      <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); run.mutate(); }}>
        <div className="grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-1 text-sm">Ticker
            <input required value={ticker} onChange={(e) => setTicker(e.target.value.toUpperCase())} className="min-h-9 rounded-lg border border-border bg-surface px-3" placeholder="NVDA" maxLength={10} />
          </label>
          <label className="flex flex-col gap-1 text-sm">Mode
            <select value={mode} onChange={(e) => setMode(e.target.value as Mode)} className="min-h-9 rounded-lg border border-border bg-surface px-2">
              {Object.entries(modeLabel).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </label>
        </div>
        <label className="flex flex-col gap-1 text-sm">Question <span className="text-xs text-muted">(optional)</span>
          <textarea value={query} onChange={(e) => setQuery(e.target.value)} rows={2} maxLength={2000} className="rounded-lg border border-border bg-surface px-3 py-2" placeholder="e.g. How much of EPS growth came from buybacks?" />
        </label>
        <details className="text-sm">
          <summary className="cursor-pointer text-muted">Advanced: as-of date, transcript</summary>
          <label className="mt-2 flex flex-col gap-1">As of (historical, no look-ahead)
            <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} className="min-h-9 rounded-lg border border-border bg-surface px-3" />
          </label>
          <label className="mt-2 flex flex-col gap-1">Earnings call transcript (paste; cited as your upload)
            <textarea value={transcript} onChange={(e) => setTranscript(e.target.value)} rows={3} className="rounded-lg border border-border bg-surface px-3 py-2" />
          </label>
        </details>
        {run.error && <ErrorPanel error={run.error} onRetry={() => run.mutate()} />}
        <Button variant="primary" type="submit" disabled={run.isPending || !ticker}><Play size={16} aria-hidden /> {run.isPending ? "Starting…" : "Run research"}</Button>
        <p className="text-xs text-muted">Research only, not investment advice.</p>
      </form>
    </Card>
  );
}

export function NewRunPage() {
  const params = new URLSearchParams(location.search);
  return (
    <div className="mx-auto max-w-lg">
      <NewRunCard initialTicker={params.get("ticker") ?? ""} initialMode={(params.get("mode") as Mode) ?? "pre_only"} />
    </div>
  );
}
