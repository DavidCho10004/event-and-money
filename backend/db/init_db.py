"""
테이블 생성 스크립트

실행 방법 (프로젝트 루트에서):
    python -m backend.db.init_db
"""
import logging
from backend.db.database import engine, Base
from backend.models import Event, Asset, Price, Return, DominoChain  # noqa: F401

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def create_tables():
    """모든 테이블 생성 (이미 존재하면 무시)"""
    logger.info("테이블 생성 시작...")
    Base.metadata.create_all(bind=engine)
    tables = list(Base.metadata.tables.keys())
    logger.info("테이블 생성 완료: %s", tables)
    return tables


if __name__ == "__main__":
    create_tables()
