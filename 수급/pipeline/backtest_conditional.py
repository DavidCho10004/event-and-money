# -*- coding: utf-8 -*-
"""
조건부 백테스트 1라운드 — "수급 이상 종목 중 무엇을 골라야 하나" (C1~C3, 이 판으로 A군 1~3 종료)

베이스 (고정): 월말 외인 강도(그 달 순매수량 ÷ 전월말 상장주식수) 상위 30
    AND 지속형(t-2·t-1·t 3개월 연속 외인 순매수량 양수) — 두 조건의 교집합.
    베이스 단독 성적을 모든 비교의 기준선으로 먼저 산출.
각 가설은 베이스를 조건 하나로 이등분 — 충족군 vs 미충족군 (d/d′ 방식 대조군 내장).
다중 교차 금지: 조건은 한 번에 하나만.

C1 밸류   : PBR 하위 50% vs 상위 50% — 같은 달 시장 내 상대 기준
            (그 달 PBR>0 전 종목의 중앙값, 절대값 아님 — 시기별 밸류에이션 수준 중립화).
            PBR 결측·0 이하는 분할에서 제외(베이스 전체에는 포함) — 제외 수 헤더 명기.
C2 위치   : 52주 고점 대비 -20% 이내(고점 근처) vs -20% 초과(낙폭 구간).
            ★ 근사 명기: 52주 고점은 일별 종가로 계산하되, 일별 커버(2025-07~) 이전
            구간은 월말 종가 12개월 최고로 근사. 2025-07 이후 진입도 창의 과거부는
            월말 근사와 혼합 — 순수 일별 52주 고점은 2026-07 이후 진입에나 가능.
            12개월 창 미달(2016-11 이전 진입)은 분할 없음.
C3 변동성 : 직전 12개월 월수익률 표준편차 하위 50% vs 상위 50% — 같은 달 시장 내
            상대 기준(12개 월수익률이 전부 있는 종목의 중앙값). 창 미달 종목은
            분할에서 제외. 12개월 창 확보 전(2017-01 이전 진입)은 분할 없음.

보유: 6개월 고정(기존 검증에서 유일하게 신호가 보이던 지평), 1개월은 참고 병기.

사전 예상 (고정 — 결과가 달라도 그대로 보고):
  C1 반반       — 밸류 트랩 필터 논리는 성립하나 F1 단독이 약했음
  C2 고점 근처 우위 — d′ 방향
  C3 저변동 우위   — 약하게 (문헌 방향, 한국 데이터 미검증)

판정: 기존 강건성 자동(기간분할 / 상위3제거 / 비용후) + 충족−미충족 월별 격차 t.
     분할군 종목 수 월평균 10 미만이면 '표본 희박' 딱지. 튜닝 금지, 결과 그대로.
출력: data/processed/backtest/backtest_cond_{monthly,summary,robustness}.csv
"""
import logging
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # cp949 콘솔 대비

import store
from backtest import (MARKETS, OUT_DIR, M_CUTOFF, COST_PER_TRADE,
                      build_panel, ym_offset, returns_between,
                      market_proxy_return, random_benchmark, summarize, robustness)
from backtest_hypotheses import strength_series

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backtest_conditional")
logger.setLevel(logging.INFO)

BASE_TOP = 30          # 베이스: 외인 강도 상위 N
PERSIST_MONTHS = 3     # 베이스: 연속 순매수 개월 수
HOLDS_C = [6, 1]       # 6개월 고정 + 1개월 참고
DRAWDOWN_NEAR = -0.20  # C2: 고점 근처 판정 임계
VOL_WINDOW = 12        # C3: 월수익률 창 (개월)
SPARSE_MIN = 10        # 분할군 월평균 종목 수 미만이면 '표본 희박'

STRATS = ["베이스", "C1_저PBR", "C1_고PBR", "C2_고점근처", "C2_낙폭", "C3_저변동", "C3_고변동"]
PAIRS = {"C1(저PBR−고PBR)": ("C1_저PBR", "C1_고PBR"),
         "C2(고점근처−낙폭)": ("C2_고점근처", "C2_낙폭"),
         "C3(저변동−고변동)": ("C3_저변동", "C3_고변동")}

EXPECT = ("# 사전 예상(고정): C1 반반 / C2 고점근처 우위 / C3 저변동 약한 우위 "
          "— 결과가 달라도 그대로 보고, 튜닝 금지")
APPROX = ("# C2 52주 고점 근사: 일별 종가(2025-07~) + 그 이전 구간은 월말 종가 "
          "12개월 최고 혼합 — 순수 일별 고점 아님")


def add_extras(panel, market: str):
    """패널에 PBR 피벗·일별 종가·월말 대표일을 추가 (C1·C2용)."""
    sn = store.read_snapshots()
    sn = sn[sn["시장"] == market]
    has_close = sn[sn["종가"].notna()].copy()
    has_close["ym"] = has_close["날짜"].str[:7]
    month_end = has_close.groupby("ym")["날짜"].max()

    snap_me = sn[sn["날짜"].isin(month_end.values)].copy()
    snap_me["ym"] = snap_me["날짜"].str[:7]
    panel["pbr"] = snap_me.pivot_table(index="코드", columns="ym", values="PBR")

    d = has_close[has_close["날짜"] > M_CUTOFF]
    panel["daily_closes"] = d.pivot_table(index="코드", columns="날짜", values="종가")
    panel["month_end"] = month_end
    return panel


def pick_base(panel, ym: str) -> list:
    """외인 강도 상위 30 ∩ 3개월 연속 순매수 (전부 t 이하 데이터)."""
    yms = panel["yms"]
    prevs = [ym_offset(yms, ym, -k) for k in range(1, PERSIST_MONTHS)]
    if any(p is None for p in prevs):
        return []
    s0 = strength_series(panel, ym)
    laggs = [strength_series(panel, p) for p in prevs]
    if s0 is None or any(s is None for s in laggs):
        return []
    universe = panel["closes"][ym].dropna().index
    top = s0.reindex(universe).dropna().nlargest(BASE_TOP)
    return [c for c in top.index
            if top[c] > 0 and all(s.get(c, np.nan) > 0 for s in laggs)]


def split_c1(panel, ym: str, base: list):
    """PBR 하위/상위 50% (시장 내 그 달 PBR>0 중앙값 기준). (하위, 상위, 제외수)."""
    if ym not in panel["pbr"].columns:
        return [], [], len(base)
    pbr_mkt = panel["pbr"][ym]
    valid = pbr_mkt[pbr_mkt > 0]
    if valid.empty:
        return [], [], len(base)
    med = valid.median()
    pb = pbr_mkt.reindex(base)
    low = [c for c in base if pb.get(c, np.nan) > 0 and pb[c] <= med]
    high = [c for c in base if pb.get(c, np.nan) > 0 and pb[c] > med]
    return low, high, len(base) - len(low) - len(high)


def high_52w(panel, ym: str, codes: list) -> pd.Series:
    """52주 고점 (혼합 근사 — 파일 머리 참조). 창은 월말 12개 (현재월 포함)."""
    yms = panel["yms"]
    i = yms.index(ym)
    window = yms[i - 11: i + 1]
    monthly_part = [m for m in window if m <= M_CUTOFF[:7]]
    daily_part = [m for m in window if m > M_CUTOFF[:7]]

    highs = []
    if monthly_part:
        highs.append(panel["closes"][monthly_part].reindex(codes).max(axis=1))
    if daily_part:
        end_date = panel["month_end"].get(ym)
        dc = panel["daily_closes"]
        cols = [d for d in dc.columns if d[:7] in set(daily_part) and d <= end_date]
        if cols:
            highs.append(dc[cols].reindex(codes).max(axis=1))
    return pd.concat(highs, axis=1).max(axis=1) if highs else pd.Series(np.nan, index=codes)


def split_c2(panel, ym: str, base: list):
    """고점 대비 -20% 이내 vs 초과. 창 미달(월말 12개 미만)이면 None."""
    if panel["yms"].index(ym) < 11:
        return None
    high = high_52w(panel, ym, base)
    dd = panel["closes"][ym].reindex(base) / high - 1
    near = [c for c in base if dd.get(c, np.nan) >= DRAWDOWN_NEAR]
    deep = [c for c in base if dd.get(c, np.nan) < DRAWDOWN_NEAR]
    return near, deep


def split_c3(panel, ym: str, base: list):
    """직전 12개월 월수익률 표준편차 하위/상위 50% (시장 내 중앙값 기준).
    창 미달이면 None, 12개 수익률 미완비 종목은 분할 제외. (하위, 상위, 제외수)."""
    yms = panel["yms"]
    i = yms.index(ym)
    if i < VOL_WINDOW:
        return None
    window = yms[i - VOL_WINDOW: i + 1]                    # 월말 13개 → 수익률 12개
    px = panel["closes"][window]
    rets = pd.DataFrame(px.iloc[:, 1:].values / px.iloc[:, :-1].values - 1,
                        index=px.index)
    full = rets.notna().all(axis=1)
    vol = rets.std(axis=1, ddof=1)[full]
    universe = panel["closes"][ym].dropna().index
    vol = vol.reindex(universe).dropna()
    if vol.empty:
        return None
    med = vol.median()
    lowv = [c for c in base if c in vol.index and vol[c] <= med]
    highv = [c for c in base if c in vol.index and vol[c] > med]
    return lowv, highv, len(base) - len(lowv) - len(highv)


def run() -> tuple:
    rows = []
    excl_notes = []
    for market in MARKETS:
        logger.info("[%s] 패널 구축...", market)
        panel = add_extras(build_panel(market), market)
        yms = [y for y in panel["yms"] if "2016-04" <= y <= "2026-06"]
        excl_c1, excl_c3, base_sizes = [], [], []

        for mi, ym in enumerate(yms):
            base = pick_base(panel, ym)
            base_sizes.append(len(base))

            groups = {s: [] for s in STRATS}
            groups["베이스"] = base
            if base:
                low, high, ex1 = split_c1(panel, ym, base)
                groups["C1_저PBR"], groups["C1_고PBR"] = low, high
                excl_c1.append(ex1)
                c2 = split_c2(panel, ym, base)
                if c2 is not None:
                    groups["C2_고점근처"], groups["C2_낙폭"] = c2
                c3 = split_c3(panel, ym, base)
                if c3 is not None:
                    groups["C3_저변동"], groups["C3_고변동"], ex3 = c3
                    excl_c3.append(ex3)

            for hold in HOLDS_C:
                ym_out = ym_offset(panel["yms"], ym, hold)
                if ym_out is None:
                    continue
                mkt_ret = market_proxy_return(panel, ym, ym_out)
                for strat in STRATS:
                    codes = groups[strat]
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
            logger.info("[%s] %s 베이스=%d", market, ym, len(base))

        excl_notes.append(
            f"# {market}: 베이스 월평균 {np.mean(base_sizes):.1f}종목, "
            f"C1 분할제외(PBR 결측·0이하) 월평균 {np.mean(excl_c1):.1f}, "
            f"C3 분할제외(12개월 창 미완비) 월평균 {np.mean(excl_c3):.1f}")

    df = pd.DataFrame(rows)
    df["초과_시장"] = df["수익률"] - df["시장프록시"]
    df["초과_랜덤"] = df["수익률"] - df["랜덤평균"]
    df["수익률_비용후"] = df["수익률"] - COST_PER_TRADE
    df["초과_랜덤_비용후"] = df["수익률_비용후"] - df["랜덤평균"]
    return df, excl_notes


def gap_tests(df: pd.DataFrame) -> list:
    """충족−미충족 월별 격차 t (같은 달에 양쪽 다 존재하는 월만, 짝지은 비교)."""
    lines = []
    for label, (a, b) in PAIRS.items():
        for market in MARKETS:
            for hold in HOLDS_C:
                sub = df[(df["시장"] == market) & (df["보유"] == hold) & (df["종목수"] > 0)]
                ga = sub[sub["전략"] == a].set_index("진입월")["초과_랜덤"]
                gb = sub[sub["전략"] == b].set_index("진입월")["초과_랜덤"]
                gap = (ga - gb).dropna()
                if len(gap) < 10:
                    lines.append(f"{label} {market} {hold}m: 짝지은 표본 {len(gap)}개 — 판정 불가")
                    continue
                t = gap.mean() / (gap.std(ddof=1) / np.sqrt(len(gap)))
                verdict = ("충족군 유의 우위" if t > 2 else
                           "미충족군 유의 우위" if t < -2 else "유의차 없음")
                lines.append(f"{label} {market} {hold}m: 격차 {gap.mean()*100:+.2f}%p, "
                             f"t={t:.2f}, n={len(gap)} → {verdict}")
    return lines


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df, excl_notes = run()

    summary = summarize(df)
    summary["표본희박"] = summary["평균종목수"] < SPARSE_MIN
    summary["전략"] = pd.Categorical(summary["전략"], categories=STRATS, ordered=True)
    summary = summary.sort_values(["시장", "보유개월", "전략"])
    robust = robustness(df)
    gaps = gap_tests(df)

    header = [EXPECT, APPROX] + excl_notes

    def save(name, frame, extra=()):
        with open(OUT_DIR / name, "w", encoding="utf-8-sig", newline="") as f:
            f.write("\n".join(header) + "\n")
            for line in extra:
                f.write(f"# {line}\n")
            frame.to_csv(f, index=False)

    save("backtest_cond_monthly.csv", df)
    save("backtest_cond_summary.csv", summary)
    save("backtest_cond_robustness.csv", robust, extra=gaps)

    print("\n".join(header))
    print()
    print(summary.to_string(index=False))
    print()
    print(robust.to_string(index=False))
    print()
    print("\n".join(gaps))


if __name__ == "__main__":
    main()
