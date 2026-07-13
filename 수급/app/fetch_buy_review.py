# -*- coding: utf-8 -*-
"""
매수 검토 매트릭스 수집 스크립트 (친구분 엑셀 '★주식 매수 검토' 컨셉)

전 종목(코스피/코스닥)에 대해 아래 데이터를 수집해서
종목 1행짜리 스크리닝 매트릭스 CSV를 만든다.

  1) 시가총액 스냅샷      : '24년초 / '24년말 / '25년말 / '26년 월말
  2) 주가(종가) 스냅샷    : 같은 시점
  3) PER / PBR 스냅샷     : 같은 시점  (선행 PER은 KRX 미제공 → 제외)
  4) 투자자별 순매수 대금 : 개인/외국인/기관/사모 — '24년, '25년 연간 + '26년 월별 (억원)
  5) 외국인 지분율        : 스냅샷 시점별 + '25년말 대비 증감(%p)

실행 (규훈님 PC, 국내망 필요 — 클라우드에서는 KRX가 차단됨):
    매수검토_수집.bat 더블클릭
    또는  python app/fetch_buy_review.py [--market KOSPI|KOSDAQ|ALL]

출력:
    data/매수검토_KOSPI.csv, data/매수검토_KOSDAQ.csv  (utf-8-sig, 엑셀에서 바로 열림)

소요 시간: 시장당 약 3~5분 (KRX 호출 약 40회, 호출 간 1초 대기)
"""

import argparse
import logging
import sys
import time
from datetime import datetime

import pandas as pd
from pykrx import stock

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent   # 수급/
DATA_DIR = BASE_DIR / "data"

INVESTORS = ["개인", "외국인", "기관합계", "사모"]   # 엑셀 프로토타입과 동일한 4주체
MAX_RETRIES = 3
RETRY_DELAY = 5      # 초
CALL_DELAY = 1.0     # KRX 부하 방지용 호출 간 대기


def _retry(func, *args, **kwargs):
    """KRX 호출 실패 시 재시도. 최종 실패하면 None 반환."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = func(*args, **kwargs)
            time.sleep(CALL_DELAY)
            return result
        except Exception as e:  # noqa: BLE001 - pykrx는 다양한 예외를 던짐
            logger.warning("호출 실패(%d/%d): %s %s — %s", attempt, MAX_RETRIES, func.__name__, args, e)
            time.sleep(RETRY_DELAY)
    logger.error("최종 실패: %s %s", func.__name__, args)
    return None


def snapshot_dates(today: datetime) -> dict:
    """스냅샷 시점 목록: 라벨 → YYYYMMDD (해당 월의 마지막 영업일 근처 날짜).

    pykrx의 by_ticker 계열 함수는 휴장일을 넣으면 빈 값이 나오므로
    get_nearest_business_day_in_a_week 로 보정한다.
    """
    raw = {"24년초": "20240102", "24년말": "20241230", "25년말": "20251230"}
    # '26년 1월부터 지난달 말까지 + 최근 영업일
    y, m = 2026, 1
    while (y, m) < (today.year, today.month):
        # 월말: 다음달 1일 하루 전
        if m == 12:
            eom = f"{y}1231"
        else:
            eom = (pd.Timestamp(y, m + 1, 1) - pd.Timedelta(days=1)).strftime("%Y%m%d")
        raw[f"26년{m}월말"] = eom
        m += 1
    raw["최근"] = today.strftime("%Y%m%d")

    dates = {}
    for label, d in raw.items():
        bday = _retry(stock.get_nearest_business_day_in_a_week, d)
        if bday:
            dates[label] = bday
    return dates


def fetch_snapshots(market: str, dates: dict) -> pd.DataFrame:
    """시점별 시가총액/종가/PER/PBR/외국인지분율을 수집해 종목 코드 기준으로 병합."""
    frames = []
    for label, d in dates.items():
        logger.info("[%s] 스냅샷 수집: %s (%s)", market, label, d)

        cap = _retry(stock.get_market_cap_by_ticker, d, market=market)
        ohlcv = _retry(stock.get_market_ohlcv_by_ticker, d, market=market)
        fund = _retry(stock.get_market_fundamental_by_ticker, d, market=market)
        frgn = _retry(stock.get_exhaustion_rates_of_foreign_investment, d, market=market)

        if cap is None or cap.empty:
            logger.warning("[%s] %s 시가총액 없음 — 건너뜀", market, label)
            continue

        df = pd.DataFrame(index=cap.index)
        df[f"시가총액(억)_{label}"] = (cap["시가총액"] / 1e8).round(0)
        if ohlcv is not None and not ohlcv.empty:
            df[f"주가_{label}"] = ohlcv["종가"]
        if fund is not None and not fund.empty:
            df[f"PER_{label}"] = fund["PER"]
            df[f"PBR_{label}"] = fund["PBR"]
        if frgn is not None and not frgn.empty:
            df[f"외인지분율_{label}"] = frgn["지분율"]
        frames.append(df)

    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, axis=1)
    out.index.name = "코드"
    return out


def fetch_netbuy(market: str, today: datetime) -> pd.DataFrame:
    """투자자별 순매수 거래대금(억원): '24년/'25년 연간 + '26년 월별 + '26년 누계."""
    periods = [("24년", "20240102", "20241230"), ("25년", "20250102", "20251230")]
    y, m = 2026, 1
    while (y, m) <= (today.year, today.month):
        start = f"{y}{m:02d}01"
        if (y, m) == (today.year, today.month):
            end = today.strftime("%Y%m%d")
        elif m == 12:
            end = f"{y}1231"
        else:
            end = (pd.Timestamp(y, m + 1, 1) - pd.Timedelta(days=1)).strftime("%Y%m%d")
        periods.append((f"26년{m}월", start, end))
        m += 1

    frames = []
    for inv in INVESTORS:
        inv_label = "기관" if inv == "기관합계" else inv
        for p_label, fs, ts in periods:
            logger.info("[%s] 순매수 수집: %s / %s (%s~%s)", market, inv_label, p_label, fs, ts)
            df = _retry(stock.get_market_net_purchases_of_equities, fs, ts, market, inv)
            if df is None or df.empty:
                logger.warning("[%s] %s %s 순매수 없음 — 건너뜀", market, inv_label, p_label)
                continue
            col = pd.DataFrame(index=df.index)
            col[f"순매수(억)_{inv_label}_{p_label}"] = (df["순매수거래대금"] / 1e8).round(1)
            frames.append(col)

    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, axis=1)

    # '26년 누계 (투자자별 월 합산)
    for inv in INVESTORS:
        inv_label = "기관" if inv == "기관합계" else inv
        month_cols = [c for c in out.columns if c.startswith(f"순매수(억)_{inv_label}_26년")]
        if month_cols:
            out[f"순매수(억)_{inv_label}_26년누계"] = out[month_cols].sum(axis=1).round(1)

    out.index.name = "코드"
    return out


def build_matrix(market: str, today: datetime) -> pd.DataFrame:
    """스냅샷 + 순매수를 병합해 종목 1행짜리 매트릭스 생성."""
    dates = snapshot_dates(today)
    if not dates:
        logger.error("[%s] 영업일 조회 실패 — KRX 접속 상태를 확인하세요.", market)
        return pd.DataFrame()
    logger.info("[%s] 스냅샷 시점 %d개: %s", market, len(dates), dates)

    snap = fetch_snapshots(market, dates)
    netbuy = fetch_netbuy(market, today)
    if snap.empty and netbuy.empty:
        return pd.DataFrame()

    matrix = snap.join(netbuy, how="outer")

    # 종목명 + 외국인 지분율 증감('25년말 → 최근)
    matrix.insert(0, "회사명", [stock.get_market_ticker_name(c) for c in matrix.index])
    if "외인지분율_25년말" in matrix.columns and "외인지분율_최근" in matrix.columns:
        matrix["외인지분율_증감p"] = (matrix["외인지분율_최근"] - matrix["외인지분율_25년말"]).round(2)

    return matrix


def main():
    parser = argparse.ArgumentParser(description="매수 검토 매트릭스 수집")
    parser.add_argument("--market", default="ALL", choices=["KOSPI", "KOSDAQ", "ALL"])
    args = parser.parse_args()

    markets = ["KOSPI", "KOSDAQ"] if args.market == "ALL" else [args.market]
    today = datetime.today()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    ok = []
    for market in markets:
        matrix = build_matrix(market, today)
        if matrix.empty:
            logger.error("[%s] 수집 실패 — 데이터 없음", market)
            continue
        out_path = DATA_DIR / f"매수검토_{market}.csv"
        matrix.to_csv(out_path, encoding="utf-8-sig")
        logger.info("[%s] 저장 완료: %s (%d종목 × %d열)", market, out_path, len(matrix), len(matrix.columns))
        ok.append(market)

    if not ok:
        logger.error("수집된 시장이 없습니다. 국내망에서 실행했는지 / pykrx 상태를 확인하세요.")
        sys.exit(1)
    logger.info("완료: %s", ", ".join(ok))


if __name__ == "__main__":
    main()
