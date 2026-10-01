"use client";

import { Download, Printer } from "lucide-react";
import { useState } from "react";
import { CategorySelect } from "@/components/Filters";
import { Markdown } from "@/components/Markdown";
import { AiBadge, Button, Card, EmptyOrTable, ErrorBox, Field, PageHeader, Select, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { downloadText, label, METRIC_LABELS, toCsv } from "@/lib/format";
import { qs } from "@/lib/hooks";
import type { Brief, Complaint, EmergingFeed, ForecastVolume, GapIndex, KpiSummary, Page, Rankings, SlaRow } from "@/lib/types";

type Row = Record<string, string | number | null>;
interface Built {
  title: string;
  rows: Row[];
  brief?: Brief;
}

const REPORTS: { id: string; name: string; description: string }[] = [
  { id: "kpi", name: "KPI summary", description: "All indicators with previous-period and year-ago comparison" },
  { id: "ward-ranking", name: "Ward ranking", description: "Wards ranked by a chosen indicator" },
  { id: "gap-index", name: "Service Gap Index", description: "Composite gap score and components per ward" },
  { id: "sla", name: "SLA breaches by department", description: "Breach counts and rates" },
  { id: "emerging", name: "Emerging issues", description: "Current spikes with affected wards" },
  { id: "forecast", name: "Complaint forecast", description: "6-month city forecast with intervals" },
  { id: "complaints", name: "Complaints extract", description: "Up to 200 most recent complaints (PII masked per role)" },
  { id: "brief", name: "AI policy brief", description: "Monthly executive summary (AI-generated)" },
];

export default function ReportsPage(): JSX.Element {
  const [kind, setKind] = useState("kpi");
  const [months, setMonths] = useState(3);
  const [metric, setMetric] = useState("sla_compliance_pct");
  const [category, setCategory] = useState("");
  const [built, setBuilt] = useState<Built | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function build(): Promise<void> {
    setBusy(true);
    setErr(null);
    setBuilt(null);
    try {
      let b: Built;
      if (kind === "kpi") {
        const d = await api<KpiSummary>(`/kpis/summary${qs({ months, category })}`);
        b = { title: `KPI summary ${d.period.from} to ${d.period.to}`, rows: Object.keys(METRIC_LABELS).filter((k) => k in d.current).map((k) => ({
          Indicator: METRIC_LABELS[k] ?? k, Current: d.current[k as keyof typeof d.current] ?? null, "Previous period": d.previous_period[k as keyof typeof d.current] ?? null,
          "Change %": d.change_vs_previous_pct[k] ?? null, "Last year": d.same_period_last_year[k as keyof typeof d.current] ?? null, "YoY change %": d.change_vs_last_year_pct[k] ?? null })) };
      } else if (kind === "ward-ranking") {
        const d = await api<Rankings>(`/kpis/rankings${qs({ by: "ward", metric, months, category })}`);
        b = { title: `Ward ranking by ${METRIC_LABELS[metric]} (${d.period.from} to ${d.period.to})`, rows: d.items.map((i) => ({ Rank: i.rank, Ward: i.name, Code: i.code, [METRIC_LABELS[metric] ?? metric]: (i[metric as keyof typeof i] as number | null) ?? null, Complaints: i.complaints })) };
      } else if (kind === "gap-index") {
        const d = await api<GapIndex>(`/service-gaps/index${qs({ months: Math.max(months, 3), category })}`);
        b = { title: `Service Gap Index (${d.period.from} to ${d.period.to})`, rows: d.items.map((i) => ({ Rank: i.rank, Ward: i.name, SGI: i.sgi, "Breach rate": Number((i.breach_rate * 100).toFixed(1)), "Delay ratio": Number(i.delay_ratio.toFixed(2)), "Backlog share %": Number((i.backlog_share * 100).toFixed(1)), "Repeat %": Number((i.repeat_rate * 100).toFixed(1)), Satisfaction: i.satisfaction })) };
      } else if (kind === "sla") {
        const d = await api<SlaRow[]>(`/service-gaps/sla${qs({ by: "department", months: Math.max(months, 3), category })}`);
        b = { title: "SLA breaches by department", rows: d.map((r) => ({ Department: r.name ?? String(r.key), Complaints: r.complaints, Breached: r.breached, "Breach rate %": r.breach_rate_pct, "Avg resolution (h)": r.avg_resolution_hours, Open: r.open })) };
      } else if (kind === "emerging") {
        const d = await api<EmergingFeed>(`/emerging-issues${qs({ category })}`);
        b = { title: `Emerging issues as of ${d.as_of}`, rows: d.issues.map((i) => ({ Category: label(i.category), Trend: i.trend, Severity: i.severity, "Complaints (14 days)": i.recent_complaints, Wards: i.wards.map((w) => w.name).join("; "), Summary: i.summary.text })) };
      } else if (kind === "forecast") {
        const d = await api<ForecastVolume>(`/forecasts/volume${qs({ category, horizon: 6 })}`);
        b = { title: `Complaint forecast${category ? ` - ${label(category)}` : ""} (model: ${d.model ?? "n/a"})`, rows: d.forecast.map((f) => ({ Month: f.month, Forecast: f.mean, Low: f.lo, High: f.hi })) };
      } else if (kind === "complaints") {
        const d = await api<Page<Complaint>>(`/complaints${qs({ category, page_size: 200 })}`);
        b = { title: "Complaints extract", rows: d.items.map((c) => ({ ID: c.id, Submitted: c.submitted_at, Ward: c.ward_name, Category: c.category, Status: c.status, "SLA breached": c.sla_breached === null ? "" : c.sla_breached ? "yes" : "no", Description: c.description })) };
      } else {
        const d = await api<Brief>("/insights/brief");
        b = { title: `AI policy brief ${d.month}`, rows: [], brief: d };
      }
      setBuilt(b);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not build the report");
    } finally {
      setBusy(false);
    }
  }

  const stamp = new Date().toISOString().slice(0, 10);
  return (
    <>
      <PageHeader title="Reports" subtitle="Build a report, then download it as CSV or save it as PDF (print dialog → Save as PDF)" />
      <Card className="no-print mb-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <Field label="Report">
            <Select value={kind} onChange={(e) => { setKind(e.target.value); setBuilt(null); }}>
              {REPORTS.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Period">
            <Select value={months} onChange={(e) => setMonths(Number(e.target.value))}>
              {[1, 3, 6, 12].map((m) => (
                <option key={m} value={m}>
                  Last {m} month{m > 1 ? "s" : ""}
                </option>
              ))}
            </Select>
          </Field>
          {kind === "ward-ranking" && (
            <Field label="Indicator">
              <Select value={metric} onChange={(e) => setMetric(e.target.value)}>
                {["sla_compliance_pct", "resolution_rate", "avg_resolution_hours", "satisfaction", "complaints_per_10k", "repeat_rate_pct", "efficiency_score", "open_backlog"].map((m) => (
                  <option key={m} value={m}>
                    {METRIC_LABELS[m]}
                  </option>
                ))}
              </Select>
            </Field>
          )}
          <CategorySelect value={category} onChange={setCategory} />
          <div className="flex items-end">
            <Button onClick={() => void build()} disabled={busy}>
              {busy && <Spinner />}Build report
            </Button>
          </div>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">{REPORTS.find((r) => r.id === kind)?.description}</p>
      </Card>
      {err && <ErrorBox message={err} />}
      {built && (
        <Card className="print-area">
          <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
            <div>
              <h2 className="text-lg font-semibold">{built.title}</h2>
              <p className="text-xs text-muted-foreground">Sahyadripur Municipal Corporation · generated {stamp} · SG-DSS (synthetic demonstration data)</p>
            </div>
            <div className="no-print flex gap-2">
              {built.rows.length > 0 && (
                <Button variant="outline" onClick={() => downloadText(`sgdss-${kind}-${stamp}.csv`, toCsv(built.rows))}>
                  <Download className="h-4 w-4" /> CSV
                </Button>
              )}
              <Button variant="outline" onClick={() => window.print()}>
                <Printer className="h-4 w-4" /> Save as PDF
              </Button>
            </div>
          </div>
          {built.brief ? (
            <>
              <AiBadge label={built.brief.label} />
              <div className="mt-2"><Markdown text={built.brief.markdown} /></div>
            </>
          ) : (
            <EmptyOrTable rows={built.rows} />
          )}
        </Card>
      )}
    </>
  );
}
