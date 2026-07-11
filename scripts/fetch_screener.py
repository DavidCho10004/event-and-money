"""
fetch_screener.py — KOSPI200 종목 리스트 수집

KOSPI200 지수(코드 1028)의 구성 종목 코드와 이름을 pykrx로 받아
data/processed/kospi200_tickers.csv 로 저장한다.

⚠️ 네트워크 주의:
    pykrx는 KRX 서버(data.krx.co.kr)에 실시간 접속한다.
    국내망(규훈님 로컬 Windows)에서 실행할 것.
    일부 클라우드/해외 IP에서는 KRX가 봇으로 판단해 '400 LOGOUT'을 반환하며 차단한다.

실행 방법 (프로젝트 루트에서):
    # Windows에서 한글 인코딩 안전을 위해 UTF-8 모드 권장
    set PYTHONUTF8=1
    python scripts/fetch_screener.py                 # 최근 영업일 기준
    python scripts/fetch_screener.py --date 20260710 # 특정 일자 기준
"""
import sys
import argparse
import logging
from pathlib import Path

import pandas as pd
from pykrx import stock

# 프로젝트 루트 기준 경로
ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "processed"
OUT_FILE = OUT_DIR / "kospi200_tickers.csv"

KOSPI200_INDEX = "1028"  # KRX 지수코드: KOSPI 200

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def get_reference_date(date_arg):
    """기준 영업일 결정. 인자가 없으면 최근 영업일을 사용."""
    if date_arg:
        return date_arg
    ref = stock.get_nearest_business_day_in_a_week()
    logger.info("기준 영업일 (최근): %s", ref)
    return ref


def fetch_kospi200_tickers(ref_date):
    """KOSPI200 구성종목의 (종목코드, 종목명) 목록을 DataFrame으로 반환."""
    tickers = stock.get_index_portfolio_deposit_file(KOSPI200_INDEX, ref_date)
    if not tickers:
        raise RuntimeError(
            f"KOSPI200 구성종목을 받지 못했습니다 (기준일 {ref_date}). "
            "KRX 접속 차단(LOGOUT) 또는 휴장일일 수 있습니다."
        )

    rows = []
    for code in tickers:
        name = stock.get_market_ticker_name(code)
        rows.append({"code": code, "name": name})

    df = pd.DataFrame(rows).sort_values("code").reset_index(drop=True)
    return df


def main():
    parser = argparse.ArgumentParser(description="KOSPI200 종목 리스트 수집")
    parser.add_argument(
        "--date",
        default=None,
        help="기준 영업일 (YYYYMMDD). 미지정 시 최근 영업일 사용.",
    )
    parser.add_argument(
        "--market",
        default="KOSPI",
        help="시장 구분(호환용 인자). 현재는 KOSPI200 고정.",
    )
    args = parser.parse_args()

    ref_date = get_reference_date(args.date)

    logger.info("KOSPI200 구성종목 수집 중... (기준일 %s)", ref_date)
    df = fetch_kospi200_tickers(ref_date)
    logger.info("→ %d개 종목 확보", len(df))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_FILE, index=False, encoding="utf-8-sig")
    logger.info("저장 완료: %s", OUT_FILE)


if __name__ == "__main__":
    main()
