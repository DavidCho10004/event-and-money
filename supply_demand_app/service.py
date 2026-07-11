"""
수급 분석 데이터 서비스 (독립 앱) — 투자자별 순매수 시계열 + 상관계수

데이터 우선순위:
  1) {DATA_DIR}/supply_demand_{MARKET}_{FREQ}.csv 가 있으면 그것을 읽음 (실데이터)
     - DATA_DIR 은 환경변수 SUPPLY_DEMAND_DATA_DIR, 없으면 repo의 data/processed 를 자동 탐색
     - 실데이터는 로컬에서 scripts/fetch_supply_demand.py + analyze_supply_demand.py 로 생성/커밋
  2) 없으면 결정론적 데모(샘플) 데이터 생성 (is_demo=True) → UI 확인용

의존성: 파이썬 표준 라이브러리만 사용 (pandas 불필요 → 배포 가볍게).
"""
import os
import csv
import math
import random
from pathlib import Path
from datetime import date, timedelta

APP_DIR = Path(__file__).resolve().parent


def _resolve_data_dir():
    """실데이터(processed CSV) 폴더를 탐색. 없으면 None(→데모)."""
    env = os.environ.get("SUPPLY_DEMAND_DATA_DIR")
    candidates = []
    if env:
        candidates.append(Path(env))
    # 같은 저장소에 얹혀 있을 때: ../data/processed
    candidates.append(APP_DIR.parent / "data" / "processed")
    # 앱 폴더 안에 별도로 둘 때: ./data/processed
    candidates.append(APP_DIR / "data" / "processed")
    for c in candidates:
        if c.exists():
            return c
    return None


DATA_DIR = _resolve_data_dir()

INVESTORS = ["개인", "외국인", "기관"]
MARKET_NAME = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}
FREQ_NAME = {"D": "일간", "W": "주간", "M": "월간"}


# ── 상관계수 (stdlib 구현) ──
def _pearson(x, y):
    n = len(x)
    if n < 3:
        return None
    mx, my = sum(x) / n, sum(y) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    vx = math.sqrt(sum((a - mx) ** 2 for a in x))
    vy = math.sqrt(sum((b - my) ** 2 for b in y))
    if vx == 0 or vy == 0:
        return None
    return cov / (vx * vy)


def _rank(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _spearman(x, y):
    if len(x) < 3:
        return None
    return _pearson(_rank(x), _rank(y))


def _correlations(netbuy, returns):
    rows = []
    for inv in INVESTORS:
        pairs = [(nb, r) for nb, r in zip(netbuy[inv], returns) if r is not None]
        if len(pairs) < 3:
            rows.append({"investor": inv, "n": len(pairs), "pearson": None, "spearman": None})
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        pe, sp = _pearson(xs, ys), _spearman(xs, ys)
        rows.append({
            "investor": inv, "n": len(pairs),
            "pearson": round(pe, 3) if pe is not None else None,
            "spearman": round(sp, 3) if sp is not None else None,
        })
    return rows


# ── 일별 → 주/월 집계 ──
def _bucket_key(d, freq):
    if freq == "W":
        y, w, _ = d.isocalendar()
        return (y, w)
    if freq == "M":
        return (d.year, d.month)
    return d


def _aggregate(daily, freq):
    if freq == "D":
        groups = [(row["date"], [row]) for row in daily]
    else:
        groups_map, order = {}, []
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
        dates.append(rows[-1]["date"].isoformat())
        close.append(rows[-1]["close"])
        for inv in INVESTORS:
            netbuy[inv].append(sum(r[inv] for r in rows) / 1e8)  # 원 → 억원

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

    return {"dates": dates, "close": [round(c, 2) for c in close],
            "netbuy": netbuy, "cum": cum, "returns": returns}


# ── 실데이터 로드 ──
def _load_processed_daily(market):
    if DATA_DIR is None:
        return None
    path = DATA_DIR / f"supply_demand_{market}_D.csv"
    if not path.exists():
        return None
    daily = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                d = date.fromisoformat(r["날짜"][:10])
                daily.append({"date": d, "close": float(r["종가"]),
                              "개인": float(r["개인"]) * 1e8,
                              "외국인": float(r["외국인"]) * 1e8,
                              "기관": float(r["기관"]) * 1e8})
            except (KeyError, ValueError):
                continue
    return daily or None


# ── 데모(샘플) 데이터 ──
def _demo_daily(market):
    rng = random.Random(hash(market) & 0xFFFF)
    days = []
    d = date(2022, 1, 3)
    while d <= date(2024, 12, 31):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)

    base = 2500 if market == "KOSPI" else 850
    daily, logret_cum = [], 0.0
    for d in days:
        foreign = rng.gauss(0, 3000e8)
        inst = rng.gauss(0, 2000e8)
        indiv = -(foreign + inst) + rng.gauss(0, 1000e8)
        ret = 4e-14 * foreign + rng.gauss(0, 0.008)
        logret_cum += ret
        daily.append({"date": d, "close": base * math.exp(logret_cum),
                      "개인": indiv, "외국인": foreign, "기관": inst})
    return daily


def get_supply_demand(market, freq):
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
        "market": market, "market_name": MARKET_NAME[market],
        "freq": freq, "freq_name": FREQ_NAME[freq],
        "is_demo": is_demo,
        "dates": agg["dates"], "close": agg["close"],
        "netbuy": agg["netbuy"], "cum": agg["cum"], "corr": corr,
    }
