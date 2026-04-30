"""
yfinance로 가격 데이터 수집 → prices 테이블 적재

실행 방법 (프로젝트 루트에서):
    python scripts/fetch_all_prices.py --pilot    # 5개 사건만 (검증용)
    python scripts/fetch_all_prices.py            # 전체 30개 사건
"""
import sys
import argparse
import logging
import time
from pathlib import Path
from datetime import date, timedelta

import yfinance as yf
import pandas as pd

# 프로젝트 루트를 Python 경로에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db.database import SessionLocal
from backend.models import Event, Asset, Price

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

PILOT_EVENTS = ["A04", "A08", "B05", "C02", "D06"]
BUFFER_BEFORE = 30    # 사건일 - 30일 (D-30 차트용)
BUFFER_AFTER = 400    # 사건일 + 400일 (D+365 + 주말/공휴일 여유)
MAX_RETRIES = 3
RETRY_DELAY = 5       # 초


def get_date_range(events):
    """사건 목록에서 필요한 전체 날짜 범위 계산"""
    starts = [e.event_date - timedelta(days=BUFFER_BEFORE) for e in events]
    ends = [e.event_date + timedelta(days=BUFFER_AFTER) for e in events]
    global_start = min(starts)
    global_end = min(max(ends), date.today())
    return global_start, global_end


def fetch_single_asset(yahoo_symbol, fetch_start, fetch_end):
    """yfinance에서 단일 자산의 가격 데이터를 다운로드 (재시도 포함)"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            df = yf.download(
                yahoo_symbol,
                start=str(fetch_start),
                end=str(fetch_end + timedelta(days=1)),
                progress=False,
            )
            # yfinance가 MultiIndex 컬럼을 반환하는 경우 처리
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.droplevel(1)
            return df

        except Exception as e:
            logger.warning("  시도 %d/%d 실패: %s", attempt, MAX_RETRIES, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)

    return pd.DataFrame()


def save_prices_to_db(db, symbol, df):
    """다운로드된 DataFrame → prices 테이블 저장 (중복 스킵)"""
    if df.empty:
        return 0

    # 이미 DB에 있는 날짜를 메모리에 로드 → 매 행마다 쿼리하지 않음
    existing_dates = set(
        row[0] for row in
        db.query(Price.trade_date).filter(Price.symbol == symbol).all()
    )

    # Adjusted Close 컬럼 찾기 (yfinance 버전에 따라 이름이 다름)
    close_col = "Adj Close" if "Adj Close" in df.columns else "Close"

    rows = []
    for trade_date, row in df.iterrows():
        td = trade_date.date()
        if td in existing_dates:
            continue

        adj_close = row.get(close_col)
        if pd.isna(adj_close):
            continue

        volume = row.get("Volume")
        rows.append(Price(
            symbol=symbol,
            trade_date=td,
            adj_close=round(float(adj_close), 6),
            volume=int(volume) if pd.notna(volume) else None,
        ))

    if rows:
        db.add_all(rows)
        db.commit()

    return len(rows)


def main():
    parser = argparse.ArgumentParser(description="yfinance 가격 데이터 수집")
    parser.add_argument("--pilot", action="store_true", help="5개 파일럿 사건만 수집")
    args = parser.parse_args()

    db = SessionLocal()

    # 대상 사건 조회
    if args.pilot:
        events = db.query(Event).filter(Event.id.in_(PILOT_EVENTS)).all()
        logger.info("파일럿 모드: %s", [e.id for e in events])
    else:
        events = db.query(Event).all()
        logger.info("전체 모드: %d개 사건", len(events))

    global_start, global_end = get_date_range(events)
    logger.info("수집 범위: %s ~ %s", global_start, global_end)

    assets = db.query(Asset).all()
    logger.info("자산 수: %d개", len(assets))
    logger.info("=" * 60)

    total_inserted = 0
    failed_assets = []

    for i, asset in enumerate(assets, 1):
        # 자산의 데이터 시작일 이전은 건너뛰기
        fetch_start = global_start
        if asset.data_start and asset.data_start > fetch_start:
            fetch_start = asset.data_start

        if fetch_start >= global_end:
            logger.info("[%02d/%d] %s — 범위 밖, 스킵", i, len(assets), asset.symbol)
            continue

        logger.info("[%02d/%d] %s (%s) 수집 중...", i, len(assets), asset.symbol, asset.name_en)

        df = fetch_single_asset(asset.yahoo_symbol, fetch_start, global_end)

        if df.empty:
            logger.warning("  → 데이터 없음!")
            failed_assets.append(asset.symbol)
            continue

        count = save_prices_to_db(db, asset.symbol, df)
        total_inserted += count
        logger.info("  → %d건 저장 (다운로드 %d행)", count, len(df))

    db.close()

    logger.info("=" * 60)
    logger.info("수집 완료: 총 %d건 저장", total_inserted)
    if failed_assets:
        logger.warning("실패한 자산: %s", failed_assets)


if __name__ == "__main__":
    main()
