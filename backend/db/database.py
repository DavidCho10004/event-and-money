"""
DB 연결 및 세션 관리
- 로컬 개발: SQLite (backend/db/eventandmoney.db)
- 프로덕션: Supabase PostgreSQL (DATABASE_URL 환경변수로 전환)
"""
import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from dotenv import load_dotenv

load_dotenv()

# SQLite 기본값 — 나중에 .env에 PostgreSQL URL 넣으면 자동 전환
_DB_PATH = Path(__file__).resolve().parent / "eventandmoney.db"
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{_DB_PATH}")

# SQLite는 check_same_thread=False 필요 (FastAPI 멀티스레드 대비)
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, echo=False, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 의존성 주입용 DB 세션 제너레이터"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
