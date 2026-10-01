"use client";

import dynamic from "next/dynamic";
import { useState } from "react";
import { CategorySelect } from "@/components/Filters";
import { Async, Card, CardHeader, Field, PageHeader, Select, Skeleton, Table } from "@/components/ui";
import type { MapLayers } from "@/components/MapView";
import { addMonths, label, METRIC_LABELS, monthLabel, monthRange, num } from "@/lib/format";
import { qs, useApi } from "@/lib/hooks";
import type { Choropleth, Heatmap, Hotspots, KpiSummary } from "@/lib/types";

const MapView = dynamic(() => import("@/components/MapView"), { ssr: false, loading: () => <Skeleton className="h-[520px] w-full" /> });
const FIRST_MONTH = "2023-10";
const METRICS = ["complaints_per_10k", "sla_compliance_pct", "avg_resolution_hours", "resolution_rate", "satisfaction", "repeat_rate_pct", "efficiency_score", "open_backlog", "gap_index"];

export default function MapPage(): JSX.Element {
  const summary = useApi<KpiSummary>("/kpis/summary");
  const latest = summary.data?.period.to ?? null;
  const [metric, setMetric] = useState("complaints_per_10k");
  const [category, setCategory] = useState("");
  const [length, setLength] = useState(6);
  const [endIdx, setEndIdx] = useState<number | null>(null);
  const [layers, setLayers] = useState<MapLayers>({ choropleth: true, heat: false, hotspots: true });

  const months = latest ? monthRange(FIRST_MONTH, latest) : [];
  const end = months.length ? months[endIdx ?? months.length - 1] : null;
  const from = end ? addMonths(end, -(length - 1)) : null;
  const filter = end && from ? { category, date_from: from, date_to: end } : null;

  const choro = useApi<Choropleth>(filter ? `/gis/choropleth${qs({ metric, ...filter })}` : null);
  const heat = useApi<Heatmap>(filter && layers.heat ? `/gis/heatmap${qs(filter)}` : null);
  const hot = useApi<Hotspots>(filter && layers.hotspots ? `/gis/hotspots${qs(filter)}` : null);

  const toggle = (k: keyof MapLayers): void => setLayers((l) => ({ ...l, [k]: !l[k] }));
  const rows = [...(choro.data?.features ?? [])].sort((a, b) => (b.properties.value ?? -1) - (a.properties.value ?? -1));

  return (
    <>
      <PageHeader title="GIS Map" subtitle="Ward choropleths, complaint heatmap and DBSCAN hotspots for any period" />
      <Card className="mb-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Colour wards by">
            <Select value={metric} onChange={(e) => setMetric(e.target.value)}>
              {METRICS.map((m) => (
                <option key={m} value={m}>
                  {METRIC_LABELS[m] ?? label(m)}
                </option>
              ))}
            </Select>
          </Field>
          <CategorySelect value={category} onChange={setCategory} />
          <Field label="Window length">
            <Select value={length} onChange={(e) => setLength(Number(e.target.value))}>
              {[1, 3, 6, 12].map((n) => (
                <option key={n} value={n}>
                  {n} month{n > 1 ? "s" : ""}
                </option>
              ))}
            </Select>
          </Field>
          <fieldset>
            <legend className="mb-1 text-xs font-medium text-muted-foreground">Layers</legend>
            <div className="flex flex-wrap gap-3 pt-1.5 text-sm">
              {(["choropleth", "heat", "hotspots"] as const).map((k) => (
                <label key={k} className="flex items-center gap-1.5">
                  <input type="checkbox" checked={layers[k]} onChange={() => toggle(k)} />
                  {k === "choropleth" ? "Wards" : k === "heat" ? "Heatmap" : "Hotspots"}
                </label>
              ))}
            </div>
          </fieldset>
        </div>
        {months.length > 0 && (
          <div className="mt-4">
            <label className="block text-xs font-medium text-muted-foreground" htmlFor="time-slider">
              Time slider: {from && monthLabel(from)} to {end && monthLabel(end)}
            </label>
            <input
              id="time-slider"
              type="range"
              min={length - 1}
              max={months.length - 1}
              value={endIdx ?? months.length - 1}
              onChange={(e) => setEndIdx(Number(e.target.value))}
              className="mt-1 w-full accent-[hsl(var(--primary))]"
            />
          </div>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="lg:col-span-2">
          <MapView choropleth={choro.data} heat={heat.data} hotspots={hot.data} layers={layers} />
          <p className="mt-2 text-xs text-muted-foreground">
            {choro.loading ? "Updating…" : choro.error ? choro.error : `${METRIC_LABELS[metric] ?? metric}: ${num(choro.data?.min, 1)} to ${num(choro.data?.max, 1)}`}
            {choro.data && (choro.data.higher_is_better ? " (higher is better; red = worse)" : " (lower is better; red = worse)")}
            {hot.data && layers.hotspots ? ` · ${hot.data.clusters.length} hotspots (${hot.data.params.algorithm}, ${hot.data.params.eps_m} m, min ${hot.data.params.min_samples})` : ""}
          </p>
        </div>
        <Card>
          <CardHeader title="Ward ranking" subtitle="Worst first for the selected metric" />
          <Async state={choro} height="h-64">
            {() => (
              <Table head={["Ward", METRIC_LABELS[metric] ?? metric]}>
                {(choro.data?.higher_is_better ? [...rows].reverse() : rows).map((f) => (
                  <tr key={f.properties.ward_id}>
                    <td>{f.properties.name}</td>
                    <td className="tabular-nums">{num(f.properties.value, 1)}</td>
                  </tr>
                ))}
              </Table>
            )}
          </Async>
        </Card>
      </div>

      {hot.data && layers.hotspots && hot.data.clusters.length > 0 && (
        <Card className="mt-4">
          <CardHeader title="Hotspot clusters" subtitle="Dense clusters of complaints found by DBSCAN" />
          <Table head={["#", "Ward", "Complaints", "Dominant category", "Share"]}>
            {hot.data.clusters.slice(0, 12).map((c, i) => (
              <tr key={c.id}>
                <td>{i + 1}</td>
                <td>{c.ward ?? "-"}</td>
                <td className="tabular-nums">{c.complaints}</td>
                <td>{label(c.dominant_category)}</td>
                <td>{Math.round(c.category_share * 100)}%</td>
              </tr>
            ))}
          </Table>
        </Card>
      )}
    </>
  );
}
