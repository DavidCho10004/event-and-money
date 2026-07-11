"""
수급 분석 웹 서비스 — 투자자별 순매수 시계열 + 상관계수 제공

데이터 우선순위:
  1) data/processed/supply_demand_{MARKET}_{FREQ}.csv 가 있으면 그것을 읽음 (실데이터)
     → scripts/fetch_supply_demand.py + analyze_supply_demand.py 로 로컬 생성/커밋
  2) 없으면 결정론적 데모(샘플) 데이터를 생성 (is_demo=True) → 웹에서 UI만 확인용

의존성: 파이썬 표준 라이브러리만 사용 (pandas/matplotlib 불필요 → Railway 배포 가볍게).
"""
import csv
import math
import random
from pathlib import Path
from datetime import date, timedelta

ROOT = Path(__file__).resolve().parent.parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"

INVESTORS = ["개인", "외국인", "기관"]
MARKET_NAME = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}
FREQ_NAME = {"D": "일간", "W": "주간", "M": "월간"}


# ──────────────────────────────────────────────────────────
# 상관계수 (stdlib 구현)
# ──────────────────────────────────────────────────────────
def _pearson(x, y):
    n = len(x)
    if n < 3:
        return None
    mx = sum(x) / n
    my = sum(y) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    vx = math.sqrt(sum((a - mx) ** 2 for a in x))
    vy = math.sqrt(sum((b - my) ** 2 for b in y))
    if vx == 0 or vy == 0:
        return None
    return cov / (vx * vy)


def _rank(values):
    """평균 순위(동점은 평균 처리)"""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1  # 1-based 평균 순위
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(x, y):
    if len(x) < 3:
        return None
    return _pearson(_rank(x), _rank(y))


def _correlations(netbuy, returns):
    """투자자별 순매수 vs 지수 수익률 상관표"""
    rows = []
    for inv in INVESTORS:
        pairs = [(nb, r) for nb, r in zip(netbuy[inv], returns) if r is not None]
        if len(pairs) < 3:
            rows.append({"investor": inv, "n": len(pairs), "pearson": None, "spearman": None})
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        pe = _pearson(xs, ys)
        sp = _spearman(xs, ys)
        rows.append({
            "investor": inv,
            "n": len(pairs),
            "pearson": round(pe, 3) if pe is not None else None,
            "spearman": round(sp, 3) if sp is not None else None,
        })
    return rows


# ──────────────────────────────────────────────────────────
# 공통: 일별 → 주/월 집계 + 누적/수익률
# ──────────────────────────────────────────────────────────
def _bucket_key(d, freq):
    if freq == "W":
        y, w, _ = d.isocalendar()
        return (y, w)
    if freq == "M":
        return (d.year, d.month)
    return d  # 일간은 날짜 자체


def _aggregate(daily, freq):
    """daily: list of dict(date, close, 개인, 외국인, 기관) [원 단위] → freq 집계 결과"""
    if freq == "D":
        buckets = [(row["date"], row) for row in daily]
        groups = [(k, [row]) for k, row in buckets]
    else:
        groups_map = {}
        order = []
        for row in daily:
            k = _bucket_key(row["date"], freq)
            if k not in groups_map:
                groups_map[k] = []
                order.append(k)
            groups_map[k].append(row)
        groups = [(k, groups_map[k]) for k in order]

    dates, close = [], []
    netbuy = {inv: [] for inv in INVESTORS}
    for _, rows in groups:
        dates.append(rows[-1]["date"].isoformat())  # 마지막 거래일
        close.append(rows[-1]["close"])             # 종가 = 마지막값
        for inv in INVESTORS:
            netbuy[inv].append(sum(r[inv] for r in rows) / 1e8)  # 순매수 합계 → 억원

    # 누적 순매수 + 수익률
    cum = {inv: [] for inv in INVESTORS}
    for inv in INVESTORS:
        s = 0.0
        for v in netbuy[inv]:
            s += v
            cum[inv].append(round(s, 1))
        netbuy[inv] = [round(v, 1) for v in netbuy[inv]]
    returns = [None]
    for i in range(1, len(close)):
        prev = close[i - 1]
        returns.append(round((close[i] / prev - 1) * 100, 3) if prev else None)

    return {
        "dates": dates,
        "close": [round(c, 2) for c in close],
        "netbuy": netbuy,
        "cum": cum,
        "returns": returns,
    }


# ──────────────────────────────────────────────────────────
# 1) 실데이터 로드 (processed CSV)
# ──────────────────────────────────────────────────────────
def _load_processed_daily(market):
    """일별 processed CSV를 원 단위 daily 리스트로 복원 (억원 → 원 역변환)"""
    path = PROCESSED_DIR / f"supply_demand_{market}_D.csv"
    if not path.exists():
        return None
    daily = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                d = date.fromisoformat(r["날짜"][:10])
                daily.append({
                    "date": d,
                    "close": float(r["종가"]),
                    # processed는 억원 단위 → 원으로 되돌려 집계 로직 재사용
                    "개인": float(r["개인"]) * 1e8,
                    "외국인": float(r["외국인"]) * 1e8,
                    "기관": float(r["기관"]) * 1e8,
                })
            except (KeyError, ValueError):
                continue
    return daily or None


# ──────────────────────────────────────────────────────────
# 2) 데모(샘플) 데이터 — 결정론적 생성
# ──────────────────────────────────────────────────────────
def _demo_daily(market):
    """UI 확인용 합성 일별 데이터. 시장별 고정 시드로 항상 동일하게 생성."""
    rng = random.Random(hash(market) & 0xFFFF)
    start = date(2022, 1, 3)
    days = []
    d = start
    while d <= date(2024, 12, 31):
        if d.weekday() < 5:  # 평일만
            days.append(d)
        d += timedelta(days=1)

    base = 2500 if market == "KOSPI" else 850  # 코스피/코스닥 대략적 레벨
    daily = []
    logret_cum = 0.0
    for d in days:
        foreign = rng.gauss(0, 3000e8)
        inst = rng.gauss(0, 2000e8)
        indiv = -(foreign + inst) + rng.gauss(0, 1000e8)  # 개인은 반대 방향 경향
        # 외국인 순매수가 지수를 이끄는 구조 (데모용 명시적 상관)
        ret = 4e-14 * foreign + rng.gauss(0, 0.008)
        logret_cum += ret
        close = base * math.exp(logret_cum)
        daily.append({"date": d, "close": close,
                      "개인": indiv, "외국인": foreign, "기관": inst})
    return daily


# ──────────────────────────────────────────────────────────
# 공개 함수
# ──────────────────────────────────────────────────────────
def get_supply_demand(market, freq):
    """웹 API용 수급 데이터 묶음 반환"""
    if market not in MARKET_NAME:
        market = "KOSPI"
    if freq not in FREQ_NAME:
        freq = "W"

    daily = _load_processed_daily(market)
    is_demo = daily is None
    if is_demo:
        daily = _demo_daily(market)

    agg = _aggregate(daily, freq)
    corr = _correlations(agg["netbuy"], agg["returns"])

    return {
        "market": market,
        "market_name": MARKET_NAME[market],
        "freq": freq,
        "freq_name": FREQ_NAME[freq],
        "is_demo": is_demo,
        "dates": agg["dates"],
        "close": agg["close"],
        "netbuy": agg["netbuy"],
        "cum": agg["cum"],
        "corr": corr,
    }
