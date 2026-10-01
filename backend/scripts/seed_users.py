"""Create (or update) demo accounts, one per role. Idempotent.

Usage: python -m scripts.seed_users
Passwords come from DEMO_PASSWORD (default 'Demo@12345'); never use these in production.
"""

import os

from sqlalchemy import select

from app.core.security import hash_password
from app.db.models import Department, User, UserRole, Ward
from app.db.session import SessionLocal

DEMO_USERS: list[tuple[str, str, UserRole]] = [
    ("admin@sgdss.org", "System Admin", UserRole.ADMIN),
    ("commissioner@sgdss.org", "Municipal Commissioner", UserRole.COMMISSIONER),
    ("dept.head@sgdss.org", "Department Head", UserRole.DEPARTMENT_HEAD),
    ("ward.officer@sgdss.org", "Ward Officer", UserRole.WARD_OFFICER),
    ("analyst@sgdss.org", "Data Analyst", UserRole.ANALYST),
]


# Scoped roles get a ward / department so role-based data scoping is visible in the demo
# (W05 and the drainage department are the underperformers in the synthetic data).
DEMO_WARD_CODE = "W05"
DEMO_DEPARTMENT_CODE = "DSD"


def main() -> None:
    password = os.environ.get("DEMO_PASSWORD", "Demo@12345")
    with SessionLocal() as db:
        ward_id = db.scalar(select(Ward.id).where(Ward.code == DEMO_WARD_CODE))
        dept_id = db.scalar(select(Department.id).where(Department.code == DEMO_DEPARTMENT_CODE))
        for email, name, role in DEMO_USERS:
            scope = {
                "ward_id": ward_id if role == UserRole.WARD_OFFICER else None,
                "department_id": dept_id if role == UserRole.DEPARTMENT_HEAD else None,
            }
            user = db.scalar(select(User).where(User.email == email))
            if user is None:
                db.add(User(email=email, full_name=name, role=role, hashed_password=hash_password(password), **scope))
                print(f"created {email} ({role.value})")
            else:
                user.role = role
                user.hashed_password = hash_password(password)
                user.ward_id, user.department_id = scope["ward_id"], scope["department_id"]
                print(f"updated {email} ({role.value})")
        db.commit()


if __name__ == "__main__":
    main()
