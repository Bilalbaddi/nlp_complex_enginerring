"use client";

import { useState } from "react";
import { CategorySelect } from "@/components/Filters";
import { AiBadge, Async, Badge, Card, CardHeader, Empty, PageHeader, Table } from "@/components/ui";
import { dateTime, label, num } from "@/lib/format";
import { qs, useApi } from "@/lib/hooks";
import type { EmergingFeed, Topic } from "@/lib/types";

export default function EmergingPage(): JSX.Element {
  const [category, setCategory] = useState("");
  const feed = useApi<EmergingFeed>(`/emerging-issues${qs({ category })}`);
  const topics = useApi<{ n_complaints: number; method?: string; topics: Topic[] }>("/emerging-issues/topics");

  return (
    <>
      <PageHeader
        title="Emerging issues"
        subtitle="Civic problems rising faster than normal, with affected wards and sample complaints"
        actions={<div className="w-48"><CategorySelect value={category} onChange={setCategory} /></div>}
      />
      <Async state={feed} height="h-64">
        {(d) => (
          <>
            <p className="mb-3 text-sm text-muted-foreground">
              Data as of {d.as_of}. An issue is flagged when the last 14 days run at least {d.thresholds.ratio_min}× the area&apos;s own 8-week baseline (z ≥ {d.thresholds.z_min}, ≥ {d.thresholds.min_recent} complaints).
            </p>
            {d.issues.length === 0 ? (
              <Empty>No unusual spikes detected{category ? " for this category" : ""}.</Empty>
            ) : (
              <div className="grid gap-4 lg:grid-cols-2">
                {d.issues.map((i) => (
                  <Card key={i.id}>
                    <div className="flex flex-wrap items-center gap-2">
                      <h2 className="text-base font-semibold">{label(i.category)}</h2>
                      <Badge tone={i.trend === "rising" ? "bad" : i.trend === "falling" ? "good" : "neutral"}>{i.trend}</Badge>
                      <Badge tone="info">severity {num(i.severity, 1)}</Badge>
                      <span className="ml-auto text-sm tabular-nums text-muted-foreground">{i.recent_complaints} complaints in 14 days</span>
                    </div>
                    <div className="mt-2 rounded-md bg-muted p-3 text-sm">
                      <p>{i.summary.text}</p>
                      <div className="mt-1.5">{i.summary.ai_generated ? <AiBadge label={i.summary} /> : <span className="text-xs text-muted-foreground">Rule-based summary</span>}</div>
                    </div>
                    <Table head={["Ward", "Last 14 days", "Normal / week", "× normal"]} className="mt-2">
                      {i.wards.map((w) => (
                        <tr key={w.ward_id}>
                          <td>{w.name}</td>
                          <td className="tabular-nums">{w.recent}</td>
                          <td className="tabular-nums">{num(w.baseline_weekly, 1)}</td>
                          <td className="tabular-nums">{num(w.ratio, 1)}×</td>
                        </tr>
                      ))}
                    </Table>
                    <p className="mt-2 text-xs font-medium text-muted-foreground">Sample complaints</p>
                    <ul className="mt-1 space-y-1 text-sm">
                      {i.samples.slice(0, 3).map((s) => (
                        <li key={s.id} className="text-muted-foreground">
                          <span className="text-foreground">{s.ward}</span> · {dateTime(s.submitted_at)}: {s.text}
                        </li>
                      ))}
                    </ul>
                  </Card>
                ))}
              </div>
            )}
          </>
        )}
      </Async>

      <Card className="mt-4">
        <CardHeader title="Topic discovery" subtitle="Themes in the last 14 days vs the 14 days before (TF-IDF + NMF clustering of complaint text)" />
        <Async state={topics}>
          {(d) => (
            <Table head={["Key terms", "Last 14 days", "Previous 14", "Growth", "Example"]}>
              {d.topics.map((t) => (
                <tr key={t.topic}>
                  <td className="font-medium">{t.terms.slice(0, 4).join(", ")}</td>
                  <td className="tabular-nums">{t.recent}</td>
                  <td className="tabular-nums">{t.previous}</td>
                  <td><Badge tone={t.growth >= 1.5 ? "bad" : t.growth >= 1.1 ? "warn" : "neutral"}>{num(t.growth, 2)}×</Badge></td>
                  <td className="max-w-xs truncate text-muted-foreground" title={t.examples[0]}>{t.examples[0]}</td>
                </tr>
              ))}
            </Table>
          )}
        </Async>
      </Card>
    </>
  );
}
