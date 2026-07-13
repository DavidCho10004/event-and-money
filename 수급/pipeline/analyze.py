# -*- coding: utf-8 -*-
"""
스크리너 테이블 생성 — 저장소 → 루트 data/processed/buy_review_{시장}.csv

기간 구조: 상대 기간(6m/3m/1m/1w)별로 지분율 변화폭·순위·순매수·플래그를 각각 계산.
  - 기준 시점 지분율은 해당 시점 스냅샷에서 직접 조회 (역산 금지)
  - 기준 스냅샷이 허용 오차 내에 없으면 그 기간은 '미제공' (meta에 기록, 지어내지 않음)
  - 1w는 일별 지분율 스냅샷이 쌓인 뒤에 자동 활성화
강도(%)는 거래량·상장주식수 확보 후. 수급동행은 2단계 예약.
"""
import json
import logging

import pandas as pd
import store

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT = store.BASE_DIR.parent            # 저장소 루트 (event-and-money/)
OUT_DIR = ROOT / "data" / "processed"   # Railway가 읽는 위치

# 기간 정의: (일수, 기준 스냅샷 허용 오차 일수)
PERIODS = {"6m": (182, 25), "3m": (91, 25), "1m": (30, 25), "1w": (7, 3)}

# 주식수 변동 의심 판정 기준 (조정 가능)
SUSPECT_DELTA_MIN = 1.0    # 이 이상 지분율이 움직였는데 (%p)
SUSPECT_NETBUY_EOK = 50    # 순매수가 이 금액(억) 이하로 미미하거나, 방향이 반대면 의심


def pick_base_date(dates: list, latest: str, days: int, tol: int) -> str | None:
    """latest - days 에 가장 가까운 스냅샷 날짜 선택 (오차 tol일 초과 시 None).

    거리가 5일 이내로 비슷한 후보가 둘이면 더 최근 쪽을 선택
    (예: '1개월'이 45일 전 스냅샷보다 직전 월말을 가리키도록).
    """
    target = pd.Timestamp(latest) - pd.Timedelta(days=days)
    cands = [(abs((pd.Timestamp(d) - target).days), d) for d in dates if d < latest]
    cands.sort()
    if not cands or cands[0][0] > tol:
        return None
    best_diff, best = cands[0]
    for diff, d in cands[1:]:
        if diff - best_diff <= 5 and d > best:
            best = d
        break
    return best


def build(market: str) -> tuple[pd.DataFrame, dict]:
    snap = store.read_snapshots()
    flow = store.read_flows()
    snap = snap[snap["시장"] == market]
    flow = flow[flow["시장"] == market]

    # 이중계산 방지: 일별(D)이 커버하는 구간에서는 월별(M) 행 제외
    d_rows = flow[flow["해상도"] == "D"]
    if not d_rows.empty:
        d_start = d_rows["날짜"].min()
        flow = flow[(flow["해상도"] == "D") | (flow["날짜"] < d_start)]

    # 지분율이 있는 날짜만 기준 후보로 사용
    frgn_dates = sorted(snap[snap["외인지분율"].notna()]["날짜"].unique())
    latest = frgn_dates[-1]
    latest_snap = snap[snap["날짜"] == latest].set_index("코드")

    def frgn_at(d):
        s = snap[(snap["날짜"] == d) & (snap["외인지분율"].notna())]
        return s.set_index("코드")["외인지분율"]

    f_latest = frgn_at(latest)
    df = pd.DataFrame(index=f_latest.index)
    df["외인지분율_최근"] = f_latest
    df["PBR_최근"] = latest_snap["PBR"]
    df["시가총액_억"] = (latest_snap["시가총액"] / 1e8).round(0)
    df["종가_최근"] = latest_snap["종가"]
    df["강도_외국인"] = pd.NA   # 거래량·상장주식수 확보 후
    df["수급동행"] = pd.NA      # 2단계 예약

    meta_periods = {}
    for p, (days, tol) in PERIODS.items():
        base = pick_base_date(frgn_dates, latest, days, tol)
        if base is None:
            meta_periods[p] = {"available": False, "기준일": None}
            continue
        f_base = frgn_at(base)
        delta = (f_latest - f_base).round(2)
        df[f"지분율기준_{p}"] = f_base
        df[f"변화폭_{p}"] = delta
        df[f"순위_{p}"] = delta.rank(ascending=False, method="min").astype("Int64")

        # 기간 순매수 (기준일 이후의 flows 합, M/D 중복 없음 전제: M은 일별 커버 이전 구간만)
        fp = flow[(flow["날짜"] > base) & (flow["날짜"] <= latest)]
        pivot = fp.pivot_table(index="코드", columns="투자자", values="거래대금", aggfunc="sum")
        for inv in ["외국인", "기관합계", "개인", "사모"]:
            label = "기관" if inv == "기관합계" else inv
            df[f"순매수억_{label}_{p}"] = ((pivot[inv] / 1e8).round(1)
                                          if inv in pivot.columns else pd.NA)

        # 플래그 (기간 정합: 같은 기간의 변화폭 vs 순매수)
        nb = df[f"순매수억_외국인_{p}"]
        moved = delta.abs() >= SUSPECT_DELTA_MIN
        df[f"플래그_주식수변동의심_{p}"] = (moved & (((delta * nb) < 0) | (nb.abs() <= SUSPECT_NETBUY_EOK))).fillna(False)
        df[f"플래그_기관동반_{p}"] = (df[f"순매수억_기관_{p}"] > 0).fillna(False)
        df[f"플래그_개인순매도_{p}"] = (df[f"순매수억_개인_{p}"] < 0).fillna(False)
        meta_periods[p] = {"available": True, "기준일": base}

    names = pd.read_csv(store.PROCESSED_DIR / f"names_{market}.csv", dtype={"코드": str})
    names["코드"] = names["코드"].str.zfill(6)
    df = df.join(names.set_index("코드")["회사명"]).reset_index().rename(columns={"index": "코드"})
    cols = ["코드", "회사명"] + [c for c in df.columns if c not in ("코드", "회사명")]
    return df[cols], {"기준일": latest, "종목수": len(df), "periods": meta_periods}


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meta = {}
    for market in ["KOSPI", "KOSDAQ"]:
        df, m = build(market)
        out = OUT_DIR / f"buy_review_{market}.csv"
        df.to_csv(out, index=False, encoding="utf-8-sig")
        meta[market] = m
        logger.info("[%s] 저장: %s (%d종목) periods=%s", market, out.name, len(df), m["periods"])
    (OUT_DIR / "buy_review_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
