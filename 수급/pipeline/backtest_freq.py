# -*- coding: utf-8 -*-
"""
B. 빈도 가설 (친구 제안의 규율 버전) — 일·주 단위 수급 탐색 종료 라운드의 절반

신호: 매월 말 t, "직전 20거래일 중 외인 강도(일별 순매수량÷그날 상장주식수)
     상위 30위 안에 든 일수" 상위 20종목 매수 → 1/3개월 보유.
변형: B1 = 외인 단독 / B2 = 외인+기관합계 거래량 합산 강도 (이 2개만, 추가 변형 금지)

사전 예상: 월 H2(지속성)와 동형 — 서열은 있되 랜덤 미달.
한계 (파일 머리 고정):
  - 강도의 분모는 유통물량이 아닌 상장주식수 근사
  - 일별 데이터가 2025-07부터라 진입 표본 ~10회 안팎 — 표본 부족, 기간 분할 불가
    (전 진입이 2021~26 후반 구간) → 강건성은 상위3 제거·비용후만 유효
벤치마크·강건성: 기존 엔진 자동 적용. 임계값 튜닝 금지.
"""
import logging

import numpy as np
import pandas as pd

import store
from backtest import (MARKETS, OUT_DIR, build_panel, ym_offset, returns_between,
                      market_proxy_return, random_benchmark, summarize, robustness,
                      COST_PER_TRADE)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("backtest_freq")
logger.setLevel(logging.INFO)

FREQ_WINDOW = 20    # 직전 20거래일
FREQ_TOP = 30       # 일별 강도 상위 30위 안에 들면 '진입 일수' 1
PORT_N = 20         # 진입 일수 상위 20종목
HOLDS = [1, 3]

HEADER = (
    "# 사전 예상: 월 H2(지속성)와 동형 — 서열은 있되 랜덤 미달 (달라도 그대로 보고)",
    "# 한계: 강도 분모는 유통물량이 아닌 상장주식수 근사",
    "# 한계: 일별 데이터 2025-07~ → 진입 표본 ~10회, 전부 후반 구간 — 기간 분할 무의미",
)


def daily_rank_counts(market: str):
    """날짜별 강도 상위 30 집합 → 종목별 '진입 일수'를 월말 시점마다 계산할 원천."""
    sn = store.read_snapshots()
    sn = sn[sn["시장"] == market]
    shares = sn[sn["상장주식수"].notna()].pivot_table(index="코드", columns="날짜", values="상장주식수")

    fl = store.read_flows()
    fl = fl[(fl["시장"] == market) & (fl["해상도"] == "D")]
    vol_f = fl[fl["투자자"] == "외국인"].pivot_table(index="코드", columns="날짜", values="거래량", aggfunc="sum")
    vol_i = fl[fl["투자자"] == "기관합계"].pivot_table(index="코드", columns="날짜", values="거래량", aggfunc="sum")

    days = sorted(set(vol_f.columns) & set(shares.columns))
    tops = {"B1": {}, "B2": {}}
    for d in days:
        s = shares[d]
        st1 = (vol_f[d] / s).dropna()
        tops["B1"][d] = set(st1.nlargest(FREQ_TOP).index)
        v2 = vol_f[d].fillna(0) + (vol_i[d].fillna(0) if d in vol_i.columns else 0)
        st2 = (v2 / s).dropna()
        tops["B2"][d] = set(st2.nlargest(FREQ_TOP).index)
    return days, tops


def main():
    rows = []
    for market in MARKETS:
        logger.info("[%s] 패널/일별 순위 구축...", market)
        panel = build_panel(market)
        days, tops = daily_rank_counts(market)

        for mi, ym in enumerate(panel["yms"]):
            # 월말 시점: 해당 월의 마지막 일별 거래일까지의 직전 20거래일
            month_days = [d for d in days if d[:7] == ym]
            if not month_days:
                continue
            t_idx = days.index(month_days[-1])
            if t_idx + 1 < FREQ_WINDOW:
                continue
            window = days[t_idx + 1 - FREQ_WINDOW: t_idx + 1]

            universe = panel["closes"][ym].dropna().index
            for strat in ["B1", "B2"]:
                counts = pd.Series(0, index=universe, dtype=int)
                for d in window:
                    hit = list(tops[strat][d] & set(universe))
                    counts.loc[hit] += 1
                port = list(counts.nlargest(PORT_N).index)
                for hold in HOLDS:
                    ym_out = ym_offset(panel["yms"], ym, hold)
                    if ym_out is None:
                        continue
                    mkt_ret = market_proxy_return(panel, ym, ym_out)
                    ret, n, dropped = returns_between(panel, port, ym, ym_out)
                    rand = random_benchmark(panel, ym, ym_out, n, mi)
                    rows.append({"시장": market, "전략": strat, "보유": hold,
                                 "진입월": ym, "종목수": n, "만기누락": dropped,
                                 "수익률": ret, "시장프록시": mkt_ret, "랜덤평균": rand,
                                 "최대진입일수": int(counts.max())})
            logger.info("[%s] %s", market, ym)

    df = pd.DataFrame(rows)
    df["초과_시장"] = df["수익률"] - df["시장프록시"]
    df["초과_랜덤"] = df["수익률"] - df["랜덤평균"]
    df["수익률_비용후"] = df["수익률"] - COST_PER_TRADE
    df["초과_랜덤_비용후"] = df["수익률_비용후"] - df["랜덤평균"]

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    def save(name, frame):
        with open(OUT_DIR / name, "w", encoding="utf-8-sig", newline="") as f:
            f.write("\n".join(HEADER) + "\n")
            frame.to_csv(f, index=False)

    save("backtest_freq_monthly.csv", df)
    summary = summarize(df)
    save("backtest_freq_summary.csv", summary)

    print("\n".join(HEADER))
    print(summary.to_string(index=False))
    # 표본이 적어 robustness()의 최소 표본(10) 미달 가능 — 상위3 제거만 수동 산출
    print()
    for (mkt, strat, hold), g in df.groupby(["시장", "전략", "보유"]):
        g = g[g["종목수"] > 0].dropna(subset=["수익률", "랜덤평균"])
        if g.empty:
            continue
        trimmed = g.sort_values("초과_랜덤", ascending=False).iloc[3:]["초과_랜덤"]
        print(f"{mkt} {strat} {hold}m: n={len(g)} 초과랜덤 {g['초과_랜덤'].mean()*100:+.2f}%p"
              f" | 상위3제거 {trimmed.mean()*100:+.2f}%p | 비용후 {g['초과_랜덤_비용후'].mean()*100:+.2f}%p")


if __name__ == "__main__":
    main()
