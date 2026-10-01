from fastapi import APIRouter

from app.api.v1 import analytics, auth, complaints, forecasts, gis, health, insights, ingest, jobs, master, nlp, users

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(jobs.router)
api_router.include_router(master.router)
api_router.include_router(complaints.router)
api_router.include_router(ingest.router)
api_router.include_router(nlp.router)
api_router.include_router(analytics.router)
api_router.include_router(gis.router)
api_router.include_router(forecasts.router)
api_router.include_router(insights.router)
