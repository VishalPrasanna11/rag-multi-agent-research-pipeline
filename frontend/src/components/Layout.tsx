import { Command } from "cmdk";
import * as Dialog from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { Bell, BookMarked, History, Home, Keyboard, Search, Settings } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Outlet, useNavigate } from "react-router";
import { api } from "../lib/api";
import { useMeta } from "../lib/hooks";
import { cn } from "./ui";

const NAV = [
  { to: "/", label: "Watchlist", icon: Home, key: "h" },
  { to: "/history", label: "History", icon: History, key: "y" },
  { to: "/saved", label: "Saved", icon: BookMarked, key: "s" },
  { to: "/settings", label: "Settings", icon: Settings, key: "," },
];

export function Layout() {
  const [palette, setPalette] = useState(false);
  const [help, setHelp] = useState(false);
  const navigate = useNavigate();
  const { data: meta } = useMeta();
  const { data: notes } = useQuery({ queryKey: ["notifications", "unread"], queryFn: () => api<any[]>("/notifications?unread=true"), refetchInterval: 60_000 });

  useEffect(() => {
    let g = false;
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      const typing = tag === "INPUT" || tag === "TEXTAREA" || (e.target as HTMLElement)?.isContentEditable;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((p) => !p); return; }
      if (typing) return;
      if (e.key === "?") { setHelp(true); return; }
      if (e.key === "/") { e.preventDefault(); setPalette(true); return; }
      if (g) { const item = NAV.find((n) => n.key === e.key); if (item) navigate(item.to); g = false; return; }
      if (e.key === "g") { g = true; setTimeout(() => (g = false), 800); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate]);

  return (
    <div className="min-h-screen">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-surface focus:p-2">Skip to content</a>
      {meta?.fixture_mode && (
        <div role="status" className="no-print border-b border-warn bg-surface-2 px-4 py-1.5 text-center text-xs text-warn">
          Demo data: recorded fixtures, not live. Live research needs an LLM key on the server.
        </div>
      )}
      {meta && !meta.durable_storage && !meta.fixture_mode && (
        <div role="status" className="no-print border-b border-warn bg-surface-2 px-4 py-1.5 text-center text-xs text-warn">
          Storage is not durable on this server: reports may be lost on restart (needs a Postgres DATABASE_URL).
        </div>
      )}
      <nav aria-label="Main" className="no-print sticky top-0 z-30 border-b border-border bg-surface/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-1 px-4 py-2">
          <NavLink to="/" aria-label="Earnings Intelligence, watchlist home" className="mr-3 flex items-center gap-2 font-semibold">
            <img src="/favicon.svg" alt="" width={22} height={22} /> <span className="hidden sm:inline">Earnings Intelligence</span>
          </NavLink>
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => cn("flex min-h-9 items-center gap-1.5 rounded-lg px-2.5 text-sm", isActive ? "bg-surface-2 font-medium" : "text-muted hover:text-text")}>
              <Icon size={16} aria-hidden /> <span className="hidden md:inline">{label}</span><span className="sr-only md:hidden">{label}</span>
            </NavLink>
          ))}
          <div className="ml-auto flex items-center gap-1">
            <button onClick={() => setPalette(true)} className="flex min-h-9 items-center gap-2 rounded-lg border border-border px-2.5 text-sm text-muted" aria-label="Open command palette (Ctrl or Cmd K)">
              <Search size={16} aria-hidden /> <span className="hidden lg:inline">Search or run…</span> <kbd className="hidden rounded border border-border px-1 text-xs lg:inline">⌘K</kbd>
            </button>
            <NavLink to="/settings#notifications" className="relative flex min-h-9 min-w-9 items-center justify-center rounded-lg hover:bg-surface-2" aria-label={`Notifications${notes?.length ? `, ${notes.length} unread` : ""}`}>
              <Bell size={16} aria-hidden />
              {!!notes?.length && <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-brand" aria-hidden />}
            </NavLink>
            <button onClick={() => setHelp(true)} className="hidden min-h-9 min-w-9 items-center justify-center rounded-lg hover:bg-surface-2 sm:flex" aria-label="Keyboard shortcuts">
              <Keyboard size={16} aria-hidden />
            </button>
          </div>
        </div>
      </nav>
      <main id="main" tabIndex={-1} className="mx-auto max-w-7xl px-4 py-6">
        <Outlet />
      </main>
      <footer className="mx-auto max-w-7xl px-4 pb-8 text-xs text-muted">{meta?.disclaimer}</footer>
      <Palette open={palette} onOpenChange={setPalette} />
      <Dialog.Root open={help} onOpenChange={setHelp}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-40 bg-black/40" />
          <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(92vw,420px)] -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border bg-surface p-5">
            <Dialog.Title className="text-base font-semibold">Keyboard shortcuts</Dialog.Title>
            <Dialog.Description className="sr-only">List of keyboard shortcuts</Dialog.Description>
            <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
              {[["⌘K / Ctrl K, /", "Command palette"], ["g h", "Watchlist"], ["g y", "History"], ["g s", "Saved reports"], ["g ,", "Settings"], ["?", "This help"], ["Esc", "Close dialogs"]].map(([k, v]) => (
                <div key={k} className="contents"><dt><kbd className="rounded border border-border px-1.5">{k}</kbd></dt><dd>{v}</dd></div>
              ))}
            </dl>
            <Dialog.Close asChild><button className="mt-4 rounded-lg border border-border px-3 py-1.5 text-sm">Close</button></Dialog.Close>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}

function Palette({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  const [q, setQ] = useState("");
  const navigate = useNavigate();
  const go = (to: string) => { onOpenChange(false); setQ(""); navigate(to); };
  const t = q.trim().toUpperCase().replace("$", "");
  const valid = /^[A-Z][A-Z0-9.-]{0,9}$/.test(t);
  return (
    <Command.Dialog open={open} onOpenChange={onOpenChange} label="Command palette" shouldFilter={false}
      overlayClassName="fixed inset-0 z-40 bg-black/40"
      contentClassName="fixed left-1/2 top-[15vh] z-50 w-[min(92vw,560px)] -translate-x-1/2 overflow-hidden rounded-xl border border-border bg-surface shadow-xl">
      <Command.Input value={q} onValueChange={setQ} placeholder="Ticker (e.g. NVDA) or page…" className="w-full border-b border-border bg-transparent px-4 py-3 outline-none" />
      <Command.List className="max-h-80 overflow-auto p-2">
        <Command.Empty className="p-3 text-sm text-muted">Type a ticker symbol.</Command.Empty>
        {valid && (
          <Command.Group heading="Ticker" className="text-xs text-muted">
            {[["pre_only", "Pre-earnings brief"], ["post_only", "Post-earnings debrief"], ["full_cycle", "Full cycle"]].map(([m, l]) => (
              <Command.Item key={m} onSelect={() => go(`/new?ticker=${t}&mode=${m}`)} className="cursor-pointer rounded-lg px-3 py-2 text-sm text-text aria-selected:bg-surface-2">
                Run {l} for {t}
              </Command.Item>
            ))}
            <Command.Item onSelect={() => go(`/company/${t}`)} className="cursor-pointer rounded-lg px-3 py-2 text-sm text-text aria-selected:bg-surface-2">Open {t} company profile</Command.Item>
          </Command.Group>
        )}
        <Command.Group heading="Go to" className="text-xs text-muted">
          {NAV.map((n) => (
            <Command.Item key={n.to} onSelect={() => go(n.to)} className="cursor-pointer rounded-lg px-3 py-2 text-sm text-text aria-selected:bg-surface-2">{n.label}</Command.Item>
          ))}
        </Command.Group>
      </Command.List>
    </Command.Dialog>
  );
}
