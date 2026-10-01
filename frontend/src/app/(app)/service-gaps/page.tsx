"use client";

import { useState } from "react";
import { Columns, RankBars } from "@/components/charts";
import { CategorySelect } from "@/components/Filters";
import { Async, Badge, Card, CardHeader, Empty, Field, PageHeader, Select, Table, Tabs } from "@/components/ui";
import { dateTime, label, num, pct } from "@/lib/format";
import { qs, useApi } from "@/lib/hooks";
import type { Backlog, Distribution, GapIndex, RepeatHotspot, SlaRow } from "@/lib/types";

type Tab = "index" | "sla" | "distribution" | "backlog" | "repeat";

export default function ServiceGapsPage(): JSX.Element {
  const [tab, setTab] = useState<Tab>("index");
  const [months, setMonths] = useState(12);
  const [category, setCategory] = useState("");
  const [by, setBy] = useState("department");

  const index = useApi<GapIndex>(tab === "index" ? `/service-gaps/index${qs({ months, category })}` : null);
  const sla = useApi<SlaRow[]>(tab === "sla" ? `/service-gaps/sla${qs({ by, months, category })}` : null);
  const dist = useApi<Distribution>(tab === "distribution" ? `/service-gaps/distribution${qs({ months, category })}` : null);
  const backlog = useApi<Backlog>(tab === "backlog" ? `/service-gaps/backlog${qs({ category })}` : null);
  const repeat = useApi<RepeatHotspot[]>(tab === "repeat" ? `/service-gaps/repeat-hotspots${qs({ days: 90, category })}` : null);

  return (
    <>
      <PageHeader
        title="Service gaps"
        subtitle="Where and how the city is missing its service levels"
        actions={
          <>
            <div className="w-40"><CategorySelect value={category} onChange={setCategory} /></div>
            {tab !== "backlog" && tab !== "repeat" && (
              <Field label="Period" className="w-32">
                <Select value={months} onChange={(e) => setMonths(Number(e.target.value))}>
                  {[3, 6, 12, 24, 36].map((m) => (
                    <option key={m} value={m}>
                      {m} months
                    </option>
                  ))}
                </Select>
              </Field>
            )}
          </>
        }
      />
      <Tabs
        value={tab}
        onChange={setTab}
        items={[
          { id: "index", label: "Service Gap Index" },
          { id: "sla", label: "SLA breaches" },
          { id: "distribution", label: "Resolution times" },
          { id: "backlog", label: "Backlog aging" },
          { id: "repeat", label: "Repeat hotspots" },
        ]}
      />

      {tab === "index" && (
        <Async state={index} height="h-96">
          {(d) => (
            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader title="Service Gap Index by ward" subtitle={`0 = best, 100 = worst · ${d.period.from} to ${d.period.to}`} />
                <RankBars data={d.items.map((i) => ({ name: i.name, sgi: i.sgi }))} valueKey="sgi" seriesColor={4} format={(v) => num(v, 0)} />
              </Card>
              <Card>
                <CardHeader title="What drives the index" subtitle="Weights: 35% SLA breaches, 20% delay vs SLA, 20% backlog, 15% repeats, 10% dissatisfaction" />
                <Table head={["#", "Ward", "SGI", "Breach", "Delay", "Backlog", "Repeat", "Rating"]}>
                  {d.items.slice(0, 10).map((i) => (
                    <tr key={i.ward_id}>
                      <td>{i.rank}</td>
                      <td>{i.name}</td>
                      <td><Badge tone={i.sgi >= 66 ? "bad" : i.sgi >= 33 ? "warn" : "good"}>{num(i.sgi, 0)}</Badge></td>
                      <td className="tabular-nums">{pct(i.breach_rate * 100, 0)}</td>
                      <td className="tabular-nums">{num(i.delay_ratio, 2)}×</td>
                      <td className="tabular-nums">{pct(i.backlog_share * 100, 0)}</td>
                      <td className="tabular-nums">{pct(i.repeat_rate * 100, 1)}</td>
                      <td className="tabular-nums">{num(i.satisfaction, 1)}</td>
                    </tr>
                  ))}
                </Table>
              </Card>
            </div>
          )}
        </Async>
      )}

      {tab === "sla" && (
        <Card>
          <CardHeader
            title="SLA breach rate"
            subtitle="A complaint breaches when resolved after its SLA, or still open past it"
            action={
              <Field label="Group by" className="w-36">
                <Select value={by} onChange={(e) => setBy(e.target.value)}>
                  <option value="department">Department</option>
                  <option value="ward">Ward</option>
                  <option value="month">Month</option>
                </Select>
              </Field>
            }
          />
          <Async state={sla} height="h-80">
            {(d) => (
              <>
                <RankBars data={d.map((r) => ({ name: String(r.name ?? r.key), rate: r.breach_rate_pct }))} valueKey="rate" seriesColor={2} format={(v) => `${v}%`} />
                <Table head={["Group", "Complaints", "Breached", "Breach rate", "Avg resolution (h)", "Open"]} className="mt-3">
                  {d.map((r) => (
                    <tr key={String(r.key)}>
                      <td>{r.name ?? String(r.key)}</td>
                      <td className="tabular-nums">{num(r.complaints)}</td>
                      <td className="tabular-nums">{num(r.breached)}</td>
                      <td className="tabular-nums">{pct(r.breach_rate_pct)}</td>
                      <td className="tabular-nums">{num(r.avg_resolution_hours, 1)}</td>
                      <td className="tabular-nums">{num(r.open)}</td>
                    </tr>
                  ))}
                </Table>
              </>
            )}
          </Async>
        </Card>
      )}

      {tab === "distribution" && (
        <Async state={dist} height="h-80">
          {(d) => (
            <Card>
              <CardHeader title="How long do resolutions take?" subtitle={`Resolved complaints since ${d.window_from}`} />
              <Columns data={d.buckets} xKey="label" series={[{ key: "count", label: "Complaints" }]} height={280} />
              <div className="mt-3 flex flex-wrap gap-4 text-sm">
                {Object.entries(d.percentiles_hours).map(([k, v]) => (
                  <span key={k}>
                    <span className="text-muted-foreground">{k.toUpperCase()}:</span> <strong className="tabular-nums">{num(v, 0)} h</strong>
                  </span>
                ))}
              </div>
            </Card>
          )}
        </Async>
      )}

      {tab === "backlog" && (
        <Async state={backlog} height="h-80">
          {(d) => (
            <div className="grid gap-4 lg:grid-cols-2">
              <Card>
                <CardHeader title="Open complaints by age" subtitle={`${num(d.total_open)} open · ${num(d.total_overdue)} past their SLA`} />
                <Columns data={d.by_age.map((a) => ({ bucket: a.bucket, open: a.count, overdue: a.overdue }))} xKey="bucket" series={[{ key: "open", label: "Open" }, { key: "overdue", label: "Overdue" }]} />
              </Card>
              <Card>
                <CardHeader title="Backlog by department" />
                <Table head={["Department", "Open", "Overdue"]}>
                  {d.by_department.map((r) => (
                    <tr key={r.category}>
                      <td>{r.name}</td>
                      <td className="tabular-nums">{num(r.open)}</td>
                      <td className="tabular-nums">{num(r.overdue)}</td>
                    </tr>
                  ))}
                </Table>
              </Card>
            </div>
          )}
        </Async>
      )}

      {tab === "repeat" && (
        <Card>
          <CardHeader title="Repeat-complaint hotspots" subtitle="Places (~275 m cells) with 5+ complaints of the same type in the last 90 days" />
          <Async state={repeat}>
            {(d) =>
              d.length === 0 ? (
                <Empty>No repeat hotspots in this period.</Empty>
              ) : (
                <Table head={["Ward", "Category", "Complaints", "Last complaint", "Location"]}>
                  {d.map((r, i) => (
                    <tr key={i}>
                      <td>{r.ward}</td>
                      <td>{label(r.category)}</td>
                      <td className="tabular-nums">{r.complaints}</td>
                      <td>{dateTime(r.last_at)}</td>
                      <td className="tabular-nums text-muted-foreground">{r.lat.toFixed(4)}, {r.lon.toFixed(4)}</td>
                    </tr>
                  ))}
                </Table>
              )
            }
          </Async>
        </Card>
      )}
    </>
  );
}
