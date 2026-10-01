"""Generate docs/API.md from the live FastAPI app (paths, parameters, required roles), so the reference cannot drift.

Usage (from backend/): python -m scripts.gen_api_docs
"""

from collections import defaultdict
from typing import Any

from fastapi.routing import APIRoute

from app.api.deps import get_current_user
from app.core.config import ROOT_DIR
from app.main import app

OUT = ROOT_DIR / "docs" / "API.md"
PREFIX = "/api/v1"
ROLE_NAMES = {"commissioner": "commissioner", "department_head": "dept head", "ward_officer": "ward officer", "analyst": "analyst", "admin": "admin"}
ORDER = ["system", "auth", "users", "master data", "complaints", "ingestion", "nlp", "analytics", "gis", "forecasting", "llm", "jobs"]


def walk(routes: list[Any]) -> list[APIRoute]:
    out: list[APIRoute] = []
    for r in routes:
        if isinstance(r, APIRoute):
            out.append(r)
        elif hasattr(r, "original_router"):
            out += walk(r.original_router.routes)
    return out


def access(route: APIRoute) -> str:
    for dep in route.dependant.dependencies:
        fn = dep.call
        if fn is not None and getattr(fn, "__qualname__", "").startswith("require_roles.<locals>"):
            names = [getattr(r, "value", str(r)) for r in fn.__closure__[0].cell_contents] if fn.__closure__ else []  # type: ignore[index]
            roles = sorted(n for n in names if n != "admin")
            return ", ".join(ROLE_NAMES.get(n, n) for n in roles) + (" (+ admin)" if roles else "admin only")
    if any(d.call is get_current_user for d in route.dependant.dependencies):
        return "any signed-in user"
    return "public"


def params(route: APIRoute) -> str:
    q = [p.name for p in route.dependant.query_params]
    b = [getattr(getattr(getattr(p, "field_info", None), "annotation", None), "__name__", p.name) for p in route.dependant.body_params]
    path = [p.name for p in route.dependant.path_params]
    parts = []
    if path:
        parts.append("path: " + ", ".join(path))
    if q:
        parts.append("query: " + ", ".join(q))
    if b:
        parts.append("body: " + ", ".join(b))
    return "; ".join(parts) or "-"


def main() -> None:
    routes = walk(app.routes)
    by_tag: dict[str, list[APIRoute]] = defaultdict(list)
    for r in routes:
        by_tag[(r.tags or ["other"])[0]].append(r)  # type: ignore[arg-type]
    lines = [
        "# API reference",
        "",
        "_Generated from the running app by `backend/scripts/gen_api_docs.py`. Interactive docs with request/response schemas: `http://localhost:8000/docs` (OpenAPI JSON at `/openapi.json`)._",
        "",
        f"All paths are under `{PREFIX}`. Every response uses the envelope `{{ \"success\": bool, \"data\": ..., \"error\": {{ \"code\", \"message\", \"details\" }} | null }}`; "
        "list endpoints return `data: { items: [...], meta: { page, page_size, total, pages } }`. Authenticate with `Authorization: Bearer <access token>` (from `POST /auth/login`). "
        "`admin` can call everything. Ward officers and department heads are automatically scoped to their ward / department on data endpoints. "
        "Errors: 401 not signed in, 403 role not allowed, 404 not found, 409 duplicate, 413/415 bad upload, 422 validation, 429 rate limited, 503 LLM unavailable.",
        "",
        f"**{len(routes)} endpoints.**",
        "",
    ]
    tags = [t for t in ORDER if t in by_tag] + sorted(t for t in by_tag if t not in ORDER)
    for tag in tags:
        lines += [f"## {tag.title()}", "", "| Method | Path | Access | Parameters | What it does |", "|---|---|---|---|---|"]
        for r in sorted(by_tag[tag], key=lambda x: (x.path, sorted(x.methods)[0])):
            doc = ((r.endpoint.__doc__ or "").strip().split("\n")[0] or r.name.replace("_", " ").capitalize()).replace("|", "/")
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                lines.append(f"| {m} | `{r.path}` | {access(r)} | {params(r)} | {doc} |")
        lines.append("")
    lines += [
        "## Streaming: `POST /assistant/chat`",
        "",
        "Server-Sent Events over a POST. Body `{ \"message\": str, \"history\": [{\"role\": \"user\"|\"assistant\", \"content\": str}] }`. Events (each `data: <json>`):",
        "",
        "```",
        'data: {"type": "tool", "name": "rank_wards", "args": {...}}        # a data function the AI called',
        'data: {"type": "token", "text": "Worst drainage ..."}              # answer text, in chunks',
        'data: {"type": "done", "tools_used": [...], "label": {"ai_generated": true, "model": "...", "demo_mode": false}}',
        'data: {"type": "error", "message": "..."}                          # instead of done on failure',
        "data: [DONE]",
        "```",
        "",
        "## Assistant tools (what the AI is allowed to call)",
        "",
        "`get_kpis`, `rank_wards`, `rank_departments`, `sla_breaches`, `service_gap_index`, `backlog`, `emerging_issues`, `forecast_volume`, `resource_forecast`, `ward_risk`, `search_policy_documents`. "
        "Arguments are validated, results are truncated to 3,500 characters, and the same role scoping as the REST endpoints applies. The AI cannot run SQL or any other function.",
        "",
    ]
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT}: {len(routes)} endpoints")


if __name__ == "__main__":
    main()
