"""
코스피/코스닥 투자자별 수급 분석 — 로컬 Streamlit 웹앱

실행:
    streamlit run app/streamlit_app.py

기능:
    - 분석 대상 토글 (지수 / 개별종목-코스피200)
    - 지수: 시장(코스피/코스닥), 개인·외국인·기관, 단위=억원
    - 개별종목: 코스피200 종목 선택, 외국인·기관(개인 미제공), 단위=주
    - 주기 토글 (일간/주간/월간)
    - 차트1: 종가 + 투자자별 누적 순매수선
    - 차트2: 투자자별 순매수 막대
    - 상관표: 투자자별 순매수 ↔ 종가 수익률 (Pearson/Spearman)
    - [새로고침] 버튼으로 네이버 금융에서 최신 데이터 재수집
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import streamlit as st

# app 폴더를 import 경로에 추가 (streamlit run 어디서 실행하든 동작)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from data_fetch import (  # noqa: E402
    load_or_fetch,
    load_or_fetch_stock,
    fetch_kospi200_list,
)
from analyze import (  # noqa: E402
    prepare,
    correlation_table,
    FREQ_KR,
    INVESTOR_KR,
    _present_investors,
)

# ---------------------------------------------------------------------------
# 기본 설정
# ---------------------------------------------------------------------------
matplotlib.rcParams["font.family"] = "Malgun Gothic"  # Windows 한글 폰트
matplotlib.rcParams["axes.unicode_minus"] = False  # 마이너스 깨짐 방지

# 한국 관행 색상: 상승/매수=빨강, 하락/매도=파랑
COLORS = {
    "individual": "#7f7f7f",  # 개인 = 회색
    "foreign": "#d62728",     # 외국인 = 빨강
    "institution": "#1f77b4", # 기관 = 파랑
    "index": "#111111",       # 지수 = 검정
}

st.set_page_config(page_title="수급 분석", page_icon="📊", layout="wide")


# ---------------------------------------------------------------------------
# 데이터 로드 (캐시)
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner="네이버 금융에서 지수 데이터 수집 중…")
def get_index_data(market: str, pages: int, refresh: bool):
    return load_or_fetch(market, pages=pages, refresh=refresh)


@st.cache_data(show_spinner="네이버 금융에서 종목 수급 수집 중…")
def get_stock_data(code: str, pages: int, refresh: bool):
    return load_or_fetch_stock(code, pages=pages, refresh=refresh)


@st.cache_data(show_spinner="코스피200 종목 목록 불러오는 중…")
def get_kospi200(refresh: bool = False):
    return fetch_kospi200_list(refresh=refresh)


# ---------------------------------------------------------------------------
# 차트
# ---------------------------------------------------------------------------
def chart_price_vs_cumulative(df, title, price_label, unit, investors):
    """차트1: 종가(좌축) + 존재하는 투자자별 누적 순매수(우축).

    지수/개별종목 공용. investors에 그릴 투자자 키 리스트를 넘긴다.
    """
    fig, ax1 = plt.subplots(figsize=(11, 4.5))

    ax1.plot(df["date"], df["close"], color=COLORS["index"], linewidth=1.8,
             label=price_label)
    ax1.set_ylabel(price_label, color=COLORS["index"])
    ax1.tick_params(axis="y", labelcolor=COLORS["index"])

    ax2 = ax1.twinx()
    for inv in investors:
        ax2.plot(df["date"], df[f"cum_{inv}"], color=COLORS[inv],
                 linewidth=1.4, label=f"{INVESTOR_KR[inv]} 누적순매수")
    ax2.axhline(0, color="#bbbbbb", linewidth=0.8, linestyle="--")
    ax2.set_ylabel(f"누적 순매수 ({unit})")

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=9)

    ax1.set_title(title, fontsize=13, fontweight="bold")
    ax1.grid(True, alpha=0.25)
    fig.tight_layout()
    return fig


def chart_net_bars(df, freq_kr, unit, investors):
    """차트2: 존재하는 투자자별 순매수 막대. 지수/개별종목 공용."""
    fig, ax = plt.subplots(figsize=(11, 3.5))

    n = len(df)
    k = len(investors)
    width = 0.8 / k
    xs = range(n)
    for i, inv in enumerate(investors):
        offsets = [x + (i - (k - 1) / 2) * width for x in xs]
        ax.bar(offsets, df[inv], width=width, color=COLORS[inv],
               label=INVESTOR_KR[inv])

    ax.axhline(0, color="#333333", linewidth=0.8)
    ax.set_ylabel(f"순매수 ({unit})")
    ax.set_title(f"투자자별 순매수 ({freq_kr})", fontsize=13, fontweight="bold")

    step = max(1, n // 12)
    ticks = list(xs)[::step]
    labels = [df["date"].iloc[i].strftime("%y-%m-%d") for i in ticks]
    ax.set_xticks(ticks)
    ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
    ax.legend(loc="upper left", fontsize=9, ncol=k)
    ax.grid(True, axis="y", alpha=0.25)
    fig.tight_layout()
    return fig


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.title("📊 코스피·코스닥 투자자별 수급 분석")
st.caption("개인·외국인·기관 순매수 흐름과 주가 등락의 관계를 확인합니다. (출처: 네이버 금융)")

# --- 사이드바 컨트롤 ---
with st.sidebar:
    st.header("설정")
    mode = st.radio("분석 대상", ["지수", "개별종목"], horizontal=True)

    if mode == "지수":
        market_kr = st.radio("시장", ["코스피", "코스닥"], horizontal=True)
        market = "KOSPI" if market_kr == "코스피" else "KOSDAQ"
    else:
        # 코스피200 종목 선택
        kospi200 = get_kospi200()
        if kospi200.empty:
            st.error("코스피200 목록을 불러오지 못했습니다.")
            st.stop()
        # "이름 (코드)" 형태로 선택
        options = {f"{r['name']} ({r['code']})": r["code"]
                   for _, r in kospi200.iterrows()}
        picked = st.selectbox("종목 (코스피200)", list(options.keys()))
        stock_code = options[picked]
        stock_name = picked.split(" (")[0]

    freq_kr = st.radio("주기", ["일간", "주간", "월간"], horizontal=True)
    freq = {"일간": "D", "주간": "W", "월간": "M"}[freq_kr]

    months = st.slider("조회 기간(개월)", min_value=3, max_value=24, value=12, step=1)

    refresh = st.button("🔄 데이터 새로고침", use_container_width=True)
    st.caption("새로고침은 네이버 금융에서 최신 데이터를 다시 받아옵니다.")

# --- 데이터 로드 (모드별) ---
if mode == "지수":
    pages = max(5, months * 3)  # 1페이지 ≈ 6영업일
    if refresh:
        get_index_data.clear()
    raw = get_index_data(market, pages, refresh)
    title_head = market_kr
    price_label = "지수 (pt)"
    unit = "억원"
else:
    pages = max(2, (months + 1) // 1)  # 종목: 1페이지 ≈ 30영업일 ≈ 1.5개월
    pages = max(2, round(months / 1.5) + 1)
    if refresh:
        get_stock_data.clear()
    raw = get_stock_data(stock_code, pages, refresh)
    title_head = stock_name
    price_label = "주가 (원)"
    unit = "주"

if raw is None or raw.empty:
    st.error("데이터를 불러오지 못했습니다. 네트워크(국내망) 상태를 확인하고 새로고침해 주세요.")
    st.stop()

prepared = prepare(raw, freq)
investors = _present_investors(prepared)  # 지수=3, 종목=2(개인 없음)

# 기간 정보
d0 = prepared["date"].min().strftime("%Y-%m-%d")
d1 = prepared["date"].max().strftime("%Y-%m-%d")
st.info(f"**{title_head}** · **{freq_kr}** · {d0} ~ {d1} · {len(prepared)}개 구간")

if mode == "개별종목":
    st.caption("ℹ️ 개별종목은 네이버 금융이 **개인 데이터를 제공하지 않아** "
               "외국인·기관만 표시합니다. 단위는 **주(수량)** 기준입니다.")

# --- 차트 ---
chart_title = f"{title_head} vs 외국인·기관 누적 순매수 ({freq_kr})"
st.pyplot(chart_price_vs_cumulative(prepared, chart_title, price_label, unit, investors))
st.pyplot(chart_net_bars(prepared, freq_kr, unit, investors))

# 외국인 보유율 (종목 모드에서만)
if mode == "개별종목" and "foreign_ratio" in prepared.columns:
    latest_ratio = prepared["foreign_ratio"].dropna().iloc[-1]
    st.metric("외국인 보유율 (최근)", f"{latest_ratio:.2f}%")

# --- 상관표 ---
st.subheader(f"수급 ↔ {'주가' if mode == '개별종목' else '지수'} 수익률 상관계수")
st.caption("+1에 가까울수록 '해당 투자자가 살 때 오른다', "
           "−1에 가까울수록 '살 때 오히려 내린다'를 뜻합니다.")
corr = correlation_table(prepared)
st.dataframe(corr, use_container_width=True)

# --- 원자료 (접이식) ---
with st.expander(f"원자료 보기 ({unit})"):
    cols = ["date", "close"] + investors
    show = prepared[cols].copy()
    kr = {"date": "날짜", "close": ("주가" if mode == "개별종목" else "지수")}
    kr.update({inv: INVESTOR_KR[inv] for inv in investors})
    show.columns = [kr[c] for c in cols]
    st.dataframe(show.sort_values("날짜", ascending=False),
                 use_container_width=True, hide_index=True)
