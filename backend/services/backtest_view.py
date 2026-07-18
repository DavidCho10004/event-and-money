"""
백테스트 결과 뷰어 — data/processed/backtest/*.csv 읽기 전용

파일 머리의 '#' 주석 줄(사전 예상 기록)은 건너뛰고 파싱한다.
의존성: 표준 라이브러리만.
"""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BT_DIR = ROOT / "data" / "processed" / "backtest"

STRAT_NAME = {
    "a": "a 삼박자+3%p", "b": "b 강도 상위20", "c": "c 교집합",
    "d": "d 매집+미반응", "dp": "d′ 매집+기반응(대조군)",
    "H1": "H1 폭락 레짐", "H2i": "H2 지속형", "H2ii": "H2 스파이크형", "H3": "H3 개인 쏠림(역신호)",
    "E1": "E1 사건 월 개인 쏠림", "E2": "E2 사건 월 외인 쏠림(대조군)",
    "베이스": "베이스 (강도30 ∩ 지속형)",
    "C1_저PBR": "C1 저PBR (충족)", "C1_고PBR": "C1 고PBR (미충족)",
    "C2_고점근처": "C2 고점근처 (충족)", "C2_낙폭": "C2 낙폭 (미충족)",
    "C3_저변동": "C3 저변동 (충족)", "C3_고변동": "C3 고변동 (미충족)",
}
CURVE_COMBO = ("KOSPI", "b", "6")   # 누적 곡선 기본 조합


def _read(name):
    path = BT_DIR / name
    if not path.exists():
        return [], []
    comments, rows = [], []
    with open(path, encoding="utf-8-sig") as f:
        header = None
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("#"):
                comments.append(line.lstrip("# "))
                continue
            if header is None:
                header = next(csv.reader([line]))
                continue
            rows.append(dict(zip(header, next(csv.reader([line])))))
    return comments, rows


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def get_backtest_view() -> dict:
    _, summary = _read("backtest_summary.csv")
    _, robust = _read("backtest_robustness.csv")
    hyp_comments, hyp_summary = _read("backtest_hyp_summary.csv")
    _, hyp_robust = _read("backtest_hyp_robustness.csv")
    ev_comments, ev_summary = _read("backtest_event_summary.csv")
    _, ev_robust = _read("backtest_event_robustness.csv")
    cond_comments, cond_summary = _read("backtest_cond_summary.csv")
    _, cond_robust = _read("backtest_cond_robustness.csv")
    _, monthly = _read("backtest_monthly.csv")

    robust_map = {(r["시장"], r["전략"], r["보유개월"]): r
                  for r in robust + hyp_robust + ev_robust + cond_robust}

    def enrich(rows):
        out = []
        for r in rows:
            key = (r["시장"], r["전략"], r["보유개월"])
            rb = robust_map.get(key, {})
            out.append({
                **r,
                "전략명": STRAT_NAME.get(r["전략"], r["전략"]),
                "상위3제거": rb.get("초과랜덤_상위3제거%p"),
                "전구간_생존": rb.get("전구간_생존") == "True",
            })
        return out

    # 누적 곡선 (기본 조합) — 중첩 진입 단순 복리임을 화면에 명시
    mkt, strat, hold = CURVE_COMBO
    pts = [r for r in monthly
           if r["시장"] == mkt and r["전략"] == strat and r["보유"] == hold
           and _num(r["수익률"]) is not None and _num(r["랜덤평균"]) is not None]
    pts.sort(key=lambda r: r["진입월"])
    curve = {"labels": [], "strategy": [], "market": [], "random": []}
    cs = cm = cr = 1.0
    for r in pts:
        cs *= 1 + _num(r["수익률"])
        cm *= 1 + (_num(r["시장프록시"]) or 0)
        cr *= 1 + (_num(r["랜덤평균"]) or 0)
        curve["labels"].append(r["진입월"])
        curve["strategy"].append(round(cs, 3))
        curve["market"].append(round(cm, 3))
        curve["random"].append(round(cr, 3))

    return {
        "summary": enrich(summary),
        "hyp_summary": enrich(hyp_summary),
        "hyp_comments": hyp_comments,
        "ev_summary": enrich(ev_summary),
        "ev_comments": ev_comments,
        "cond_summary": enrich(cond_summary),
        "cond_comments": cond_comments,
        "curve": curve,
        "curve_label": f"{mkt} · {STRAT_NAME[strat]} · {hold}개월 보유",
        "is_empty": not summary,
    }
