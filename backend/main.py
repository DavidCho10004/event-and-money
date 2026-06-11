"""
Event & Money — FastAPI 웹 서버

실행: python -m backend.main
접속: http://localhost:8000
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import timedelta

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func

from backend.db.database import SessionLocal
from backend.models import Event, Asset, Price, Return

app = FastAPI(title="Event & Money")

TEMPLATES_DIR = Path(__file__).parent / "templates"
OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

if OUTPUTS_DIR.exists():
    app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")

CATEGORY_NAMES = {
    "war": "전쟁/군사",
    "financial": "금융위기",
    "pandemic": "팬데믹/재난",
    "policy": "정책/통화",
    "industry": "산업/기술",
    # 마이크로 사건 카테고리
    "corporate_scandal": "기업 스캔들",
    "owner_risk": "오너 리스크",
    "product_safety": "제품/안전",
    "labor": "노동/파업",
    "succession": "승계/지배구조",
    "listing_event": "상장/IPO",
    "capital_event": "자본 이벤트",
}

SCALE_NAMES = {"macro": "매크로", "micro": "마이크로"}

PERIOD_ORDER = ["D-30", "D-7", "D-1", "D+1", "D+7", "D+30", "D+180", "D+365"]
# UI에서 D-와 D+ 사이에 시각적 구분선을 그릴 인덱스
PRE_EVENT_COUNT = 3

EVENTS_JSON = Path(__file__).parent.parent / "data" / "events.json"

def _load_summaries():
    with open(EVENTS_JSON, "r", encoding="utf-8") as f:
        return {e["id"]: e.get("summary") for e in json.load(f)}

SUMMARIES = _load_summaries()


@app.get("/", response_class=HTMLResponse)
def index(request: Request, category: str = Query(None), scale: str = Query(None)):
    db = SessionLocal()
    query = db.query(Event).order_by(Event.event_date)
    if category:
        query = query.filter(Event.category == category)
    if scale in ("macro", "micro"):
        query = query.filter(Event.scale == scale)
    events = query.all()

    categories = db.query(Event.category, func.count()).group_by(Event.category).all()
    cat_list = [(c, CATEGORY_NAMES.get(c, c), n) for c, n in sorted(categories)]

    scale_counts = dict(db.query(Event.scale, func.count()).group_by(Event.scale).all())

    db.close()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "events": events,
        "categories": cat_list,
        "current_category": category,
        "current_scale": scale,
        "scale_counts": scale_counts,
        "category_names": CATEGORY_NAMES,
    })


@app.get("/event/{event_id}", response_class=HTMLResponse)
def event_detail(request: Request, event_id: str):
    db = SessionLocal()
    event = db.query(Event).filter(Event.id == event_id).first()

    returns = (
        db.query(Return)
        .filter(Return.event_id == event_id)
        .order_by(Return.symbol, Return.period)
        .all()
    )

    # symbol별로 그룹핑 + asset 정보 매핑
    assets_map = {a.symbol: a for a in db.query(Asset).all()}
    table = {}
    for r in returns:
        if r.symbol not in table:
            asset = assets_map.get(r.symbol)
            table[r.symbol] = {
                "name_ko": asset.name_ko if asset else r.symbol,
                "asset_class": asset.asset_class if asset else "",
                "role": None,  # 'affected' | 'comparable' | None
                "periods": {},
            }
        table[r.symbol]["periods"][r.period] = r

    # ── 마이크로 사건: 3축 분해 + 직접영향/비교군 + CAR(초과수익률) ──
    attribution = None
    car_table = None
    affected = json.loads(event.affected_entities) if (event and event.affected_entities) else []
    comparable = json.loads(event.comparable_universe) if (event and event.comparable_universe) else []

    if event and event.scale == "micro":
        if event.attr_political is not None:
            attribution = {
                "political": event.attr_political,
                "corporate": event.attr_corporate,
                "macro": event.attr_macro,
                "rationale": event.attr_rationale,
            }

        # 직접영향 → 비교군 → 나머지 순으로 테이블 재정렬 + role 태그
        for s in affected:
            if s in table:
                table[s]["role"] = "affected"
        for s in comparable:
            if s in table:
                table[s]["role"] = "comparable"
        order = (
            [s for s in affected if s in table]
            + [s for s in comparable if s in table]
            + [s for s in table if s not in affected and s not in comparable]
        )
        table = {s: table[s] for s in order}

        # CAR = 직접영향 평균 수익률 − 비교군 평균 수익률 (시점별)
        def _avg_return(symbols, period):
            vals = [
                float(table[s]["periods"][period].return_pct)
                for s in symbols
                if s in table and period in table[s]["periods"]
            ]
            return sum(vals) / len(vals) if vals else None

        if affected and comparable:
            car_table = []
            for p in PERIOD_ORDER:
                a = _avg_return(affected, p)
                c = _avg_return(comparable, p)
                car_table.append({
                    "period": p,
                    "affected": a,
                    "comparable": c,
                    "car": (a - c) if (a is not None and c is not None) else None,
                })
            # 데이터가 하나도 없으면 섹션 자체를 숨김
            if all(row["car"] is None for row in car_table):
                car_table = None

    db.close()
    return templates.TemplateResponse("event_detail.html", {
        "request": request,
        "event": event,
        "table": table,
        "periods": PERIOD_ORDER,
        "pre_event_count": PRE_EVENT_COUNT,
        "category_names": CATEGORY_NAMES,
        "summary": SUMMARIES.get(event_id),
        "attribution": attribution,
        "car_table": car_table,
    })


@app.get("/api/timeline/{event_id}")
def api_timeline(event_id: str):
    """마이크로 사건의 직접영향·비교군 자산 가격 추이를 한 번에 반환 (인라인 차트용).

    윈도우:
      - 기본: 사건일 -45 ~ +30
      - announce_date가 사건일 7일 이상 앞이면 announce_date - 7부터 시작 (트랙C 사전반응)
    값: 사건일(D=0) 가격 = 0%, 이후 일별 누적수익률 (%)
    """
    db = SessionLocal()
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        db.close()
        return JSONResponse({"error": "event not found"}, status_code=404)

    affected = json.loads(event.affected_entities) if event.affected_entities else []
    comparable = json.loads(event.comparable_universe) if event.comparable_universe else []
    all_symbols = list(dict.fromkeys(affected + comparable))  # 순서 유지 + 중복 제거
    if not all_symbols:
        db.close()
        return JSONResponse({
            "event_id": event_id,
            "event_date": str(event.event_date),
            "announce_date": str(event.announce_date) if event.announce_date else None,
            "series": [],
        })

    # 윈도우 결정
    default_start = event.event_date - timedelta(days=45)
    if event.announce_date and event.announce_date < event.event_date - timedelta(days=7):
        start = event.announce_date - timedelta(days=7)
    else:
        start = default_start
    end = event.event_date + timedelta(days=30)

    assets_map = {a.symbol: a for a in db.query(Asset).filter(Asset.symbol.in_(all_symbols)).all()}

    series = []
    for symbol in all_symbols:
        prices = (
            db.query(Price)
            .filter(Price.symbol == symbol, Price.trade_date >= start, Price.trade_date <= end)
            .order_by(Price.trade_date)
            .all()
        )
        if not prices:
            continue

        # 기준가 = 사건일 당일 또는 직전 거래일 (정상화 기준)
        base = None
        for p in prices:
            if p.trade_date <= event.event_date:
                base = float(p.adj_close)
        if not base:
            continue

        asset = assets_map.get(symbol)
        series.append({
            "symbol": symbol,
            "name": asset.name_ko if asset else symbol,
            "role": "affected" if symbol in affected else "comparable",
            "data": [
                {"date": str(p.trade_date), "value": round((float(p.adj_close) / base - 1) * 100, 2)}
                for p in prices
            ],
        })

    db.close()
    return JSONResponse({
        "event_id": event_id,
        "event_date": str(event.event_date),
        "announce_date": str(event.announce_date) if event.announce_date else None,
        "series": series,
    })


@app.get("/api/prices/{event_id}/{symbol}")
def api_prices(event_id: str, symbol: str):
    """사건 기준 D-30~D+365 가격 데이터를 JSON으로 반환"""
    db = SessionLocal()
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        db.close()
        return JSONResponse({"error": "event not found"}, status_code=404)

    asset = db.query(Asset).filter(Asset.symbol == symbol).first()

    start = event.event_date - timedelta(days=45)
    end = event.event_date + timedelta(days=400)

    prices = (
        db.query(Price)
        .filter(Price.symbol == symbol, Price.trade_date >= start, Price.trade_date <= end)
        .order_by(Price.trade_date)
        .all()
    )

    # 기준가 = event_date 당일 또는 직전 거래일
    base_price = None
    for p in prices:
        if p.trade_date <= event.event_date:
            base_price = float(p.adj_close)

    if not base_price:
        base_price = float(prices[0].adj_close) if prices else 1

    data = {
        "event_id": event_id,
        "event_date": str(event.event_date),
        "event_name": event.name_ko,
        "symbol": symbol,
        "asset_name": asset.name_ko if asset else symbol,
        "yahoo_symbol": asset.yahoo_symbol if asset else symbol,
        "dates": [str(p.trade_date) for p in prices],
        "returns": [round((float(p.adj_close) / base_price - 1) * 100, 2) for p in prices],
        "prices": [round(float(p.adj_close), 2) for p in prices],
        "base_price": round(base_price, 2),
        "base_date": str(event.event_date),
    }
    db.close()
    return JSONResponse(data)


SUMMARY_ASSETS = [
    "^GSPC", "^IXIC", "^KS11", "^N225",
    "CL=F", "GC=F", "DX-Y.NYB",
    "USDKRW=X", "^TNX", "^VIX",
]


def _heatmap_cell_class(v):
    """수익률(%)을 5단계 빨강/파랑 색상 클래스로 매핑. 한국식: 상승=빨강."""
    if v is None:
        return "cell-na"
    if v > 20: return "cell-pos-5"
    if v > 10: return "cell-pos-4"
    if v > 5:  return "cell-pos-3"
    if v > 2:  return "cell-pos-2"
    if v > 0:  return "cell-pos-1"
    if v > -2:  return "cell-neg-1"
    if v > -5:  return "cell-neg-2"
    if v > -10: return "cell-neg-3"
    if v > -20: return "cell-neg-4"
    return "cell-neg-5"


@app.get("/heatmap", response_class=HTMLResponse)
def heatmap(request: Request,
            scale: str = Query(None),
            period: str = Query("D+30"),
            assets: str = Query("summary")):
    """인터랙티브 히트맵 (HTML 테이블).

    필터: scale (all/macro/micro), period (D-30~D+365), assets (summary/all)
    """
    if period not in PERIOD_ORDER:
        period = "D+30"
    if assets not in ("summary", "all"):
        assets = "summary"

    db = SessionLocal()

    # 사건 (최신순, scale 필터)
    eq = db.query(Event).order_by(Event.event_date.desc())
    if scale in ("macro", "micro"):
        eq = eq.filter(Event.scale == scale)
    events = eq.all()

    # 자산 (asset_class 그룹별 정렬)
    aq = db.query(Asset).order_by(Asset.asset_class, Asset.symbol)
    asset_list = aq.all()
    if assets == "summary":
        asset_list = [a for a in asset_list if a.symbol in SUMMARY_ASSETS]
        # SUMMARY_ASSETS 순서 보존
        order = {s: i for i, s in enumerate(SUMMARY_ASSETS)}
        asset_list.sort(key=lambda a: order.get(a.symbol, 999))

    # 수익률 매트릭스 한 번에 로드
    event_ids = [e.id for e in events]
    asset_symbols = [a.symbol for a in asset_list]
    returns = []
    if event_ids and asset_symbols:
        returns = (
            db.query(Return)
            .filter(
                Return.period == period,
                Return.event_id.in_(event_ids),
                Return.symbol.in_(asset_symbols),
            )
            .all()
        )
    matrix = {(r.event_id, r.symbol): float(r.return_pct) for r in returns}

    scale_counts = dict(db.query(Event.scale, func.count()).group_by(Event.scale).all())

    db.close()

    rows = []
    for e in events:
        cells = []
        for a in asset_list:
            v = matrix.get((e.id, a.symbol))
            cells.append({"value": v, "cls": _heatmap_cell_class(v)})
        rows.append({"event": e, "cells": cells})

    return templates.TemplateResponse("heatmap.html", {
        "request": request,
        "rows": rows,
        "asset_list": asset_list,
        "scale": scale,
        "period": period,
        "periods": PERIOD_ORDER,
        "assets_mode": assets,
        "scale_counts": scale_counts,
        "category_names": CATEGORY_NAMES,
    })


def _default_compare_symbol(event, returns_by_symbol):
    """비교 화면에서 자동 선택될 대표 자산.

    우선순위:
      1) 마이크로: affected_entities[0] (수익률 데이터가 있을 때만)
      2) 데이터 있는 자산 중 우선순위 순 (^GSPC > ^KS11 > 첫 자산)
    """
    if event.affected_entities:
        affected = json.loads(event.affected_entities)
        for s in affected:
            if s in returns_by_symbol:
                return s
    for preferred in ("^GSPC", "^KS11"):
        if preferred in returns_by_symbol:
            return preferred
    return next(iter(returns_by_symbol), None)


def _event_compare_data(db, event_id, override_symbol=None):
    """단일 사건의 비교용 데이터 묶음.

    반환: dict(event, table_row(symbol+periods 매핑), default_symbol, attribution)
    """
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event:
        return None

    returns = (
        db.query(Return)
        .filter(Return.event_id == event_id)
        .order_by(Return.symbol, Return.period)
        .all()
    )
    returns_by_symbol = {}
    for r in returns:
        returns_by_symbol.setdefault(r.symbol, {})[r.period] = r

    symbol = override_symbol if override_symbol in returns_by_symbol \
        else _default_compare_symbol(event, returns_by_symbol)

    asset = db.query(Asset).filter(Asset.symbol == symbol).first() if symbol else None
    periods_data = returns_by_symbol.get(symbol, {}) if symbol else {}

    attribution = None
    if event.scale == "micro" and event.attr_political is not None:
        attribution = {
            "political": event.attr_political,
            "corporate": event.attr_corporate,
            "macro": event.attr_macro,
            "rationale": event.attr_rationale,
        }

    return {
        "event": event,
        "symbol": symbol,
        "asset_name": asset.name_ko if asset else (symbol or ""),
        "yahoo_symbol": asset.yahoo_symbol if asset else symbol,
        "periods_data": periods_data,
        "attribution": attribution,
        "available_symbols": sorted(returns_by_symbol.keys()),
    }


@app.get("/compare", response_class=HTMLResponse)
def compare(request: Request,
            a: str = Query(None), b: str = Query(None),
            sa: str = Query(None), sb: str = Query(None)):
    """두 사건을 나란히 비교. 쿼리: a, b (event_id) / sa, sb (각 사건 대표 자산 override)"""
    db = SessionLocal()
    events_all = db.query(Event).order_by(Event.event_date).all()

    side_a = _event_compare_data(db, a, sa) if a else None
    side_b = _event_compare_data(db, b, sb) if b else None

    db.close()
    return templates.TemplateResponse("compare.html", {
        "request": request,
        "events_all": events_all,
        "side_a": side_a,
        "side_b": side_b,
        "a": a, "b": b,
        "periods": PERIOD_ORDER,
        "pre_event_count": PRE_EVENT_COUNT,
        "category_names": CATEGORY_NAMES,
    })


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=True)
