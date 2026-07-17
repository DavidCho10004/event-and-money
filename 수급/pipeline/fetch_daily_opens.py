# -*- coding: utf-8 -*-
"""
일별 시가 백필 (1회성, 약 10분) — 단기(일 단위) 백테스트 전용

일별 지분율이 있는 모든 날짜에 대해 get_market_ohlcv_by_ticker 로 시가를 보충한다.
스냅샷 upsert는 NA-보존 병합이라 기존 종가/지분율 등은 유지된다.
날짜 단위 즉시 저장 → 중단 후 재실행 시 이어받기.
"""
import logging
import os
import sys
import time

import pandas as pd
from pykrx import stock

import store

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fetch_daily_opens")
logger.setLevel(logging.INFO)

MARKETS = ["KOSPI", "KOSDAQ"]
MAX_RETRIES, RETRY_DELAY, CALL_DELAY = 3, 5, 1.0


def _retry(func, *args, **kwargs):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = func(*args, **kwargs)
            time.sleep(CALL_DELAY)
            return result
        except Exception as e:  # noqa: BLE001
            logger.warning("호출 실패(%d/%d): %s — %s", attempt, MAX_RETRIES, args, e)
            time.sleep(RETRY_DELAY)
    return None


def main():
    if not (os.environ.get("KRX_ID") and os.environ.get("KRX_PW")):
        logger.error("KRX_ID / KRX_PW 환경 변수가 없습니다.")
        sys.exit(1)

    snap = store.read_snapshots()
    has_open = "시가" in snap.columns
    by_day = snap.groupby("날짜").agg(
        has_frgn=("외인지분율", lambda s: s.notna().any()),
        has_open=("시가", lambda s: s.notna().any()) if has_open else ("외인지분율", lambda s: False),
    )
    targets = sorted(by_day[(by_day["has_frgn"]) & (~by_day["has_open"])].index)
    if not targets:
        print("NOTHING_TO_FILL")
        return
    logger.info("시가 보충 대상 %d일: %s ~ %s", len(targets), targets[0], targets[-1])

    for i, day in enumerate(targets, 1):
        d = day.replace("-", "")
        rows = []
        for market in MARKETS:
            ohlcv = _retry(stock.get_market_ohlcv_by_ticker, d, market=market)
            if ohlcv is None or ohlcv.empty:
                logger.warning("%s %s 조회 실패 — 건너뜀", d, market)
                continue
            rows.append(pd.DataFrame({
                "날짜": d, "시장": market, "코드": ohlcv.index.astype(str),
                "종가": pd.NA, "시가": ohlcv["시가"].values, "시가총액": pd.NA,
                "상장주식수": pd.NA, "PER": pd.NA, "PBR": pd.NA, "외인지분율": pd.NA,
            }))
        if rows:
            store.upsert_snapshots(pd.concat(rows, ignore_index=True))
            logger.info("(%d/%d) %s 저장", i, len(targets), d)

    print(f"FILLED {len(targets)} days")


if __name__ == "__main__":
    main()
