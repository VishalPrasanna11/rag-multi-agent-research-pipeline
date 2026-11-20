import { CandlestickSeries, ColorType, createChart, createSeriesMarkers, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const ts = (d: string) => (Date.parse(`${d}T00:00:00Z`) / 1000) as UTCTimestamp;

export default function CandleChart({ prices, events }: { prices: any[]; events: any[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || !prices.length) return;
    const chart = createChart(ref.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: css("--surface") }, textColor: css("--muted"), attributionLogo: true },
      grid: { vertLines: { color: css("--border") }, horzLines: { color: css("--border") } },
      rightPriceScale: { borderColor: css("--border") }, timeScale: { borderColor: css("--border") },
    });
    const s = chart.addSeries(CandlestickSeries, {
      upColor: css("--up"), downColor: css("--down"), borderUpColor: css("--up"), borderDownColor: css("--down"),
      wickUpColor: css("--up"), wickDownColor: css("--down"),
    });
    s.setData(prices.map((p) => ({ time: ts(p.time), open: p.open, high: p.high, low: p.low, close: p.close })));
    const first = prices[0].time;
    createSeriesMarkers(s, events.filter((e) => e.date >= first).map((e) => ({
      time: ts(e.date), position: "aboveBar" as const, shape: "circle" as const, color: css("--brand"),
      text: e.surprise_pct != null ? `E ${e.surprise_pct > 0 ? "+" : ""}${e.surprise_pct.toFixed(1)}%` : "E",
    })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [prices, events]);
  return <div ref={ref} className="h-80 w-full" role="img" aria-label="Daily candlestick price chart with earnings dates marked E and the EPS surprise" />;
}

export function EstimatesChart({ events }: { events: any[] }) {
  const data = events.filter((e) => e.eps_act != null).slice(-12).map((e) => ({ date: e.date, Estimate: e.eps_est, Actual: e.eps_act }));
  return (
    <div className="h-64 w-full" role="img" aria-label="EPS estimate versus actual by quarter">
      <ResponsiveContainer>
        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
          <XAxis dataKey="date" tick={{ fill: "var(--muted)", fontSize: 11 }} />
          <YAxis tick={{ fill: "var(--muted)", fontSize: 11 }} />
          <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--border)", color: "var(--text)" }} />
          <Legend />
          <Bar dataKey="Estimate" fill="var(--muted)" isAnimationActive={false} />
          <Bar dataKey="Actual" fill="var(--brand)" isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
