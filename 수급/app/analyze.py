"""
집계/분석 모듈 — 일별 수급·지수 데이터를 주간·월간으로 집계하고,
누적 순매수와 수급-주가 상관관계를 계산한다.

집계 규칙:
- 지수 종가(close): 기간의 마지막 값 (last)
- 투자자별 순매수: 기간의 합계 (sum)  ← 순매수는 "합"이 맞다
"""
from __future__ import annotations

import pandas as pd

# 전체 투자자 주체 정의 (지수는 3주체, 개별종목은 개인 없이 2주체)
INVESTOR_KR = {
    "individual": "개인",
    "foreign": "외국인",
    "institution": "기관",
}
INVESTORS = list(INVESTOR_KR.keys())

FREQ_RULE = {"D": None, "W": "W-FRI", "M": "ME"}
FREQ_KR = {"D": "일간", "W": "주간", "M": "월간"}


def _present_investors(df: pd.DataFrame) -> list[str]:
    """DataFrame에 실제로 존재하는 투자자 컬럼만 반환한다.

    지수 데이터는 [개인,외국인,기관], 개별종목은 [외국인,기관]만 있다.
    """
    return [inv for inv in INVESTORS if inv in df.columns]


def resample(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """일별 데이터를 주간(W)/월간(M)으로 집계한다. freq='D'는 원본 그대로.

    close는 마지막 값(last), 순매수는 합계(sum)로 집계한다.
    존재하는 투자자 컬럼만 처리하므로 지수·개별종목 모두 대응한다.

    Args:
        df: columns=[date, close, (individual), foreign, institution, ...]
        freq: 'D' | 'W' | 'M'
    """
    if freq == "D":
        return df.copy().reset_index(drop=True)

    rule = FREQ_RULE[freq]
    g = df.set_index("date")
    agg: dict[str, str] = {"close": "last"}
    for inv in _present_investors(df):
        agg[inv] = "sum"
    # 외국인 보유율(있으면) 기간 마지막 값
    if "foreign_ratio" in df.columns:
        agg["foreign_ratio"] = "last"
    out = g.resample(rule).agg(agg).dropna(subset=["close"]).reset_index()
    return out


def add_cumulative(df: pd.DataFrame) -> pd.DataFrame:
    """존재하는 투자자별 누적 순매수 컬럼(cum_*)을 추가한다."""
    out = df.copy()
    for inv in _present_investors(df):
        out[f"cum_{inv}"] = out[inv].cumsum()
    return out


def correlation_table(df: pd.DataFrame) -> pd.DataFrame:
    """투자자별 순매수 ↔ 종가 수익률 상관계수(Pearson/Spearman)를 계산한다.

    존재하는 투자자 컬럼만 계산하므로 지수·개별종목 모두 대응한다.

    Returns:
        index=[개인/외국인/기관 중 존재하는 것], columns=[Pearson, Spearman]
    """
    work = df.copy()
    work["ret"] = work["close"].pct_change()
    work = work.dropna(subset=["ret"])

    rows = {}
    for inv in _present_investors(df):
        if work[inv].std() == 0 or len(work) < 3:
            pearson = spearman = float("nan")
        else:
            pearson = work[inv].corr(work["ret"], method="pearson")
            # Spearman = 랭크 변환 후 Pearson (scipy 의존성 회피)
            spearman = work[inv].rank().corr(work["ret"].rank(), method="pearson")
        rows[INVESTOR_KR[inv]] = {
            "Pearson": round(pearson, 3) if pearson == pearson else None,
            "Spearman": round(spearman, 3) if spearman == spearman else None,
        }
    return pd.DataFrame(rows).T


def prepare(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """집계 + 누적 순매수까지 한 번에 처리한다."""
    return add_cumulative(resample(df, freq))


if __name__ == "__main__":
    from data_fetch import load_or_fetch

    for mkt in ["KOSPI", "KOSDAQ"]:
        raw = load_or_fetch(mkt)
        for freq in ["D", "W", "M"]:
            prepared = prepare(raw, freq)
            corr = correlation_table(prepared)
            print(f"\n=== {mkt} / {FREQ_KR[freq]} ({len(prepared)} rows) ===")
            print(corr.to_string())
