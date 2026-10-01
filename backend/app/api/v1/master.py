"""Master data: wards (with GeoJSON boundaries) and departments."""

import json
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import Department, User, Ward
from app.db.session import get_read_db
from app.schemas.common import Envelope, ok
from app.schemas.complaint import DepartmentOut, WardOut
from app.services import cache

router = APIRouter(tags=["master data"])


@router.get("/wards", response_model=Envelope[list[WardOut]])
def list_wards(_: User = Depends(get_current_user), db: Session = Depends(get_read_db)) -> dict[str, object]:
    rows = db.execute(
        select(Ward, func.ST_Y(func.ST_Centroid(Ward.geom)), func.ST_X(func.ST_Centroid(Ward.geom))).order_by(Ward.code)
    ).all()
    out = []
    for w, lat, lon in rows:
        item = WardOut.model_validate(w)
        item.centroid_lat, item.centroid_lon = lat, lon
        out.append(item)
    return ok(out)


@router.get("/wards/geojson", response_model=Envelope[dict[str, Any]])
def wards_geojson(_: User = Depends(get_current_user), db: Session = Depends(get_read_db)) -> dict[str, object]:
    """Ward boundaries as a GeoJSON FeatureCollection (for map layers). Cached for an hour."""

    def build() -> dict[str, Any]:
        rows = db.execute(select(Ward, func.ST_AsGeoJSON(Ward.geom)).order_by(Ward.code)).all()
        return {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": w.id,
                    "properties": {
                        "id": w.id, "code": w.code, "name": w.name, "population": w.population,
                        "area_sq_km": w.area_sq_km, "road_km": w.road_km, "water_connections": w.water_connections,
                    },
                    "geometry": json.loads(g) if g else None,
                }
                for w, g in rows
            ],
        }

    data, _hit = cache.get_or_set("static:wards_geojson", 3600, build)
    return ok(data)


@router.get("/departments", response_model=Envelope[list[DepartmentOut]])
def list_departments(_: User = Depends(get_current_user), db: Session = Depends(get_read_db)) -> dict[str, object]:
    return ok([DepartmentOut.model_validate(d) for d in db.scalars(select(Department).order_by(Department.id))])
