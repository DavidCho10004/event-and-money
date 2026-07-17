# -*- coding: utf-8 -*-
"""
이벤트×수급 2단계 확인 검증 (월별 사건 기반 — 이 한 판으로 종료)

E1 (H3의 사건 조건부 확장): 사건 월 개인 순매수 강도 상위 30의 이후 1/3/6개월
   수익률이 랜덤 대비 낮은가 — 저조폭이 평상시 H3(-1.5~-3.6%p)보다 큰가.
   사전 예상: 지지 (위기 국면 개인 매수는 물타기·역추세 성격 → 평상시보다 악화)
E2 (대칭 대조군): 같은 사건 월 외인 강도 상위 30의 이후 성과.
   사전 예상: 유의미하지 않음 (기존 75개 결과와 정합)

사건: events.json 중 월별 수급 커버(2016-01~) 사건, '해외 기업 — 국내 직접 연관
   낮음' 표기 건 제외. 실제 사용 사건 수 출력에 명기.
출력: 요약 + 강건성(기존 자동) + 사건별 낱장 표 (1~2개 사건이 결과를 끌고
   가는지 눈으로 확인용). 튜닝 금지. E1은 수익이 '낮을수록' 가설 지지.
"""
import json
import logging

import numpy as np
import pandas as pd

import store
from backtest import (MARKETS, OUT_DIR, build_panel, ym_offset, returns_between,
                      market_proxy_return, random_benchmark, summarize, robustness,
                      COST_PER_TRADE)
from backtest_hypotheses import add_indiv_volume

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("backtest_event")
logger.setLevel(logging.INFO)

TOP_N = 30
HOLDS = [1, 3, 6]
FOREIGN_MICRO_KEYWORDS = ["스타벅스", "테슬라", "애플", "보잉", "디즈니"]

HEADER = (
    "# 사전 예상: E1 지지(평상시 H3보다 악화) / E2 유의미하지 않음 — 달라도 그대로 보고, 튜닝 금지",
    "# E1은 수익이 '낮을수록' 가설 지지 (H3 부호 해석과 동일)",
)


def load_event_months():
    root = store.BASE_DIR.parent
    events = json.loads((root / "data" / "events.json").read_text(encoding="utf-8"))
    used, excluded = [], []
    for e in events:
        dt = e["event_date"]
        if dt < "2016-01-01":
            continue
        if e["scale"] == "micro" and any(k in e["name_ko"] for k in FOREIGN_MICRO_KEYWORDS):
            excluded.append(f'{e["id"]} {e["name_ko"]}')
            continue
        used.append({"id": e["id"], "name": e["name_ko"], "ym": dt[:7], "date": dt})
    return used, excluded


def main():
    used, excluded = load_event_months()
    rows = []
    for market in MARKETS:
        logger.info("[%s] 패널 구축...", market)
        panel = add_indiv_volume(build_panel(market), market)
        yms = panel["yms"]
        for ei, ev in enumerate(used):
            ym = ev["ym"]
            prev = ym_offset(yms, ym, -1)
            if ym not in yms or prev is None:
                continue
            universe = panel["closes"][ym].dropna().index
            shares_prev = panel["shares"][prev].reindex(universe)
            ports = {}
            for strat, vol_key in [("E1", "flow_vol_indiv"), ("E2", "flow_vol_frgn")]:
                if ym not in panel[vol_key].columns:
                    ports[strat] = []
                    continue
                strength = (panel[vol_key][ym].reindex(universe) / shares_prev).dropna()
                ports[strat] = list(strength.nlargest(TOP_N).index)
            for hold in HOLDS:
                ym_out = ym_offset(yms, ym, hold)
                if ym_out is None:
                    continue
                mkt_ret = market_proxy_return(panel, ym, ym_out)
                for strat, codes in ports.items():
                    if not codes:
                        continue
                    ret, n, dropped = returns_between(panel, codes, ym, ym_out)
                    rand = random_benchmark(panel, ym, ym_out, n, ei)
                    rows.append({"시장": market, "전략": strat, "보유": hold,
                                 "진입월": ym, "사건ID": ev["id"], "사건": ev["name"],
                                 "종목수": n, "만기누락": dropped,
                                 "수익률": ret, "시장프록시": mkt_ret, "랜덤평균": rand})
            logger.info("[%s] %s %s", market, ev["id"], ym)

    df = pd.DataFrame(rows)
    df["초과_시장"] = df["수익률"] - df["시장프록시"]
    df["초과_랜덤"] = df["수익률"] - df["랜덤평균"]
    df["수익률_비용후"] = df["수익률"] - COST_PER_TRADE
    df["초과_랜덤_비용후"] = df["수익률_비용후"] - df["랜덤평균"]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    header = list(HEADER) + [
        f"# 사용 사건: {len(used)}건 (2016-01 이후), 제외: {len(excluded)}건 — " + "; ".join(excluded)]

    def save(name, frame):
        with open(OUT_DIR / name, "w", encoding="utf-8-sig", newline="") as f:
            f.write("\n".join(header) + "\n")
            frame.to_csv(f, index=False)

    save("backtest_event_monthly.csv", df)
    summary = summarize(df.drop(columns=["사건ID", "사건"]))
    save("backtest_event_summary.csv", summary)
    robust = robustness(df)
    save("backtest_event_robustness.csv", robust)

    print("\n".join(header))
    print()
    print(summary.to_string(index=False))
    print()
    print(robust.to_string(index=False))
    # 유의성 (E1: 낮을수록 지지)
    print()
    for (mkt, strat, hold), g in df.groupby(["시장", "전략", "보유"]):
        x = g["초과_랜덤"].dropna()
        if len(x) < 5:
            continue
        t = x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))
        print(f"{strat} {mkt} {hold}m: 초과_랜덤 {x.mean()*100:+.2f}%p, t={t:.2f}, n={len(x)}")
    # 사건별 낱장 (6m, E1) — 특정 사건이 끌고 가는지 확인용
    print("\n===== 사건별 낱장 (E1, 6개월, 초과_랜덤 %p) =====")
    piv = (df[(df["전략"] == "E1") & (df["보유"] == 6)]
           .pivot_table(index=["사건ID", "사건", "진입월"], columns="시장", values="초과_랜덤"))
    print((piv * 100).round(1).to_string())


if __name__ == "__main__":
    main()
