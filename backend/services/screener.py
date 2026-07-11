"""
수급 스크리너 — 전 종목을 투자자 순매수 기준으로 순위화

층 1(스크리너): 특정 기간 동안 개인/외국인/기관이 가장 많이 순매수/순매도한 종목 순위.
  - 실데이터: data/processed/screener_{MARKET}_{PERIOD}_{INVESTOR}.csv (fetch_screener.py 산출물)
  - 없으면 결정론적 데모(샘플) 데이터 (is_demo=True)

의존성: 파이썬 표준 라이브러리만 사용 (pandas 불필요 → 웹 배포 가벼움).
"""
import csv
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"

MARKET_NAME = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}
PERIOD_NAME = {"1d": "1일", "1w": "1주", "1m": "1개월", "3m": "3개월"}
INVESTOR_LIST = ["외국인", "기관", "개인"]

# 데모용 대표 코스피 종목 (실데이터 연결 전 화면 확인용)
_DEMO_KOSPI = [
    ("005930", "삼성전자"), ("000660", "SK하이닉스"), ("373220", "LG에너지솔루션"),
    ("207940", "삼성바이오로직스"), ("005380", "현대차"), ("000270", "기아"),
    ("068270", "셀트리온"), ("105560", "KB금융"), ("005490", "POSCO홀딩스"),
    ("035420", "NAVER"), ("055550", "신한지주"), ("006400", "삼성SDI"),
    ("012330", "현대모비스"), ("051910", "LG화학"), ("035720", "카카오"),
    ("028260", "삼성물산"), ("086790", "하나금융지주"), ("032830", "삼성생명"),
    ("066570", "LG전자"), ("096770", "SK이노베이션"), ("015760", "한국전력"),
    ("259960", "크래프톤"), ("003670", "포스코퓨처엠"), ("000810", "삼성화재"),
    ("011200", "HMM"), ("034020", "두산에너빌리티"), ("316140", "우리금융지주"),
    ("024110", "기업은행"), ("017670", "SK텔레콤"), ("033780", "KT&G"),
    ("009150", "삼성전기"), ("012450", "한화에어로스페이스"), ("138040", "메리츠금융지주"),
    ("003550", "LG"), ("003490", "대한항공"), ("010950", "S-Oil"),
    ("009540", "HD한국조선해양"), ("090430", "아모레퍼시픽"), ("036570", "엔씨소프트"),
    ("352820", "하이브"),
]


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


# ── 데모(샘플) 데이터 ──
def _demo_rows(market, period, investor):
    # 시장/기간/주체별 고정 시드 → 항상 동일
    seed = (hash((market, period, investor)) & 0xFFFF)
    rng = random.Random(seed)
    # 기간이 길수록 순매수 규모 크게
    scale = {"1d": 1, "1w": 3, "1m": 8, "3m": 18}[period]
    rows = []
    for code, name in _DEMO_KOSPI:
        base = rng.gauss(0, 500) * scale  # 억원
        # 대형주는 규모 키우기
        if code in ("005930", "000660", "373220"):
            base *= 2.5
        # 개인은 외국인/기관과 반대 방향 경향
        if investor == "개인":
            base = -base + rng.gauss(0, 200) * scale
        price = round(base * 0.0006 + rng.gauss(0, 1.5) * math.sqrt(scale), 2)
        rows.append({
            "code": code, "name": name,
            "netbuy_eok": round(base, 1),
            "price_change_pct": price,
        })
    return rows


def get_screener(market, period, investor, top=40):
    """스크리너 데이터 반환 (순매수 내림차순 정렬 + 순위 부여)"""
    if market not in MARKET_NAME:
        market = "KOSPI"
    if period not in PERIOD_NAME:
        period = "1w"
    if investor not in INVESTOR_LIST:
        investor = "외국인"

    rows = _load_processed(market, period, investor)
    is_demo = rows is None
    if is_demo:
        rows = _demo_rows(market, period, investor)

    # 순매수 내림차순 정렬
    rows.sort(key=lambda r: (r["netbuy_eok"] is None, -(r["netbuy_eok"] or 0)))
    rows = rows[:top]
    for i, r in enumerate(rows, 1):
        r["rank"] = i

    return {
        "market": market, "market_name": MARKET_NAME[market],
        "period": period, "period_name": PERIOD_NAME[period],
        "investor": investor,
        "is_demo": is_demo,
        "count": len(rows),
        "rows": rows,
    }
