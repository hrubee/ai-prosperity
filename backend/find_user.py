import sys
from sqlalchemy.orm import Session
from app.db import SessionLocal
from app.models import User

def main():
    db = SessionLocal()
    user = db.query(User).filter(User.email == "radianmedia.org@gmail.com").first()
    if user:
        print(f"User found: ID={user.id}, Email={user.email}, Role={user.role}")
    else:
        print("User not found.")
    db.close()

if __name__ == "__main__":
    main()
