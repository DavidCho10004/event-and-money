"""
매수 검토 스크리너 — 외국인 지분율 변화폭 기준 카드 리스트

데이터: data/processed/buy_review_{MARKET}.csv + buy_review_meta.json
       (수급/pipeline/analyze.py 산출물, 주간 갱신)
- 필터 칩: 외인 +3%p/+5%p, PBR 상한, 기관 동반, 개인 순매도 (URL 쿼리로 상태 유지)
- 주식수 변동 의심 종목은 기본 정렬에서 하단 배치 + 경고 배지

의존성: 표준 라이브러리만 (기존 screener.py 패턴).
"""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"

MARKET_NAME = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}
LIST_LIMIT = 100      # 카드 리스트 표시 상한
PBR_MAX_DEFAULT = 2.0  # 'PBR 상한' 칩을 켰을 때의 상한값


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _flag(v):
    return str(v).strip().lower() == "true"


def _load(market: str):
    path = PROCESSED_DIR / f"buy_review_{market}.csv"
    if not path.exists():
        return None
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows.append({
                "code": str(r["코드"]).zfill(6),
                "name": r["회사명"],
                "delta": _num(r["변화폭_25년말比"]),
                "delta_mom": _num(r["변화폭_전월比"]),
                "rank": int(float(r["변화폭순위"])) if r.get("변화폭순위") else None,
                "frgn_now": _num(r["외인지분율_최근"]),
                "netbuy_frgn": _num(r["순매수억_외국인_26누계"]),
                "netbuy_inst": _num(r["순매수억_기관_26누계"]),
                "netbuy_indiv": _num(r["순매수억_개인_26누계"]),
                "pbr": _num(r["PBR_최근"]),
                "suspect": _flag(r.get("플래그_주식수변동의심")),
                "inst_buy": _flag(r.get("플래그_기관동반")),
                "indiv_sell": _flag(r.get("플래그_개인순매도")),
            })
    return rows


def get_buy_review(market: str = "KOSPI", f3: bool = False, f5: bool = False,
                   pbr: bool = False, inst: bool = False, indiv: bool = False) -> dict:
    market = market if market in MARKET_NAME else "KOSPI"
    filters = {"f3": f3, "f5": f5, "pbr": pbr, "inst": inst, "indiv": indiv}
    base = {"market": market, "market_name": MARKET_NAME[market],
            "filters": filters, "pbr_max": PBR_MAX_DEFAULT}

    rows = _load(market)
    if rows is None:
        return {**base, "as_of": None, "total": 0, "matched": 0, "shown": 0,
                "rows": [], "is_empty": True}

    total = len(rows)
    rows = [x for x in rows if x["delta"] is not None]

    # 필터 적용 (+5%p가 켜지면 +3%p보다 우선)
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
    # 정렬: 변화폭 내림차순. 의심 종목은 순위 자리를 유지하되
    # 경고색 카드+배지로 시각 구분 (하단 배치 시 표시 상한에 밀려 안 보이는 문제 방지)
    rows.sort(key=lambda x: -x["delta"])
    shown = rows[:LIST_LIMIT]

    as_of = None
    meta_path = PROCESSED_DIR / "buy_review_meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        as_of = meta.get(market, {}).get("기준일")

    return {**base, "as_of": as_of, "total": total, "matched": matched,
            "shown": len(shown), "rows": shown, "is_empty": False}
