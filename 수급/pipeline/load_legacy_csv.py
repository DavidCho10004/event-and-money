# -*- coding: utf-8 -*-
"""
기존 매수검토_{시장}.csv (와이드 포맷, 2026-07-13 수집) → 누적 저장소 적재 (1회성)

- 스냅샷: 라벨 10개 시점 → snapshots (시총/종가/PER/PBR/외인지분율; 상장주식수는 미수집 → NA)
- 순매수: 24년/25년 연간 + 26년 월별 → flows 해상도 'M' (거래대금만; 거래량 미수집 → NA)
- 종목명: data/processed/names_{시장}.csv 로 저장 (analyze에서 사용)
"""
import logging
import pandas as pd
import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# 라벨 → 실제 영업일 (수집 로그 기준)
SNAP_DATES = {
    "24년초": "20240102", "24년말": "20241230", "25년말": "20251230",
    "26년1월말": "20260130", "26년2월말": "20260227", "26년3월말": "20260331",
    "26년4월말": "20260430", "26년5월말": "20260529", "26년6월말": "20260630",
    "최근": "20260713",
}
# 순매수 기간 라벨 → 대표 날짜(그 구간의 마지막 영업일)
FLOW_DATES = {
    "24년": "20241230", "25년": "20251230",
    "26년1월": "20260130", "26년2월": "20260227", "26년3월": "20260331",
    "26년4월": "20260430", "26년5월": "20260529", "26년6월": "20260630",
    "26년7월": "20260713",
}
INVESTOR_MAP = {"개인": "개인", "외국인": "외국인", "기관": "기관합계", "사모": "사모"}


def load_market(market: str):
    df = pd.read_csv(store.DATA_DIR / f"매수검토_{market}.csv", dtype={"코드": str})
    df["코드"] = df["코드"].str.zfill(6)

    # 종목명 저장
    store.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    df[["코드", "회사명"]].to_csv(store.PROCESSED_DIR / f"names_{market}.csv",
                                index=False, encoding="utf-8-sig")

    # 스냅샷 적재
    snaps = []
    for label, date in SNAP_DATES.items():
        s = pd.DataFrame({
            "날짜": date, "시장": market, "코드": df["코드"],
            "종가": df.get(f"주가_{label}"),
            "시가총액": df.get(f"시가총액(억)_{label}") * 1e8,  # 억원 → 원
            "상장주식수": pd.NA,
            "PER": df.get(f"PER_{label}"), "PBR": df.get(f"PBR_{label}"),
            "외인지분율": df.get(f"외인지분율_{label}"),
        })
        snaps.append(s)
    store.upsert_snapshots(pd.concat(snaps, ignore_index=True))

    # 순매수 적재 (해상도 M)
    flows = []
    for inv_csv, inv_std in INVESTOR_MAP.items():
        for label, date in FLOW_DATES.items():
            col = f"순매수(억)_{inv_csv}_{label}"
            if col not in df.columns:
                continue
            f = pd.DataFrame({
                "날짜": date, "시장": market, "코드": df["코드"], "투자자": inv_std,
                "거래대금": df[col] * 1e8,  # 억원 → 원
                "거래량": pd.NA, "해상도": "M",
            })
            flows.append(f.dropna(subset=["거래대금"]))
    store.upsert_flows(pd.concat(flows, ignore_index=True))
    logger.info("[%s] 적재 완료: 스냅샷 %d시점, 순매수 %d기간×4주체", market, len(SNAP_DATES), len(FLOW_DATES))


if __name__ == "__main__":
    for m in ["KOSPI", "KOSDAQ"]:
        load_market(m)
    snap, flow = store.read_snapshots(), store.read_flows()
    logger.info("저장소 현황: snapshots %d행, flows %d행", len(snap), len(flow))
