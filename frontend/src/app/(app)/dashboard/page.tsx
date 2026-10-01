"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useState } from "react";
import { Columns, ForecastChart, MonthLines, RankBars } from "@/components/charts";
import { useDepartments } from "@/components/Filters";
import { Markdown } from "@/components/Markdown";
import { AiBadge, Async, Badge, Button, Card, CardHeader, ErrorBox, PageHeader, Skeleton, Spinner, Stat, Table, toneFor } from "@/components/ui";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { dateTime, label, monthLabel, num, pct } from "@/lib/format";
import { qs, useApi } from "@/lib/hooks";
import { ROLE_LABEL } from "@/lib/nav";
import type { Backlog, Brief, Choropleth, Complaint, EmergingFeed, ForecastVolume, Hotspots, KpiSummary, KpiTrendPoint, Page, ResourceForecast, SlaRow } from "@/lib/types";

const MapView = dynamic(() => import("@/components/MapView"), { ssr: false, loading: () => <Skeleton className="h-[340px] w-full" /> });

function BriefCard(): JSX.Element {
  const [brief, setBrief] = useState<Brief | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  async function load(refresh: boolean): Promise<void> {
    setBusy(true);
    setErr(null);
    try {
      setBrief(await api<Brief>(`/insights/brief${qs({ refresh })}`));
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Could not generate the brief");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Card>
      <CardHeader
        title="AI policy brief"
        subtitle="Monthly executive summary written from the KPIs, emerging issues and forecasts"
        action={
          <div className="flex items-center gap-2">
            {brief && <AiBadge label={brief.label} />}
            <Button size="sm" onClick={() => void load(false)} disabled={busy}>
              {busy ? <Spinner /> : null}
              {brief ? "Reload" : "Generate brief"}
            </Button>
            {brief && (
              <Button size="sm" variant="outline" onClick={() => void load(true)} disabled={busy}>
                Regenerate
              </Button>
            )}
          </div>
        }
      />
      {err && <ErrorBox message={err} />}
      {!brief && !err && <p className="text-sm text-muted-foreground">Click “Generate brief”. The first generation takes about 20 seconds; later loads are cached.</p>}
      {brief && <Markdown text={brief.markdown} />}
    </Card>
  );
}

function LiveFeed({ wardOnly }: { wardOnly: boolean }): JSX.Element {
  const state = useApi<Page<Complaint>>(`/complaints${qs({ page_size: 6, sort: "newest" })}`, { pollMs: 15000 });
  return (
    <Card>
      <CardHeader title="Live complaint feed" subtitle={wardOnly ? "Latest complaints in your ward · refreshes every 15 s" : "Latest complaints · refreshes every 15 s"} />
      <Async state={state} height="h-40">
        {(d) => (
          <ul className="divide-y divide-border">
            {d.items.map((c) => (
              <li key={c.id} className="py-2 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge tone={toneFor(c.status)}>{label(c.status)}</Badge>
                  <span className="font-medium">{label(c.category)}</span>
                  <span className="text-muted-foreground">{c.ward_name}</span>
                  <span className="ml-auto text-xs text-muted-foreground">{dateTime(c.submitted_at)}</span>
                </div>
                <p className="mt-0.5 line-clamp-2 text-muted-foreground">{c.description}</p>
              </li>
            ))}
          </ul>
        )}
      </Async>
      <Link href="/complaints" className="mt-2 inline-block text-sm text-primary underline">
        Open the complaints explorer
      </Link>
    </Card>
  );
}

function EmergingCard(): JSX.Element {
  const state = useApi<EmergingFeed>("/emerging-issues");
  return (
    <Card>
      <CardHeader title="Emerging issues" subtitle="Spikes against each area's own recent baseline" action={<Link href="/emerging" className="text-sm text-primary underline">All issues</Link>} />
      <Async state={state}>
        {(d) =>
          d.issues.length === 0 ? (
            <p className="text-sm text-muted-foreground">No unusual spikes right now.</p>
          ) : (
            <ul className="space-y-3">
              {d.issues.slice(0, 3).map((i) => (
                <li key={i.id} className="text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{label(i.category)}</span>
                    <Badge tone={i.trend === "rising" ? "bad" : "neutral"}>{i.trend}</Badge>
                    <span className="text-muted-foreground">{i.wards.slice(0, 3).map((w) => w.name).join(", ")}</span>
                    <span className="ml-auto tabular-nums text-muted-foreground">{i.recent_complaints} in 14 days</span>
                  </div>
                  <p className="mt-0.5 text-muted-foreground">{i.summary.text}</p>
                </li>
              ))}
            </ul>
          )
        }
      </Async>
    </Card>
  );
}

export default function OverviewPage(): JSX.Element {
  const { user } = useAuth();
  const role = user?.role ?? "analyst";
  const isWard = role === "ward_officer";
  const isDept = role === "department_head";
  const summary = useApi<KpiSummary>("/kpis/summary?months=1");
  const trend = useApi<KpiTrendPoint[]>("/kpis/trend");
  const depts = useDepartments();
  const myCategory = depts.find((d) => d.id === user?.department_id)?.category ?? null;

  const forecast = useApi<ForecastVolume>(isWard ? null : "/forecasts/volume?horizon=6");
  const sla = useApi<SlaRow[]>(isDept ? "/service-gaps/sla?by=ward&months=3" : null);
  const backlog = useApi<Backlog>(isDept || isWard ? "/service-gaps/backlog" : null);
  const resources = useApi<ResourceForecast>(isDept ? "/forecasts/resources?horizon=3" : null);
  const choro = useApi<Choropleth>(isWard ? "/gis/choropleth?metric=gap_index" : null);
  const hot = useApi<Hotspots>(isWard ? "/gis/hotspots" : null);

  const s = summary.data;
  const hib = s?.higher_is_better ?? {};
  const tile = (key: keyof KpiSummary["current"], title: string, fmt: (v: number | null | undefined) => string): JSX.Element => (
    <Stat label={title} value={fmt(s?.current[key] ?? null)} change={s?.change_vs_previous_pct[key] ?? null} higherIsBetter={hib[key] ?? true} note="vs last month" />
  );

  return (
    <>
      <PageHeader
        title={isWard ? "Ward overview" : isDept ? "Department overview" : "City overview"}
        subtitle={s ? `${ROLE_LABEL[role]} view · ${monthLabel(s.period.to)} · data as of ${s.as_of}` : ROLE_LABEL[role]}
      />
      {summary.error && !s ? (
        <ErrorBox message={summary.error} onRetry={summary.reload} />
      ) : !s ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {tile("complaints", "Complaints received", (v) => num(v))}
          {tile("sla_compliance_pct", "SLA compliance", (v) => pct(v))}
          {tile("resolution_rate", "Resolution rate", (v) => pct(v))}
          {tile("avg_resolution_hours", "Avg resolution time", (v) => `${num(v, 0)} h`)}
          {tile("satisfaction", "Citizen satisfaction", (v) => `${num(v, 2)} / 5`)}
          <Stat label="Open backlog" value={num(s.current.open_backlog)} note="" />
          {tile("repeat_rate_pct", "Repeat complaints", (v) => pct(v))}
          {tile("efficiency_score", "Efficiency score", (v) => num(v, 0))}
        </div>
      )}
      {s && <p className="mt-2 text-xs text-muted-foreground">The newest month looks worse on resolution rate, resolution time and backlog partly because recent complaints are still being worked on.{isWard || isDept ? " Figures cover your assigned area." : ""}</p>}

      <div className="mt-4 grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader title="Complaints per month" subtitle="Last 24 months" />
          <Async state={trend} height="h-64">{(d) => <MonthLines data={d.map((p) => ({ month: p.month, complaints: p.complaints, sla: p.sla_compliance_pct }))} series={[{ key: "complaints", label: "Complaints" }]} />}</Async>
          <p className="mt-1 text-xs text-muted-foreground">Complaints peak in the monsoon (June to September).</p>
        </Card>
        <EmergingCard />
      </div>

      {!isWard && !isDept && (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader title="Complaint forecast" subtitle="Next 6 months with 95% interval" action={<Link href="/forecasts" className="text-sm text-primary underline">Details</Link>} />
            <Async state={forecast} height="h-72">{(d) => (d.forecast.length ? <ForecastChart history={d.history} forecast={d.forecast} /> : <p className="text-sm text-muted-foreground">Not enough history to forecast.</p>)}</Async>
          </Card>
          <LiveFeed wardOnly={false} />
        </div>
      )}
      {!isWard && !isDept && (
        <div className="mt-4">
          <BriefCard />
        </div>
      )}

      {isDept && (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader title="SLA breaches by ward" subtitle="Share of complaints breaching the SLA, last 3 months" />
            <Async state={sla} height="h-72">{(d) => <RankBars data={d.slice(0, 12).map((r) => ({ name: r.name ?? String(r.key), rate: r.breach_rate_pct }))} valueKey="rate" format={(v) => `${v}%`} seriesColor={2} />}</Async>
          </Card>
          <Card>
            <CardHeader title="Backlog aging" subtitle="Open complaints by age" />
            <Async state={backlog} height="h-72">{(d) => <Columns data={d.by_age.map((a) => ({ bucket: a.bucket, open: a.count, overdue: a.overdue }))} xKey="bucket" series={[{ key: "open", label: "Open" }, { key: "overdue", label: "Overdue" }]} />}</Async>
          </Card>
          <Card className="lg:col-span-2">
            <CardHeader title="Resource forecast" subtitle="Crew-days needed vs capacity, next 3 months" />
            <Async state={resources}>
              {(d) => (
                <Table head={["Department", ...d.months.map(monthLabel), "Capacity / month"]}>
                  {d.categories.filter((c) => !myCategory || c.category === myCategory).map((c) => (
                    <tr key={c.category}>
                      <td>{c.department}</td>
                      {c.crew_days_needed.map((n, i) => (
                        <td key={i} className="tabular-nums">{num(n)}{(c.shortfall[i] ?? 0) > 0 && <Badge tone="bad" className="ml-1">short</Badge>}</td>
                      ))}
                      <td className="tabular-nums">{num(c.capacity_crew_days_per_month)}</td>
                    </tr>
                  ))}
                </Table>
              )}
            </Async>
          </Card>
          <div className="lg:col-span-2"><LiveFeed wardOnly={false} /></div>
        </div>
      )}

      {isWard && (
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader title="Your ward" subtitle="Hotspots of repeated complaints" action={<Link href="/map" className="text-sm text-primary underline">Full map</Link>} />
            <MapView choropleth={choro.data} heat={null} hotspots={hot.data} layers={{ choropleth: true, heat: false, hotspots: true }} height={340} />
          </Card>
          <LiveFeed wardOnly />
          <Card className="lg:col-span-2">
            <CardHeader title="Backlog aging" subtitle="Open complaints in your ward by age" />
            <Async state={backlog}>{(d) => <Columns data={d.by_age.map((a) => ({ bucket: a.bucket, open: a.count, overdue: a.overdue }))} xKey="bucket" series={[{ key: "open", label: "Open" }, { key: "overdue", label: "Overdue" }]} height={220} />}</Async>
          </Card>
        </div>
      )}
    </>
  );
}
