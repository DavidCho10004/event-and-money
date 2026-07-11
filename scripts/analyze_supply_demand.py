"""
투자자별 수급 → 주가 변동 분석 + 시각화

입력 : data/raw/supply_demand_{MARKET}.csv   (fetch_supply_demand.py 산출물)
출력 :
  - data/processed/supply_demand_{MARKET}_{FREQ}.csv   (일/주/월 집계 + 누적순매수 + 수익률)
  - outputs/charts/supply_demand_{MARKET}_{FREQ}.png    (상단 지수+누적선, 하단 순매수 막대)
  - 콘솔에 상관계수 요약표 출력

분석 로직 (설계 합의안):
  (A) 누적 순매수선 vs 지수 : 외국인/기관 누적 순매수 곡선을 지수에 겹쳐, 수급-주가 동행 확인
  (B) 상관계수 : 기간 순매수 vs 같은 기간 지수 수익률의 Pearson/Spearman 상관

집계 규칙:
  - 지수 종가(레벨)  → 기간의 마지막 거래일 값 (last)
  - 순매수 금액      → 기간 합계 (sum)   ※ 순매수는 "흐름(flow)"이라 합산이 맞음
  - 주간(W-FRI)은 금요일 종료, 월간(M)은 월말 기준

실행 방법 (프로젝트 루트에서):
    python scripts/analyze_supply_demand.py                 # 코스피+코스닥, 일/주/월 전부
    python scripts/analyze_supply_demand.py --market KOSPI --freq W
"""
import sys
import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # 서버/헤드리스 환경에서 파일로만 저장
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
PROCESSED_DIR = ROOT / "data" / "processed"
CHART_DIR = ROOT / "outputs" / "charts"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

INVESTORS = ["개인", "외국인", "기관"]  # 3대 주체 (기타법인은 차트에서 제외, CSV엔 포함)
# 한국 관행: 매수(순매수+)=빨강, 매도(순매수-)=파랑
COLOR = {"개인": "#888888", "외국인": "#d62728", "기관": "#1f77b4"}

FREQ_LABEL = {"D": "일간", "W": "주간", "M": "월간"}
# pandas resample 규칙
FREQ_RULE = {"D": None, "W": "W-FRI", "M": "ME"}


def setup_korean_font():
    """프로젝트에 포함된 Pretendard 폰트를 matplotlib에 등록 (한글 깨짐 방지)"""
    font_path = ROOT / "backend" / "static" / "fonts" / "Pretendard-Regular.otf"
    if font_path.exists():
        font_manager.fontManager.addfont(str(font_path))
        plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(font_path)).get_name()
    else:
        logger.warning("Pretendard 폰트를 못 찾음 — 한글이 깨질 수 있습니다: %s", font_path)
    plt.rcParams["axes.unicode_minus"] = False  # 마이너스 기호 깨짐 방지


def load_raw(market):
    """원본 일별 수급 CSV 로드"""
    path = RAW_DIR / f"supply_demand_{market}.csv"
    if not path.exists():
        logger.error("원본 없음: %s — 먼저 fetch_supply_demand.py 를 실행하세요.", path)
        return None
    df = pd.read_csv(path, parse_dates=["날짜"], index_col="날짜")
    return df


def aggregate(df, freq):
    """일별 데이터를 지정 주기로 집계 → 누적순매수/수익률 컬럼 추가"""
    netbuy_cols = [c for c in df.columns if c in ["개인", "외국인", "기관", "기타법인"]]

    if freq == "D":
        agg = df.copy()
    else:
        rule = FREQ_RULE[freq]
        # 종가는 마지막 값, 순매수 금액은 합계
        agg = pd.DataFrame()
        agg["종가"] = df["종가"].resample(rule).last()
        for c in netbuy_cols:
            agg[c] = df[c].resample(rule).sum()
        agg = agg.dropna(subset=["종가"])

    # 순매수 단위를 억원으로 변환(가독성) — 원 → 억원
    for c in netbuy_cols:
        agg[c] = agg[c] / 1e8
    # (A) 누적 순매수 (억원)
    for c in netbuy_cols:
        agg[f"누적_{c}"] = agg[c].cumsum()
    # 지수 수익률 (%)
    agg["수익률"] = agg["종가"].pct_change() * 100
    return agg, netbuy_cols


def correlations(agg, netbuy_cols):
    """(B) 기간 순매수 vs 같은 기간 지수 수익률 상관 (Pearson/Spearman)"""
    rows = []
    ret = agg["수익률"]
    for c in netbuy_cols:
        s = agg[c]
        valid = ret.notna() & s.notna()
        n = int(valid.sum())
        if n < 3:
            rows.append({"투자자": c, "n": n, "Pearson": np.nan, "Spearman": np.nan})
            continue
        pear = s[valid].corr(ret[valid], method="pearson")
        # Spearman = 순위(rank)로 변환한 뒤의 Pearson. scipy 없이 계산.
        spear = s[valid].rank().corr(ret[valid].rank(), method="pearson")
        rows.append({"투자자": c, "n": n, "Pearson": round(pear, 3), "Spearman": round(spear, 3)})
    return pd.DataFrame(rows)


def make_chart(agg, market, freq, out_path):
    """상단: 지수 종가 + 외국인/기관 누적순매수 / 하단: 투자자별 순매수 막대"""
    name_kr = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}.get(market, market)
    flabel = FREQ_LABEL[freq]

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(12, 8), sharex=True, gridspec_kw={"height_ratios": [2, 1]}
    )

    # --- 상단: 지수 종가 (좌축) + 누적 순매수 (우축) ---
    ax1.plot(agg.index, agg["종가"], color="black", lw=1.6, label=f"{name_kr} 지수")
    ax1.set_ylabel("지수", color="black")
    ax1b = ax1.twinx()
    for c in ["외국인", "기관"]:
        col = f"누적_{c}"
        if col in agg.columns:
            ax1b.plot(agg.index, agg[col], color=COLOR[c], lw=1.2, alpha=0.85,
                      label=f"{c} 누적순매수")
    ax1b.set_ylabel("누적 순매수 (억원)")
    ax1b.axhline(0, color="gray", lw=0.6, ls="--")
    # 범례 통합
    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax1b.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=9)
    ax1.set_title(f"{name_kr} 투자자별 수급 vs 지수 ({flabel})", fontsize=14, fontweight="bold")

    # --- 하단: 투자자별 순매수 막대 (양수=빨강/매수, 음수=파랑/매도 관행) ---
    width = (agg.index[1] - agg.index[0]).days * 0.25 if len(agg) > 1 else 1
    for i, c in enumerate(INVESTORS):
        if c in agg.columns:
            ax2.bar(agg.index + pd.Timedelta(days=(i - 1) * width),
                    agg[c], width=width, color=COLOR[c], label=c, alpha=0.9)
    ax2.axhline(0, color="gray", lw=0.6)
    ax2.set_ylabel("순매수 (억원)")
    ax2.legend(loc="upper left", ncol=3, fontsize=9)

    fig.text(0.99, 0.01, "Source: KRX (pykrx)", ha="right", va="bottom",
             fontsize=8, color="gray")
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    logger.info("차트 저장: %s", out_path)


def run(market, freq):
    df = load_raw(market)
    if df is None:
        return False
    agg, netbuy_cols = aggregate(df, freq)

    # 처리 결과 저장 (커밋 대상: data/processed/)
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    proc_path = PROCESSED_DIR / f"supply_demand_{market}_{freq}.csv"
    agg.to_csv(proc_path, encoding="utf-8-sig")
    logger.info("집계 저장: %s (%d개 구간)", proc_path, len(agg))

    # (B) 상관계수 출력
    corr = correlations(agg, netbuy_cols)
    print(f"\n=== [{market} / {FREQ_LABEL[freq]}] 순매수 ↔ 지수 수익률 상관 ===")
    print(corr.to_string(index=False))
    print("(+상관: 그 주체가 살 때 지수가 오르는 경향 / n: 표본 구간 수)\n")

    # (A) 차트
    make_chart(agg, market, freq, CHART_DIR / f"supply_demand_{market}_{freq}.png")
    return True


def main():
    parser = argparse.ArgumentParser(description="투자자 수급 → 주가 변동 분석/시각화")
    parser.add_argument("--market", choices=["KOSPI", "KOSDAQ"], help="한 시장만 (기본: 둘 다)")
    parser.add_argument("--freq", choices=["D", "W", "M"], help="한 주기만 (기본: 일/주/월 전부)")
    args = parser.parse_args()

    setup_korean_font()
    markets = [args.market] if args.market else ["KOSPI", "KOSDAQ"]
    freqs = [args.freq] if args.freq else ["D", "W", "M"]

    ok = 0
    for m in markets:
        for f in freqs:
            if run(m, f):
                ok += 1
    if ok == 0:
        logger.error("처리된 결과가 없습니다. data/raw/ 에 원본 CSV가 있는지 확인하세요.")
        sys.exit(1)


if __name__ == "__main__":
    main()
