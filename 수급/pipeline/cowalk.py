# -*- coding: utf-8 -*-
"""
수급동행 진단 — 주간 외인 수급 강도 vs 주가 수익률의 종목별 상관 (기술 통계, 전략화 금지)

목적: 종목 상세의 "수급동행 배지" 채우기. 이것은 **검증이 아니라 진단 도구**다.
"유사율 상위 종목 매수" 같은 전략화 금지 — A 검증(익일 갭 선취)에서 이미
공개 수급 정보의 실행 우위가 없음이 확인됐다.

계산 (전 종목, 코스피+코스닥):
  - 일별 flows(D행)를 ISO 주 단위 합산 → 주간 외인 순매수 강도
    = 주간 순매수 주식수 ÷ 그 주 마지막 거래일 상장주식수 (주중 변동 미보정 근사)
  - 주간 주가 수익률 = 그 주 마지막 종가 ÷ 직전 주 마지막 종가 − 1
    (직전 주 종가 결측 시 NaN — 보간·추정 금지)
  - 최근 52주 롤링 창. 종목별 Pearson 상관을 시차 4개로:
    동행(같은 주) / 선행 1·2·4주 (수급이 앞섬: corr(강도_t, 수익률_{t+k}))
  - 동행 유효 관측 40주 미만 → '관측 부족' (배지 없음). 분산 0(순매수 전무 등)도 동일 처리.

배지 등급: 동행 상관의 시장별 3분위 — 상(상위 1/3)/중/하. 관측 부족은 등급 없음.
스크리너 카드에는 표시하지 않는다 (상세 페이지 전용 — 카드 과밀 방지).

사전 예상 (고정 — 결과가 달라도 그대로 보고):
  동행 상관 분포는 양(+)으로 치우침 / 선행 상관 분포는 0 중심
  (A 검증의 "신호는 동행, 선행분은 갭이 선취"와 정합해야 함 — 이 정합 확인이
   이 라운드의 품질 검증. 만약 선행 분포가 뚜렷이 양이면 전략 제안 없이 원인 분석만.)

단독 실행: python cowalk.py → 분포 진단 CSV + 검산 낱장 출력 (data/processed/cowalk/)
주간 갱신: analyze.py가 compute_cowalk()를 호출해 buy_review CSV의 수급동행 컬럼을 채움.
"""
import logging
import sys

import numpy as np
import pandas as pd

import store

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # cp949 콘솔 대비
logger = logging.getLogger(__name__)

M_CUTOFF = "2025-06-30"        # 일별 층 시작 직전 (store 스키마 참조)
WINDOW_WEEKS = 52              # 롤링 창 (주)
MIN_OBS = 40                   # 이 미만이면 '관측 부족'
LAGS = [0, 1, 2, 4]            # 0=동행, k=선행 k주 (수급이 앞섬)
GRADES = ["하", "중", "상"]     # 동행 상관 시장별 3분위

OUT_DIR = store.BASE_DIR.parent / "data" / "processed" / "cowalk"

EXPECT = ("# 사전 예상(고정): 동행 상관 양(+) 치우침 / 선행 상관 0 중심 "
          "— A 검증(갭 선취)과 정합해야 함. 전략화 금지, 기술 통계 전용")


def _weekly_frames():
    """시장별 {S(강도), R(수익률), last_date(주 라벨)} — 최근 WINDOW_WEEKS+1주.

    반환 프레임 인덱스: ISO 주 키 'YYYY-Www' (정렬 가능), 컬럼: 종목코드.
    """
    fl = store.read_flows()
    fl = fl[(fl["해상도"] == "D") & (fl["투자자"] == "외국인")].copy()
    sn = store.read_snapshots()
    sn = sn[sn["날짜"] > M_CUTOFF].copy()

    def week_key(dates: pd.Series) -> pd.Series:
        iso = pd.to_datetime(dates).dt.isocalendar()
        return iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)

    fl["주"] = week_key(fl["날짜"])
    sn["주"] = week_key(sn["날짜"])

    out = {}
    for market in ["KOSPI", "KOSDAQ"]:
        f = fl[fl["시장"] == market]
        s = sn[sn["시장"] == market]

        vol = f.pivot_table(index="주", columns="코드", values="거래량", aggfunc="sum")

        # 그 주 마지막 거래일의 종가·상장주식수 (결측 행 제외 후 주별 마지막)
        s_close = s[s["종가"].notna()].sort_values("날짜")
        close_w = s_close.pivot_table(index="주", columns="코드", values="종가", aggfunc="last")
        s_sh = s[s["상장주식수"].notna()].sort_values("날짜")
        shares_w = s_sh.pivot_table(index="주", columns="코드", values="상장주식수", aggfunc="last")
        last_date = s_close.groupby("주")["날짜"].max()

        weeks = sorted(close_w.index)[-(WINDOW_WEEKS + 1):]   # 수익률 52개 확보용 +1
        close_w = close_w.reindex(weeks)
        vol = vol.reindex(weeks)
        shares_w = shares_w.reindex(weeks)

        S = (vol / shares_w * 100).iloc[1:]                          # 강도(%)
        R = (close_w / close_w.shift(1) - 1).iloc[1:] * 100          # 수익률(%)
        out[market] = {"S": S, "R": R, "last_date": last_date.reindex(weeks[1:])}
    return out


def compute_cowalk() -> pd.DataFrame:
    """종목별 수급동행 표: 시장·코드·n주·r_동행·r_선행1·r_선행2·r_선행4·수급동행(등급)."""
    frames = _weekly_frames()
    rows = []
    for market, fr in frames.items():
        S, R = fr["S"], fr["R"]
        codes = S.columns.intersection(R.columns)
        S, R = S[codes], R[codes]

        n_obs = (S.notna() & R.notna()).sum()
        corr = {}
        for k in LAGS:
            corr[k] = S.corrwith(R.shift(-k))                # 선행 k주: 강도_t vs 수익률_{t+k}

        df = pd.DataFrame({
            "시장": market, "코드": codes,
            "n주": n_obs.reindex(codes).values,
            "r_동행": corr[0].reindex(codes).round(3).values,
            "r_선행1": corr[1].reindex(codes).round(3).values,
            "r_선행2": corr[2].reindex(codes).round(3).values,
            "r_선행4": corr[4].reindex(codes).round(3).values,
        })
        eligible = (df["n주"] >= MIN_OBS) & df["r_동행"].notna()
        df["수급동행"] = ""
        if eligible.sum() >= 3:
            df.loc[eligible, "수급동행"] = pd.qcut(
                df.loc[eligible, "r_동행"], 3, labels=GRADES).astype(str)
        rows.append(df)
    return pd.concat(rows, ignore_index=True)


def _hist_and_summary(table: pd.DataFrame):
    """시차별 상관 분포 히스토그램(폭 0.1) + 요약(중앙값·상하위 10%·양수비율)."""
    bins = np.round(np.arange(-1.0, 1.05, 0.1), 1)
    hist_rows, sum_rows = [], []
    for market, g in table.groupby("시장"):
        g = g[g["n주"] >= MIN_OBS]
        for lag, col in [("동행", "r_동행"), ("선행1주", "r_선행1"),
                         ("선행2주", "r_선행2"), ("선행4주", "r_선행4")]:
            x = g[col].dropna()
            counts, edges = np.histogram(x, bins=bins)
            for c, lo, hi in zip(counts, edges[:-1], edges[1:]):
                hist_rows.append({"시장": market, "시차": lag,
                                  "구간시작": lo, "구간끝": hi, "종목수": int(c)})
            sum_rows.append({
                "시장": market, "시차": lag, "종목수": len(x),
                "중앙값": round(x.median(), 3),
                "하위10%": round(x.quantile(0.10), 3),
                "상위10%": round(x.quantile(0.90), 3),
                "양수비율%": round((x > 0).mean() * 100, 1),
            })
    return pd.DataFrame(hist_rows), pd.DataFrame(sum_rows)


def main():
    import time
    t0 = time.time()
    table = compute_cowalk()
    elapsed = time.time() - t0

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    def save(name, frame):
        with open(OUT_DIR / name, "w", encoding="utf-8-sig", newline="") as f:
            f.write(EXPECT + "\n")
            frame.to_csv(f, index=False)

    save("cowalk_corr.csv", table)
    hist, summary = _hist_and_summary(table)
    save("cowalk_hist.csv", hist)
    save("cowalk_summary.csv", summary)

    short = (table["n주"] < MIN_OBS).sum()
    print(f"계산 시간 {elapsed:.1f}초 | 전체 {len(table)}종목, 관측 부족(<{MIN_OBS}주) {short}종목")
    print()
    print(summary.to_string(index=False))

    # 검산 ①: 동행 상관 최상위 1종목의 주간 낱장
    top = table[table["n주"] >= MIN_OBS].nlargest(1, "r_동행").iloc[0]
    frames = _weekly_frames()
    fr = frames[top["시장"]]
    piece = pd.DataFrame({
        "주": fr["S"].index,
        "마지막거래일": fr["last_date"].values,
        "강도pct": fr["S"][top["코드"]].round(3).values,
        "수익률pct": fr["R"][top["코드"]].round(2).values,
    })
    save("cowalk_check_top1.csv", piece)
    print(f"\n검산 낱장: {top['시장']} {top['코드']} r_동행={top['r_동행']}"
          f" (cowalk_check_top1.csv, {len(piece)}주)")
    print(piece.tail(8).to_string(index=False))

    # 검산 ②: 지정 종목 r값 (상세 차트와 눈 대조용)
    for code in ["005930"] + [c for c in sys.argv[1:]]:
        hit = table[table["코드"] == code]
        if not hit.empty:
            h = hit.iloc[0]
            print(f"\n{h['시장']} {code}: r_동행={h['r_동행']} 선행1={h['r_선행1']}"
                  f" 선행2={h['r_선행2']} 선행4={h['r_선행4']} n={h['n주']} 등급={h['수급동행'] or '관측부족'}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
