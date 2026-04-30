"""
events.json, assets.json → DB 시딩 스크립트

실행 방법 (프로젝트 루트에서):
    python -m backend.db.seed_data
"""
import json
import logging
from datetime import date
from pathlib import Path

from backend.db.database import SessionLocal
from backend.models import Event, Asset

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
EVENTS_JSON = PROJECT_ROOT / "data" / "events.json"
ASSETS_JSON = PROJECT_ROOT / "data" / "assets.json"


def seed_events():
    """data/events.json → events 테이블"""
    with open(EVENTS_JSON, "r", encoding="utf-8") as f:
        events_data = json.load(f)

    db = SessionLocal()
    try:
        inserted, skipped = 0, 0
        for row in events_data:
            if db.query(Event).filter(Event.id == row["id"]).first():
                skipped += 1
                continue

            event = Event(
                id=row["id"],
                name_ko=row["name_ko"],
                name_en=row["name_en"],
                event_date=date.fromisoformat(row["event_date"]),
                category=row["category"],
                sub_type=row.get("sub_type"),
                description_ko=row.get("description_ko"),
                description_en=row.get("description_en"),
                energy_impact=row.get("energy_impact", False),
            )
            db.add(event)
            inserted += 1

        db.commit()
        logger.info("events 시딩 완료: %d건 입력, %d건 스킵", inserted, skipped)
    except Exception as e:
        db.rollback()
        logger.error("events 시딩 실패: %s", e)
        raise
    finally:
        db.close()


def seed_assets():
    """data/assets.json → assets 테이블"""
    with open(ASSETS_JSON, "r", encoding="utf-8") as f:
        assets_data = json.load(f)

    db = SessionLocal()
    try:
        inserted, skipped = 0, 0
        for row in assets_data:
            if db.query(Asset).filter(Asset.symbol == row["symbol"]).first():
                skipped += 1
                continue

            data_start = None
            if row.get("data_start"):
                data_start = date.fromisoformat(row["data_start"])

            asset = Asset(
                symbol=row["symbol"],
                name_ko=row["name_ko"],
                name_en=row["name_en"],
                asset_class=row["asset_class"],
                data_start=data_start,
                yahoo_symbol=row.get("yahoo_symbol"),
                description=row.get("description"),
            )
            db.add(asset)
            inserted += 1

        db.commit()
        logger.info("assets 시딩 완료: %d건 입력, %d건 스킵", inserted, skipped)
    except Exception as e:
        db.rollback()
        logger.error("assets 시딩 실패: %s", e)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("데이터 시딩 시작")
    logger.info("=" * 50)
    seed_events()
    seed_assets()
    logger.info("시딩 완료!")
