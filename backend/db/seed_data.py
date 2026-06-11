"""
events.json, assets.json → DB 시딩 스크립트

실행 방법 (프로젝트 루트에서):
    python -m backend.db.seed_data

기능:
1. events 테이블에 마이크로 사건용 컬럼이 없으면 ALTER TABLE로 추가 (SQLite 마이그레이션)
2. events.json, assets.json을 DB에 시딩 (기존 행은 attribution/affected_entities 등 갱신)
"""
import json
import logging
from datetime import date
from pathlib import Path

from sqlalchemy import text, inspect

from backend.db.database import SessionLocal, engine
from backend.models import Event, Asset

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
EVENTS_JSON = PROJECT_ROOT / "data" / "events.json"
ASSETS_JSON = PROJECT_ROOT / "data" / "assets.json"

# 마이크로 사건용 신규 컬럼 (SQLite ALTER TABLE 마이그레이션 대상)
MICRO_COLUMNS = [
    ("scale", "VARCHAR(10) DEFAULT 'macro'"),
    ("attr_political", "INTEGER"),
    ("attr_corporate", "INTEGER"),
    ("attr_macro", "INTEGER"),
    ("attr_rationale", "TEXT"),
    ("affected_entities", "TEXT"),
    ("comparable_universe", "TEXT"),
]


def migrate_events_table():
    """events 테이블에 없는 컬럼이 있으면 추가 (SQLite/PG 공용)"""
    inspector = inspect(engine)
    if "events" not in inspector.get_table_names():
        # 테이블 자체가 없으면 SQLAlchemy create_all에서 생성될 것
        return

    existing = {col["name"] for col in inspector.get_columns("events")}
    with engine.begin() as conn:
        for col_name, col_type in MICRO_COLUMNS:
            if col_name in existing:
                continue
            sql = f"ALTER TABLE events ADD COLUMN {col_name} {col_type}"
            logger.info("MIGRATE: %s", sql)
            conn.execute(text(sql))


def seed_events():
    """data/events.json → events 테이블 (upsert: 기존 행도 마이크로 필드 갱신)"""
    with open(EVENTS_JSON, "r", encoding="utf-8") as f:
        events_data = json.load(f)

    db = SessionLocal()
    try:
        inserted, updated = 0, 0
        for row in events_data:
            scale = row.get("scale", "macro")
            attribution = row.get("attribution") or {}
            attr_political = attribution.get("political")
            attr_corporate = attribution.get("corporate")
            attr_macro = attribution.get("macro")
            attr_rationale = row.get("attribution_rationale")
            affected = row.get("affected_entities")
            comparable = row.get("comparable_universe")

            affected_json = json.dumps(affected, ensure_ascii=False) if affected else None
            comparable_json = json.dumps(comparable, ensure_ascii=False) if comparable else None

            event = db.query(Event).filter(Event.id == row["id"]).first()
            if event:
                # 기존 행: 마이크로 필드만 갱신 (텍스트 필드는 유지하고 싶을 수 있으나
                # 운영상 항상 JSON을 truth로 두는 편이 단순함)
                event.scale = scale
                event.attr_political = attr_political
                event.attr_corporate = attr_corporate
                event.attr_macro = attr_macro
                event.attr_rationale = attr_rationale
                event.affected_entities = affected_json
                event.comparable_universe = comparable_json
                updated += 1
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
                scale=scale,
                attr_political=attr_political,
                attr_corporate=attr_corporate,
                attr_macro=attr_macro,
                attr_rationale=attr_rationale,
                affected_entities=affected_json,
                comparable_universe=comparable_json,
            )
            db.add(event)
            inserted += 1

        db.commit()
        logger.info("events 시딩 완료: %d건 신규 / %d건 갱신", inserted, updated)
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
    migrate_events_table()
    seed_events()
    seed_assets()
    logger.info("시딩 완료!")
