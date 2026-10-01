"""GIS & spatial analytics (PostGIS for queries, scikit-learn DBSCAN for hotspots). All results are cached."""

import json
from typing import Any

import numpy as np
from scipy.spatial import ConvexHull
from sklearn.cluster import DBSCAN
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services import cache, kpis, service_gaps
from app.services.cube import Cube, month_add

EARTH_R = 6371008.8
CHOROPLETH_METRICS = [*kpis.METRICS, "gap_index"]

_FILTER = (
    "location IS NOT NULL AND (CAST(:c AS text) IS NULL OR category = :c) AND (CAST(:w AS int) IS NULL OR ward_id = :w) "
    "AND (CAST(:a AS date) IS NULL OR submitted_at >= CAST(:a AS date)) "
    "AND (CAST(:b AS date) IS NULL OR submitted_at < CAST(:b AS date) + interval '1 month')"
)


def _p(category: str | None, ward_id: int | None, date_from: str | None, date_to: str | None) -> dict[str, Any]:
    return {"c": category, "w": ward_id, "a": date_from[:7] + "-01" if date_from else None, "b": date_to[:7] + "-01" if date_to else None}


def heatmap(db: Session, *, category: str | None, ward_id: int | None, date_from: str | None, date_to: str | None,
            cell: float = 0.003) -> dict[str, Any]:
    """Complaint density on a regular lat/lon grid (cell in degrees; 0.003 is about 330 m). Returns [lat, lon, weight]."""
    p = {**_p(category, ward_id, date_from, date_to), "cell": cell}

    def run() -> dict[str, Any]:
        rows = db.execute(
            text(f"SELECT ST_Y(g), ST_X(g), n FROM (SELECT ST_SnapToGrid(location, :cell) AS g, count(*) AS n FROM complaints "
                 f"WHERE {_FILTER} GROUP BY 1) x ORDER BY n DESC LIMIT 5000"), p
        ).all()
        return {"points": [[round(r[0], 5), round(r[1], 5), int(r[2])] for r in rows], "max": int(rows[0][2]) if rows else 0,
                "cell_deg": cell}

    val, _ = cache.get_or_set(cache.make_key("gis:heat", p), 1800, run)
    return val


def _hull(pts: np.ndarray) -> dict[str, Any] | None:
    if len(pts) < 3:
        return None
    try:
        h = ConvexHull(pts[:, ::-1])  # (lon, lat)
    except Exception:  # collinear points
        return None
    ring = [[float(pts[i, 1]), float(pts[i, 0])] for i in h.vertices]
    return {"type": "Polygon", "coordinates": [ring + [ring[0]]]}


def hotspots(db: Session, cube: Cube, *, category: str | None, ward_id: int | None, date_from: str | None, date_to: str | None,
             eps_m: float = 100.0, min_samples: int = 50, max_points: int = 12000) -> dict[str, Any]:
    """DBSCAN (haversine metric) on complaint locations. Clusters = dense areas of complaints; noise is dropped."""
    p = {**_p(category, ward_id, date_from, date_to), "lim": max_points}
    key = cache.make_key("gis:hot", p, eps_m, min_samples)

    def run() -> dict[str, Any]:
        rows = db.execute(
            text(f"SELECT ST_Y(location), ST_X(location), category, ward_id FROM complaints WHERE {_FILTER} "
                 f"ORDER BY submitted_at DESC LIMIT :lim"), p
        ).all()
        if len(rows) < min_samples:
            return {"clusters": [], "n_points": len(rows)}
        pts = np.array([[r[0], r[1]] for r in rows])
        cats = np.array([r[2] for r in rows])
        wards = np.array([r[3] for r in rows])
        # sampled data is thinner than the full set: scale min_samples with the sampling fraction is unnecessary here
        labels = DBSCAN(eps=eps_m / EARTH_R, min_samples=min_samples, metric="haversine", algorithm="ball_tree").fit_predict(np.radians(pts))
        out = []
        for lab in sorted(set(labels) - {-1}):
            m = labels == lab
            vals, counts = np.unique(cats[m], return_counts=True)
            wv, wc = np.unique(wards[m], return_counts=True)
            out.append({
                "id": int(lab), "complaints": int(m.sum()), "center": [round(float(pts[m, 0].mean()), 5), round(float(pts[m, 1].mean()), 5)],
                "dominant_category": str(vals[counts.argmax()]), "category_share": round(float(counts.max() / m.sum()), 2),
                "ward": (cube.ward_by_id.get(int(wv[wc.argmax()])) or {}).get("name"), "hull": _hull(pts[m]),
            })
        out.sort(key=lambda c: -c["complaints"])
        return {"clusters": out[:60], "n_points": len(rows), "noise_share": round(float((labels == -1).mean()), 3)}

    val, _ = cache.get_or_set(key, 1800, run)
    return {**val, "params": {"eps_m": eps_m, "min_samples": min_samples, "algorithm": "DBSCAN (haversine)"}}


def ward_geojson(db: Session) -> dict[str, Any]:
    def build() -> dict[str, Any]:
        rows = db.execute(text("SELECT id, code, name, population, ST_AsGeoJSON(geom) FROM wards ORDER BY code")).all()
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": json.loads(r[4]), "properties": {"ward_id": r[0], "code": r[1], "name": r[2], "population": r[3]}}
            for r in rows]}

    val, _ = cache.get_or_set("gis:wardgeo:v1", 3600, build)
    return val


def choropleth(db: Session, cube: Cube, *, metric: str, category: str | None, date_from: str | None, date_to: str | None) -> dict[str, Any]:
    end = (date_to or cube.latest_month)[:7]
    start = (date_from or month_add(end, -11))[:7]
    months = max(1, (int(end[:4]) - int(start[:4])) * 12 + int(end[5:7]) - int(start[5:7]) + 1)
    if metric == "gap_index":
        g = service_gaps.gap_index(cube, months=months, category=category)
        vals = {i["ward_id"]: i["sgi"] for i in g["items"]}
    else:
        r = kpis.rankings(cube, by="ward", metric=metric, months=months, month=end, category=category)
        vals = {i["id"]: i[metric] for i in r["items"]}
    gj = ward_geojson(db)
    feats = [{**f, "properties": {**f["properties"], "value": vals.get(f["properties"]["ward_id"]), "metric": metric}} for f in gj["features"]]
    nums = [v for v in vals.values() if v is not None]
    return {"type": "FeatureCollection", "features": feats, "metric": metric, "period": {"from": start, "to": end},
            "min": min(nums) if nums else None, "max": max(nums) if nums else None,
            "higher_is_better": kpis.HIGHER_IS_BETTER.get(metric, False)}


def within(db: Session, *, lat: float, lon: float, radius_m: float, category: str | None, limit: int = 100) -> dict[str, Any]:
    """Complaints within `radius_m` metres (PostGIS ST_DWithin on geography). No citizen PII is returned."""
    pt = "ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography"
    p = {"lat": lat, "lon": lon, "r": radius_m, "c": category, "lim": limit}
    where = f"ST_DWithin(location::geography, {pt}, :r) AND (CAST(:c AS text) IS NULL OR category = :c)"
    cats = db.execute(text(f"SELECT category, count(*) FROM complaints WHERE {where} GROUP BY 1 ORDER BY 2 DESC"), p).all()
    near = db.execute(
        text(f"SELECT id, category, status, submitted_at, ST_Y(location), ST_X(location), ST_Distance(location::geography, {pt}) AS d "
             f"FROM complaints WHERE {where} ORDER BY d LIMIT :lim"), p
    ).all()
    return {"center": [lat, lon], "radius_m": radius_m, "total": int(sum(c[1] for c in cats)),
            "by_category": [{"category": c[0], "count": int(c[1])} for c in cats],
            "nearest": [{"id": r[0], "category": r[1], "status": r[2], "submitted_at": r[3].isoformat(), "lat": r[4], "lon": r[5],
                         "distance_m": round(float(r[6]), 1)} for r in near]}


def ward_summary(db: Session, cube: Cube, ward_id: int) -> dict[str, Any]:
    w = cube.ward_by_id[ward_id]
    s = kpis.summary(db, cube, ward_id=ward_id, category=None, months=12, month=None)
    df = cube.filt(ward_id=ward_id, date_from=month_add(cube.latest_month, -11))
    top = df.groupby("category")["n"].sum().sort_values(ascending=False).head(5)
    gi = service_gaps.gap_index(cube, months=12)
    mine = next((i for i in gi["items"] if i["ward_id"] == ward_id), None)
    return {"ward": w, "kpis": s["current"], "change_vs_last_year_pct": s["change_vs_last_year_pct"],
            "top_categories": [{"category": c, "complaints": int(n)} for c, n in top.items()], "gap_index": mine,
            "wards_ranked": len(gi["items"])}
