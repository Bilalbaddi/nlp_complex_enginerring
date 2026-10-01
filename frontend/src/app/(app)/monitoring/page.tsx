"use client";

import { useState } from "react";
import { Columns } from "@/components/charts";
import { Async, Badge, Card, CardHeader, PageHeader, Stat, Table, Tabs } from "@/components/ui";
import { dateTime, label, num } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { BacktestAll, LlmUsage, ModelRun } from "@/lib/types";

interface ClassMetrics {
  accuracy: number;
  macro_f1: number;
  macro_precision: number;
  macro_recall: number;
  labels: string[];
  confusion_matrix: number[][];
  per_class: Record<string, { precision: number; recall: number; f1: number; support: number }>;
}

const asClass = (v: unknown): ClassMetrics | null => (v && typeof v === "object" && "confusion_matrix" in v ? (v as ClassMetrics) : null);
const summaryOf = (m: Record<string, unknown>): ClassMetrics | null => asClass(m.vs_true_label) ?? asClass(m.vs_recorded_label) ?? asClass(m);

function Confusion({ m }: { m: ClassMetrics }): JSX.Element {
  const max = Math.max(...m.confusion_matrix.flat(), 1);
  return (
    <div className="overflow-x-auto">
      <table className="text-xs" aria-label="Confusion matrix: rows are the true category, columns the predicted category">
        <thead>
          <tr>
            <th className="p-1 text-left font-medium text-muted-foreground">true ↓ / predicted →</th>
            {m.labels.map((l) => (
              <th key={l} className="p-1 font-medium text-muted-foreground" title={l}>
                {l.slice(0, 5)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {m.confusion_matrix.map((row, i) => (
            <tr key={i}>
              <th className="whitespace-nowrap p-1 text-left font-medium">{label(m.labels[i])}</th>
              {row.map((v, j) => (
                <td key={j} className="p-0">
                  <div
                    className="flex h-7 w-11 items-center justify-center tabular-nums"
                    style={{ background: `hsl(var(--primary) / ${(0.08 + 0.82 * (v / max)).toFixed(2)})`, color: v / max > 0.55 ? "hsl(var(--primary-foreground))" : "inherit", outline: i === j ? "1px solid hsl(var(--foreground) / 0.4)" : undefined }}
                  >
                    {v}
                  </div>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function MonitoringPage(): JSX.Element {
  const [tab, setTab] = useState<"classifiers" | "models" | "forecast" | "llm">("classifiers");
  const models = useApi<ModelRun[]>("/nlp/models");
  const review = useApi<{ pending: number; reviewed: number; predicted: number; total: number }>("/nlp/review/stats");
  const usage = useApi<LlmUsage>(tab === "llm" ? "/llm/usage?days=7" : null);
  const bt = useApi<BacktestAll>(tab === "forecast" ? "/forecasts/backtest" : null);
  const [sel, setSel] = useState<number | null>(null);

  return (
    <>
      <PageHeader title="Model monitoring" subtitle="Classifier quality, review queue, forecast accuracy and LLM usage" />
      <Async state={review}>
        {(r) => (
          <div className="mb-4 grid gap-3 sm:grid-cols-4">
            <Stat label="Complaints with predictions" value={num(r.predicted)} note="" />
            <Stat label="Awaiting human review" value={num(r.pending)} />
            <Stat label="Reviewed by humans" value={num(r.reviewed)} />
            <Stat label="Low-confidence share" value={`${num((100 * r.pending) / Math.max(r.predicted, 1), 1)}%`} />
          </div>
        )}
      </Async>
      <Tabs
        value={tab}
        onChange={setTab}
        items={[
          { id: "classifiers", label: "Category classifiers" },
          { id: "models", label: "All model runs" },
          { id: "forecast", label: "Forecast accuracy" },
          { id: "llm", label: "LLM usage" },
        ]}
      />

      {tab === "classifiers" && (
        <Async state={models} height="h-72">
          {(all) => {
            const rows = all.filter((m) => m.task === "category" && summaryOf(m.metrics));
            const active = rows.find((m) => m.id === sel) ?? rows.find((m) => m.is_active) ?? rows[0];
            const metrics = active ? summaryOf(active.metrics) : null;
            return (
              <div className="grid gap-4 lg:grid-cols-2">
                <Card>
                  <CardHeader title="Classifier comparison" subtitle="Accuracy and macro-F1 against the generator's true labels (click a row for its confusion matrix)" />
                  <Table head={["Model", "Accuracy", "Macro F1", "Test n", "Active"]}>
                    {rows.map((m) => {
                      const s = summaryOf(m.metrics);
                      return (
                        <tr key={m.id} className="cursor-pointer hover:bg-muted/50" onClick={() => setSel(m.id)} aria-selected={active?.id === m.id}>
                          <td className={active?.id === m.id ? "font-semibold text-primary" : ""}>{label(m.name.replace("category_", ""))}</td>
                          <td className="tabular-nums">{s ? num(100 * s.accuracy, 1) + "%" : "-"}</td>
                          <td className="tabular-nums">{s ? num(s.macro_f1, 3) : "-"}</td>
                          <td className="tabular-nums">{m.n_test}</td>
                          <td>{m.is_active && <Badge tone="good">active</Badge>}</td>
                        </tr>
                      );
                    })}
                  </Table>
                  <p className="mt-2 text-xs text-muted-foreground">The synthetic data is cleaner than real complaints, so scores are optimistic. The LLM row is a small stratified sample.</p>
                </Card>
                <Card>
                  <CardHeader title={active ? `Confusion matrix: ${label(active.name)}` : "Confusion matrix"} />
                  {metrics ? <Confusion m={metrics} /> : <p className="text-sm text-muted-foreground">No data.</p>}
                </Card>
                {metrics && (
                  <Card className="lg:col-span-2">
                    <CardHeader title="Per-category precision / recall / F1" />
                    <Table head={["Category", "Precision", "Recall", "F1", "Support"]}>
                      {Object.entries(metrics.per_class).map(([c, v]) => (
                        <tr key={c}>
                          <td>{label(c)}</td>
                          <td className="tabular-nums">{num(v.precision, 3)}</td>
                          <td className="tabular-nums">{num(v.recall, 3)}</td>
                          <td className="tabular-nums">{num(v.f1, 3)}</td>
                          <td className="tabular-nums">{v.support}</td>
                        </tr>
                      ))}
                    </Table>
                  </Card>
                )}
              </div>
            );
          }}
        </Async>
      )}

      {tab === "models" && (
        <Card>
          <Async state={models}>
            {(all) => (
              <Table head={["Model", "Task", "Version", "Trained on", "Tested on", "Seconds", "Created", "Active"]}>
                {all.map((m) => (
                  <tr key={m.id}>
                    <td className="font-medium">{m.name}</td>
                    <td>{m.task}</td>
                    <td className="tabular-nums">{m.version}</td>
                    <td className="tabular-nums">{m.n_train || "-"}</td>
                    <td className="tabular-nums">{m.n_test || "-"}</td>
                    <td className="tabular-nums">{num(m.train_seconds, 1)}</td>
                    <td>{dateTime(m.created_at)}</td>
                    <td>{m.is_active && <Badge tone="good">active</Badge>}</td>
                  </tr>
                ))}
              </Table>
            )}
          </Async>
        </Card>
      )}

      {tab === "forecast" && (
        <Card>
          <CardHeader title="Forecast backtest" subtitle="Rolling-origin, 3 months ahead; error of the best model per series" />
          <Async state={bt}>
            {(d) => (
              <>
                <p className="mb-2 text-sm">Average MAPE: <strong>{num(d.avg_best_mape_pct, 1)}%</strong></p>
                <Table head={["Series", "Best model", "MAE", "MAPE"]}>
                  {d.series.map((s) => (
                    <tr key={s.series}>
                      <td>{s.series}</td>
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

      {tab === "llm" && (
        <Async state={usage} height="h-64">
          {(u) => (
            <>
              <div className="mb-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
                <Stat label="LLM calls (7 days)" value={num(u.calls)} />
                <Stat label="Tokens" value={num(u.prompt_tokens + u.completion_tokens)} />
                <Stat label="Avg latency (uncached)" value={`${num(u.avg_latency_ms)} ms`} />
                <Stat label="Cache hit rate" value={`${num(u.cache_hit_rate_pct, 1)}%`} />
                <Stat label="Errors" value={num(u.errors)} />
              </div>
              <div className="grid gap-4 lg:grid-cols-2">
                <Card>
                  <CardHeader title="Calls per day" />
                  <Columns data={u.daily.map((d) => ({ day: d.day.slice(5), calls: d.calls }))} xKey="day" series={[{ key: "calls", label: "Calls" }]} height={220} />
                </Card>
                <Card>
                  <CardHeader title="By purpose" />
                  <Table head={["Purpose", "Model", "Calls", "Tokens", "Latency", "Cache hits"]}>
                    {u.by_purpose.map((p, i) => (
                      <tr key={i}>
                        <td>{p.purpose}</td>
                        <td className="text-xs text-muted-foreground">{p.model}</td>
                        <td className="tabular-nums">{p.calls}</td>
                        <td className="tabular-nums">{num(p.tokens)}</td>
                        <td className="tabular-nums">{num(p.avg_latency_ms)} ms</td>
                        <td className="tabular-nums">{p.cache_hits}</td>
                      </tr>
                    ))}
                  </Table>
                </Card>
              </div>
            </>
          )}
        </Async>
      )}
    </>
  );
}
