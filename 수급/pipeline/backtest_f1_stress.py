# -*- coding: utf-8 -*-
"""
F1(저PBR) 실전화 스트레스 테스트 — 성과를 깎는 방향의 변경만 허용.

① 상장폐지 최악 가정: 만기 종가 누락 포지션을 전부 -100%로 계산 (병기)
② 유동성 하한: 진입 시점 시가총액 하위 LIQ_MCAP_PCTL(20%) 제외 후 저PBR 20 선정
   ※ 원 지시의 "일 거래량 N% 이내" 논리는 종목별 총거래대금 시계열이 저장소에
     없어 직접 적용 불가 → 시총 하위 컷을 프록시로 사용 (성과 상향 튜닝 아님을
     위해 컷은 20% 고정, 결과가 나빠져도 유지)
③ 비용 상향: 소형주 현실 반영 왕복 1.0% 버전 병기 (기존 0.3% 대비)
④ 최악 조합 = ②유동성 컷 + ①-100% 가정 + ③1.0% 비용 — 이것이 최종 판정.

랜덤 벤치마크는 기존 방식(만기 누락 제외) 그대로 → 전략에만 불리한 비대칭 비교
(보수적 방향이므로 허용).
"""
import logging

import numpy as np
import pandas as pd

import store
from backtest import (MARKETS, OUT_DIR, build_panel, ym_offset,
                      market_proxy_return, random_benchmark)
from backtest_factors import add_valuation, FACTOR_TOP

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("f1_stress")
logger.setLevel(logging.INFO)

HOLD = 6
LIQ_MCAP_PCTL = 0.20      # ② 진입 시점 시총 하위 20% 제외
COST_BASE = 0.003
COST_HIGH = 0.010         # ③ 왕복 1.0%

HEADER = ("# F1 스트레스 테스트 — 성과를 깎는 방향만 허용 (상향 튜닝 금지)",
          "# ①만기누락=-100% ②시총 하위 20% 제외(거래대금 미보유로 프록시) ③왕복 1.0%",
          "# 랜덤 벤치마크는 만기누락 제외 방식 유지 → 전략에만 불리한 보수적 비교")


def rets_with_worst(panel, codes, ym_in, ym_out):
    """(누락제외 평균, 누락=-100% 평균, 종목수, 누락수)."""
    c_in = panel["closes"][ym_in].reindex(codes)
    c_out = panel["closes"][ym_out].reindex(codes)
    ret = c_out / c_in - 1
    valid = ret.dropna()
    n_miss = len(codes) - len(valid)
    drop_mean = valid.mean() if len(valid) else np.nan
    worst_mean = (valid.sum() + (-1.0) * n_miss) / len(codes) if len(codes) else np.nan
    return drop_mean, worst_mean, len(codes), n_miss


def main():
    rows = []
    for market in MARKETS:
        panel = add_valuation(build_panel(market), market)
        yms = [y for y in panel["yms"] if "2016-04" <= y <= "2026-06"]
        for mi, ym in enumerate(yms):
            ym_out = ym_offset(panel["yms"], ym, HOLD)
            if ym_out is None:
                continue
            universe = panel["closes"][ym].dropna().index
            pbr = panel["pbr"][ym].reindex(universe) if ym in panel["pbr"].columns else pd.Series(dtype=float)
            pbr = pbr[pbr > 0]
            cap = panel["cap"][ym].reindex(universe)
            liq_floor = cap.quantile(LIQ_MCAP_PCTL)

            ports = {
                "F1": list(pbr.nsmallest(FACTOR_TOP).index),
                "F1L": list(pbr[cap.reindex(pbr.index) >= liq_floor]
                            .nsmallest(FACTOR_TOP).index),
            }
            mkt_ret = market_proxy_return(panel, ym, ym_out)
            for strat, codes in ports.items():
                if not codes:
                    continue
                drop_r, worst_r, n, miss = rets_with_worst(panel, codes, ym, ym_out)
                rand = random_benchmark(panel, ym, ym_out, n, mi)
                rows.append({"시장": market, "전략": strat, "진입월": ym,
                             "종목수": n, "만기누락": miss,
                             "수익률": drop_r, "수익률_최악": worst_r,
                             "시장프록시": mkt_ret, "랜덤평균": rand})
            logger.info("[%s] %s", market, ym)

    df = pd.DataFrame(rows)
    # 변형별 초과수익 (랜덤 대비)
    df["기본"] = df["수익률"] - df["랜덤평균"]
    df["①최악가정"] = df["수익률_최악"] - df["랜덤평균"]
    df["③비용1%"] = df["수익률"] - COST_HIGH - df["랜덤평균"]
    df["④최악조합"] = df["수익률_최악"] - COST_HIGH - df["랜덤평균"]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "backtest_f1_stress.csv", "w", encoding="utf-8-sig", newline="") as f:
        f.write("\n".join(HEADER) + "\n")
        df.to_csv(f, index=False)

    print("\n".join(HEADER))
    print(f"\n===== 보유 {HOLD}개월, 초과수익(랜덤 대비 %p) =====")
    summary = []
    for (mkt, strat), g in df.groupby(["시장", "전략"]):
        g = g.dropna(subset=["수익률", "랜덤평균"])
        for var in ["기본", "①최악가정", "③비용1%", "④최악조합"]:
            x = g[var] if strat == "F1L" or var in ("기본", "①최악가정", "③비용1%") else g[var]
            first = g[g["진입월"] <= "2020-12"][var].mean()
            second = g[g["진입월"] >= "2021-01"][var].mean()
            trimmed = g.sort_values(var, ascending=False).iloc[3:][var].mean()
            survive = first > 0 and second > 0 and trimmed > 0
            summary.append({
                "시장": mkt, "전략": strat, "변형": var,
                "평균%p": round(g[var].mean() * 100, 2),
                "전반%p": round(first * 100, 2), "후반%p": round(second * 100, 2),
                "상위3제거%p": round(trimmed * 100, 2),
                "생존": survive,
                "만기누락합": int(g["만기누락"].sum()),
            })
    sdf = pd.DataFrame(summary)
    with open(OUT_DIR / "backtest_f1_stress_summary.csv", "w", encoding="utf-8-sig", newline="") as f:
        f.write("\n".join(HEADER) + "\n")
        sdf.to_csv(f, index=False)
    print(sdf.to_string(index=False))

    final = sdf[(sdf["전략"] == "F1L") & (sdf["변형"] == "④최악조합")]
    print("\n===== ④ 최종 판정 (유동성 컷 + 누락 -100% + 비용 1.0%) =====")
    for r in final.itertuples():
        print(f"{r.시장}: 평균 {r.평균 if hasattr(r,'평균') else ''}{r._4:+.2f}%p, "
              f"전반 {r._5:+.2f} / 후반 {r._6:+.2f} / 상위3제거 {r._7:+.2f} → "
              f"{'생존 ⭕' if r.생존 else '탈락 ✗'}")


if __name__ == "__main__":
    main()
