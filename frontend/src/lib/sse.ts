import { authHeaders } from "./api";
import type { StreamEvent } from "./types";

const TERMINAL = new Set(["run.completed", "run.failed", "run.cancelled"]);

/** SSE over fetch (supports Authorization headers) with Last-Event-ID resume and backoff reconnects. */
export function streamRun(runId: string, onEvent: (e: StreamEvent) => void, opts: { after?: number; onState?: (s: string) => void } = {}) {
  const ctrl = new AbortController();
  let last = opts.after ?? 0;
  let stopped = false;
  let attempt = 0;

  async function loop() {
    while (!stopped) {
      try {
        opts.onState?.(attempt ? "reconnecting" : "connecting");
        const res = await fetch(`/api/v1/runs/${runId}/events`, {
          headers: { Accept: "text/event-stream", "Last-Event-ID": String(last), ...(await authHeaders()) },
          signal: ctrl.signal, credentials: "same-origin",
        });
        if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
        opts.onState?.("open");
        attempt = 0;
        const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
        let buf = "";
        let terminal = false;
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += value.replace(/\r\n?/g, "\n"); // sse-starlette uses CRLF
          let idx;
          while ((idx = buf.indexOf("\n\n")) >= 0) {
            const frame = buf.slice(0, idx);
            buf = buf.slice(idx + 2);
            const data = frame.split("\n").filter((l) => l.startsWith("data:")).map((l) => l.slice(5).trim()).join("\n");
            if (!data) continue; // ping/comment
            const ev = JSON.parse(data) as StreamEvent;
            last = ev.seq;
            onEvent(ev);
            if (TERMINAL.has(ev.type) || ev.type === "clarification.required") terminal = true;
          }
        }
        if (terminal) { opts.onState?.("closed"); return; }
      } catch (err) {
        if (stopped || (err as Error).name === "AbortError") return;
      }
      attempt += 1;
      await new Promise((r) => setTimeout(r, Math.min(8000, 500 * 2 ** attempt)));
    }
  }
  loop();
  return { close: () => { stopped = true; ctrl.abort(); }, lastSeq: () => last };
}
