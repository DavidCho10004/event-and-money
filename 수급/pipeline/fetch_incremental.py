# -*- coding: utf-8 -*-
"""
주간 증분 수집 (일별 층) — 규훈님 PC에서 실행 (KRX 로그인 필요)

동작:
    1. 저장소의 일별(D) 마지막 수집일 확인
       - 없으면(첫 실행) 기본 365일 전부터 백필 시작 (--lookback 조정 가능)
    2. 마지막 수집일 다음 날 ~ 마지막 '완료된' 거래일 사이의 거래일 목록 조회
       - 당일 장 마감 집계 전(18시 이전)이면 오늘은 제외 (방어 로직)
    3. 거래일 × 시장 × 투자자 6종의 순매수(대금+거래량)를 하루 단위로 수집
       → 하루 끝날 때마다 즉시 upsert (중간 실패 시 그 날부터 이어받기)
    4. 스냅샷: 수집 구간 안의 월말 + 최신 거래일에 대해
       시총/종가/상장주식수/PER/PBR/외인지분율 단면 upsert

멱등성: 같은 날 두 번 실행해도 upsert 키(날짜×시장×코드×투자자×해상도)로 중복 없음.
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta

import pandas as pd
from pykrx import stock

import store

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fetch_incremental")
logger.setLevel(logging.INFO)

MARKETS = ["KOSPI", "KOSDAQ"]
# 항등식 검증(validate.py)을 위해 KRX 제공 주체 전체를 커버:
#   개인 + 외국인 + 기타외국인 + 기관합계 + 기타법인 ≈ 0
#   사모는 기관합계의 부분집합 → 화면용으로만 저장, 합산 검증에서 제외
INVESTORS = ["개인", "외국인", "기타외국인", "기관합계", "사모", "기타법인"]

MAX_RETRIES = 3
RETRY_DELAY = 5
CALL_DELAY = 1.0
DAY_COMPLETE_HOUR = 18   # 이 시각 이전이면 당일 데이터는 집계 전으로 간주


def _retry(func, *args, **kwargs):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = func(*args, **kwargs)
            time.sleep(CALL_DELAY)
            return result
        except Exception as e:  # noqa: BLE001
            logger.warning("호출 실패(%d/%d): %s %s — %s", attempt, MAX_RETRIES, func.__name__, args, e)
            time.sleep(RETRY_DELAY)
    logger.error("최종 실패: %s %s", func.__name__, args)
    return None


def trading_days(start: str, end: str) -> list:
    """KOSPI 지수 시세의 날짜 인덱스로 거래일 목록 조회 (YYYYMMDD)."""
    if start > end:
        return []
    df = _retry(stock.get_index_ohlcv, start, end, "1001")
    if df is None or df.empty:
        return []
    return [d.strftime("%Y%m%d") for d in df.index]


def fetch_flows_for_day(day: str) -> pd.DataFrame:
    """하루치 시장×투자자 순매수 롱포맷 수집."""
    rows = []
    for market in MARKETS:
        for inv in INVESTORS:
            df = _retry(stock.get_market_net_purchases_of_equities, day, day, market, inv)
            if df is None:
                raise RuntimeError(f"{day} {market} {inv} 수집 실패 (재시도 소진)")
            if df.empty:
                continue
            part = pd.DataFrame({
                "날짜": day, "시장": market, "코드": df.index.astype(str),
                "투자자": inv, "거래대금": df["순매수거래대금"].values,
                "거래량": df["순매수거래량"].values, "해상도": "D",
            })
            rows.append(part)
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def fetch_snapshot_for_day(day: str) -> pd.DataFrame:
    """하루치 전 종목 단면(시총/종가/상장주식수/PER/PBR/외인지분율)."""
    rows = []
    for market in MARKETS:
        cap = _retry(stock.get_market_cap_by_ticker, day, market=market)
        fund = _retry(stock.get_market_fundamental_by_ticker, day, market=market)
        frgn = _retry(stock.get_exhaustion_rates_of_foreign_investment, day, market=market)
        if cap is None or cap.empty:
            logger.warning("[%s] %s 스냅샷 없음 — 건너뜀", market, day)
            continue
        df = pd.DataFrame({
            "날짜": day, "시장": market, "코드": cap.index.astype(str),
            "종가": cap["종가"].values, "시가총액": cap["시가총액"].values,
            "상장주식수": cap["상장주식수"].values,
        })
        df = df.set_index("코드")
        if fund is not None and not fund.empty:
            df["PER"] = fund["PER"]
            df["PBR"] = fund["PBR"]
        else:
            df["PER"] = pd.NA
            df["PBR"] = pd.NA
        if frgn is not None and not frgn.empty:
            df["외인지분율"] = frgn["지분율"]
        else:
            df["외인지분율"] = pd.NA
        rows.append(df.reset_index())
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


def month_ends_in(days: list) -> list:
    """거래일 목록에서 각 달의 마지막 거래일만 추출."""
    by_month = {}
    for d in days:
        by_month[d[:6]] = d   # 정렬된 목록이므로 마지막 값이 월말 거래일
    return sorted(by_month.values())


def main():
    parser = argparse.ArgumentParser(description="주간 증분 수집 (일별 층)")
    parser.add_argument("--lookback", type=int, default=365,
                        help="첫 실행 시 백필 일수 (기본 365)")
    parser.add_argument("--days", type=int, default=None,
                        help="테스트용: 최근 N거래일만 수집")
    args = parser.parse_args()

    if not (os.environ.get("KRX_ID") and os.environ.get("KRX_PW")):
        logger.error("KRX_ID / KRX_PW 환경 변수가 없습니다. run_weekly.bat 로 실행하세요.")
        sys.exit(1)

    now = datetime.now()
    end_dt = now if now.hour >= DAY_COMPLETE_HOUR else now - timedelta(days=1)
    end = end_dt.strftime("%Y%m%d")

    last = store.last_flow_date("D")
    if args.days:
        start = (end_dt - timedelta(days=args.days * 2)).strftime("%Y%m%d")
    elif last:
        start = (pd.Timestamp(last) + pd.Timedelta(days=1)).strftime("%Y%m%d")
    else:
        start = (end_dt - timedelta(days=args.lookback)).strftime("%Y%m%d")
        logger.info("일별 저장소가 비어 있음 → %s부터 백필", start)

    days = trading_days(start, end)
    if args.days:
        days = days[-args.days:]
    # 이미 수집된 날짜 제외 (멱등성)
    done = set()
    existing = store.read_flows()
    if not existing.empty:
        done = set(existing[existing["해상도"] == "D"]["날짜"].str.replace("-", ""))
    days = [d for d in days if d not in done]

    if not days:
        logger.info("수집할 새 거래일이 없습니다. (마지막 수집일: %s)", last)
        print("NOTHING_TO_FETCH")
        return

    logger.info("수집 대상 거래일 %d일: %s ~ %s", len(days), days[0], days[-1])
    for i, day in enumerate(days, 1):
        flows = fetch_flows_for_day(day)
        if flows.empty:
            logger.warning("%s: 순매수 데이터 없음 — 건너뜀", day)
            continue
        store.upsert_flows(flows)
        # 외국인 지분율은 일별로도 수집 (1주일 변화폭 토글용 — 하루 2회 호출 추가)
        frgn_rows = []
        for market in MARKETS:
            frgn = _retry(stock.get_exhaustion_rates_of_foreign_investment, day, market=market)
            if frgn is not None and not frgn.empty:
                frgn_rows.append(pd.DataFrame({
                    "날짜": day, "시장": market, "코드": frgn.index.astype(str),
                    "종가": pd.NA, "시가총액": pd.NA, "상장주식수": pd.NA,
                    "PER": pd.NA, "PBR": pd.NA, "외인지분율": frgn["지분율"].values,
                }))
        if frgn_rows:
            store.upsert_snapshots(pd.concat(frgn_rows, ignore_index=True))
        logger.info("(%d/%d) %s 저장 — %d행", i, len(days), day, len(flows))

    # 스냅샷: 구간 내 월말 거래일 + 최신 거래일
    snap_days = sorted(set(month_ends_in(days) + [days[-1]]))
    for day in snap_days:
        snap = fetch_snapshot_for_day(day)
        if not snap.empty:
            store.upsert_snapshots(snap)
            logger.info("스냅샷 저장: %s — %d행", day, len(snap))

    print(f"FETCHED {len(days)} days ({days[0]}~{days[-1]})")


if __name__ == "__main__":
    main()
