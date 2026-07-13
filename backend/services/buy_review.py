"""
매수 검토 스크리너 (MVP) — 외국인 지분율 변화폭 기준 카드 리스트

데이터: data/processed/buy_review_{MARKET}.csv + buy_review_meta.json
       (수급/pipeline/analyze.py 산출물, 주간 갱신)
MVP 범위: 시장 전환 + 변화폭순 고정 정렬. 필터/플래그/배지 색단계는 다음 라운드.

의존성: 표준 라이브러리만 (기존 screener.py 패턴).
"""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"

MARKET_NAME = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}
LIST_LIMIT = 100  # 카드 리스트 표시 상한 (변화폭 상위)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def get_buy_review(market: str = "KOSPI") -> dict:
    market = market if market in MARKET_NAME else "KOSPI"
    path = PROCESSED_DIR / f"buy_review_{market}.csv"
    meta_path = PROCESSED_DIR / "buy_review_meta.json"

    if not path.exists():
        return {"market": market, "market_name": MARKET_NAME[market],
                "as_of": None, "total": 0, "shown": 0, "rows": [], "is_empty": True}

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
                "pbr": _num(r["PBR_최근"]),
            })

    total = len(rows)
    rows = [x for x in rows if x["delta"] is not None]
    rows.sort(key=lambda x: x["delta"], reverse=True)
    shown = rows[:LIST_LIMIT]

    as_of = None
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        as_of = meta.get(market, {}).get("기준일")

    return {"market": market, "market_name": MARKET_NAME[market],
            "as_of": as_of, "total": total, "shown": len(shown),
            "rows": shown, "is_empty": False}
