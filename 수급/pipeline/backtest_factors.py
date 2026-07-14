# -*- coding: utf-8 -*-
"""
팩터 3종 단독 검증 — 수급과 동일 규율 (사전 고정, 튜닝 금지)

사전 예상 (결과가 달라도 그대로 보고):
  F1 밸류 약한 지지 / F2 모멘텀 지지(단 최악의 달 큼) / F3 사전 예상 없음(프록시 시험)

F1 밸류   : 매월 말 PBR 하위 20 (PBR > 0)
            ※ 관리종목 플래그는 저장소에 없어 제외 불가 — 한계로 명기
F2 모멘텀 : 직전 6개월 수익률(t-7월말→t-1월말) 상위 20 — 최근 1개월 제외 (표준 관례)
F3 퀄리티 : ROE 프록시 = PBR ÷ PER (= EPS/BPS 항등식), PER·PBR 모두 양수 한정, 상위 20

공통: 진입 t월말 종가, 보유 1/3/6개월, 균등 가중,
     벤치마크(시총가중 프록시 + 시드고정 랜덤 100회)·강건성 자동 적용 — 기존 엔진 재사용.
"""
import logging

import numpy as np
import pandas as pd

import store
from backtest import (HOLDS, MARKETS, OUT_DIR, build_panel, ym_offset,
                      returns_between, market_proxy_return, random_benchmark,
                      summarize, robustness, COST_PER_TRADE)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("backtest_factors")
logger.setLevel(logging.INFO)

FACTOR_TOP = 20
MOM_LOOKBACK = 7   # t-7월말 → t-1월말 (최근 1개월 제외)

EXPECT = ("# 사전 예상: F1 약한 지지 / F2 지지(최악의 달 큼) / F3 프록시 시험 — 튜닝 금지",
          "# 한계: 관리종목 플래그 미보유로 제외 불가, F3의 ROE는 PBR/PER 프록시임")


def add_valuation(panel, market):
    """월말 PBR/PER 피벗 추가."""
    sn = store.read_snapshots()
    sn = sn[sn["시장"] == market]
    me_dates = set()
    has_close = sn[sn["종가"].notna()].copy()
    has_close["ym"] = has_close["날짜"].str[:7]
    me_dates = set(has_close.groupby("ym")["날짜"].max().values)
    snap_me = sn[sn["날짜"].isin(me_dates)].copy()
    snap_me["ym"] = snap_me["날짜"].str[:7]
    panel["pbr"] = snap_me.pivot_table(index="코드", columns="ym", values="PBR")
    panel["per"] = snap_me.pivot_table(index="코드", columns="ym", values="PER")
    return panel


def pick_factor_ports(panel, ym):
    yms = panel["yms"]
    universe = panel["closes"][ym].dropna().index
    ports = {}

    # F1 밸류: PBR 하위 20 (PBR > 0)
    pbr = panel["pbr"][ym].reindex(universe) if ym in panel["pbr"].columns else pd.Series(dtype=float)
    pbr = pbr[pbr > 0]
    ports["F1"] = list(pbr.nsmallest(FACTOR_TOP).index)

    # F2 모멘텀: t-7월말 → t-1월말 수익률 상위 20
    m1, m7 = ym_offset(yms, ym, -1), ym_offset(yms, ym, -MOM_LOOKBACK)
    if m1 and m7:
        ret6 = (panel["closes"][m1] / panel["closes"][m7] - 1).reindex(universe).dropna()
        ports["F2"] = list(ret6.nlargest(FACTOR_TOP).index)
    else:
        ports["F2"] = []

    # F3 퀄리티 프록시: ROE ≈ PBR/PER (둘 다 양수), 상위 20
    per = panel["per"][ym].reindex(universe) if ym in panel["per"].columns else pd.Series(dtype=float)
    ok = (pbr.reindex(universe) > 0) & (per > 0)
    roe = (pbr.reindex(universe) / per)[ok].dropna()
    ports["F3"] = list(roe.nlargest(FACTOR_TOP).index)
    return ports


def main():
    rows = []
    for market in MARKETS:
        logger.info("[%s] 패널 구축...", market)
        panel = add_valuation(build_panel(market), market)
        yms = [y for y in panel["yms"] if "2016-11" <= y <= "2026-06"]  # 모멘텀 7개월 창 확보
        for mi, ym in enumerate(yms):
            ports = pick_factor_ports(panel, ym)
            for hold in HOLDS:
                ym_out = ym_offset(panel["yms"], ym, hold)
                if ym_out is None:
                    continue
                mkt_ret = market_proxy_return(panel, ym, ym_out)
                for strat, codes in ports.items():
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
            logger.info("[%s] %s", market, ym)

    df = pd.DataFrame(rows)
    df["초과_시장"] = df["수익률"] - df["시장프록시"]
    df["초과_랜덤"] = df["수익률"] - df["랜덤평균"]
    df["수익률_비용후"] = df["수익률"] - COST_PER_TRADE
    df["초과_랜덤_비용후"] = df["수익률_비용후"] - df["랜덤평균"]

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    def save(name, frame):
        with open(OUT_DIR / name, "w", encoding="utf-8-sig", newline="") as f:
            f.write("\n".join(EXPECT) + "\n")
            frame.to_csv(f, index=False)

    save("backtest_factor_monthly.csv", df)
    summary = summarize(df)
    save("backtest_factor_summary.csv", summary)
    robust = robustness(df)
    save("backtest_factor_robustness.csv", robust)
    print("\n".join(EXPECT))
    print(summary.to_string(index=False))
    print()
    print(robust.to_string(index=False))


if __name__ == "__main__":
    main()
