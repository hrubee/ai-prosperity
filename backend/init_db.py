"""Create all tables. For v1 we use SQLAlchemy create_all; move to Alembic
migrations before the schema starts changing in production.

Usage:  python init_db.py
"""
from app.db import engine, SessionLocal
from app.models import Base, PricingPlan


def seed_pricing_plans(session) -> None:
    defaults = [
        {"months": 1, "price_inr": 1000, "name": "1 Month"},
        {"months": 3, "price_inr": 2500, "name": "3 Months"},
        {"months": 6, "price_inr": 4500, "name": "6 Months"},
        {"months": 12, "price_inr": 8000, "name": "12 Months"},
    ]
    for plan in defaults:
        existing = session.query(PricingPlan).filter_by(months=plan["months"]).first()
        if not existing:
            session.add(PricingPlan(**plan))
    session.commit()
    print("Seeded default pricing plans.")


def main() -> None:
    Base.metadata.create_all(engine)
    print("Tables created:", ", ".join(sorted(Base.metadata.tables.keys())))
    
    with SessionLocal() as session:
        seed_pricing_plans(session)


if __name__ == "__main__":
    main()
