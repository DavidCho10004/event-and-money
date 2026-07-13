# -*- coding: utf-8 -*-
"""
과거 일별 종가/시가총액/상장주식수 채우기 (1회성 — 약 15분)

일별 백필이 순매수+지분율만 받았으므로, 이미 수집된 일별 구간에 대해
get_market_cap_by_ticker 로 종가·시총·상장주식수를 보충한다.
스냅샷 upsert는 NA-보존 병합이라 기존 지분율은 유지된다.
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
logger = logging.getLogger("fetch_daily_caps")
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
    # 일별 지분율은 있는데 종가가 없는 날짜만 대상 (이어받기 자동)
    by_day = snap.groupby("날짜").agg(has_frgn=("외인지분율", lambda s: s.notna().any()),
                                     has_close=("종가", lambda s: s.notna().any()))
    targets = sorted(by_day[(by_day["has_frgn"]) & (~by_day["has_close"])].index)
    if not targets:
        print("NOTHING_TO_FILL")
        return
    logger.info("종가 보충 대상 %d일: %s ~ %s", len(targets), targets[0], targets[-1])

    for i, day in enumerate(targets, 1):
        d = day.replace("-", "")
        rows = []
        for market in MARKETS:
            cap = _retry(stock.get_market_cap_by_ticker, d, market=market)
            if cap is None or cap.empty:
                logger.warning("%s %s 조회 실패 — 건너뜀", d, market)
                continue
            rows.append(pd.DataFrame({
                "날짜": d, "시장": market, "코드": cap.index.astype(str),
                "종가": cap["종가"].values, "시가총액": cap["시가총액"].values,
                "상장주식수": cap["상장주식수"].values,
                "PER": pd.NA, "PBR": pd.NA, "외인지분율": pd.NA,
            }))
        if rows:
            store.upsert_snapshots(pd.concat(rows, ignore_index=True))
            logger.info("(%d/%d) %s 저장", i, len(targets), d)

    print(f"FILLED {len(targets)} days")


if __name__ == "__main__":
    main()
