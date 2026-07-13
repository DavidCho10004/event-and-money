# -*- coding: utf-8 -*-
"""
스크리너 테이블 생성 (MVP판) — 저장소 → 루트 data/processed/buy_review_{시장}.csv

MVP 범위: 이중 지표(금액 / 강도) + 외인지분율 변화폭 + 시장별 변화폭 순위만.
  - 강도(%) = 순매수 주식수 ÷ 상장주식수 — 현 저장소에 거래량·상장주식수가 없어
    지금은 전부 빈 값 (일별 층 수집 후 채워짐). 지어내지 않는다.
필터 칩·플래그·배지 색단계는 다음 라운드.
"""
import json
import logging
from pathlib import Path

import pandas as pd
import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT = store.BASE_DIR.parent            # 저장소 루트 (event-and-money/)
OUT_DIR = ROOT / "data" / "processed"   # Railway가 읽는 위치

BASE_LABELS = {"기준_25년말": "2025-12-30", "기준_전월": None, "기준_최근": None}


def build(market: str) -> pd.DataFrame:
    snap = store.read_snapshots()
    flow = store.read_flows()
    snap = snap[snap["시장"] == market]
    flow = flow[flow["시장"] == market]

    dates = sorted(snap["날짜"].unique())
    latest, prev_month = dates[-1], dates[-2]
    base = "2025-12-30"

    def frgn_at(d):
        s = snap[snap["날짜"] == d].set_index("코드")["외인지분율"]
        return s

    f_base, f_prev, f_latest = frgn_at(base), frgn_at(prev_month), frgn_at(latest)
    latest_snap = snap[snap["날짜"] == latest].set_index("코드")

    df = pd.DataFrame(index=f_latest.index)
    df["외인지분율_25년말"] = f_base
    df["외인지분율_전월"] = f_prev
    df["외인지분율_최근"] = f_latest
    df["변화폭_25년말比"] = (f_latest - f_base).round(2)
    df["변화폭_전월比"] = (f_latest - f_prev).round(2)
    df["PBR_최근"] = latest_snap["PBR"]
    df["시가총액_억"] = (latest_snap["시가총액"] / 1e8).round(0)
    df["종가_최근"] = latest_snap["종가"]

    # 순매수 금액: '26년 누계 (M행, 2026년 구간 합) — 투자자별
    f26 = flow[(flow["날짜"] >= "2026-01-01")]
    pivot = f26.pivot_table(index="코드", columns="투자자", values="거래대금", aggfunc="sum")
    for inv in ["외국인", "기관합계", "개인", "사모"]:
        label = "기관" if inv == "기관합계" else inv
        if inv in pivot.columns:
            df[f"순매수억_{label}_26누계"] = (pivot[inv] / 1e8).round(1)
        else:
            df[f"순매수억_{label}_26누계"] = pd.NA

    # 강도(%): 거래량·상장주식수 확보 전 — 빈 값 (지어내지 않음)
    df["강도_외국인_26누계"] = pd.NA
    df["수급동행"] = pd.NA   # 2단계 예약 컬럼

    # 시장별 변화폭 순위 (전 종목 저장)
    df["변화폭순위"] = df["변화폭_25년말比"].rank(ascending=False, method="min").astype("Int64")

    # 종목명
    names = pd.read_csv(store.PROCESSED_DIR / f"names_{market}.csv", dtype={"코드": str})
    names["코드"] = names["코드"].str.zfill(6)
    df = df.join(names.set_index("코드")["회사명"]).reset_index().rename(columns={"index": "코드"})
    df = df[["코드", "회사명"] + [c for c in df.columns if c not in ("코드", "회사명")]]
    df = df.sort_values("변화폭_25년말比", ascending=False)

    df.attrs["기준일"] = latest
    return df


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meta = {}
    for market in ["KOSPI", "KOSDAQ"]:
        df = build(market)
        out = OUT_DIR / f"buy_review_{market}.csv"
        df.to_csv(out, index=False, encoding="utf-8-sig")
        meta[market] = {"기준일": df.attrs["기준일"], "종목수": len(df)}
        logger.info("[%s] 저장: %s (%d종목)", market, out, len(df))
    (OUT_DIR / "buy_review_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    logger.info("meta: %s", meta)


if __name__ == "__main__":
    main()
