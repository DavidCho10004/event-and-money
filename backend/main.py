"""
Event & Money — FastAPI 웹 서버

실행: python -m backend.main
접속: http://localhost:8000
"""
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
}

PERIOD_ORDER = ["D+1", "D+7", "D+30", "D+180", "D+365"]


@app.get("/", response_class=HTMLResponse)
def index(request: Request, category: str = Query(None)):
    db = SessionLocal()
    query = db.query(Event).order_by(Event.event_date)
    if category:
        query = query.filter(Event.category == category)
    events = query.all()

    categories = db.query(Event.category, func.count()).group_by(Event.category).all()
    cat_list = [(c, CATEGORY_NAMES.get(c, c), n) for c, n in sorted(categories)]

    db.close()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "events": events,
        "categories": cat_list,
        "current_category": category,
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
                "periods": {},
            }
        table[r.symbol]["periods"][r.period] = r

    db.close()
    return templates.TemplateResponse("event_detail.html", {
        "request": request,
        "event": event,
        "table": table,
        "periods": PERIOD_ORDER,
        "category_names": CATEGORY_NAMES,
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
