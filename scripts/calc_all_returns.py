"""
수익률 계산 → returns 테이블 적재

계산 로직:
  - price_base = 사건일 당일 또는 직전 거래일 종가 (Adj Close)
  - price_end  = 사건일 + N일 시점 또는 직후 거래일 종가 (시장 폐장 대응)
  - return_pct = (price_end - price_base) / price_base × 100

실행 방법 (프로젝트 루트에서):
    python scripts/calc_all_returns.py --pilot    # 5개 사건만
    python scripts/calc_all_returns.py            # 전체 30개
    python scripts/calc_all_returns.py --force    # 기존 데이터 삭제 후 재계산
"""
import sys
import argparse
import logging
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db.database import SessionLocal
from backend.models import Event, Asset, Price, Return

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

PILOT_EVENTS = ["A04", "A08", "B05", "C02", "D06"]
PERIODS = [
    ("D+1", 1),
    ("D+7", 7),
    ("D+30", 30),
    ("D+180", 180),
    ("D+365", 365),
]
SEARCH_RANGE = 10  # 주말/공휴일/폐장 대비 최대 탐색일


def load_prices_for_symbol(db, symbol):
    """해당 심볼의 모든 가격을 메모리에 로드 → {date: Price} 딕셔너리"""
    prices = db.query(Price).filter(Price.symbol == symbol).all()
    return {p.trade_date: p for p in prices}


def find_price_on_or_before(price_dict, target_date):
    """target_date 이하의 가장 가까운 거래일 가격 (기준가용)"""
    for i in range(SEARCH_RANGE + 1):
        check = target_date - timedelta(days=i)
        if check in price_dict:
            return price_dict[check]
    return None


def find_price_on_or_after(price_dict, target_date):
    """target_date 이상의 가장 가까운 거래일 가격 (종료가용)
    9/11처럼 시장이 여러 날 폐장한 경우, 재개 후 첫 거래일을 찾는다."""
    for i in range(SEARCH_RANGE + 1):
        check = target_date + timedelta(days=i)
        if check in price_dict:
            return price_dict[check]
    return None


def calc_returns_for_pair(event, price_dict):
    """단일 사건 × 단일 자산의 수익률 계산"""
    # 기준가: 사건일 당일 또는 직전 거래일
    base = find_price_on_or_before(price_dict, event.event_date)
    if not base or float(base.adj_close) == 0:
        return []

    results = []
    for period_name, days in PERIODS:
        target_date = event.event_date + timedelta(days=days)

        if target_date > date.today():
            continue

        # 종료가: target_date 당일 또는 직후 거래일
        end = find_price_on_or_after(price_dict, target_date)
        if not end:
            continue

        # 기준가와 종료가가 같은 날이면 의미 없는 데이터 → 스킵
        if end.trade_date <= base.trade_date:
            continue

        return_pct = (
            (float(end.adj_close) - float(base.adj_close))
            / float(base.adj_close)
            * 100
        )

        results.append(Return(
            event_id=event.id,
            symbol=base.symbol,
            period=period_name,
            return_pct=round(return_pct, 4),
            price_base=float(base.adj_close),
            price_end=float(end.adj_close),
            date_base=base.trade_date,
            date_end=end.trade_date,
        ))

    return results


def main():
    parser = argparse.ArgumentParser(description="수익률 계산")
    parser.add_argument("--pilot", action="store_true", help="5개 파일럿 사건만")
    parser.add_argument("--force", action="store_true", help="기존 데이터 삭제 후 재계산")
    args = parser.parse_args()

    db = SessionLocal()

    if args.pilot:
        events = db.query(Event).filter(Event.id.in_(PILOT_EVENTS)).all()
        logger.info("파일럿 모드: %s", [e.id for e in events])
    else:
        events = db.query(Event).all()
        logger.info("전체 모드: %d개 사건", len(events))

    assets = db.query(Asset).all()
    event_ids = [e.id for e in events]

    # --force: 기존 데이터 삭제
    if args.force:
        deleted = db.query(Return).filter(Return.event_id.in_(event_ids)).delete()
        db.commit()
        logger.info("기존 %d건 삭제", deleted)

    logger.info("계산: %d개 사건 x %d개 자산 x %d개 시점", len(events), len(assets), len(PERIODS))
    logger.info("=" * 60)

    total_inserted = 0
    total_skipped = 0
    total_no_data = 0

    for asset in assets:
        price_dict = load_prices_for_symbol(db, asset.symbol)
        if not price_dict:
            continue

        asset_count = 0
        for event in events:
            existing = db.query(Return).filter(
                Return.event_id == event.id,
                Return.symbol == asset.symbol,
            ).count()
            if existing > 0:
                total_skipped += existing
                continue

            returns = calc_returns_for_pair(event, price_dict)
            if not returns:
                total_no_data += 1
                continue

            db.add_all(returns)
            asset_count += len(returns)

        db.commit()
        total_inserted += asset_count
        if asset_count > 0:
            logger.info("  %s: %d건 계산", asset.symbol, asset_count)

    db.close()

    logger.info("=" * 60)
    logger.info("완료: %d건 입력 / %d건 스킵(기존) / %d건 데이터없음", total_inserted, total_skipped, total_no_data)


if __name__ == "__main__":
    main()
