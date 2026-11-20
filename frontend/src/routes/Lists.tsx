import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { TrackRecord } from "./Company";
import { Badge, Button, Card, Empty, ErrorPanel, Skeleton, statusTone } from "../components/ui";
import { api, post } from "../lib/api";
import { applyDensity, applyTheme, storedTheme, useMeta } from "../lib/hooks";
import { fmtDateTime, modeLabel } from "../lib/format";
import type { RunSummary } from "../lib/types";

export function HistoryPage() {
  const q = useQuery({ queryKey: ["runs"], queryFn: () => api<RunSummary[]>("/runs?limit=100") });
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">History</h1>
      {q.isLoading ? <Skeleton className="h-64" /> : q.error ? <ErrorPanel error={q.error} onRetry={() => q.refetch()} /> : !q.data?.length ? <Empty>No runs yet.</Empty> : (
        <Card>
          <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table">
            <table className="w-full min-w-[560px] text-sm">
              <caption className="sr-only">Research runs</caption>
              <thead className="text-left text-xs text-muted"><tr><th scope="col" className="py-2">When</th><th scope="col">Ticker</th><th scope="col">Mode</th><th scope="col">Status</th><th scope="col">Review</th><th scope="col" className="text-right">Cost</th></tr></thead>
              <tbody>{q.data.map((r) => (
                <tr key={r.id} className="border-t border-border" style={{ height: "var(--row)" }}>
                  <td>{fmtDateTime(r.created_at)}</td>
                  <th scope="row" className="text-left"><Link to={r.report_id ? `/reports/${r.report_id}` : `/run/${r.id}`} className="font-semibold text-brand underline">{r.ticker}</Link></th>
                  <td>{modeLabel[r.mode]}{r.fixture ? " · demo" : ""}</td>
                  <td><Badge tone={statusTone(r.status)}>{r.status}</Badge></td>
                  <td>{r.review_status ? <Badge tone={statusTone(r.review_status)}>{r.review_status}</Badge> : "—"}</td>
                  <td className="num text-right">${r.cost_usd.toFixed(3)}</td>
                </tr>))}</tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}

export function SavedPage() {
  const q = useQuery({ queryKey: ["reports", "saved"], queryFn: () => api<any[]>("/reports?saved=true&limit=100") });
  return (
    <div className="flex flex-col gap-4">
      <h1 className="text-2xl font-semibold">Saved reports</h1>
      {q.isLoading ? <Skeleton className="h-40" /> : !q.data?.length ? <Empty>Save a report from its page to keep it here.</Empty> : (
        <ul className="grid gap-3 md:grid-cols-2">{q.data.map((r) => (
          <li key={r.id}><Link to={`/reports/${r.id}`} className="block rounded-xl border border-border bg-surface p-4 hover:bg-surface-2">
            <div className="flex justify-between"><span className="font-semibold">{r.ticker}</span><Badge tone={statusTone(r.review_status)}>{r.review_status}</Badge></div>
            <p className="mt-1 text-sm">{r.headline}</p><p className="text-xs text-muted">{modeLabel[r.mode]} · {fmtDateTime(r.created_at)}</p>
          </Link></li>))}</ul>
      )}
    </div>
  );
}

export function SettingsPage() {
  const qc = useQueryClient();
  const { data: meta } = useMeta();
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<any>("/me") });
  const notes = useQuery({ queryKey: ["notifications"], queryFn: () => api<any[]>("/notifications") });
  const track = useQuery({ queryKey: ["track"], queryFn: () => api<any>("/track-record") });
  const save = useMutation({ mutationFn: (p: any) => api("/me/preferences", { method: "PATCH", body: JSON.stringify(p) }), onSuccess: () => qc.invalidateQueries({ queryKey: ["me"] }) });
  const read = useMutation({ mutationFn: () => post("/notifications/read"), onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }) });
  const prefs = me.data?.preferences ?? {};
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Settings</h1>
      <div className="grid gap-6 lg:grid-cols-2">
        <Card title="Preferences">
          <div className="flex flex-col gap-4 text-sm">
            <label className="flex items-center justify-between gap-3">Theme
              <select defaultValue={storedTheme()} onChange={(e) => { applyTheme(e.target.value); save.mutate({ theme: e.target.value }); }} className="min-h-9 rounded-lg border border-border bg-surface px-2">
                <option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option>
              </select></label>
            <label className="flex items-center justify-between gap-3">Density
              <select defaultValue={document.documentElement.dataset.density} onChange={(e) => { applyDensity(e.target.value); save.mutate({ density: e.target.value }); }} className="min-h-9 rounded-lg border border-border bg-surface px-2">
                <option value="comfortable">Comfortable</option><option value="compact">Compact</option>
              </select></label>
            <label className="flex items-center justify-between gap-3">Default mode
              <select value={prefs.default_mode ?? "pre_only"} onChange={(e) => save.mutate({ default_mode: e.target.value })} className="min-h-9 rounded-lg border border-border bg-surface px-2">
                {Object.entries(modeLabel).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select></label>
            <label className="flex items-center justify-between gap-3">In-app alerts for watchlist filings
              <input type="checkbox" className="h-5 w-5" checked={prefs.alerts_enabled ?? true} onChange={(e) => save.mutate({ alerts_enabled: e.target.checked })} /></label>
            <p className="text-xs text-muted">Signed in as {me.data?.anonymous ? "an anonymous browser session (your data is tied to this browser)" : me.data?.email ?? me.data?.id}.</p>
          </div>
        </Card>
        <Card id="notifications" title="Notifications" actions={<Button className="min-h-8" onClick={() => read.mutate()}>Mark all read</Button>}>
          {!notes.data?.length ? <p className="text-muted">No notifications. New 8-K earnings filings for watchlist tickers appear here.</p> : (
            <ul className="flex flex-col gap-2 text-sm">{notes.data.map((n) => (
              <li key={n.id} className={n.read ? "text-muted" : ""}>{!n.read && <span className="sr-only">Unread: </span>}<span className="font-medium">{n.title}</span> · {fmtDateTime(n.created_at)}
                {n.body?.report_id && <> · <Link to={`/reports/${n.body.report_id}`} className="text-brand underline">report</Link></>}</li>))}</ul>
          )}
        </Card>
        <Card title="Data sources and licensing">
          <ul className="flex flex-col gap-2 text-sm">{meta?.data_sources.map((d) => <li key={d.name}><span className="font-medium">{d.name}</span>: {d.use}. <span className="text-muted">{d.license}</span></li>)}</ul>
        </Card>
        <Card title="Server configuration gaps">
          <p className="mb-2 text-sm text-muted">Features shown as "not available" until these are configured on the server:</p>
          <ul className="flex flex-col gap-1 text-sm">{Object.entries(meta?.needs ?? {}).map(([k, v]) => <li key={k}><span className="font-medium">{k.replaceAll("_", " ")}</span>: needs {v}</li>)}</ul>
        </Card>
      </div>
      {track.data && <TrackRecord tr={track.data} />}
    </div>
  );
}
