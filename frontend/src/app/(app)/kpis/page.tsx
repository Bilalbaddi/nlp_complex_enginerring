"use client";

import { ArrowDownRight, ArrowUpRight } from "lucide-react";
import { useState } from "react";
import { MonthLines, RankBars } from "@/components/charts";
import { CategorySelect, WardSelect, useWards } from "@/components/Filters";
import { Async, Card, CardHeader, Field, PageHeader, Select, Table } from "@/components/ui";
import { METRIC_LABELS, monthLabel, num } from "@/lib/format";
import { qs, useApi } from "@/lib/hooks";
import type { KpiSummary, KpiTrendPoint, Rankings } from "@/lib/types";
import { cn } from "@/lib/utils";

const ROWS = ["complaints", "resolution_rate", "sla_compliance_pct", "avg_resolution_hours", "median_resolution_hours", "satisfaction", "complaints_per_10k", "repeat_rate_pct", "efficiency_score", "open_backlog"] as const;
const RANK_METRICS = ["sla_compliance_pct", "resolution_rate", "avg_resolution_hours", "satisfaction", "complaints_per_10k", "repeat_rate_pct", "efficiency_score", "open_backlog", "complaints"];

function Change({ v, better }: { v: number | null | undefined; better: boolean }): JSX.Element {
  if (v === null || v === undefined) return <span className="text-muted-foreground">-</span>;
  const good = (v >= 0) === better;
  const Arrow = v < 0 ? ArrowDownRight : ArrowUpRight;
  return (
    <span className={cn("inline-flex items-center gap-0.5 tabular-nums", Math.abs(v) < 0.05 ? "text-muted-foreground" : good ? "text-success" : "text-destructive")}>
      <Arrow className="h-3 w-3" aria-hidden />
      {v > 0 ? "+" : ""}
      {v.toFixed(1)}%<span className="sr-only">{good ? " better" : " worse"}</span>
    </span>
  );
}

export default function KpisPage(): JSX.Element {
  const wards = useWards();
  const [months, setMonths] = useState(1);
  const [ward, setWard] = useState("");
  const [category, setCategory] = useState("");
  const [metric, setMetric] = useState("sla_compliance_pct");
  const [by, setBy] = useState<"ward" | "department">("ward");

  const f = { ward_id: ward, category };
  const summary = useApi<KpiSummary>(`/kpis/summary${qs({ ...f, months })}`);
  const trend = useApi<KpiTrendPoint[]>(`/kpis/trend${qs(f)}`);
  const ranks = useApi<Rankings>(`/kpis/rankings${qs({ by, metric, months: Math.max(months, 3), category })}`);
  const [trendMetric, setTrendMetric] = useState("sla_compliance_pct");

  return (
    <>
      <PageHeader title="KPIs" subtitle="Performance indicators with month-on-month and year-on-year comparison" />
      <Card className="mb-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Period ending at newest month">
            <Select value={months} onChange={(e) => setMonths(Number(e.target.value))}>
              {[1, 3, 6, 12].map((m) => (
                <option key={m} value={m}>
                  Last {m} month{m > 1 ? "s" : ""}
                </option>
              ))}
            </Select>
          </Field>
          <WardSelect value={ward} onChange={setWard} wards={wards} all="Whole city" />
          <CategorySelect value={category} onChange={setCategory} all="All departments" fieldLabel="Department" />
        </div>
      </Card>

      <Card>
        <CardHeader title="Indicators" subtitle={summary.data ? `${monthLabel(summary.data.period.from)} to ${monthLabel(summary.data.period.to)}` : undefined} />
        <Async state={summary} height="h-72">
          {(d) => (
            <Table head={["Indicator", "Current", "Previous period", "Change", "Same period last year", "Change (YoY)"]}>
              {ROWS.map((k) => {
                const better = d.higher_is_better[k] ?? true;
                return (
                  <tr key={k}>
                    <td className="font-medium">{METRIC_LABELS[k]}</td>
                    <td className="tabular-nums">{num(d.current[k], 2)}</td>
                    <td className="tabular-nums text-muted-foreground">{num(d.previous_period[k], 2)}</td>
                    <td><Change v={d.change_vs_previous_pct[k]} better={better} /></td>
                    <td className="tabular-nums text-muted-foreground">{num(d.same_period_last_year[k], 2)}</td>
                    <td><Change v={d.change_vs_last_year_pct[k]} better={better} /></td>
                  </tr>
                );
              })}
            </Table>
          )}
        </Async>
        <p className="mt-2 text-xs text-muted-foreground">Recent months show lower resolution rates partly because newer complaints have not had time to be resolved yet.</p>
      </Card>

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader
            title="Trend"
            action={
              <Select aria-label="Trend metric" className="w-52" value={trendMetric} onChange={(e) => setTrendMetric(e.target.value)}>
                {RANK_METRICS.map((m) => (
                  <option key={m} value={m}>
                    {METRIC_LABELS[m]}
                  </option>
                ))}
              </Select>
            }
          />
          <Async state={trend} height="h-72">
            {(d) => <MonthLines data={d.map((p) => ({ month: p.month, value: (p[trendMetric as keyof KpiTrendPoint] as number | null) ?? null }))} series={[{ key: "value", label: METRIC_LABELS[trendMetric] ?? trendMetric }]} />}
          </Async>
        </Card>
        <Card>
          <CardHeader
            title="Rankings"
            subtitle={ranks.data ? `${ranks.data.higher_is_better ? "Higher" : "Lower"} is better · ${ranks.data.period.from} to ${ranks.data.period.to}` : undefined}
            action={
              <div className="flex gap-2">
                <Select aria-label="Rank by" className="w-28" value={by} onChange={(e) => setBy(e.target.value as "ward" | "department")}>
                  <option value="ward">Wards</option>
                  <option value="department">Departments</option>
                </Select>
                <Select aria-label="Ranking metric" className="w-44" value={metric} onChange={(e) => setMetric(e.target.value)}>
                  {RANK_METRICS.map((m) => (
                    <option key={m} value={m}>
                      {METRIC_LABELS[m]}
                    </option>
                  ))}
                </Select>
              </div>
            }
          />
          <Async state={ranks} height="h-72">
            {(d) => <RankBars data={d.items.map((i) => ({ name: i.name, value: (i[metric as keyof typeof i] as number | null) ?? null }))} valueKey="value" format={(v) => num(v, 1)} seriesColor={d.higher_is_better ? 1 : 2} />}
          </Async>
          <p className="mt-1 text-xs text-muted-foreground">Ranked best to worst.</p>
        </Card>
      </div>
    </>
  );
}
