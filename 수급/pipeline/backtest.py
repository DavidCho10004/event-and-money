# -*- coding: utf-8 -*-
"""
3단계 백테스트 엔진 — 월별 10년 데이터로 스크리너 조건의 선행성 검증

전략 (각 월말 시점 t, 그 시점까지의 데이터만 사용 — 룩어헤드 차단):
  a  삼박자(그 달 외인+·기관+·개인−) AND 지분율 3개월 변화폭 +3%p↑
  b  외인 순매수 강도(그 달 순매수량 ÷ 전월말 상장주식수) 상위 20
  c  a ∩ b
진입: 각 월말 종가. 포트폴리오 20종목 초과 시 지분율 3개월 변화폭 상위 20 컷.
보유: 1/3/6개월 후 월말 종가 매도, 균등 가중.
  - 만기 시점에 종가가 없는 종목(상장폐지/거래정지)은 제외하고 dropped로 기록
    (생존 편향 상향 가능성 있음 — 결과 해석 시 유의)
벤치마크:
  ① 시장 프록시 = 그 시점 유니버스의 시가총액 가중 수익률
    (지수 시계열 미보유 → 동일 종가 데이터로 만든 프록시임을 명시)
  ② 랜덤 = 같은 시점·같은 개수 무작위 종목, 시드 고정 100회 평균

출력: data/processed/backtest/backtest_monthly.csv (진입월 단위 전체 기록)
      data/processed/backtest/backtest_summary.csv (전략×보유기간 요약)

사용: python backtest.py --start 2020-01 --end 2020-12   # 시험 실행
      python backtest.py                                  # 전체 (2016-04~)
"""
import argparse
import logging

import numpy as np
import pandas as pd

import store

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backtest")
logger.setLevel(logging.INFO)

OUT_DIR = store.BASE_DIR.parent / "data" / "processed" / "backtest"

MARKETS = ["KOSPI", "KOSDAQ"]
HOLDS = [1, 3, 6]                # 보유기간 (개월)
PORTFOLIO_CAP = 20               # 종목 수 상한 (초과 시 변화폭 상위 컷)
DELTA_MIN = 3.0                  # 전략 a: 지분율 3개월 변화폭 하한 (%p)
DELTA_WINDOW = 3                 # 변화폭 창 (개월)
STRENGTH_TOP = 20                # 전략 b: 강도 상위 N
RANDOM_DRAWS = 100               # 랜덤 벤치마크 반복 횟수
RANDOM_SEED = 42                 # 시드 고정
COST_PER_TRADE = 0.003           # 거래비용: 왕복(진입+청산) 1회당 0.3% 가정
M_CUTOFF = "2025-06-30"          # 이 날짜까지는 M행(월별 백필), 이후는 D행 월 합산


def build_panel(market: str):
    """월별 패널: ym → {closes, frgn, shares, cap, flow_amt(투자자별), flow_vol_frgn}."""
    sn = store.read_snapshots()
    sn = sn[sn["시장"] == market]
    fl = store.read_flows()
    fl = fl[fl["시장"] == market]

    # 월말 대표일: 각 YYYY-MM에서 종가가 있는 마지막 날짜
    has_close = sn[sn["종가"].notna()].copy()
    has_close["ym"] = has_close["날짜"].str[:7]
    month_end = has_close.groupby("ym")["날짜"].max()

    snap_me = sn[sn["날짜"].isin(month_end.values)].copy()
    snap_me["ym"] = snap_me["날짜"].str[:7]
    closes = snap_me.pivot_table(index="코드", columns="ym", values="종가")
    frgn = snap_me.pivot_table(index="코드", columns="ym", values="외인지분율")
    shares = snap_me.pivot_table(index="코드", columns="ym", values="상장주식수")
    cap = snap_me.pivot_table(index="코드", columns="ym", values="시가총액")

    # 월별 순매수: M행(≤M_CUTOFF, 월 합계) + D행 월 합산(그 이후)
    m = fl[(fl["해상도"] == "M") & (fl["날짜"] <= M_CUTOFF)].copy()
    d = fl[(fl["해상도"] == "D") & (fl["날짜"] > M_CUTOFF)].copy()
    m["ym"] = m["날짜"].str[:7]
    d["ym"] = d["날짜"].str[:7]
    monthly = pd.concat([m, d], ignore_index=True)

    flow_amt = {inv: monthly[monthly["투자자"] == inv].pivot_table(
        index="코드", columns="ym", values="거래대금", aggfunc="sum")
        for inv in ["외국인", "기관합계", "개인"]}
    flow_vol_frgn = monthly[monthly["투자자"] == "외국인"].pivot_table(
        index="코드", columns="ym", values="거래량", aggfunc="sum")

    yms = sorted(set(closes.columns))
    return {"yms": yms, "closes": closes, "frgn": frgn, "shares": shares,
            "cap": cap, "flow_amt": flow_amt, "flow_vol_frgn": flow_vol_frgn}


def ym_offset(yms: list, ym: str, k: int):
    """패널 월 목록에서 ym보다 k개월 뒤(음수면 앞)의 ym. 없으면 None."""
    if ym not in yms:
        return None
    i = yms.index(ym) + k
    return yms[i] if 0 <= i < len(yms) else None


def pick_portfolio(panel, ym: str) -> dict:
    """시점 ym의 전략별 포트폴리오 (그 시점까지의 데이터만 사용)."""
    yms = panel["yms"]
    prev = ym_offset(yms, ym, -1)
    base3 = ym_offset(yms, ym, -DELTA_WINDOW)
    if prev is None or base3 is None:
        return {}

    def col(piv, m):
        return piv[m] if m in piv.columns else pd.Series(dtype=float)

    closes_t = panel["closes"][ym].dropna()
    universe = closes_t.index

    amt_f = col(panel["flow_amt"]["외국인"], ym).reindex(universe)
    amt_i = col(panel["flow_amt"]["기관합계"], ym).reindex(universe)
    amt_p = col(panel["flow_amt"]["개인"], ym).reindex(universe)
    delta3 = (panel["frgn"][ym] - panel["frgn"][base3]).reindex(universe)

    # a: 삼박자 AND 3개월 변화폭 +3%p↑
    mask_a = (amt_f > 0) & (amt_i > 0) & (amt_p < 0) & (delta3 >= DELTA_MIN)
    port_a = universe[mask_a.fillna(False)]

    # b: 강도(그 달 외인 순매수량 ÷ 전월말 상장주식수) 상위 20
    vol_f = col(panel["flow_vol_frgn"], ym).reindex(universe)
    strength = vol_f / panel["shares"][prev].reindex(universe)
    port_b = strength.dropna().nlargest(STRENGTH_TOP).index

    port_c = port_a.intersection(port_b)

    def cap20(idx):
        if len(idx) <= PORTFOLIO_CAP:
            return list(idx)
        return list(delta3.reindex(idx).nlargest(PORTFOLIO_CAP).index)

    return {"a": cap20(port_a), "b": list(port_b), "c": cap20(port_c),
            "_delta3": delta3, "_universe": universe}


def returns_between(panel, codes, ym_in: str, ym_out: str):
    """진입월말→만기월말 수익률. (평균수익률, 편입수, 만기누락수) 반환."""
    c_in = panel["closes"][ym_in].reindex(codes)
    c_out = panel["closes"][ym_out].reindex(codes)
    ret = (c_out / c_in - 1)
    valid = ret.dropna()
    return (valid.mean() if len(valid) else np.nan), len(codes), len(codes) - len(valid)


def market_proxy_return(panel, ym_in: str, ym_out: str):
    """시장 프록시: 유니버스 시가총액(진입 시점) 가중 수익률."""
    c_in, c_out = panel["closes"][ym_in], panel["closes"][ym_out]
    w = panel["cap"][ym_in]
    df = pd.DataFrame({"r": c_out / c_in - 1, "w": w}).dropna()
    return (df["r"] * df["w"]).sum() / df["w"].sum() if len(df) else np.nan


def random_benchmark(panel, ym_in: str, ym_out: str, n: int, month_idx: int):
    """같은 시점·같은 개수 랜덤 포트폴리오 100회 평균 (시드 고정)."""
    pool = panel["closes"][ym_in].dropna().index
    pool = pool[panel["closes"][ym_out].reindex(pool).notna()]
    if len(pool) < n or n == 0:
        return np.nan
    rng = np.random.default_rng(RANDOM_SEED + month_idx)
    rets = []
    for _ in range(RANDOM_DRAWS):
        picks = rng.choice(pool, size=n, replace=False)
        r, _, _ = returns_between(panel, list(picks), ym_in, ym_out)
        rets.append(r)
    return float(np.nanmean(rets))


def run(start: str, end: str) -> pd.DataFrame:
    rows = []
    for market in MARKETS:
        logger.info("[%s] 패널 구축...", market)
        panel = build_panel(market)
        yms = [y for y in panel["yms"] if start <= y <= end]
        for mi, ym in enumerate(yms):
            ports = pick_portfolio(panel, ym)
            if not ports:
                continue
            for hold in HOLDS:
                ym_out = ym_offset(panel["yms"], ym, hold)
                if ym_out is None:
                    continue
                mkt_ret = market_proxy_return(panel, ym, ym_out)
                for strat in ["a", "b", "c"]:
                    codes = ports[strat]
                    if not codes:
                        rows.append({"시장": market, "전략": strat, "보유": hold,
                                     "진입월": ym, "종목수": 0, "만기누락": 0,
                                     "수익률": np.nan, "시장프록시": mkt_ret, "랜덤평균": np.nan})
                        continue
                    ret, n, dropped = returns_between(panel, codes, ym, ym_out)
                    rand = random_benchmark(panel, ym, ym_out, len(codes), mi)
                    rows.append({"시장": market, "전략": strat, "보유": hold,
                                 "진입월": ym, "종목수": n, "만기누락": dropped,
                                 "수익률": ret, "시장프록시": mkt_ret, "랜덤평균": rand})
            logger.info("[%s] %s 완료 (a=%d b=%d c=%d)", market, ym,
                        len(ports["a"]), len(ports["b"]), len(ports["c"]))
    df = pd.DataFrame(rows)
    df["초과_시장"] = df["수익률"] - df["시장프록시"]
    df["초과_랜덤"] = df["수익률"] - df["랜덤평균"]
    df["수익률_비용후"] = df["수익률"] - COST_PER_TRADE
    df["초과_랜덤_비용후"] = df["수익률_비용후"] - df["랜덤평균"]
    return df


def summarize(df: pd.DataFrame) -> pd.DataFrame:
    out = []
    for (mkt, strat, hold), g in df.groupby(["시장", "전략", "보유"]):
        g = g[g["종목수"] > 0].dropna(subset=["수익률"])
        if g.empty:
            continue
        worst = g.loc[g["수익률"].idxmin()]
        curve = (1 + g.sort_values("진입월")["수익률"]).cumprod().iloc[-1]
        out.append({
            "시장": mkt, "전략": strat, "보유개월": hold,
            "진입횟수": len(g),
            "평균종목수": round(g["종목수"].mean(), 1),
            "승률_vs시장": round((g["초과_시장"] > 0).mean() * 100, 1),
            "승률_vs랜덤": round((g["초과_랜덤"] > 0).mean() * 100, 1),
            "평균수익률%": round(g["수익률"].mean() * 100, 2),
            "평균초과_시장%p": round(g["초과_시장"].mean() * 100, 2),
            "평균초과_랜덤%p": round(g["초과_랜덤"].mean() * 100, 2),
            "평균초과_랜덤_비용후%p": round(g["초과_랜덤_비용후"].mean() * 100, 2),
            "누적배수(중첩단순)": round(curve, 2),
            "최악의달": f"{worst['진입월']} ({worst['수익률']*100:.1f}%)",
            "만기누락합": int(g["만기누락"].sum()),
        })
    return pd.DataFrame(out)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2016-04")   # 3개월 창 확보 후 첫 진입
    parser.add_argument("--end", default="2026-06")
    parser.add_argument("--tag", default="", help="출력 파일명 접미사 (시험 실행 구분용)")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = run(args.start, args.end)
    tag = f"_{args.tag}" if args.tag else ""
    df.to_csv(OUT_DIR / f"backtest_monthly{tag}.csv", index=False, encoding="utf-8-sig")
    summary = summarize(df)
    summary.to_csv(OUT_DIR / f"backtest_summary{tag}.csv", index=False, encoding="utf-8-sig")
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
