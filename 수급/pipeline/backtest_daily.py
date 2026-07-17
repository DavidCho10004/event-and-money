# -*- coding: utf-8 -*-
"""
단기 가설 검증 — 일별 외인 수급 → 익일 이후 수익률 (일 단위 검증은 이 한 판으로 종료)

신호: 매 거래일 t, 외인 순매수 강도(그날 순매수량 ÷ 그날 상장주식수) 상위 20.
진입: t+1 시가 고정. 보유 기간별 누적 초과수익을 1/2/3/5/10 거래일 종가 시점에서
     측정한 감쇠 곡선 산출 (조합 탐색 아님 — 동일 포지션의 시간 단면).
보조: ③의 사전 예상 확인용으로 1일 보유의 종가(t)→종가(t+1) 변형도 병기.
벤치마크: 동일일 랜덤 20종목 (같은 진입·청산 방식, 시드 고정 100회 평균).

사전 예상 (고정):
  종가→종가 약한 양(+) 가능 / 시가→종가 ≈ 0 (갭이 신호 선취) / 비용 후 전 조합 음수
판정 기준 (고정):
  곡선이 초반 양수 → 0 수렴: '단명 신호' / 전 구간 0 근처: '신호 없음'
  며칠 뒤에야 커지는 형태: 해석하지 않고 '우연 의심'으로 기록
한계 (고정): 표본 ~240거래일 — 관측치는 많으나 기간이 1년(단일 장세)뿐.
튜닝 금지. 시가=0(그날 거래 없음)은 결측 처리.
"""
import logging

import numpy as np
import pandas as pd

import store
from backtest import OUT_DIR, MARKETS, STRENGTH_TOP, RANDOM_DRAWS, RANDOM_SEED, COST_PER_TRADE

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("backtest_daily")
logger.setLevel(logging.INFO)

HORIZONS = [1, 2, 3, 5, 10]   # 진입(t+1 시가) 후 k거래일 종가 시점

HEADER = (
    "# 사전 예상: 종가→종가 약한 양(+) 가능 / 시가→종가 ≈ 0 (갭 선취) / 비용후 전 조합 음수",
    "# 판정 기준(고정): 초반 양수→0 수렴 = 단명 신호 / 전 구간 0 근처 = 신호 없음 / "
    "며칠 뒤 증가 = 해석 금지, 우연 의심",
    "# 한계: 표본 ~240거래일이지만 기간 1년(단일 장세) — 월 검증과 표본 성격이 다름",
)


def build_daily_panel(market: str):
    sn = store.read_snapshots()
    sn = sn[(sn["시장"] == market)]
    daily = sn[sn["외인지분율"].notna() | sn["시가"].notna() | sn["종가"].notna()]
    closes = daily.pivot_table(index="코드", columns="날짜", values="종가")
    opens = daily.pivot_table(index="코드", columns="날짜", values="시가")
    opens = opens.where(opens > 0)          # 시가 0 = 그날 거래 없음 → 결측
    shares = daily.pivot_table(index="코드", columns="날짜", values="상장주식수")

    fl = store.read_flows()
    fl = fl[(fl["시장"] == market) & (fl["해상도"] == "D") & (fl["투자자"] == "외국인")]
    vol = fl.pivot_table(index="코드", columns="날짜", values="거래량", aggfunc="sum")

    days = sorted(set(closes.columns) & set(vol.columns))
    return {"days": days, "closes": closes, "opens": opens, "shares": shares, "vol": vol}


def main():
    rows = []
    for market in MARKETS:
        logger.info("[%s] 일별 패널 구축...", market)
        p = build_daily_panel(market)
        days = p["days"]
        for i in range(len(days) - max(HORIZONS) - 1):
            t, e = days[i], days[i + 1]           # 신호일 t, 진입일 e = t+1
            strength = (p["vol"][t] / p["shares"][t]).dropna()
            top = strength.nlargest(STRENGTH_TOP).index

            open_e = p["opens"][e]
            close_t = p["closes"][t]
            rec = {"시장": market, "신호일": t, "진입일": e,
                   "종목수": len(top)}

            # 보조: 1일 보유 종가→종가 / 시가→종가 (③ 예상 확인용)
            c1 = p["closes"][days[i + 1]]
            rec["cc1"] = (c1.reindex(top) / close_t.reindex(top) - 1).mean()
            rec["oc1"] = (c1.reindex(top) / open_e.reindex(top) - 1).mean()

            # 감쇠 곡선: t+1 시가 진입 → k거래일 종가
            rng = np.random.default_rng(RANDOM_SEED + i)
            for k in HORIZONS:
                exit_day = days[i + k]
                ret = (p["closes"][exit_day].reindex(top) / open_e.reindex(top) - 1)
                rec[f"r{k}"] = ret.dropna().mean()
                # 랜덤: 같은 진입·청산 방식, 유효 종목 풀에서 20개 × 100회
                pool_ret = (p["closes"][exit_day] / open_e - 1).dropna()
                if len(pool_ret) >= STRENGTH_TOP:
                    draws = [pool_ret.iloc[rng.choice(len(pool_ret), STRENGTH_TOP, replace=False)].mean()
                             for _ in range(RANDOM_DRAWS)]
                    rec[f"rand{k}"] = float(np.mean(draws))
                else:
                    rec[f"rand{k}"] = np.nan
            rows.append(rec)
        logger.info("[%s] %d일 처리", market, len(rows))

    df = pd.DataFrame(rows)
    for k in HORIZONS:
        df[f"ex{k}"] = df[f"r{k}"] - df[f"rand{k}"]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "backtest_daily.csv", "w", encoding="utf-8-sig", newline="") as f:
        f.write("\n".join(HEADER) + "\n")
        df.to_csv(f, index=False)

    print("\n".join(HEADER))
    summary = []
    for market, g in df.groupby("시장"):
        # 보조 변형 (1일 보유): 랜덤은 시가 진입 랜덤(rand1)과 비교 — 종가→종가는 참고치
        for label, col in [("종가→종가(1일)", "cc1"), ("시가→종가(1일)", "oc1")]:
            x = (g[col] - g["rand1"]).dropna()
            t_stat = x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))
            summary.append({"시장": market, "구간": label, "일평균초과%": round(x.mean() * 100, 3),
                            "t": round(t_stat, 2), "승률%": round((x > 0).mean() * 100, 1),
                            "비용후%": round((x.mean() - COST_PER_TRADE) * 100, 3), "n": len(x)})
        for k in HORIZONS:
            x = g[f"ex{k}"].dropna()
            t_stat = x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))
            summary.append({"시장": market, "구간": f"시가진입 +{k}일", "일평균초과%": round(x.mean() * 100, 3),
                            "t": round(t_stat, 2), "승률%": round((x > 0).mean() * 100, 1),
                            "비용후%": round((x.mean() - COST_PER_TRADE) * 100, 3), "n": len(x)})
    sdf = pd.DataFrame(summary)
    with open(OUT_DIR / "backtest_daily_summary.csv", "w", encoding="utf-8-sig", newline="") as f:
        f.write("\n".join(HEADER) + "\n")
        sdf.to_csv(f, index=False)
    print(sdf.to_string(index=False))


if __name__ == "__main__":
    main()
