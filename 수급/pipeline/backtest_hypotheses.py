# -*- coding: utf-8 -*-
"""
수급 최종 가설 3종 백테스트 — 이후 수급 전략 탐색 영구 종료.

사전 예상 (결과와 달라도 그대로 보고):
  H1 약한 지지 / H2 반반 / H3 지지

H1 레짐 조건부: 그 달(t-1월말→t월말) 시장 프록시 수익률이 표본 10년 분포의
   하위 20%인 달에만 외인 강도 상위 20 진입, 6개월 보유.
   ※ 분위수는 전체 표본 분포 사용 — 지시 사양. 분포 자체에 표본 전체가 쓰이므로
     엄밀한 실시간 재현은 아님(인샘플 레짐 정의)을 명시한다.
   발생 횟수 15회 미만이면 '표본 부족' 딱지.

H2 지속성 비교:
   (i)  3개월 연속 외인 강도 양수 AND 3개월 누적 강도(합산 순매수량 ÷ t-3 상장주식수) 상위 20
   (ii) 그 달 강도 상위 20이되 직전 2개월 강도는 모두 음수 (스파이크)

H3 역신호: 개인 순매수 강도(그 달 개인 순매수량 ÷ 전월말 상장주식수) 상위 20.
   ★ 부호 해석: 이 전략의 초과수익이 랜덤 대비 '낮을수록'(음수일수록) 가설 지지.
     (개인이 몰리는 종목이 못 간다는 역신호 가설 — 수익이 높게 나오면 가설 기각)

공통: 진입 시점 t까지의 데이터만 사용(룩어헤드 차단, H1 분위수 예외는 위에 명시),
     랜덤 벤치마크(시드 고정 100회)·강건성(기간분할/상위3제거/비용후) 자동 적용.
     파라미터 튜닝 금지 — 기존 backtest.py 상수 재사용.
"""
import logging

import numpy as np
import pandas as pd

import store
from backtest import (HOLDS, MARKETS, STRENGTH_TOP, M_CUTOFF, OUT_DIR,
                      build_panel, ym_offset, returns_between,
                      market_proxy_return, random_benchmark,
                      summarize, robustness, COST_PER_TRADE)

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("backtest_hyp")
logger.setLevel(logging.INFO)

REGIME_QUANTILE = 0.20
MIN_REGIME_SAMPLES = 15

EXPECTATIONS = ("# 사전 예상: H1 약한 지지 / H2 반반 / H3 지지 "
                "(결과가 달라도 그대로 보고 — 튜닝 금지)")


def add_indiv_volume(panel, market):
    """패널에 개인 순매수 거래량 피벗 추가 (H3용)."""
    fl = store.read_flows()
    fl = fl[(fl["시장"] == market) & (fl["투자자"] == "개인")]
    m = fl[(fl["해상도"] == "M") & (fl["날짜"] <= M_CUTOFF)].copy()
    d = fl[(fl["해상도"] == "D") & (fl["날짜"] > M_CUTOFF)].copy()
    m["ym"] = m["날짜"].str[:7]
    d["ym"] = d["날짜"].str[:7]
    monthly = pd.concat([m, d], ignore_index=True)
    panel["flow_vol_indiv"] = monthly.pivot_table(
        index="코드", columns="ym", values="거래량", aggfunc="sum")
    return panel


def strength_series(panel, ym, vol_key="flow_vol_frgn"):
    """그 달 순매수량 ÷ 전월말 상장주식수 (기존 b와 동일 정의)."""
    prev = ym_offset(panel["yms"], ym, -1)
    if prev is None or ym not in panel[vol_key].columns:
        return None
    vol = panel[vol_key][ym]
    return (vol / panel["shares"][prev]).dropna()


def market_month_returns(panel):
    """월별 시장 프록시 수익률 시계열 (H1 레짐 정의용)."""
    out = {}
    for ym in panel["yms"]:
        prev = ym_offset(panel["yms"], ym, -1)
        if prev:
            out[ym] = market_proxy_return(panel, prev, ym)
    return pd.Series(out).dropna()


def pick_hypothesis_ports(panel, ym, regime_months):
    """시점 ym의 가설별 포트폴리오. 데이터는 전부 t 이하."""
    yms = panel["yms"]
    ports = {}

    s0 = strength_series(panel, ym)                     # t월 외인 강도
    s1 = strength_series(panel, ym_offset(yms, ym, -1)) if ym_offset(yms, ym, -1) else None
    s2 = strength_series(panel, ym_offset(yms, ym, -2)) if ym_offset(yms, ym, -2) else None
    if s0 is None:
        return {}

    universe = panel["closes"][ym].dropna().index
    s0 = s0.reindex(universe).dropna()

    # H1: 레짐 달에만 강도 상위 20
    ports["H1"] = list(s0.nlargest(STRENGTH_TOP).index) if ym in regime_months else []

    # H2(i): 3개월 연속 양수 AND 3개월 누적 강도 상위 20
    base3 = ym_offset(yms, ym, -3)
    if s1 is not None and s2 is not None and base3 is not None:
        vols = [panel["flow_vol_frgn"].get(m) for m in
                [ym, ym_offset(yms, ym, -1), ym_offset(yms, ym, -2)]]
        if all(v is not None for v in vols):
            cum = sum(v.reindex(universe).fillna(0) for v in vols)
            cum_strength = (cum / panel["shares"][base3].reindex(universe)).dropna()
            persistent = (s0 > 0) & (s1.reindex(universe) > 0) & (s2.reindex(universe) > 0)
            pool = cum_strength[persistent.reindex(cum_strength.index).fillna(False)]
            ports["H2i"] = list(pool.nlargest(STRENGTH_TOP).index)
        else:
            ports["H2i"] = []
        # H2(ii): 그 달 상위 20이되 직전 2개월은 음수 (스파이크)
        top_now = s0.nlargest(STRENGTH_TOP).index
        spike = [c for c in top_now
                 if (s1.get(c, np.nan) < 0) and (s2.get(c, np.nan) < 0)]
        ports["H2ii"] = spike
    else:
        ports["H2i"], ports["H2ii"] = [], []

    # H3: 개인 강도 상위 20 (★ 초과수익이 '낮을수록' 가설 지지)
    si = strength_series(panel, ym, "flow_vol_indiv")
    ports["H3"] = (list(si.reindex(universe).dropna().nlargest(STRENGTH_TOP).index)
                   if si is not None else [])
    return ports


def main():
    rows = []
    regime_counts = {}
    for market in MARKETS:
        logger.info("[%s] 패널 구축...", market)
        panel = add_indiv_volume(build_panel(market), market)
        mret = market_month_returns(panel)
        cut = mret.quantile(REGIME_QUANTILE)
        regime_months = set(mret[mret <= cut].index)
        regime_counts[market] = len(regime_months)
        logger.info("[%s] H1 레짐 달: %d회 (하위 20%% 컷 %.2f%%)",
                    market, len(regime_months), cut * 100)

        yms = [y for y in panel["yms"] if "2016-04" <= y <= "2026-06"]
        for mi, ym in enumerate(yms):
            ports = pick_hypothesis_ports(panel, ym, regime_months)
            if not ports:
                continue
            for hold in HOLDS:
                # H1은 6개월 전용 — 다른 보유기간은 건너뜀 (튜닝 아님, 사양)
                ym_out = ym_offset(panel["yms"], ym, hold)
                if ym_out is None:
                    continue
                mkt_ret = market_proxy_return(panel, ym, ym_out)
                for strat, codes in ports.items():
                    if strat == "H1" and hold != 6:
                        continue
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
    header = [EXPECTATIONS,
              f"# H1 레짐 발생: " + ", ".join(f"{m} {n}회" for m, n in regime_counts.items())
              + (" — 표본 부족(15회 미만)" if any(n < MIN_REGIME_SAMPLES for n in regime_counts.values()) else "")]

    def save(name, frame):
        path = OUT_DIR / name
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            f.write("\n".join(header) + "\n")
            frame.to_csv(f, index=False)

    save("backtest_hyp_monthly.csv", df)
    summary = summarize(df)
    save("backtest_hyp_summary.csv", summary)
    robust = robustness(df)
    save("backtest_hyp_robustness.csv", robust)

    # H3 유의성: 평균 초과_랜덤 < 0 인지 t통계 (낮을수록 가설 지지)
    print("\n".join(header))
    print(summary.to_string(index=False))
    print()
    print(robust.to_string(index=False))
    print()
    for (mkt, hold), g in df[(df["전략"] == "H3") & (df["종목수"] > 0)].groupby(["시장", "보유"]):
        x = g["초과_랜덤"].dropna()
        t = x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))
        print(f"H3 {mkt} {hold}m: 평균 초과_랜덤 {x.mean()*100:+.2f}%p, t={t:.2f}, n={len(x)}"
              f" → {'가설 지지(유의하게 낮음)' if t < -2 else '유의하지 않음' if abs(t) <= 2 else '가설 기각(오히려 높음)'}")


if __name__ == "__main__":
    main()
