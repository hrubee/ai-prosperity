from dotenv import load_dotenv
load_dotenv()
from app.db import engine, SessionLocal
from app.models import Base, PricingPlan

Base.metadata.create_all(bind=engine)

plans = [
    (1, 5000, "1 Month"),
    (3, 13500, "3 Months"),
    (6, 24000, "6 Months"),
    (12, 38400, "12 Months"),
]

db = SessionLocal()
for months, price, name in plans:
    existing = db.query(PricingPlan).filter_by(months=months).first()
    if not existing:
        plan = PricingPlan(months=months, price_inr=price, name=name)
        db.add(plan)
    else:
        existing.price_inr = price
        existing.name = name

db.commit()
db.close()
print("Plans seeded successfully!")
