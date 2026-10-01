"use client";

import "leaflet/dist/leaflet.css";
import L from "leaflet";
import { useEffect } from "react";
import { CircleMarker, GeoJSON, MapContainer, Polygon, TileLayer, Tooltip, useMap } from "react-leaflet";
import { label, num, scaleColor } from "@/lib/format";
import type { Choropleth, Heatmap, Hotspots } from "@/lib/types";

export interface MapLayers {
  choropleth: boolean;
  heat: boolean;
  hotspots: boolean;
}

function FitBounds({ choropleth }: { choropleth: Choropleth | null }): null {
  const map = useMap();
  useEffect(() => {
    if (!choropleth) return;
    const b = L.geoJSON(choropleth as unknown as GeoJSON.FeatureCollection).getBounds();
    if (b.isValid()) map.fitBounds(b, { padding: [10, 10] });
  }, [choropleth, map]);
  return null;
}

export default function MapView({
  choropleth,
  heat,
  hotspots,
  layers,
  height = 520,
  onWardClick,
}: {
  choropleth: Choropleth | null;
  heat: Heatmap | null;
  hotspots: Hotspots | null;
  layers: MapLayers;
  height?: number;
  onWardClick?: (wardId: number) => void;
}): JSX.Element {
  const min = choropleth?.min ?? 0;
  const max = choropleth?.max ?? 1;
  const higher = choropleth?.higher_is_better ?? false;
  return (
    <div role="application" aria-label="Map of complaints by ward" style={{ height }} className="overflow-hidden rounded-lg border border-border">
      <MapContainer center={[20.0, 73.78]} zoom={12} style={{ height: "100%", width: "100%" }} scrollWheelZoom>
        <TileLayer attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
        <FitBounds choropleth={choropleth} />
        {layers.choropleth && choropleth && (
          <GeoJSON
            key={`${choropleth.metric}-${choropleth.period.from}-${choropleth.period.to}`}
            data={choropleth as unknown as GeoJSON.FeatureCollection}
            style={(f) => ({
              color: "hsl(215 20% 35%)",
              weight: 1,
              fillOpacity: layers.heat ? 0.35 : 0.65,
              fillColor: scaleColor((f?.properties as { value: number | null }).value, min, max, higher),
            })}
            onEachFeature={(f, layer) => {
              const p = f.properties as { name: string; code: string; ward_id: number; value: number | null };
              layer.bindTooltip(`${p.name} (${p.code}): ${p.value === null ? "no data" : num(p.value, 1)}`, { sticky: true });
              if (onWardClick) layer.on("click", () => onWardClick(p.ward_id));
            }}
          />
        )}
        {layers.heat &&
          heat?.points.map(([lat, lon, w], i) => (
            <CircleMarker
              key={i}
              center={[lat, lon]}
              radius={3 + 13 * Math.sqrt(w / Math.max(heat.max, 1))}
              pathOptions={{ stroke: false, fillColor: "#d9480f", fillOpacity: 0.25 + 0.4 * (w / Math.max(heat.max, 1)) }}
            >
              <Tooltip>{w} complaints</Tooltip>
            </CircleMarker>
          ))}
        {layers.hotspots &&
          hotspots?.clusters.map((c) => (
            <span key={c.id}>
              {c.hull && (
                <Polygon
                  positions={(c.hull.coordinates[0] ?? []).map(([lon, lat]) => [lat, lon] as [number, number])}
                  pathOptions={{ color: "#7c3aed", weight: 2, fillOpacity: 0.2 }}
                />
              )}
              <CircleMarker center={c.center} radius={6} pathOptions={{ color: "#7c3aed", fillColor: "#7c3aed", fillOpacity: 0.9 }}>
                <Tooltip>
                  Hotspot: {c.complaints} complaints, mostly {label(c.dominant_category)} ({Math.round(c.category_share * 100)}%){c.ward ? `, ${c.ward}` : ""}
                </Tooltip>
              </CircleMarker>
            </span>
          ))}
      </MapContainer>
    </div>
  );
}
