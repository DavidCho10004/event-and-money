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
SORT_NAME = {"delta": "변화폭순", "amount": "금액순", "strength": "강도순"}
DEFAULT_SORT = "delta"   # strength는 순매수량/상장주식수 데이터 확보 후 활성화
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
                "shares_chg": _num(r.get(f"주식수변동pct_{p}")),
                "offmkt": _num(r.get(f"장외변동pct_{p}")),
                "flag_shares": _flag(r.get(f"플래그_주식수변동_{p}")),
                "flag_offmkt": _flag(r.get(f"플래그_장외변동_{p}")),
                "inst_buy": _flag(r.get(f"플래그_기관동반_{p}")),
                "indiv_sell": _flag(r.get(f"플래그_개인순매도_{p}")),
            })
    return rows


def get_buy_review(market: str = "KOSPI", p: str = DEFAULT_PERIOD,
                   sort: str = DEFAULT_SORT,
                   f3: bool = False, f5: bool = False,
                   pbr: bool = False, inst: bool = False, indiv: bool = False) -> dict:
    market = market if market in MARKET_NAME else "KOSPI"
    if sort not in SORT_NAME or sort == "strength":   # 강도순은 데이터 확보 전 비활성
        sort = DEFAULT_SORT

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
            "sort": sort, "sort_name": SORT_NAME[sort],
            "sorts": SORT_NAME, "filters": filters, "pbr_max": PBR_MAX_DEFAULT}

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

    for x in rows:
        x["warn"] = x["flag_shares"] or x["flag_offmkt"]
    matched = len(rows)
    # 플래그 종목은 순위 유지 + 경고색 카드로 시각 구분
    if sort == "amount":
        rows.sort(key=lambda x: -(x["netbuy_frgn"] if x["netbuy_frgn"] is not None else float("-inf")))
    else:
        rows.sort(key=lambda x: -x["delta"])
    shown = rows[:LIST_LIMIT]

    return {**base, "as_of": mmeta.get("기준일"), "total": total,
            "matched": matched, "shown": len(shown), "rows": shown, "is_empty": False}


def get_stock_detail(code: str, p: str = DEFAULT_PERIOD) -> dict:
    """종목 상세 (뼈대) — 스크리너 행 + detail 샤드 1개 로드.

    detail 샤드: data/processed/detail/detail_{코드 앞2자리}.csv
    차트용 시계열(rows)은 다음 단계에서 사용 — 지금은 로드·범위 확인까지.
    """
    code = str(code).zfill(6)

    # 스크리너 행에서 상단 정보 (양 시장에서 탐색)
    head, market = None, None
    for m in MARKET_NAME:
        rows = _load(m, p) or []
        found = next((x for x in rows if x["code"] == code), None)
        if found:
            head, market = found, m
            break

    # detail 샤드 1개만 읽기
    series = []
    shard = PROCESSED_DIR / "detail" / f"detail_{code[:2]}.csv"
    if shard.exists():
        with open(shard, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if str(r["코드"]).zfill(6) != code:
                    continue
                series.append({
                    "date": r["날짜"],
                    "close": _num(r["종가"]),
                    "frgn_rate": _num(r["외인지분율"]),
                    "frgn": _num(r["외인_억"]),
                    "inst": _num(r["기관_억"]),
                    "indiv": _num(r["개인_억"]),
                })

    # 주 단위 순매수 합산 (ISO 주 — 라벨은 그 주의 마지막 거래일)
    weekly = []
    bucket = {}
    for row in series:
        y, w, _ = __import__("datetime").date.fromisoformat(row["date"]).isocalendar()
        key = (y, w)
        b = bucket.setdefault(key, {"label": row["date"], "frgn": 0.0, "inst": 0.0, "indiv": 0.0})
        b["label"] = row["date"]  # 정렬된 시계열이므로 마지막 날짜가 주말 거래일
        for k, col in (("frgn", "frgn"), ("inst", "inst"), ("indiv", "indiv")):
            v = row[col]
            if v is not None:
                b[k] = round(b[k] + v, 1)
    weekly = [bucket[k] for k in sorted(bucket)]

    return {
        "code": code,
        "found": head is not None,
        "weekly": weekly,
        "market": market,
        "market_name": MARKET_NAME.get(market, ""),
        "p": p if p in PERIOD_NAME else DEFAULT_PERIOD,
        "p_name": PERIOD_NAME.get(p, PERIOD_NAME[DEFAULT_PERIOD]),
        "head": head,
        "naver_url": naver_chart_url(code),
        "series_count": len(series),
        "series_from": series[0]["date"] if series else None,
        "series_to": series[-1]["date"] if series else None,
        "series": series,
    }
