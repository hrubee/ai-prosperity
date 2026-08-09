from app.db import engine
from app.models import DhanConnection
DhanConnection.__table__.drop(engine, checkfirst=True)
DhanConnection.__table__.create(engine, checkfirst=True)
print("Recreated dhan_connections table")
