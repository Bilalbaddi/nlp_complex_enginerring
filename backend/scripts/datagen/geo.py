"""Synthetic ward geometry: a jittered lattice so neighbouring ward polygons share edges and tile the city."""

import math
from typing import Any

import numpy as np

from scripts.datagen.constants import (
    CELL_LAT, CELL_LON, CENTER_LAT, CENTER_LON, GRID_COLS, GRID_ROWS, LANDMARK_POOL, STREET_POOL, WARD_NAMES,
)

TOTAL_POPULATION = 900_000


def _area_km2(ring: list[tuple[float, float]]) -> float:
    lat0 = sum(p[1] for p in ring) / len(ring)
    kx, ky = 111.32 * math.cos(math.radians(lat0)), 110.57
    pts = [(x * kx, y * ky) for x, y in ring]
    s = sum(pts[i][0] * pts[i + 1][1] - pts[i + 1][0] * pts[i][1] for i in range(len(pts) - 1))
    return abs(s) / 2


def build_wards(rng: np.random.Generator, py_rng: Any) -> list[dict[str, Any]]:
    """Return ward dicts with code, name, polygon ring (lon/lat), corners for sampling, demographics, landmarks."""
    x0 = CENTER_LON - GRID_COLS * CELL_LON / 2
    y0 = CENTER_LAT - GRID_ROWS * CELL_LAT / 2
    lattice = [
        [
            (
                x0 + i * CELL_LON + rng.uniform(-1, 1) * CELL_LON * 0.18,
                y0 + j * CELL_LAT + rng.uniform(-1, 1) * CELL_LAT * 0.18,
            )
            for i in range(GRID_COLS + 1)
        ]
        for j in range(GRID_ROWS + 1)
    ]
    weights = rng.lognormal(0, 0.35, GRID_COLS * GRID_ROWS)
    pops = np.round(weights / weights.sum() * TOTAL_POPULATION, -2).astype(int)

    wards: list[dict[str, Any]] = []
    for k in range(GRID_COLS * GRID_ROWS):
        row, col = divmod(k, GRID_COLS)
        p00, p10, p11, p01 = lattice[row][col], lattice[row][col + 1], lattice[row + 1][col + 1], lattice[row + 1][col]
        ring = [p00, p10, p11, p01, p00]
        area = _area_km2(ring)
        outer = 1.0 if (row in (0, GRID_ROWS - 1) or col in (0, GRID_COLS - 1)) else 0.0
        landmarks = [s.format(k=py_rng.randint(1, 30)) for s in py_rng.sample(LANDMARK_POOL, 6)]
        streets = [s.format(k=py_rng.randint(1, 40)) for s in py_rng.sample(STREET_POOL, 5)]
        wards.append(
            {
                "code": f"W{k + 1:02d}",
                "name": WARD_NAMES[k],
                "population": int(pops[k]),
                "area_sq_km": round(area, 3),
                "road_km": round(area * rng.uniform(7.5, 12.0), 1),
                # outer wards have worse piped-water coverage
                "water_connections": int(pops[k] / 5.2 * rng.uniform(0.55, 0.80) if outer else pops[k] / 5.2 * rng.uniform(0.80, 0.97)),
                "ring": ring,
                "corners": np.array([p00, p10, p11, p01]),
                "landmarks": landmarks,
                "streets": streets,
            }
        )
    return wards


def to_lonlat(corners: np.ndarray, u: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Bilinear map of unit-square coords (u,v) into a ward quad. corners: (n,4,2) as p00,p10,p11,p01."""
    p00, p10, p11, p01 = corners[:, 0], corners[:, 1], corners[:, 2], corners[:, 3]
    uu, vv = u[:, None], v[:, None]
    pt = (1 - uu) * (1 - vv) * p00 + uu * (1 - vv) * p10 + uu * vv * p11 + (1 - uu) * vv * p01
    return pt[:, 0], pt[:, 1]


def wards_geojson(wards: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "name": "sahyadripur_wards",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "code": w["code"], "name": w["name"], "population": w["population"],
                    "area_sq_km": w["area_sq_km"], "road_km": w["road_km"], "water_connections": w["water_connections"],
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[round(x, 6), round(y, 6)] for x, y in w["ring"]]],
                },
            }
            for w in wards
        ],
    }
