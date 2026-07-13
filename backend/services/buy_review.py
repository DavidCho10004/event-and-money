"""
매수 검토 스크리너 — 외국인 지분율 변화폭(상대 기간) 기준 카드 리스트

데이터: data/processed/buy_review_{MARKET}.csv + buy_review_meta.json
       (수급/pipeline/analyze.py 산출물, 주간 갱신)
- 기간 토글: 6m/3m/1m/1w (기본 DEFAULT_PERIOD). 변화폭·순위·필터·플래그가 기간 기준.
- 1w는 일별 지분율 스냅샷이 쌓이면 자동 활성화 (meta의 available)
- 필터 칩·기간은 URL 쿼리로 상태 유지

의존성: 표준 라이브러리만 (기존 screener.py 패턴).
"""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"

MARKET_NAME = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}
PERIOD_NAME = {"6m": "6개월", "3m": "3개월", "1m": "1개월", "1w": "1주일"}
DEFAULT_PERIOD = "3m"
LIST_LIMIT = 100      # 카드 리스트 표시 상한
PBR_MAX_DEFAULT = 2.0  # 'PBR 상한' 칩을 켰을 때의 상한값


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _flag(v):
    return str(v).strip().lower() == "true"


def naver_chart_url(code: str) -> str:
    """네이버 금융 종목 페이지 링크 (카드의 '네이버 차트 ↗' 버튼용)."""
    return f"https://finance.naver.com/item/main.naver?code={code}"


def _load(market: str, p: str):
    path = PROCESSED_DIR / f"buy_review_{market}.csv"
    if not path.exists():
        return None
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rank = r.get(f"순위_{p}")
            rows.append({
                "code": str(r["코드"]).zfill(6),
                "naver_url": naver_chart_url(str(r["코드"]).zfill(6)),
                "name": r["회사명"],
                "frgn_base": _num(r.get(f"지분율기준_{p}")),
                "frgn_now": _num(r["외인지분율_최근"]),
                "delta": _num(r.get(f"변화폭_{p}")),
                "delta_1w": _num(r.get("변화폭_1w")),
                "rank": int(float(rank)) if rank not in (None, "") else None,
                "netbuy_frgn": _num(r.get(f"순매수억_외국인_{p}")),
                "netbuy_inst": _num(r.get(f"순매수억_기관_{p}")),
                "netbuy_indiv": _num(r.get(f"순매수억_개인_{p}")),
                "pbr": _num(r["PBR_최근"]),
                "suspect": _flag(r.get(f"플래그_주식수변동의심_{p}")),
                "inst_buy": _flag(r.get(f"플래그_기관동반_{p}")),
                "indiv_sell": _flag(r.get(f"플래그_개인순매도_{p}")),
            })
    return rows


def get_buy_review(market: str = "KOSPI", p: str = DEFAULT_PERIOD,
                   f3: bool = False, f5: bool = False,
                   pbr: bool = False, inst: bool = False, indiv: bool = False) -> dict:
    market = market if market in MARKET_NAME else "KOSPI"

    meta_path = PROCESSED_DIR / "buy_review_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    mmeta = meta.get(market, {})
    periods = {
        key: {"name": PERIOD_NAME[key],
              "available": mmeta.get("periods", {}).get(key, {}).get("available", False),
              "base_date": mmeta.get("periods", {}).get(key, {}).get("기준일")}
        for key in PERIOD_NAME
    }
    if p not in PERIOD_NAME or not periods.get(p, {}).get("available"):
        p = DEFAULT_PERIOD

    filters = {"f3": f3, "f5": f5, "pbr": pbr, "inst": inst, "indiv": indiv}
    base = {"market": market, "market_name": MARKET_NAME[market],
            "p": p, "p_name": PERIOD_NAME[p], "periods": periods,
            "base_date": periods[p]["base_date"],
            "filters": filters, "pbr_max": PBR_MAX_DEFAULT}

    rows = _load(market, p)
    if rows is None:
        return {**base, "as_of": None, "total": 0, "matched": 0, "shown": 0,
                "rows": [], "is_empty": True}

    total = len(rows)
    rows = [x for x in rows if x["delta"] is not None]

    # 필터 적용 (+5%p가 켜지면 +3%p보다 우선) — 선택 기간의 변화폭 기준
    if f5:
        rows = [x for x in rows if x["delta"] >= 5.0]
    elif f3:
        rows = [x for x in rows if x["delta"] >= 3.0]
    if pbr:
        rows = [x for x in rows if x["pbr"] is not None and 0 < x["pbr"] <= PBR_MAX_DEFAULT]
    if inst:
        rows = [x for x in rows if x["inst_buy"]]
    if indiv:
        rows = [x for x in rows if x["indiv_sell"]]

    matched = len(rows)
    # 변화폭 내림차순. 의심 종목은 순위 유지 + 경고색 카드로 시각 구분
    rows.sort(key=lambda x: -x["delta"])
    shown = rows[:LIST_LIMIT]

    return {**base, "as_of": mmeta.get("기준일"), "total": total,
            "matched": matched, "shown": len(shown), "rows": shown, "is_empty": False}
