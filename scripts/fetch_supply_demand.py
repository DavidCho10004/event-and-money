"""
코스피/코스닥 투자자별 수급(개인·외국인·기관) + 지수 종가 수집 → data/raw/ 에 CSV 저장

데이터 소스: pykrx (한국거래소 KRX). 무료, API 키 불필요.
  - 지수 종가/거래대금 : stock.get_index_ohlcv()   (코스피 1001, 코스닥 2001)
  - 투자자별 순매수 금액 : stock.get_market_trading_value_by_date()  (단위: 원)

수급 지표 정의: "순매수 거래대금(원)". (순매수 = 매수 - 매도)
  → 양수면 그 투자자 집단이 순매수(매집), 음수면 순매도(차익실현)

실행 방법 (프로젝트 루트에서):
    python scripts/fetch_supply_demand.py                       # 코스피+코스닥, 최근 3년
    python scripts/fetch_supply_demand.py --start 2015-01-01    # 시작일 지정
    python scripts/fetch_supply_demand.py --market KOSPI        # 한 시장만

주의:
  - 이 스크립트는 규훈님 로컬(Windows)에서 실행하세요. KRX는 국내망 기준이라
    일부 클라우드/사내 프록시 환경에서는 차단될 수 있습니다.
  - 결과 CSV는 data/raw/ 에 저장되며 .gitignore 대상입니다(원본은 커밋 안 함).
    분석/차트는 scripts/analyze_supply_demand.py 로 이어서 실행하세요.
"""
import sys
import time
import argparse
import logging
from pathlib import Path
from datetime import date, timedelta

import pandas as pd
from pykrx import stock

# 프로젝트 루트
ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# 시장 코드 매핑: 시장명 → (지수 티커, 투자자 조회용 시장명)
MARKETS = {
    "KOSPI": {"index_ticker": "1001", "name_kr": "코스피"},
    "KOSDAQ": {"index_ticker": "2001", "name_kr": "코스닥"},
}

MAX_RETRIES = 3
RETRY_DELAY = 5  # 초

# pykrx 버전별로 투자자 컬럼명이 조금씩 달라서, 표준 라벨로 통일한다.
INVESTOR_ALIASES = {
    "개인": "개인",
    "외국인": "외국인",
    "외국인합계": "외국인",
    "기관": "기관",
    "기관합계": "기관",
    "기타법인": "기타법인",
    "기타외국인": "기타외국인",
    "전체": "전체",
}
# 최종적으로 남길 4개 수급 주체 (기타외국인은 외국인에 포함되지 않으므로 별도 유지)
KEEP_INVESTORS = ["개인", "외국인", "기관", "기타법인"]


def _retry(fn, what):
    """네트워크 실패 시 재시도 래퍼"""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as e:  # pykrx는 requests 예외를 그대로 던짐
            logger.warning("%s 실패 (%d/%d): %s", what, attempt, MAX_RETRIES, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)
    logger.error("%s 최종 실패 — 건너뜀", what)
    return None


def fetch_index_close(from_str, to_str, index_ticker):
    """지수 일별 종가/거래대금 조회 → DataFrame(index=날짜, columns=[종가, 지수거래대금])"""
    df = _retry(
        lambda: stock.get_index_ohlcv(from_str, to_str, index_ticker),
        f"지수({index_ticker}) 조회",
    )
    if df is None or df.empty:
        return None
    out = pd.DataFrame(index=df.index)
    out["종가"] = df["종가"]
    if "거래대금" in df.columns:
        out["지수거래대금"] = df["거래대금"]
    return out


def fetch_investor_netbuy(from_str, to_str, market):
    """투자자별 일별 순매수 거래대금(원) 조회 → DataFrame(index=날짜, columns=수급주체)"""
    df = _retry(
        lambda: stock.get_market_trading_value_by_date(
            from_str, to_str, market, detail=False
        ),
        f"{market} 투자자 수급 조회",
    )
    if df is None or df.empty:
        return None
    # 컬럼명을 표준 라벨로 통일
    df = df.rename(columns=INVESTOR_ALIASES)
    # 중복 라벨(같은 이름으로 합쳐진 경우) 방지: 같은 이름은 합산
    df = df.groupby(level=0, axis=1).sum()
    cols = [c for c in KEEP_INVESTORS if c in df.columns]
    if not cols:
        logger.error("%s: 알려진 투자자 컬럼을 못 찾음. 실제 컬럼=%s", market, list(df.columns))
        return None
    return df[cols]


def fetch_market(market, from_str, to_str):
    """한 시장(코스피/코스닥)의 지수 종가 + 투자자 수급을 합쳐 하나의 일별 DataFrame으로"""
    meta = MARKETS[market]
    logger.info("[%s / %s] %s ~ %s 수집 시작", market, meta["name_kr"], from_str, to_str)

    idx = fetch_index_close(from_str, to_str, meta["index_ticker"])
    inv = fetch_investor_netbuy(from_str, to_str, market)
    if idx is None or inv is None:
        logger.error("[%s] 수집 실패 — 지수=%s, 수급=%s", market, idx is not None, inv is not None)
        return None

    merged = idx.join(inv, how="inner")
    merged.index.name = "날짜"
    logger.info("[%s] 수집 완료: %d 거래일, 컬럼=%s", market, len(merged), list(merged.columns))
    return merged


def main():
    parser = argparse.ArgumentParser(description="코스피/코스닥 투자자별 수급 수집")
    default_start = (date.today() - timedelta(days=365 * 3)).isoformat()
    parser.add_argument("--start", default=default_start, help="시작일 YYYY-MM-DD (기본: 3년 전)")
    parser.add_argument("--end", default=date.today().isoformat(), help="종료일 YYYY-MM-DD (기본: 오늘)")
    parser.add_argument(
        "--market",
        choices=list(MARKETS.keys()),
        help="한 시장만 수집 (미지정 시 코스피+코스닥 모두)",
    )
    args = parser.parse_args()

    from_str = args.start.replace("-", "")
    to_str = args.end.replace("-", "")
    targets = [args.market] if args.market else list(MARKETS.keys())

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    ok = 0
    for market in targets:
        df = fetch_market(market, from_str, to_str)
        if df is None:
            continue
        out_path = RAW_DIR / f"supply_demand_{market}.csv"
        df.to_csv(out_path, encoding="utf-8-sig")
        logger.info("저장: %s", out_path)
        ok += 1

    if ok == 0:
        logger.error("수집된 시장이 없습니다. 네트워크/pykrx 상태를 확인하세요.")
        sys.exit(1)
    logger.info("완료: %d개 시장 수집. 다음 단계 → python scripts/analyze_supply_demand.py", ok)


if __name__ == "__main__":
    main()
