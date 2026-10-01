"use client";

import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { monthLabel } from "@/lib/format";

const COLORS = [1, 2, 3, 4, 5].map((n) => `hsl(var(--chart-${n}))`);
export const color = (i: number): string => COLORS[i % COLORS.length] ?? "hsl(var(--chart-1))";
const GRID = "hsl(var(--chart-grid))";
const AXIS = { fontSize: 12, fill: "hsl(var(--muted-foreground))" };
const TOOLTIP = {
  contentStyle: { background: "hsl(var(--card))", border: "1px solid hsl(var(--border))", borderRadius: 8, fontSize: 12 },
  labelStyle: { color: "hsl(var(--foreground))" },
};

export interface Series {
  key: string;
  label: string;
}

/** Line chart over months (x values are YYYY-MM strings). */
export function MonthLines({
  data,
  series,
  height = 260,
  format = (v: number) => String(v),
}: {
  data: Record<string, string | number | null>[];
  series: Series[];
  height?: number;
  format?: (v: number) => string;
}): JSX.Element {
  return (
    <div role="img" aria-label={`Line chart of ${series.map((s) => s.label).join(", ")} by month`}>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
          <XAxis dataKey="month" tickFormatter={monthLabel} tick={AXIS} minTickGap={28} />
          <YAxis tick={AXIS} tickFormatter={(v: number) => format(v)} width={52} />
          <Tooltip {...TOOLTIP} labelFormatter={(l: string) => monthLabel(l)} formatter={(v: number) => format(v)} />
          {series.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
          {series.map((s, i) => (
            <Line key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={color(i)} strokeWidth={2} dot={false} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

/** History line + forecast line + confidence band. */
export function ForecastChart({
  history,
  forecast,
  height = 300,
}: {
  history: { month: string; complaints: number }[];
  forecast: { month: string; mean: number; lo: number; hi: number }[];
  height?: number;
}): JSX.Element {
  const rows: Record<string, string | number | null | number[]>[] = [
    ...history.map((h, i) => ({
      month: h.month,
      actual: h.complaints,
      // join the lines: the last actual point is also the first forecast point
      forecast: i === history.length - 1 && forecast.length ? h.complaints : null,
      band: null,
    })),
    ...forecast.map((f) => ({ month: f.month, actual: null, forecast: f.mean, band: [f.lo, f.hi] })),
  ];
  return (
    <div role="img" aria-label="Chart of monthly complaint history, forecast and 95% interval">
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" />
          <XAxis dataKey="month" tickFormatter={monthLabel} tick={AXIS} minTickGap={28} />
          <YAxis tick={AXIS} width={48} />
          <Tooltip {...TOOLTIP} labelFormatter={(l: string) => monthLabel(l)} />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Area dataKey="band" name="95% interval" stroke="none" fill={color(0)} fillOpacity={0.18} connectNulls={false} />
          <Line dataKey="actual" name="Actual" stroke={color(0)} strokeWidth={2} dot={false} connectNulls={false} />
          <Line dataKey="forecast" name="Forecast" stroke={color(2)} strokeWidth={2} strokeDasharray="6 4" dot={{ r: 3 }} connectNulls />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Horizontal bars for rankings (name -> value). */
export function RankBars({
  data,
  valueKey,
  nameKey = "name",
  height,
  format = (v: number) => String(v),
  seriesColor = 0,
}: {
  data: Record<string, string | number | null>[];
  valueKey: string;
  nameKey?: string;
  height?: number;
  format?: (v: number) => string;
  seriesColor?: number;
}): JSX.Element {
  return (
    <div role="img" aria-label={`Bar chart of ${valueKey}`}>
      <ResponsiveContainer width="100%" height={height ?? Math.max(180, data.length * 28 + 30)}>
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 24, bottom: 0, left: 8 }}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" horizontal={false} />
          <XAxis type="number" tick={AXIS} tickFormatter={(v: number) => format(v)} />
          <YAxis type="category" dataKey={nameKey} tick={AXIS} width={130} />
          <Tooltip {...TOOLTIP} formatter={(v: number) => format(v)} cursor={{ fill: "hsl(var(--muted))" }} />
          <Bar dataKey={valueKey} fill={color(seriesColor)} radius={[0, 4, 4, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/** Vertical columns (histograms, aging buckets). */
export function Columns({
  data,
  xKey,
  series,
  height = 240,
}: {
  data: Record<string, string | number | null>[];
  xKey: string;
  series: Series[];
  height?: number;
}): JSX.Element {
  return (
    <div role="img" aria-label={`Column chart of ${series.map((s) => s.label).join(", ")}`}>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={GRID} strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey={xKey} tick={AXIS} />
          <YAxis tick={AXIS} width={48} />
          <Tooltip {...TOOLTIP} cursor={{ fill: "hsl(var(--muted))" }} />
          {series.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
          {series.map((s, i) => (
            <Bar key={s.key} dataKey={s.key} name={s.label} fill={color(i)} radius={[4, 4, 0, 0]} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
