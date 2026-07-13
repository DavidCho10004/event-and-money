"""
수급 스크리너 — 전 종목을 투자자 순매수 기준으로 순위화

층 1(스크리너): 특정 기간 동안 개인/외국인/기관이 가장 많이 순매수/순매도한 종목 순위.
  - 실데이터: data/processed/screener_{MARKET}_{PERIOD}_{INVESTOR}.csv (fetch_screener.py 산출물)
  - 없으면 빈 상태 반환 (합성 데이터 생성 금지)

의존성: 파이썬 표준 라이브러리만 사용 (pandas 불필요 → 웹 배포 가벼움).
"""
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"

MARKET_NAME = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}
PERIOD_NAME = {"1d": "1일", "1w": "1주", "1m": "1개월", "3m": "3개월"}
INVESTOR_LIST = ["외국인", "기관", "개인"]


# ── 실데이터 로드 ──
def _load_processed(market, period, investor):
    path = PROCESSED_DIR / f"screener_{market}_{period}_{investor}.csv"
    if not path.exists():
        return None
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                rows.append({
                    "code": str(r["종목코드"]).zfill(6),
                    "name": r["종목명"],
                    "netbuy_eok": round(float(r["순매수"]) / 1e8, 1),  # 원 → 억원
                    "price_change_pct": float(r["등락률"]) if r.get("등락률") not in (None, "") else None,
                })
            except (KeyError, ValueError):
                continue
    return rows or None


def get_screener(market, period, investor, top=40):
    """스크리너 데이터 반환 (순매수 내림차순 정렬 + 순위 부여)"""
    if market not in MARKET_NAME:
        market = "KOSPI"
    if period not in PERIOD_NAME:
        period = "1w"
    if investor not in INVESTOR_LIST:
        investor = "외국인"

    rows = _load_processed(market, period, investor)
    # 실데이터가 없으면 빈 상태로 반환 — 합성/데모 데이터를 생성하지 않는다 (CLAUDE.md 규칙)
    if rows is None:
        return {
            "market": market, "market_name": MARKET_NAME[market],
            "period": period, "period_name": PERIOD_NAME[period],
            "investor": investor,
            "is_empty": True, "count": 0, "rows": [],
        }

    # 순매수 내림차순 정렬
    rows.sort(key=lambda r: (r["netbuy_eok"] is None, -(r["netbuy_eok"] or 0)))
    rows = rows[:top]
    for i, r in enumerate(rows, 1):
        r["rank"] = i

    return {
        "market": market, "market_name": MARKET_NAME[market],
        "period": period, "period_name": PERIOD_NAME[period],
        "investor": investor,
        "is_empty": False,
        "count": len(rows),
        "rows": rows,
    }
