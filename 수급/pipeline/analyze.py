# -*- coding: utf-8 -*-
"""
스크리너 테이블 생성 — 저장소 → 루트 data/processed/buy_review_{시장}.csv

기간 구조: 상대 기간(6m/3m/1m/1w)별로 지분율 변화폭·순위·순매수·플래그를 각각 계산.
  - 기준 시점 지분율은 해당 시점 스냅샷에서 직접 조회 (역산 금지)
  - 기준 스냅샷이 허용 오차 내에 없으면 그 기간은 '미제공' (meta에 기록, 지어내지 않음)
  - 1w는 일별 지분율 스냅샷이 쌓인 뒤에 자동 활성화
강도(%) = 기간 외국인 순매수 주식수 ÷ 기간 시작 시점 상장주식수 × 100.
수급동행은 2단계 예약.
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

# 팩트 플래그 기준 (조정 가능) — "의심" 휴리스틱 대신 산수로 확인되는 사실만 표시
SHARES_CHANGE_FLAG_PCT = 1.0   # 기간 중 상장주식수 변동률(%)이 이 이상이면 플래그
OFFMARKET_GAP_PCT = 1.0        # |지분율 기반 보유주식 변화 − 장내 외인 순매수량|이
                               # 상장주식수의 이 비율(%p) 이상이면 '장외 지분변동' 플래그


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

        # 기간 주가 등락률 — 기준일에 종가가 없는 종목은 그 이전 가장 가까운
        # 실제 거래일 종가 사용 (보간·추정 금지)
        closes = snap[snap["종가"].notna()][["날짜", "코드", "종가"]]
        c_base = (closes[closes["날짜"] <= base].sort_values("날짜")
                  .groupby("코드")["종가"].last())
        c_latest = latest_snap["종가"]
        df[f"주가등락pct_{p}"] = ((c_latest / c_base - 1) * 100).round(2)

        # 팩트 플래그 1: 상장주식수 변동률 (기준→최근, 실측)
        S_base = snap[snap["날짜"] == base].set_index("코드")["상장주식수"]
        S_latest = latest_snap["상장주식수"]
        shares_chg = ((S_latest / S_base - 1) * 100).round(2)
        df[f"주식수변동pct_{p}"] = shares_chg

        # 팩트 플래그 2: 장외 지분변동 — 지분율 기반 보유주식 변화와
        # 장내 외인 순매수량(외국인+기타외국인)의 격차 (상장주식수 대비 %p)
        vol = fp[fp["투자자"].isin(["외국인", "기타외국인"])].pivot_table(
            index="코드", values="거래량", aggfunc="sum")["거래량"]
        dH = (f_latest * S_latest - f_base * S_base) / 100    # 보유주식 변화(주)
        gap_pct = ((dH - vol.reindex(dH.index).fillna(0)) / S_latest * 100).round(2)
        df[f"장외변동pct_{p}"] = gap_pct

        # 강도(%): 기간 외국인 순매수 주식수 ÷ 기간 시작 상장주식수
        # (기간 중 주식수 변동 종목은 ⚠플래그로 표시되므로 분모 보정하지 않음)
        vol_f = fp[fp["투자자"] == "외국인"].pivot_table(
            index="코드", values="거래량", aggfunc="sum")["거래량"]
        strength = (vol_f / S_base * 100).round(3)
        df[f"강도pct_{p}"] = strength
        df[f"강도순위_{p}"] = strength.rank(ascending=False, method="min").astype("Int64")

        df[f"플래그_주식수변동_{p}"] = (shares_chg.abs() >= SHARES_CHANGE_FLAG_PCT).fillna(False)
        df[f"플래그_장외변동_{p}"] = (gap_pct.abs() >= OFFMARKET_GAP_PCT).fillna(False)
        df[f"플래그_기관동반_{p}"] = (df[f"순매수억_기관_{p}"] > 0).fillna(False)
        df[f"플래그_개인순매도_{p}"] = (df[f"순매수억_개인_{p}"] < 0).fillna(False)
        meta_periods[p] = {"available": True, "기준일": base}

    names = pd.read_csv(store.PROCESSED_DIR / f"names_{market}.csv", dtype={"코드": str})
    names["코드"] = names["코드"].str.zfill(6)
    df = df.join(names.set_index("코드")["회사명"]).reset_index().rename(columns={"index": "코드"})
    cols = ["코드", "회사명"] + [c for c in df.columns if c not in ("코드", "회사명")]
    return df[cols], {"기준일": latest, "종목수": len(df), "periods": meta_periods}


def build_detail() -> int:
    """종목 상세용 일별 시계열 → data/processed/detail/detail_{코드 앞2자리}.csv

    샤딩 이유: 종목당 개별 파일(~5,500개)은 저장소 비대, 단일 파일(~40MB)은
    요청마다 전체 파싱 → 앞 2자리 샤드(~100파일, 각 수백KB)로 절충.
    컬럼: 날짜, 코드, 종가, 외인지분율, 외인_억, 기관_억, 개인_억
    (종가는 일별 수집분이 있는 날만 값 존재 — 지어내지 않음)
    """
    flow = store.read_flows()
    flow = flow[(flow["해상도"] == "D") & (flow["투자자"].isin(["외국인", "기관합계", "개인"]))]
    pivot = (flow.pivot_table(index=["날짜", "코드"], columns="투자자",
                              values="거래대금", aggfunc="sum") / 1e8).round(1)
    pivot = pivot.rename(columns={"외국인": "외인_억", "기관합계": "기관_억", "개인": "개인_억"})

    snap = store.read_snapshots()
    snap = snap[snap["날짜"] >= flow["날짜"].min()]
    snap = snap.set_index(["날짜", "코드"])[["종가", "외인지분율"]]
    snap["외인지분율"] = snap["외인지분율"].astype(float).round(2)

    detail = snap.join(pivot, how="outer").reset_index()
    detail = detail[["날짜", "코드", "종가", "외인지분율", "외인_억", "기관_억", "개인_억"]]
    detail = detail.sort_values(["코드", "날짜"])

    out = OUT_DIR / "detail"
    out.mkdir(parents=True, exist_ok=True)
    for shard, part in detail.groupby(detail["코드"].str[:2]):
        part.to_csv(out / f"detail_{shard}.csv", index=False, encoding="utf-8-sig")
    logger.info("상세 시계열: %d행 → 샤드 %d개 (%s ~ %s)",
                len(detail), detail["코드"].str[:2].nunique(),
                detail["날짜"].min(), detail["날짜"].max())
    return len(detail)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    meta = {}
    for market in ["KOSPI", "KOSDAQ"]:
        df, m = build(market)
        out = OUT_DIR / f"buy_review_{market}.csv"
        df.to_csv(out, index=False, encoding="utf-8-sig")
        meta[market] = m
        logger.info("[%s] 저장: %s (%d종목) periods=%s", market, out.name, len(df), m["periods"])
    detail_rows = build_detail()
    meta["detail"] = {"rows": detail_rows, "shard": "코드 앞 2자리",
                      "path": "data/processed/detail/detail_{XX}.csv"}
    (OUT_DIR / "buy_review_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
