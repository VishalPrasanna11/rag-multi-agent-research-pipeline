import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, Plus } from "lucide-react";
import { lazy, Suspense } from "react";
import { Link, useParams } from "react-router";
import { toast } from "sonner";
import { Badge, Button, Card, ErrorPanel, Skeleton } from "../components/ui";
import { api, HttpError, post } from "../lib/api";
import { arrow, fmtDay, signClass, signed } from "../lib/format";

const CandleChart = lazy(() => import("../components/Charts"));
const EstimatesChart = lazy(() => import("../components/Charts").then((m) => ({ default: m.EstimatesChart })));

export function CompanyPage() {
  const { ticker = "" } = useParams();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["company", ticker], queryFn: () => api<any>(`/companies/${ticker}`) });
  const add = useMutation({ mutationFn: () => post("/watchlist", { ticker }), onSuccess: () => { toast.success(`${ticker} added`); qc.invalidateQueries({ queryKey: ["watchlist"] }); },
    onError: (e) => toast.error(e instanceof HttpError ? e.error.message : "Failed") });
  if (q.isLoading) return <div className="flex flex-col gap-4"><Skeleton className="h-10 w-48" /><Skeleton className="h-80" /></div>;
  if (q.error) return <ErrorPanel error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data;
  if (d.error) return <ErrorPanel error={{ message: d.error.message }} />;
  const br = d.base_rates ?? {};
  const tr = d.track_record ?? {};
  const events = [...(d.events ?? [])].reverse();
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">{d.ticker} <span className="text-base font-normal text-muted">{d.company}</span></h1>
          <p className="text-sm text-muted">SEC CIK {d.cik}{d.fixture ? " · demo data" : ""}</p>
        </div>
        <div className="flex gap-2">
          <Button onClick={() => add.mutate()}><Plus size={16} aria-hidden /> Watch</Button>
          <Link to={`/new?ticker=${d.ticker}&mode=pre_only`} className="inline-flex min-h-9 items-center gap-1.5 rounded-lg bg-brand px-3 text-sm font-medium text-brand-fg"><Play size={16} aria-hidden /> Run brief</Link>
        </div>
      </header>
      <Card title="Price and earnings dates" actions={<span className="text-xs text-muted">{d.prices_source?.license}</span>}>
        {d.prices?.length ? <Suspense fallback={<Skeleton className="h-80" />}><CandleChart prices={d.prices} events={d.events} /></Suspense>
          : <p className="text-muted">Prices unavailable: {d.prices_unavailable}</p>}
      </Card>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="EPS: estimate vs actual">
          <Suspense fallback={<Skeleton className="h-64" />}><EstimatesChart events={d.events ?? []} /></Suspense>
        </Card>
        <Card title="Base rates (this stock)">
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <div><dt className="text-muted">Events measured</dt><dd className="num text-lg">{br.n ?? 0}</dd></div>
            <div><dt className="text-muted">Up on day 1</dt><dd className="num text-lg">{br.p_up != null ? `${(br.p_up * 100).toFixed(0)}%` : "n/a"}</dd></div>
            <div><dt className="text-muted">Avg |day-1 move|</dt><dd className="num text-lg">{br.mean_abs_move != null ? `${br.mean_abs_move.toFixed(1)}%` : "n/a"}</dd></div>
            <div><dt className="text-muted">EPS beat rate</dt><dd className="num text-lg">{br.beat_rate != null ? `${(br.beat_rate * 100).toFixed(0)}%` : "n/a"}</dd></div>
            <div><dt className="text-muted">Up after beats</dt><dd className="num text-lg">{br.p_up_given_beat != null ? `${(br.p_up_given_beat * 100).toFixed(0)}% (n=${br.n_beat})` : "n/a"}</dd></div>
            <div><dt className="text-muted">Avg drift day 1→5</dt><dd className={`num text-lg ${signClass(br.mean_drift_5d)}`}>{arrow(br.mean_drift_5d)} {signed(br.mean_drift_5d)}</dd></div>
          </dl>
          <p className="mt-3 text-xs text-muted">Computed from split-adjusted prices and provider EPS history. Reaction windows: last close before the release to first close after (BMO/AMC aware).</p>
        </Card>
      </div>
      <Card title="Earnings reactions">
        <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table">
          <table className="w-full min-w-[640px] text-sm">
            <caption className="sr-only">Past earnings: EPS surprise and price reaction</caption>
            <thead className="text-left text-xs text-muted"><tr>
              <th scope="col" className="py-2">Date</th><th scope="col">Time</th><th scope="col" className="text-right">EPS est.</th><th scope="col" className="text-right">EPS actual</th>
              <th scope="col" className="text-right">Surprise</th><th scope="col" className="text-right">Gap</th><th scope="col" className="text-right">Day 1</th><th scope="col" className="text-right">Day 5</th><th scope="col" className="pl-3">Pattern</th></tr></thead>
            <tbody>
              {events.map((e: any) => (
                <tr key={e.date} className="border-t border-border" style={{ height: "var(--row)" }}>
                  <th scope="row" className="text-left font-normal">{fmtDay(e.date)}</th>
                  <td className="text-muted">{(e.time ?? "—").toUpperCase()}</td>
                  <td className="num text-right">{e.eps_est != null ? `$${e.eps_est.toFixed(2)}` : "—"}</td>
                  <td className="num text-right">{e.eps_act != null ? `$${e.eps_act.toFixed(2)}` : <Badge tone="warn">upcoming</Badge>}</td>
                  <td className={`num text-right ${signClass(e.surprise_pct)}`}>{e.surprise_pct != null ? `${arrow(e.surprise_pct)} ${signed(e.surprise_pct)}` : "—"}</td>
                  <td className={`num text-right ${signClass(e.gap_pct)}`}>{e.gap_pct != null ? signed(e.gap_pct) : "—"}</td>
                  <td className={`num text-right ${signClass(e.day1_pct)}`}>{e.day1_pct != null ? `${arrow(e.day1_pct)} ${signed(e.day1_pct)}` : "—"}</td>
                  <td className={`num text-right ${signClass(e.day5_pct)}`}>{e.day5_pct != null ? signed(e.day5_pct) : "—"}</td>
                  <td className="pl-3">{e.pattern ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-xs text-muted">Implied moves are available only from live option snapshots taken before each event; see a pre-earnings brief for the current implied move.</p>
      </Card>
      <TrackRecord tr={tr} />
    </div>
  );
}

export function TrackRecord({ tr }: { tr: any }) {
  const sources = Object.entries(tr.by_source ?? {}) as [string, any][];
  return (
    <Card title="Track record" actions={<Badge tone="muted">{tr.pending ?? 0} forecasts pending</Badge>}>
      {!sources.length ? <p className="text-muted">No scored forecasts yet. Every pre-earnings brief stores a base-rate forecast (P(up on day 1)), which is scored after the print.</p> : (
        <div className="grid gap-4 md:grid-cols-2">
          {sources.map(([src, s]) => (
            <div key={src}>
              <h3 className="font-semibold capitalize">{src === "backtest" ? "Back-test" : "Live"} (n={s.n})</h3>
              <dl className="mt-2 grid grid-cols-3 gap-2 text-sm">
                <div><dt className="text-muted">Hit rate</dt><dd className="num">{(s.hit_rate * 100).toFixed(0)}%</dd></div>
                <div><dt className="text-muted">Brier</dt><dd className="num">{s.brier.toFixed(3)}</dd></div>
                <div><dt className="text-muted">Baseline Brier</dt><dd className="num">{s.brier_baseline.toFixed(3)}</dd></div>
              </dl>
              <table className="mt-2 w-full text-sm">
                <caption className="sr-only">Calibration by probability bin</caption>
                <thead className="text-left text-xs text-muted"><tr><th scope="col">Bin</th><th scope="col" className="text-right">n</th><th scope="col" className="text-right">Predicted</th><th scope="col" className="text-right">Observed</th></tr></thead>
                <tbody>{s.calibration.map((c: any) => (
                  <tr key={c.bin} className="border-t border-border"><th scope="row" className="text-left font-normal">{c.bin}</th><td className="num text-right">{c.n}</td><td className="num text-right">{(c.predicted * 100).toFixed(0)}%</td><td className="num text-right">{(c.observed * 100).toFixed(0)}%</td></tr>
                ))}</tbody>
              </table>
            </div>
          ))}
        </div>
      )}
      <p className="mt-3 text-xs text-muted">{tr.note}</p>
    </Card>
  );
}
