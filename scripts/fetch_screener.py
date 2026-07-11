"""
수급 스크리너 실데이터 수집 — 전 종목 투자자 순매수 순위

pykrx 로 시장 전체 종목의 기간별 투자자 순매수 + 주가 등락률을 한 번에 받아
data/processed/screener_{MARKET}_{PERIOD}_{INVESTOR}.csv 로 저장.

핵심: 종목마다 따로 안 받고, get_market_net_purchases_of_equities() 한 번으로
      전 종목 순매수를 통째로 가져옴 → 몇 초면 끝.

실행 방법 (프로젝트 루트, KRX 접속 가능한 환경에서):
    python scripts/fetch_screener.py                 # 코스피, 전체 기간(1일/1주/1개월/3개월)×투자자
    python scripts/fetch_screener.py --market KOSDAQ  # 코스닥
"""
import sys
import argparse
import logging
from pathlib import Path
from datetime import timedelta

import pandas as pd
from pykrx import stock

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

MARKETS = ["KOSPI", "KOSDAQ"]
PERIOD_DAYS = {"1d": 0, "1w": 7, "1m": 30, "3m": 90}
INVESTORS = ["외국인", "기관", "개인"]

# pykrx 컬럼명 표준화(버전별 차이 대비)
NET_VALUE_ALIASES = ["순매수거래대금", "순매수대금", "순매수"]
CHANGE_ALIASES = ["등락률"]


def _pick(df, aliases):
    for a in aliases:
        if a in df.columns:
            return a
    return None


def fetch_one(market, period, investor, price_change):
    """한 (시장, 기간, 투자자) 조합의 전 종목 순매수 + 등락률 → DataFrame"""
    last = stock.get_nearest_business_day_in_a_week()
    to_d = pd.Timestamp(last)
    from_d = to_d - timedelta(days=PERIOD_DAYS[period])
    fs, ts = from_d.strftime("%Y%m%d"), to_d.strftime("%Y%m%d")

    nb = stock.get_market_net_purchases_of_equities(fs, ts, market, investor)
    if nb is None or nb.empty:
        logger.warning("%s/%s/%s: 순매수 데이터 없음", market, period, investor)
        return None
    net_col = _pick(nb, NET_VALUE_ALIASES)
    if net_col is None:
        logger.error("순매수 컬럼 못 찾음. 실제 컬럼=%s", list(nb.columns))
        return None

    out = pd.DataFrame(index=nb.index)
    out.index.name = "종목코드"
    out["종목명"] = nb["종목명"] if "종목명" in nb.columns else ""
    out["순매수"] = nb[net_col]

    # 같은 기간 주가 등락률 붙이기
    chg_col = _pick(price_change, CHANGE_ALIASES)
    if chg_col is not None:
        out["등락률"] = price_change[chg_col].reindex(out.index)
    else:
        out["등락률"] = ""
    return out


def run(market):
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    last = stock.get_nearest_business_day_in_a_week()
    to_d = pd.Timestamp(last)
    saved = 0
    for period, days in PERIOD_DAYS.items():
        from_d = to_d - timedelta(days=days)
        # 등락률은 기간당 한 번만 받아 재사용
        try:
            pc = stock.get_market_price_change(
                from_d.strftime("%Y%m%d"), to_d.strftime("%Y%m%d"), market)
        except Exception as e:
            logger.warning("%s/%s 등락률 조회 실패: %s", market, period, e)
            pc = pd.DataFrame()
        for investor in INVESTORS:
            df = fetch_one(market, period, investor, pc)
            if df is None:
                continue
            path = PROCESSED_DIR / f"screener_{market}_{period}_{investor}.csv"
            df.to_csv(path, encoding="utf-8-sig")
            logger.info("저장: %s (%d종목)", path.name, len(df))
            saved += 1
    return saved


def main():
    parser = argparse.ArgumentParser(description="수급 스크리너 실데이터 수집")
    parser.add_argument("--market", choices=MARKETS, help="한 시장만 (기본: 코스피)")
    args = parser.parse_args()

    targets = [args.market] if args.market else ["KOSPI"]
    total = 0
    for m in targets:
        logger.info("[%s] 스크리너 수집 시작", m)
        total += run(m)
    if total == 0:
        logger.error("저장된 파일이 없습니다. 네트워크/pykrx 상태 확인.")
        sys.exit(1)
    logger.info("완료: %d개 파일 저장 → data/processed/", total)


if __name__ == "__main__":
    main()
