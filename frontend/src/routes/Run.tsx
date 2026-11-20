import { useMutation, useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed, Loader2, Square, XCircle } from "lucide-react";
import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { Markdown, ReportCtx } from "../components/Markdown";
import { Badge, Button, Card, ErrorPanel, statusTone } from "../components/ui";
import { api, post } from "../lib/api";
import { modeLabel } from "../lib/format";
import { streamRun } from "../lib/sse";
import type { Section, StreamEvent } from "../lib/types";

interface AgentState { status: "pending" | "running" | "done" | "error"; message?: string; detail?: string }
interface State { agents: Record<string, AgentState>; order: string[]; sections: (Section & { agent: string })[];
  messages: string[]; terminal?: StreamEvent; clarification?: { question: string; options: string[]; seq: number }; critique: StreamEvent[];
  facts?: { count: number; unavailable: number } }

function reduce(s: State, e: StreamEvent): State {
  const a = e.agent ?? undefined;
  const set = (name: string, patch: Partial<AgentState>) => {
    const order = s.order.includes(name) ? s.order : [...s.order, name];
    return { ...s, order, agents: { ...s.agents, [name]: { ...(s.agents[name] ?? { status: "pending" }), ...patch } } };
  };
  switch (e.type) {
    case "run.started": {
      const names = ["Data", ...((e.payload.agents as string[]) ?? []), "Synthesis", "Critique"];
      return { ...s, order: [...new Set(names)], agents: Object.fromEntries(names.map((n) => [n, s.agents[n] ?? { status: "pending" }])) };
    }
    case "agent.started": return a ? { ...set(a, { status: "running", message: e.payload.message }), messages: [...s.messages, `${a}: ${e.payload.message}`] } : s;
    case "agent.progress": return a ? { ...set(a, { status: s.agents[a]?.status === "done" ? "done" : "running", message: e.payload.message }), messages: [...s.messages, `${a}: ${e.payload.message}`] } : s;
    case "agent.completed": return a ? set(a, { status: e.payload.status === "error" ? "error" : "done", message: e.payload.message }) : s;
    case "fact.added": return { ...s, facts: { count: e.payload.count, unavailable: e.payload.unavailable } };
    case "section.delta":
      if (e.payload.section) return { ...s, sections: [...s.sections.filter((x) => x.id !== e.payload.section.id), { ...e.payload.section, agent: a ?? "" }] };
      return s;
    case "critique.round": return { ...set("Critique", { status: "running", message: `Round ${e.payload.round}: ${e.payload.issues.length} issue(s), ${e.payload.action}` }), critique: [...s.critique, e] };
    case "clarification.required": return { ...s, clarification: { question: e.payload.question, options: e.payload.options, seq: e.seq } };
    case "clarification.answered": return { ...s, clarification: undefined };
    case "run.completed": case "run.failed": case "run.cancelled":
      return { ...s, terminal: e, agents: e.type === "run.completed" ? Object.fromEntries(Object.entries(s.agents).map(([k, v]) => [k, { ...v, status: "done" as const }])) : s.agents };
    default: return s;
  }
}

export function RunPage() {
  const { runId = "" } = useParams();
  const navigate = useNavigate();
  const [state, dispatch] = useReducer(reduce, { agents: {}, order: [], sections: [], messages: [], critique: [] });
  const [conn, setConn] = useState("connecting");
  const [gen, setGen] = useState(0);
  const run = useQuery({ queryKey: ["run", runId], queryFn: () => api<any>(`/runs/${runId}`), refetchInterval: state.terminal ? false : 5000 });

  const resumeAfter = useRef(0);
  useEffect(() => {
    const s = streamRun(runId, dispatch, { onState: setConn, after: resumeAfter.current });
    return () => s.close();
  }, [runId, gen]);

  const stop = useMutation({ mutationFn: () => post(`/runs/${runId}/cancel`) });
  const answer = useMutation({
    mutationFn: (a: string) => post(`/runs/${runId}/resume`, { answer: a }),
    onSuccess: () => {
      resumeAfter.current = state.clarification?.seq ?? 0; // continue after the answered question
      dispatch({ type: "clarification.answered" } as StreamEvent);
      setGen((g) => g + 1);
    },
  });
  const reportId = state.terminal?.type === "run.completed" ? state.terminal.payload.report_id : null;
  useEffect(() => { if (reportId) { const t = setTimeout(() => navigate(`/reports/${reportId}`), 1200); return () => clearTimeout(t); } }, [reportId, navigate]);

  const last = state.messages[state.messages.length - 1];
  const running = !state.terminal && !state.clarification;
  const ctx = useMemo(() => ({ facts: {}, citations: {} }), []);

  return (
    <div className="grid gap-6 lg:grid-cols-[340px_1fr]">
      <div className="flex flex-col gap-4">
        <div>
          <p className="text-sm text-muted">Research run</p>
          <h1 className="text-2xl font-semibold">{run.data?.ticker ?? "…"} <span className="text-base font-normal text-muted">{modeLabel[run.data?.mode] ?? ""}</span></h1>
          <div className="mt-1 flex flex-wrap gap-2">
            <Badge tone={statusTone(state.terminal ? state.terminal.type.split(".")[1] === "completed" ? "completed" : state.terminal.type.split(".")[1] : run.data?.status)}>
              {state.terminal ? state.terminal.type.replace("run.", "") : state.clarification ? "needs your input" : "running"}
            </Badge>
            {run.data?.fixture && <Badge tone="warn">demo data</Badge>}
            {conn === "reconnecting" && <Badge tone="warn">reconnecting…</Badge>}
          </div>
        </div>
        <Card title="Agents">
          <ol className="flex flex-col gap-3" aria-label="Agent progress">
            {state.order.map((name) => {
              const a = state.agents[name]!;
              const Icon = a.status === "done" ? CheckCircle2 : a.status === "error" ? XCircle : a.status === "running" ? Loader2 : CircleDashed;
              return (
                <li key={name} className="flex gap-2">
                  <Icon size={18} aria-hidden className={a.status === "running" ? "mt-0.5 shrink-0 animate-spin text-brand" : a.status === "done" ? "mt-0.5 shrink-0 text-ok" : a.status === "error" ? "mt-0.5 shrink-0 text-danger" : "mt-0.5 shrink-0 text-muted"} />
                  <div>
                    <p className="font-medium">{name} <span className="sr-only">({a.status})</span></p>
                    {a.message && <p className="text-sm text-muted">{a.message}</p>}
                  </div>
                </li>
              );
            })}
          </ol>
          {state.facts && <p className="mt-3 text-sm text-muted">{state.facts.count} cited facts · {state.facts.unavailable} marked unavailable</p>}
          <div aria-live="polite" className="sr-only">{last}</div>
          {running && <Button variant="danger" className="mt-4 w-full" onClick={() => stop.mutate()} disabled={stop.isPending}><Square size={14} aria-hidden /> {stop.isPending ? "Stopping…" : "Stop"}</Button>}
        </Card>
      </div>
      <div className="flex flex-col gap-4">
        {state.clarification && (
          <Card title="A quick question">
            <p>{state.clarification.question}</p>
            <div className="mt-3 flex flex-wrap gap-2" role="group" aria-label="Suggested answers">
              {state.clarification.options.map((o) => <Button key={o} onClick={() => answer.mutate(o)} disabled={answer.isPending}>{o}</Button>)}
            </div>
            {answer.error && <div className="mt-3"><ErrorPanel error={answer.error} /></div>}
          </Card>
        )}
        {state.terminal?.type === "run.failed" && <ErrorPanel error={{ message: state.terminal.payload.error?.message ?? "Run failed" }} />}
        {state.terminal?.type === "run.cancelled" && <Card><p>Run stopped. <Link to="/" className="text-brand underline">Back to watchlist</Link></p></Card>}
        {reportId && <Card><p role="status">Report ready. Opening… <Link className="text-brand underline" to={`/reports/${reportId}`}>Open now</Link></p></Card>}
        {state.sections.length === 0 && running && (
          <Card><p className="text-muted">Sections appear here as each agent finishes. Every number is drawn from cited data, so they render as underlined links once the report opens.</p></Card>
        )}
        <ReportCtx.Provider value={ctx}>
          {state.sections.map((s) => (
            <Card key={s.id} title={<>{s.title} <span className="text-xs font-normal text-muted">· {s.agent}</span></>}>
              <Markdown text={s.markdown} />
            </Card>
          ))}
        </ReportCtx.Provider>
      </div>
    </div>
  );
}
