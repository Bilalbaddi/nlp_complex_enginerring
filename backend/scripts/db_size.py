"""Report database size vs the Supabase free-tier limit. Usage: python -m scripts.db_size"""

from sqlalchemy import text

from app.db.session import engine

LIMIT_MB = 500.0

TABLES_SQL = text(
    """
    SELECT c.relname AS name,
           pg_total_relation_size(c.oid) AS total,
           pg_relation_size(c.oid) AS data,
           pg_indexes_size(c.oid) AS indexes,
           c.reltuples::bigint AS rows
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind = 'r' AND n.nspname = current_schema() AND c.relname <> 'spatial_ref_sys'
    ORDER BY total DESC
    """
)


def mb(n: int) -> float:
    return n / (1024 * 1024)


def main() -> None:
    with engine.connect() as conn:
        db_bytes = conn.execute(text("SELECT pg_database_size(current_database())")).scalar_one()
        rows = conn.execute(TABLES_SQL).all()
    print(f"{'table':<24}{'rows(est)':>12}{'data MB':>10}{'index MB':>10}{'total MB':>10}")
    for r in rows:
        print(f"{r.name:<24}{r.rows:>12}{mb(r.data):>10.1f}{mb(r.indexes):>10.1f}{mb(r.total):>10.1f}")
    pct = mb(db_bytes) / LIMIT_MB * 100
    print(f"\nDatabase total: {mb(db_bytes):.1f} MB of {LIMIT_MB:.0f} MB free tier ({pct:.0f}%)")
    if pct > 70:
        print("WARNING: above 70% of the free-tier limit; reduce SEED_COMPLAINT_COUNT or drop unused data.")


if __name__ == "__main__":
    main()
