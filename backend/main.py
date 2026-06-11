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


@app.get("/heatmap", response_class=HTMLResponse)
def heatmap(request: Request):
    return templates.TemplateResponse("heatmap.html", {"request": request})


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("backend.main:app", host="0.0.0.0", port=port, reload=True)
