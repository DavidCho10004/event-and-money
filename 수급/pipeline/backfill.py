# -*- coding: utf-8 -*-
"""
10년 월별 백필 (1회성, 약 1.5~2시간) — 규훈님 PC에서 실행 (KRX 로그인 필요)

수집 범위: 2016-01 ~ 일별 층 시작 직전 달 (기본 2025-06)
  - 일별(D) 층이 2025-07-14부터 커버하므로 월별(M)은 그 이전까지만 → 이중계산 없음
월 단위 수집 내용:
  1) 월말 스냅샷: 시총/종가/상장주식수/PER/PBR/외인지분율
     - 종목 목록은 해당 시점 기준 (생존 편향 완화 — get_market_cap_by_ticker가
       그 날짜의 상장 종목만 반환)
  2) 투자자 6주체 월별 순매수 (거래대금+거래량) → flows 해상도 'M'

체크포인트: data/_checkpoints/backfill_10y.json — {시장}:{연월}:{항목} 단위 완료 기록.
중단 후 재실행 시 미완료 항목만 수행. 월 하나 끝날 때마다 즉시 저장.

시험 실행: python pipeline/backfill.py --start 201601 --end 201603
완료 후 커버리지 리포트(연도별 × 항목별 존재율) 출력 — '사모' 등 과거 구간
분류 부재는 그대로 기록한다 (지어내지 않음).
"""
import argparse
import logging
import os
import sys
import time

import pandas as pd
from pykrx import stock

import store

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill")
logger.setLevel(logging.INFO)

MARKETS = ["KOSPI", "KOSDAQ"]
INVESTORS = ["개인", "외국인", "기타외국인", "기관합계", "사모", "기타법인"]
MAX_RETRIES, RETRY_DELAY, CALL_DELAY = 3, 5, 1.0
CHECKPOINT = "backfill_10y"
DEFAULT_START, DEFAULT_END = "201601", "202506"   # 일별 층(2025-07~)과 겹치지 않게


def _retry(func, *args, **kwargs):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = func(*args, **kwargs)
            time.sleep(CALL_DELAY)
            return result
        except Exception as e:  # noqa: BLE001
            logger.warning("호출 실패(%d/%d): %s %s — %s", attempt, MAX_RETRIES,
                           func.__name__, args[:2], e)
            time.sleep(RETRY_DELAY)
    logger.error("최종 실패: %s %s", func.__name__, args[:2])
    return None


def month_list(start: str, end: str) -> list:
    """YYYYMM 목록."""
    out = []
    cur = pd.Period(start, freq="M")
    last = pd.Period(end, freq="M")
    while cur <= last:
        out.append(cur.strftime("%Y%m"))
        cur += 1
    return out


def month_range(ym: str) -> tuple:
    """(월초 YYYYMMDD, 월말 YYYYMMDD)."""
    p = pd.Period(ym, freq="M")
    return p.start_time.strftime("%Y%m%d"), p.end_time.strftime("%Y%m%d")


def collect_snapshot(market: str, ym: str) -> bool:
    _, eom = month_range(ym)
    day = _retry(stock.get_nearest_business_day_in_a_week, eom)
    if not day:
        return False
    cap = _retry(stock.get_market_cap_by_ticker, day, market=market)
    if cap is None or cap.empty:
        return False
    fund = _retry(stock.get_market_fundamental_by_ticker, day, market=market)
    frgn = _retry(stock.get_exhaustion_rates_of_foreign_investment, day, market=market)
    df = pd.DataFrame({
        "날짜": day, "시장": market, "코드": cap.index.astype(str),
        "종가": cap["종가"].values, "시가총액": cap["시가총액"].values,
        "상장주식수": cap["상장주식수"].values,
    }).set_index("코드")
    df["PER"] = fund["PER"] if fund is not None and not fund.empty else pd.NA
    df["PBR"] = fund["PBR"] if fund is not None and not fund.empty else pd.NA
    df["외인지분율"] = frgn["지분율"] if frgn is not None and not frgn.empty else pd.NA
    store.upsert_snapshots(df.reset_index())
    return True


def collect_flows(market: str, ym: str, investor: str) -> str:
    """수집 결과: 'ok' | 'empty' | 'fail'. empty(해당 월 데이터 없음)도 완료로 기록."""
    fs, ts = month_range(ym)
    eom_bday = _retry(stock.get_nearest_business_day_in_a_week, ts)
    if not eom_bday:
        return "fail"
    df = _retry(stock.get_market_net_purchases_of_equities, fs, ts, market, investor)
    if df is None:
        return "fail"
    if df.empty:
        return "empty"
    part = pd.DataFrame({
        "날짜": eom_bday, "시장": market, "코드": df.index.astype(str),
        "투자자": investor, "거래대금": df["순매수거래대금"].values,
        "거래량": df["순매수거래량"].values, "해상도": "M",
    })
    store.upsert_flows(part)
    return "ok"


def coverage_report(start: str, end: str):
    """연도별 × 항목별 존재율 리포트 (사모 등 과거 부재를 그대로 기록)."""
    sn = store.read_snapshots()
    fl = store.read_flows()
    fl = fl[fl["해상도"] == "M"]
    print("\n===== 커버리지 리포트 (월별 층) =====")
    for year in sorted({ym[:4] for ym in month_list(start, end)}):
        months = [m for m in month_list(start, end) if m.startswith(year)]
        sn_y = sn[sn["날짜"].str.startswith(year)]
        row = [f"{year}: 스냅샷 {sn_y['날짜'].nunique()}/{len(months)*2}시점(양시장)"]
        fl_y = fl[fl["날짜"].str.startswith(year)]
        for inv in INVESTORS:
            n = fl_y[fl_y["투자자"] == inv]["날짜"].nunique()
            row.append(f"{inv} {n}")
        print(" | ".join(row))
    print("(숫자 = 데이터가 존재하는 월말 시점 수. '사모' 등이 0이면 해당 구간 KRX 미제공)")


def main():
    parser = argparse.ArgumentParser(description="10년 월별 백필")
    parser.add_argument("--start", default=DEFAULT_START, help="YYYYMM (기본 201601)")
    parser.add_argument("--end", default=DEFAULT_END, help="YYYYMM (기본 202506)")
    args = parser.parse_args()

    if not (os.environ.get("KRX_ID") and os.environ.get("KRX_PW")):
        logger.error("KRX_ID / KRX_PW 환경 변수가 없습니다. bat으로 실행하세요.")
        sys.exit(1)

    months = month_list(args.start, args.end)
    ckpt = store.load_checkpoint(CHECKPOINT)
    total = len(months) * len(MARKETS) * (1 + len(INVESTORS))
    done0 = len(ckpt)
    logger.info("대상: %s~%s %d개월 × 2시장 — 작업 %d건 (완료 %d건, 이어받기)",
                args.start, args.end, len(months), total, done0)

    fails = 0
    for ym in months:
        for market in MARKETS:
            key = f"{market}:{ym}:snapshot"
            if key not in ckpt:
                if collect_snapshot(market, ym):
                    ckpt[key] = "ok"
                    store.save_checkpoint(CHECKPOINT, ckpt)
                else:
                    fails += 1
                    logger.error("%s 실패 — 재실행 시 재시도됩니다", key)
            for inv in INVESTORS:
                key = f"{market}:{ym}:{inv}"
                if key in ckpt:
                    continue
                result = collect_flows(market, ym, inv)
                if result == "fail":
                    fails += 1
                    logger.error("%s 실패 — 재실행 시 재시도됩니다", key)
                else:
                    ckpt[key] = result
                    store.save_checkpoint(CHECKPOINT, ckpt)
        logger.info("[%s] 완료 (%d/%d건)", ym, len(ckpt), total)

    coverage_report(args.start, args.end)
    if fails:
        print(f"\nWARNING: {fails}건 실패 — 같은 명령으로 재실행하면 실패분만 재시도합니다.")
        sys.exit(1)
    print("\nBACKFILL COMPLETE")


if __name__ == "__main__":
    main()
