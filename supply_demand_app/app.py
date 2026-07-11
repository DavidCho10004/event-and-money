"""
수급 분석 (Supply & Demand) — 독립 실행 웹앱

Event & Money 본체와 분리된 별도 프로그램. 코스피/코스닥 투자자별 순매수(개인·외국인·기관)를
일/주/월 시계열로 보여주고 지수 등락과의 관계를 확인한다.

로컬 실행:
    pip install -r supply_demand_app/requirements.txt
    python -m uvicorn supply_demand_app.app:app --reload --port 8090
    → http://localhost:8090

배포(Railway 별도 서비스): supply_demand_app/ 을 root 로 하는 서비스를 새로 만들면 됨.
"""
import sys
from pathlib import Path

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

from service import get_supply_demand  # noqa: E402

app = FastAPI(title="수급 분석 — Supply & Demand")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
def index(request: Request,
          market: str = Query("KOSPI"),
          freq: str = Query("W")):
    """수급 시계열 + 지수 등락 뷰어"""
    data = get_supply_demand(market, freq)
    return templates.TemplateResponse("index.html", {"request": request, "data": data})


@app.get("/api/supply-demand")
def api_supply_demand(market: str = Query("KOSPI"), freq: str = Query("W")):
    """수급 시계열 + 상관계수 JSON"""
    return JSONResponse(get_supply_demand(market, freq))


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.environ.get("PORT", 8090))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
