"use client";

import { useState } from "react";
import { Columns, ForecastChart, MonthLines } from "@/components/charts";
import { CategorySelect, WardSelect, useWards } from "@/components/Filters";
import { AiBadge, Async, Badge, Button, Card, CardHeader, ErrorBox, Field, Input, PageHeader, Spinner, Table, Tabs } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { label, monthLabel, num } from "@/lib/format";
import { qs, useApi } from "@/lib/hooks";
import type { AiLabel, BacktestAll, ForecastVolume, ResourceForecast, WardRisk, WhatIf } from "@/lib/types";

type Tab = "volume" | "resources" | "risk" | "whatif" | "backtest";

function WhatIfPanel(): JSX.Element {
  const [category, setCategory] = useState("roads");
  const [crew, setCrew] = useState(20);
  const [demand, setDemand] = useState(0);
  const [res, setRes] = useState<{ result: WhatIf; explanation: string; label: AiLabel } | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function run(): Promise<void> {
    setBusy(true);
    setErr(null);
    try {
      setRes(await api("/scenarios/explain", { method: "POST", body: JSON.stringify({ category, crew_change_pct: crew, demand_change_pct: demand, horizon_months: 6 }) }));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Scenario failed");
    } finally {
      setBusy(false);
    }
  }
  const r = res?.result;
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader title="What-if scenario" subtitle="Change a department's crews or demand and see the projected backlog and SLA (computed numerically; AI only explains)" />
        <div className="grid gap-3 sm:grid-cols-4">
          <CategorySelect value={category} onChange={setCategory} required fieldLabel="Department / category" />
          <Field label={`Crews / throughput: ${crew > 0 ? "+" : ""}${crew}%`}>
            <input type="range" min={-50} max={150} step={5} value={crew} onChange={(e) => setCrew(Number(e.target.value))} className="w-full accent-[hsl(var(--primary))]" />
          </Field>
          <Field label={`Extra demand: ${demand > 0 ? "+" : ""}${demand}%`}>
            <input type="range" min={-30} max={100} step={5} value={demand} onChange={(e) => setDemand(Number(e.target.value))} className="w-full accent-[hsl(var(--primary))]" />
          </Field>
          <div className="flex items-end">
            <Button onClick={() => void run()} disabled={busy}>
              {busy && <Spinner />}Run scenario
            </Button>
          </div>
        </div>
        {err && <div className="mt-3"><ErrorBox message={err} /></div>}
      </Card>
      {r && res && (
        <>
          <div className="grid gap-3 sm:grid-cols-4">
            <Card><p className="text-xs text-muted-foreground">Backlog after 6 months</p><p className="text-xl font-semibold tabular-nums">{num(r.scenario.final_backlog)} <span className="text-sm font-normal text-muted-foreground">(baseline {num(r.baseline.final_backlog)})</span></p></Card>
            <Card><p className="text-xs text-muted-foreground">SLA compliance after 6 months</p><p className="text-xl font-semibold tabular-nums">{num(r.scenario.final_sla_compliance_pct, 1)}% <span className="text-sm font-normal text-muted-foreground">(baseline {num(r.baseline.final_sla_compliance_pct, 1)}%)</span></p></Card>
            <Card><p className="text-xs text-muted-foreground">Monthly capacity</p><p className="text-xl font-semibold tabular-nums">{num(r.scenario.capacity_per_month)} <span className="text-sm font-normal text-muted-foreground">(now {num(r.current.capacity_per_month)})</span></p></Card>
            <Card><p className="text-xs text-muted-foreground">Open backlog today</p><p className="text-xl font-semibold tabular-nums">{num(r.current.open_backlog)}</p></Card>
          </div>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Projected backlog" />
              <MonthLines data={r.baseline.months.map((m, i) => ({ month: m.month, baseline: m.backlog, scenario: r.scenario.months[i]?.backlog ?? null }))} series={[{ key: "baseline", label: "Baseline" }, { key: "scenario", label: "Scenario" }]} height={240} />
            </Card>
            <Card>
              <CardHeader title="Explanation" action={<AiBadge label={res.label} />} />
              <p className="text-sm leading-relaxed">{res.explanation}</p>
              <p className="mt-3 text-xs text-muted-foreground">Assumptions: capacity = recent monthly resolutions × (1 + crew change)^0.8 (diminishing returns); backlog accumulates demand minus capacity; SLA moves with utilisation.</p>
            </Card>
          </div>
        </>
      )}
    </div>
  );
}

export default function ForecastsPage(): JSX.Element {
  const { user } = useAuth();
  const wards = useWards();
  const [tab, setTab] = useState<Tab>("volume");
  const [category, setCategory] = useState("");
  const [ward, setWard] = useState("");
  const [ratios, setRatios] = useState("");
  const isWard = user?.role === "ward_officer";

  const volume = useApi<ForecastVolume>(tab === "volume" ? `/forecasts/volume${qs({ category, ward_id: ward, horizon: 6 })}` : null);
  const resources = useApi<ResourceForecast>(tab === "resources" ? `/forecasts/resources${qs({ horizon: 6, ratios })}` : null);
  const risk = useApi<WardRisk>(tab === "risk" ? "/forecasts/risk" : null);
  const backtest = useApi<BacktestAll>(tab === "backtest" ? "/forecasts/backtest" : null);

  const tabs: { id: Tab; label: string }[] = [
    { id: "volume", label: "Complaint volume" },
    ...(isWard ? [] : [{ id: "resources" as Tab, label: "Resource needs" }]),
    { id: "risk", label: "Infrastructure risk" },
    ...(isWard ? [] : [{ id: "whatif" as Tab, label: "What-if scenarios" }]),
    ...(user?.role === "analyst" || user?.role === "admin" || user?.role === "commissioner" ? [{ id: "backtest" as Tab, label: "Model accuracy" }] : []),
  ];

  return (
    <>
      <PageHeader title="Forecasts" subtitle="Complaint volume, resource needs and risk for the next 1–6 months" />
      <Tabs value={tab} onChange={setTab} items={tabs} />

      {tab === "volume" && (
        <Card>
          <div className="mb-3 grid gap-3 sm:grid-cols-3">
            <CategorySelect value={category} onChange={setCategory} />
            {!isWard && <WardSelect value={ward} onChange={setWard} wards={wards} all="Whole city" />}
          </div>
          <Async state={volume} height="h-80">
            {(d) =>
              d.forecast.length === 0 ? (
                <p className="text-sm text-muted-foreground">Not enough history to forecast this selection.</p>
              ) : (
                <>
                  <ForecastChart history={d.history} forecast={d.forecast} />
                  <div className="mt-3 flex flex-wrap items-center gap-3 text-sm">
                    <Badge tone="info">Model: {d.model?.replace(/_/g, " ")}</Badge>
                    {d.backtest?.mape_pct != null && <span>Backtest error: <strong>{num(d.backtest.mape_pct, 1)}% MAPE</strong>, {num(d.backtest.mae, 1)} complaints MAE</span>}
                    <span className="text-muted-foreground">Shaded band = 95% interval; it widens with the horizon.</span>
                  </div>
                  <Table className="mt-3" head={["Month", "Forecast", "Low", "High"]}>
                    {d.forecast.map((f) => (
                      <tr key={f.month}>
                        <td>{monthLabel(f.month)}</td>
                        <td className="tabular-nums font-medium">{num(f.mean)}</td>
                        <td className="tabular-nums text-muted-foreground">{num(f.lo)}</td>
                        <td className="tabular-nums text-muted-foreground">{num(f.hi)}</td>
                      </tr>
                    ))}
                  </Table>
                </>
              )
            }
          </Async>
        </Card>
      )}

      {tab === "resources" && (
        <Card>
          <CardHeader title="Crew-days needed by department" subtitle="Forecast complaints × crew-days per complaint, compared with available capacity" />
          <div className="mb-3 max-w-md">
            <Field label='Override crew-days per complaint (JSON, e.g. {"drainage": 4})'>
              <Input value={ratios} onChange={(e) => setRatios(e.target.value)} placeholder='{"roads": 4.5}' />
            </Field>
          </div>
          <Async state={resources} height="h-72">
            {(d) => (
              <>
                <Columns data={d.months.map((m, i) => ({ month: monthLabel(m), crew_days: d.total_crew_days[i] ?? 0 }))} xKey="month" series={[{ key: "crew_days", label: "Total crew-days needed" }]} height={220} />
                <Table className="mt-3" head={["Department", "Crew-days / complaint", ...d.months.map(monthLabel), "Capacity / month", "Peak utilisation"]}>
                  {d.categories.map((c) => {
                    const peak = Math.max(...c.utilisation_pct.map((u) => u ?? 0));
                    return (
                      <tr key={c.category}>
                        <td>{c.department}</td>
                        <td className="tabular-nums">{c.crew_days_per_complaint}</td>
                        {c.crew_days_needed.map((n, i) => (
                          <td key={i} className="tabular-nums">{num(n)}</td>
                        ))}
                        <td className="tabular-nums">{num(c.capacity_crew_days_per_month)}</td>
                        <td><Badge tone={peak > 100 ? "bad" : peak > 80 ? "warn" : "good"}>{num(peak, 0)}%</Badge></td>
                      </tr>
                    );
                  })}
                </Table>
                <p className="mt-2 text-xs text-muted-foreground">Crew-day ratios are planning assumptions, not measured values; adjust them above. Capacity = staff × 22 working days × 60% field share.</p>
              </>
            )}
          </Async>
        </Card>
      )}

      {tab === "risk" && (
        <Card>
          <CardHeader title="Infrastructure risk by ward" subtitle="30% forecast load, 25% growth, 25% service gap index, 20% infrastructure stress" />
          <Async state={risk} height="h-72">
            {(d) => (
              <Table head={["Ward", "Risk score", "Level", "Forecast next 3 months", "Gap index", "Growth (6m vs prior 6m)"]}>
                {d.items.map((i) => (
                  <tr key={i.ward_id}>
                    <td>{i.name}</td>
                    <td className="tabular-nums font-medium">{num(i.risk_score, 0)}</td>
                    <td><Badge tone={i.level === "high" ? "bad" : i.level === "medium" ? "warn" : "good"}>{i.level}</Badge></td>
                    <td className="tabular-nums">{num(i.forecast_next_3_months)}</td>
                    <td className="tabular-nums">{num(i.gap_index, 0)}</td>
                    <td className="tabular-nums">{num(i.growth, 2)}×</td>
                  </tr>
                ))}
              </Table>
            )}
          </Async>
        </Card>
      )}

      {tab === "whatif" && <WhatIfPanel />}

      {tab === "backtest" && (
        <Card>
          <CardHeader title="Forecast accuracy (rolling-origin backtest)" subtitle="Each model predicts 3 months ahead from 3 past points in time; the best model per series is used" />
          <Async state={backtest} height="h-72">
            {(d) => (
              <>
                <p className="mb-2 text-sm">Average error of the best model: <strong>{num(d.avg_best_mape_pct, 1)}% MAPE</strong>. Seasonal categories (drainage) are hardest: only three years of history exist.</p>
                <Table head={["Series", "Avg complaints / month", "Best model", "MAE", "MAPE"]}>
                  {d.series.map((s) => (
                    <tr key={s.series}>
                      <td>{s.series}</td>
                      <td className="tabular-nums">{num(s.mean_monthly)}</td>
                      <td>{label(s.best)}</td>
                      <td className="tabular-nums">{num(s.best_mae, 1)}</td>
                      <td className="tabular-nums">{s.best_mape_pct === null ? "-" : `${num(s.best_mape_pct, 1)}%`}</td>
                    </tr>
                  ))}
                </Table>
              </>
            )}
          </Async>
        </Card>
      )}
    </>
  );
}
