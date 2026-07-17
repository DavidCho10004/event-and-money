"""
수급 분석 — 시장 조망: 시장별 투자자(외인/기관/개인) 순매수 주간/월간 추이

데이터: data/processed/market_flows_{MARKET}_{W|M}.csv (수급/pipeline/analyze.py 산출물)
       일별 flows 실데이터 기반 — 합성/데모 폴백 없음 (CLAUDE.md 규칙).
지수 종가는 저장소에 없어 이번 버전에 포함하지 않음 (지어내기 금지).

의존성: 표준 라이브러리만.
"""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"

MARKET_NAME = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}
FREQ_NAME = {"W": "주간", "M": "월간"}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _jo(eok):
    """억원 → '+N.NN조' 문자열."""
    if eok is None:
        return "—"
    return f"{eok / 10000:+.2f}조"


def get_supply_demand(market: str = "KOSPI", freq: str = "W") -> dict:
    market = market if market in MARKET_NAME else "KOSPI"
    freq = freq if freq in FREQ_NAME else "W"

    path = PROCESSED_DIR / f"market_flows_{market}_{freq}.csv"
    base = {"market": market, "market_name": MARKET_NAME[market],
            "freq": freq, "freq_name": FREQ_NAME[freq]}
    if not path.exists():
        return {**base, "is_empty": True, "rows": [], "summary": None, "as_of": None}

    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append({"label": r["라벨"],
                         "frgn": _num(r.get("외인_억")),
                         "inst": _num(r.get("기관_억")),
                         "indiv": _num(r.get("개인_억"))})
    if not rows:
        return {**base, "is_empty": True, "rows": [], "summary": None, "as_of": None}

    # 최근 1주 요약 카드는 freq와 무관하게 주간 파일에서
    summary = None
    wpath = PROCESSED_DIR / f"market_flows_{market}_W.csv"
    if wpath.exists():
        with open(wpath, encoding="utf-8-sig") as f:
            wrows = list(csv.DictReader(f))
        if wrows:
            last = wrows[-1]
            summary = {"label": last["라벨"],
                       "frgn": _jo(_num(last.get("외인_억"))), "frgn_raw": _num(last.get("외인_억")),
                       "inst": _jo(_num(last.get("기관_억"))), "inst_raw": _num(last.get("기관_억")),
                       "indiv": _jo(_num(last.get("개인_억"))), "indiv_raw": _num(last.get("개인_억"))}

    return {**base, "is_empty": False, "rows": rows,
            "summary": summary, "as_of": rows[-1]["label"]}
