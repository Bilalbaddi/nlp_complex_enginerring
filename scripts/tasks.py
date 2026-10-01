#!/usr/bin/env python3
"""Cross-platform task runner for SG-DSS (replaces the Makefile). Standard library only.

Usage:  python scripts/tasks.py <command> [args...]      (run `python scripts/tasks.py help` for the list)
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV = BACKEND / ".venv"
IS_WIN = os.name == "nt"
VENV_PY = VENV / ("Scripts/python.exe" if IS_WIN else "bin/python")
PLACEHOLDER_SECRET = "change-me-to-a-long-random-string"


def read_dotenv() -> dict[str, str]:
    env: dict[str, str] = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def child_env() -> dict[str, str]:
    """Environment for child processes: .env values plus a project-local model cache for HF/torch."""
    env = {**os.environ}
    dotenv = read_dotenv()
    for k, v in dotenv.items():
        env.setdefault(k, v)
    cache = Path(env.get("MODEL_CACHE_DIR", ".cache/models"))
    cache = cache if cache.is_absolute() else ROOT / cache
    cache.mkdir(parents=True, exist_ok=True)
    for var in ("HF_HOME", "SENTENCE_TRANSFORMERS_HOME", "TORCH_HOME", "FASTEMBED_CACHE_PATH"):
        env.setdefault(var, str(cache))
    env["PYTHONUNBUFFERED"] = "1"
    return env


def run(cmd: list[str], cwd: Path, check: bool = True) -> int:
    print(f"\n$ {' '.join(cmd)}   (in {cwd.relative_to(ROOT) if cwd != ROOT else '.'})", flush=True)
    try:
        proc = subprocess.run(cmd, cwd=cwd, env=child_env())
    except KeyboardInterrupt:
        return 130
    if check and proc.returncode != 0:
        sys.exit(proc.returncode)
    return proc.returncode


def need_venv() -> str:
    if not VENV_PY.exists():
        sys.exit("Backend virtualenv missing. Run: python scripts/tasks.py setup")
    return str(VENV_PY)


def npm() -> str:
    exe = shutil.which("npm.cmd" if IS_WIN else "npm") or shutil.which("npm")
    if not exe:
        sys.exit("npm not found on PATH. Install Node.js 20+.")
    return exe


# ---------------------------------------------------------------- commands


def cmd_setup(_: list[str]) -> None:
    """Create .env, the backend venv (+ dependencies) and install frontend packages."""
    env_file = ROOT / ".env"
    if not env_file.exists():
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        text = text.replace(PLACEHOLDER_SECRET, secrets.token_urlsafe(48))
        env_file.write_text(text, encoding="utf-8")
        print("Created .env with a fresh JWT_SECRET. Fill in DATABASE_URL, SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY before `migrate`.")
    if not VENV_PY.exists():
        run([sys.executable, "-m", "venv", str(VENV)], ROOT)
    run([str(VENV_PY), "-m", "pip", "install", "--upgrade", "pip"], BACKEND)
    run([str(VENV_PY), "-m", "pip", "install", "-r", "requirements.txt"], BACKEND)
    run([npm(), "install", "--no-audit", "--no-fund"], FRONTEND)
    print("\nSetup complete. Next: edit .env, then `python scripts/tasks.py migrate`.")


def cmd_migrate(_: list[str]) -> None:
    """Apply Alembic migrations (also enables the postgis and vector extensions)."""
    run([need_venv(), "-m", "alembic", "upgrade", "head"], BACKEND)


def cmd_revision(args: list[str]) -> None:
    """Autogenerate a migration: revision "message" (always read the result for stray drop_* calls)."""
    if not args:
        sys.exit('usage: revision "message"')
    run([need_venv(), "-m", "alembic", "revision", "--autogenerate", "-m", " ".join(args)], BACKEND)


def cmd_seed_users(_: list[str]) -> None:
    """Create/update one demo account per role."""
    run([need_venv(), "-m", "scripts.seed_users"], BACKEND)


def cmd_dev_backend(args: list[str]) -> None:
    """Run the API with auto-reload on http://localhost:8000 (docs at /docs)."""
    port = read_dotenv().get("BACKEND_PORT", "8000")
    run([need_venv(), "-m", "uvicorn", "app.main:app", "--reload", "--port", port, *args], BACKEND)


def cmd_worker(args: list[str]) -> None:
    """Run the Postgres job worker (not needed in development while JOBS_INLINE=true). Extra args: --once, --poll N."""
    run([need_venv(), "-m", "app.workers.worker", *args], BACKEND)


def cmd_dev_frontend(args: list[str]) -> None:
    """Run the Next.js dev server on http://localhost:3000."""
    run([npm(), "run", "dev", "--", *args], FRONTEND)


def cmd_test(args: list[str]) -> None:
    """Run backend tests (pytest), then frontend checks unless args are given."""
    code = run([need_venv(), "-m", "pytest", *args], BACKEND, check=False)
    if code != 0:
        sys.exit(code)
    if not args:
        run([npm(), "run", "typecheck"], FRONTEND)
        run([npm(), "test"], FRONTEND)


def cmd_cov(args: list[str]) -> None:
    """Backend tests with a coverage report for services, ml, llm, nlp, core and ingestion (takes ~15 min on a remote DB)."""
    pkgs = [f"--cov=app/{p}" for p in ("services", "ml", "llm", "nlp", "core", "ingestion")]
    run([need_venv(), "-m", "pytest", *pkgs, "--cov-report=term", *args], BACKEND)


def _backend_module(module: str, label: str, phase: str, args: list[str]) -> None:
    if not (BACKEND / (module.replace(".", "/") + ".py")).exists():
        sys.exit(f"`{label}` is not available yet: {module} is added in {phase}.")
    run([need_venv(), "-m", module, *args], BACKEND)


def cmd_seed(args: list[str]) -> None:
    """Generate synthetic data and load it into Supabase (size: SEED_COMPLAINT_COUNT). Options: --reset, --complaints N."""
    _backend_module("scripts.seed", "seed", "Phase 2", args)


def cmd_generate(args: list[str]) -> None:
    """Only generate the synthetic CSV/GeoJSON/policy files under data/ (no database). Options: --complaints N."""
    _backend_module("scripts.generate_data", "generate", "Phase 2", args)


def cmd_train(args: list[str]) -> None:
    """Train + evaluate all ML models, register them, write predictions. Options: --no-embeddings, --no-predict."""
    _backend_module("scripts.train", "train", "Phase 3", args)


def cmd_predict(args: list[str]) -> None:
    """Write predictions for every complaint with the already-trained active models (no retraining)."""
    _backend_module("scripts.predict_all", "predict", "Phase 3", args)


def cmd_groq_eval(args: list[str]) -> None:
    """Zero-shot Groq classifier on a small stratified sample, compared with TF-IDF. Options: --n 200 --batch 10."""
    _backend_module("scripts.groq_eval", "groq-eval", "Phase 3", args)


def cmd_embed(args: list[str]) -> None:
    """Embed complaints via the Hugging Face Inference API into pgvector (resumable). Options: --limit N."""
    _backend_module("scripts.embed_backfill", "embed", "Phase 3", args)


def cmd_db_size(_: list[str]) -> None:
    """Show database and per-table size against the 500 MB Supabase free-tier limit."""
    run([need_venv(), "-m", "scripts.db_size"], BACKEND)


def cmd_check(_: list[str]) -> None:
    """Call /health on the running backend and print the result."""
    port = read_dotenv().get("BACKEND_PORT", "8000")
    url = f"http://localhost:{port}/api/v1/health"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            body = json.load(r)
    except (urllib.error.URLError, TimeoutError) as exc:
        sys.exit(f"Backend not reachable at {url}: {exc}. Start it with `dev-backend`.")
    print(json.dumps(body, indent=2))
    d = body["data"]
    ok = d["database"] == "ok" and d["postgis"] and d["pgvector"] and d["storage"] == "ok"
    sys.exit(0 if ok else 1)


COMMANDS: dict[str, Callable[[list[str]], None]] = {
    "setup": cmd_setup,
    "migrate": cmd_migrate,
    "revision": cmd_revision,
    "seed-users": cmd_seed_users,
    "dev-backend": cmd_dev_backend,
    "worker": cmd_worker,
    "dev-frontend": cmd_dev_frontend,
    "test": cmd_test,
    "seed": cmd_seed,
    "generate": cmd_generate,
    "train": cmd_train,
    "predict": cmd_predict,
    "groq-eval": cmd_groq_eval,
    "cov": cmd_cov,
    "embed": cmd_embed,
    "db-size": cmd_db_size,
    "check": cmd_check,
}


def main() -> None:
    args = sys.argv[1:]
    if not args or args[0] in ("help", "-h", "--help") or args[0] not in COMMANDS:
        print(__doc__)
        for name, fn in COMMANDS.items():
            print(f"  {name:<13} {(fn.__doc__ or '').strip().splitlines()[0]}")
        sys.exit(0 if not args or args[0] in ("help", "-h", "--help") else 2)
    COMMANDS[args[0]](args[1:])


if __name__ == "__main__":
    main()
