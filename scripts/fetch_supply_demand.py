"""
fetch_supply_demand.py — KOSPI200 종목별 투자자 수급(순매수) 수집

fetch_screener.py가 만든 kospi200_tickers.csv를 읽어,
각 종목의 최근 N거래일 투자자별 순매수(거래대금 기준)를 pykrx로 받아
data/processed/supply_demand.csv 로 저장한다.

⚠️ 네트워크 주의:
    pykrx는 KRX 서버(data.krx.co.kr)에 실시간 접속한다.
    국내망(규훈님 로컬 Windows)에서 실행할 것.
    클라우드/해외 IP에서는 KRX가 '400 LOGOUT'으로 차단할 수 있다.
    종목이 200개이므로 요청이 많다 → 종목당 딜레이를 둔다.

실행 방법 (프로젝트 루트에서, fetch_screener.py 실행 후):
    set PYTHONUTF8=1
    python scripts/fetch_supply_demand.py                # 최근 60거래일
    python scripts/fetch_supply_demand.py --days 60
    python scripts/fetch_supply_demand.py --from 20260401 --to 20260710
"""
import sys
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
from pykrx import stock

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
TICKERS_FILE = PROCESSED_DIR / "kospi200_tickers.csv"
OUT_FILE = PROCESSED_DIR / "supply_demand.csv"

# pykrx 응답 컬럼명 → 표준 컬럼명 매핑 (버전/응답에 따라 이름이 다를 수 있어 방어적으로 처리)
INVESTOR_COLS = {
    "기관합계": "inst_net",       # 기관 순매수
    "기타법인": "corp_net",       # 기타법인 순매수
    "개인": "indi_net",           # 개인 순매수
    "외국인합계": "foreign_net",  # 외국인 순매수(외국인+기타외국인)
    "외국인": "foreign_net",      # (일부 응답은 '외국인'으로 옴)
    "전체": "total_net",
}

REQUEST_DELAY = 0.4   # 종목당 요청 간격(초) — KRX 부하/차단 완화
MAX_RETRIES = 3
RETRY_DELAY = 5

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def resolve_date_range(args):
    """--from/--to 우선, 없으면 --days 거래일 수만큼 뒤로 잡아 (fromdate, todate) 반환.

    거래일 정확 계산을 위해 달력일수는 넉넉히(약 2배) 잡고, pykrx가 거래일만 반환하도록 한다.
    """
    if args.from_date and args.to_date:
        return args.from_date, args.to_date

    todate = args.to_date or stock.get_nearest_business_day_in_a_week()
    to_dt = datetime.strptime(todate, "%Y%m%d")
    # 거래일 ~ 달력일 비율 대략 5/7 → days*2 로 여유있게 소급
    from_dt = to_dt - timedelta(days=args.days * 2 + 10)
    return from_dt.strftime("%Y%m%d"), todate


def load_tickers():
    """kospi200_tickers.csv 로드. 없으면 명확한 안내와 함께 종료."""
    if not TICKERS_FILE.exists():
        logger.error(
            "종목 리스트가 없습니다: %s\n"
            "먼저 fetch_screener.py를 실행하세요:\n"
            "    python scripts/fetch_screener.py",
            TICKERS_FILE,
        )
        sys.exit(1)
    df = pd.read_csv(TICKERS_FILE, dtype={"code": str})
    df["code"] = df["code"].str.zfill(6)  # leading zero 보존
    return df


def fetch_one(code, fromdate, todate, days):
    """단일 종목의 일자별 투자자 순매수를 표준 형식 DataFrame으로 반환 (재시도 포함)."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            df = stock.get_market_trading_value_by_date(
                fromdate, todate, code, on="순매수", detail=False
            )
            if df is None or df.empty:
                return pd.DataFrame()

            df = df.rename(columns=INVESTOR_COLS)
            # 표준 컬럼만 남기기 (없는 컬럼은 무시)
            keep = [c for c in ["foreign_net", "inst_net", "indi_net", "corp_net", "total_net"]
                    if c in df.columns]
            df = df[keep].copy()
            df.index.name = "date"
            df = df.reset_index()
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")

            # 최근 days 거래일만
            df = df.tail(days).copy()
            df.insert(0, "code", code)
            return df

        except Exception as e:  # noqa: BLE001 - pykrx 내부 예외 종류가 다양
            logger.warning("  %s 시도 %d/%d 실패: %s", code, attempt, MAX_RETRIES, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)
    return pd.DataFrame()


def main():
    parser = argparse.ArgumentParser(description="KOSPI200 종목별 투자자 순매수 수집")
    parser.add_argument("--days", type=int, default=60, help="최근 거래일 수 (기본 60)")
    parser.add_argument("--from", dest="from_date", default=None, help="시작일 YYYYMMDD")
    parser.add_argument("--to", dest="to_date", default=None, help="종료일 YYYYMMDD")
    parser.add_argument("--market", default="KOSPI", help="호환용 인자")
    args = parser.parse_args()

    fromdate, todate = resolve_date_range(args)
    logger.info("수집 범위: %s ~ %s (최근 %d거래일)", fromdate, todate, args.days)

    tickers = load_tickers()
    logger.info("대상 종목: %d개", len(tickers))
    logger.info("=" * 60)

    frames = []
    failed = []
    for i, row in enumerate(tickers.itertuples(index=False), 1):
        code, name = row.code, row.name
        logger.info("[%03d/%d] %s %s", i, len(tickers), code, name)
        one = fetch_one(code, fromdate, todate, args.days)
        if one.empty:
            failed.append(code)
        else:
            one.insert(1, "name", name)
            frames.append(one)
        time.sleep(REQUEST_DELAY)

    if not frames:
        logger.error(
            "수집된 데이터가 없습니다. KRX 접속 차단(LOGOUT)일 가능성이 큽니다. "
            "국내망 로컬에서 실행했는지 확인하세요."
        )
        sys.exit(1)

    result = pd.concat(frames, ignore_index=True)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUT_FILE, index=False, encoding="utf-8-sig")

    logger.info("=" * 60)
    logger.info("저장 완료: %s (%d행)", OUT_FILE, len(result))
    if failed:
        logger.warning("실패 종목 %d개: %s", len(failed), failed[:20])


if __name__ == "__main__":
    main()
