import { clsx } from "clsx";
import { AlertTriangle, Copy, RotateCw } from "lucide-react";
import type { ReactNode } from "react";
import { HttpError } from "../lib/api";

export const cn = (...c: (string | false | null | undefined)[]) => clsx(c);

export function Card({ title, actions, children, className, id }: { title?: ReactNode; actions?: ReactNode;
  children: ReactNode; className?: string; id?: string }) {
  return (
    <section id={id} className={cn("rounded-xl border border-border bg-surface", className)} aria-labelledby={id ? `${id}-h` : undefined}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
          {title && <h2 id={id ? `${id}-h` : undefined} className="text-base font-semibold">{title}</h2>}
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function Button({ variant = "secondary", className, ...p }: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger" }) {
  return (
    <button
      {...p}
      className={cn(
        "inline-flex min-h-9 items-center justify-center gap-1.5 rounded-lg px-3 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50",
        variant === "primary" && "bg-brand text-brand-fg hover:opacity-90",
        variant === "secondary" && "border border-border bg-surface hover:bg-surface-2",
        variant === "ghost" && "hover:bg-surface-2",
        variant === "danger" && "border border-danger text-danger hover:bg-surface-2",
        className,
      )}
    />
  );
}

const BADGE: Record<string, string> = {
  ok: "border-ok text-ok", warn: "border-warn text-warn", danger: "border-danger text-danger",
  brand: "border-brand text-brand", muted: "border-border text-muted",
};
export function Badge({ tone = "muted", children, title }: { tone?: keyof typeof BADGE; children: ReactNode; title?: string }) {
  return <span title={title} className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium", BADGE[tone])}>{children}</span>;
}

export function statusTone(s?: string | null): keyof typeof BADGE {
  switch (s) {
    case "approved": case "completed": case "confirmed": case "reported": case "high": return "ok";
    case "awaiting_review": case "pending": case "estimated": case "medium": case "awaiting_input": case "changes_requested": return "warn";
    case "failed": case "rejected": case "low": return "danger";
    case "running": case "queued": return "brand";
    default: return "muted";
  }
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cn("animate-pulse rounded bg-surface-2", className)} />;
}

export function ErrorPanel({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const e = error instanceof HttpError ? error.error : { message: String((error as Error)?.message ?? error), request_id: "", retryable: true, code: "error" };
  return (
    <div role="alert" className="flex flex-col gap-2 rounded-xl border border-danger bg-surface p-4">
      <p className="flex items-center gap-2 font-medium text-danger"><AlertTriangle size={16} aria-hidden /> {e.message}</p>
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <span>code: {e.code}</span>
        {e.request_id && (
          <button className="inline-flex items-center gap-1 underline" onClick={() => navigator.clipboard?.writeText(e.request_id)}>
            <Copy size={12} aria-hidden /> request id {e.request_id}
          </button>
        )}
        {e.retryable && onRetry && <Button onClick={onRetry} className="min-h-7"><RotateCw size={14} aria-hidden /> Retry</Button>}
      </div>
    </div>
  );
}

export function Empty({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return <div className="flex flex-col items-start gap-3 rounded-xl border border-dashed border-border p-6 text-muted">{children}{action}</div>;
}
