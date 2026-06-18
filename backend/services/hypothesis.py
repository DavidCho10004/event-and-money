"""
5대 가설 검증 엔진 — Return 테이블에서 직접 통계 산출

원칙 (CLAUDE.md 역할 2/5):
- 모든 결론에 수치 근거 (n, 평균, 중앙값 등)
- 데이터가 없으면 "미확보"로 명시, 추정값 금지
- 표본이 작으면 "참고용"으로 표시

scipy 의존 없이 표준 라이브러리로 계산 (웹 배포 경량 유지).
"""
import statistics
from collections import defaultdict
from datetime import timedelta

from backend.models import Event, Price, Return

# 기준 자산: 글로벌 대표 지수
BENCH = "^GSPC"
KOSPI = "^KS11"
OIL = "CL=F"


def _ret(matrix, event_id, symbol, period):
    """matrix에서 수익률 조회 (없으면 None)"""
    return matrix.get((event_id, symbol, period))


def _load_matrix(db):
    """(event_id, symbol, period) → return_pct 매트릭스 로드"""
    matrix = {}
    for r in db.query(Return).all():
        matrix[(r.event_id, r.symbol, r.period)] = float(r.return_pct)
    return matrix


def _mean(xs):
    return round(statistics.mean(xs), 2) if xs else None


def _median(xs):
    return round(statistics.median(xs), 2) if xs else None


def _pearson(xs, ys):
    """Pearson 상관계수 (표준 라이브러리)"""
    if len(xs) < 3:
        return None
    try:
        return round(statistics.correlation(xs, ys), 3)
    except statistics.StatisticsError:
        return None


def h1_recovery_speed(db, matrix):
    """가설 1: 시장 회복 속도가 시대별로 빨라지고 있는가?

    방법: 매크로 사건 중 ^GSPC D+30 < 0 (충격 확인된) 사건을
    시대 3구간으로 나눠 D+180 시점 회복률(D+180 > 0 비율)과 평균 비교.
    """
    eras = [("~1989", None, 1990), ("1990~2009", 1990, 2010), ("2010~", 2010, None)]
    events = db.query(Event).filter(Event.scale == "macro").all()

    rows = []
    for era_name, y_from, y_to in eras:
        shocked = []
        for e in events:
            y = e.event_date.year
            if (y_from and y < y_from) or (y_to and y >= y_to):
                continue
            d30 = _ret(matrix, e.id, BENCH, "D+30")
            d180 = _ret(matrix, e.id, BENCH, "D+180")
            if d30 is not None and d30 < 0 and d180 is not None:
                shocked.append((e, d30, d180))

        n = len(shocked)
        recovered = [s for s in shocked if s[2] > 0]
        rows.append({
            "era": era_name,
            "n": n,
            "recovered_n": len(recovered),
            "recovered_pct": round(len(recovered) / n * 100) if n else None,
            "avg_d30": _mean([s[1] for s in shocked]),
            "avg_d180": _mean([s[2] for s in shocked]),
            "events": [f"{s[0].id} {s[0].name_ko[:18]}" for s in shocked],
        })

    valid = [r for r in rows if r["n"] >= 2]
    if len(valid) >= 2 and all(r["recovered_pct"] is not None for r in valid):
        trend_up = valid[-1]["recovered_pct"] >= valid[0]["recovered_pct"]
        verdict = "지지" if trend_up else "기각 방향"
    else:
        verdict = "데이터 부족"

    return {"rows": rows, "verdict": verdict}


def h2_energy_dependence(db, matrix):
    """가설 2: 에너지 공급 차질 동반 사건만 장기 영향을 미치는가?

    방법: energy_impact True/False 그룹의 ^GSPC D+180·D+365 평균/중앙값 비교.
    """
    events = db.query(Event).filter(Event.scale == "macro").all()
    groups = {"energy": [], "non_energy": []}
    for e in events:
        key = "energy" if e.energy_impact else "non_energy"
        groups[key].append(e)

    out = {}
    for key, evts in groups.items():
        d180 = [v for e in evts if (v := _ret(matrix, e.id, BENCH, "D+180")) is not None]
        d365 = [v for e in evts if (v := _ret(matrix, e.id, BENCH, "D+365")) is not None]
        out[key] = {
            "n": len(evts),
            "n_d365": len(d365),
            "mean_d180": _mean(d180),
            "median_d180": _median(d180),
            "mean_d365": _mean(d365),
            "median_d365": _median(d365),
        }

    e_m, n_m = out["energy"]["mean_d365"], out["non_energy"]["mean_d365"]
    if e_m is not None and n_m is not None:
        verdict = "지지" if e_m < n_m else "기각 방향"
    else:
        verdict = "데이터 부족"
    return {"groups": out, "verdict": verdict}


def _daily_returns_series(db, symbol, start, end):
    """일별 로그 수익률 시리즈 반환 (날짜 정렬, 결측 무시).

    반환: list[(date, log_return)]
    """
    import math
    rows = (
        db.query(Price)
        .filter(Price.symbol == symbol,
                Price.trade_date >= start,
                Price.trade_date <= end)
        .order_by(Price.trade_date)
        .all()
    )
    series = []
    prev = None
    for p in rows:
        price = float(p.adj_close)
        if prev is not None and prev > 0 and price > 0:
            series.append((p.trade_date, math.log(price / prev)))
        prev = price
    return series


def _ccf(xs, ys, max_lag):
    """교차상관함수 (Cross-Correlation Function).

    lag k에서 corr(xs[t], ys[t+k]) 계산. k>0 = xs가 ys보다 k일 선행.
    표준 라이브러리만으로.
    반환: dict[lag] = (corr, n_valid)
    """
    out = {}
    n = min(len(xs), len(ys))
    for lag in range(-max_lag, max_lag + 1):
        # xs[i] vs ys[i + lag]
        if lag >= 0:
            a = xs[: n - lag]
            b = ys[lag : n]
        else:
            a = xs[-lag : n]
            b = ys[: n + lag]
        if len(a) < 5:
            out[lag] = (None, len(a))
            continue
        try:
            out[lag] = (round(statistics.correlation(a, b), 3), len(a))
        except statistics.StatisticsError:
            out[lag] = (None, len(a))
    return out


def h3_chain_lag(db, matrix):
    """가설 3 (정밀): 1차→2차 변인 사이의 일별 lead-lag.

    방법:
    - 각 매크로 사건 D-30 ~ D+30 윈도우에서 유가(CL=F)·S&P500(^GSPC) 일별 로그수익률 추출
    - 각 사건별 CCF 계산 (lag -10 ~ +10일)
    - 모든 사건 평균 → 평균 best lag 식별
    - lag > 0 = 유가가 주가에 선행 (가설 지지)
    """
    events = db.query(Event).filter(Event.scale == "macro").all()
    max_lag = 10
    per_event = []   # 사건별 (best_lag, best_corr)
    accum = {lag: [] for lag in range(-max_lag, max_lag + 1)}

    for e in events:
        start = e.event_date - timedelta(days=45)
        end = e.event_date + timedelta(days=45)

        oil_series = _daily_returns_series(db, OIL, start, end)
        spx_series = _daily_returns_series(db, BENCH, start, end)
        # 두 시리즈를 공통 거래일 기준으로 정렬
        oil_map = dict(oil_series)
        spx_map = dict(spx_series)
        common_dates = sorted(set(oil_map.keys()) & set(spx_map.keys()))
        if len(common_dates) < 15:
            continue

        xs = [oil_map[d] for d in common_dates]  # 유가
        ys = [spx_map[d] for d in common_dates]  # S&P
        ccf = _ccf(xs, ys, max_lag)

        # 사건별 가장 강한 상관 lag
        best = None
        for lag, (corr, n_v) in ccf.items():
            if corr is None:
                continue
            if best is None or abs(corr) > abs(best[1]):
                best = (lag, corr)
        if best is not None:
            per_event.append({"id": e.id, "year": e.event_date.year,
                              "best_lag": best[0], "best_corr": best[1]})

        for lag, (corr, _) in ccf.items():
            if corr is not None:
                accum[lag].append(corr)

    # 모든 사건 평균 상관 (lag별)
    avg_ccf = []
    for lag in range(-max_lag, max_lag + 1):
        avg_ccf.append({"lag": lag,
                        "avg_corr": _mean(accum[lag]),
                        "n": len(accum[lag])})

    # 모든 사건 평균에서 가장 강한 양의 상관 lag 찾기
    candidates = [r for r in avg_ccf if r["avg_corr"] is not None and r["n"] >= 5]
    if candidates:
        peak = max(candidates, key=lambda r: r["avg_corr"])
    else:
        peak = None

    # 판정: peak lag > 0 + 상관이 +0.15 이상 + 표본 충분
    if peak and peak["avg_corr"] is not None:
        if peak["avg_corr"] > 0.15 and peak["lag"] > 0:
            verdict = "지지"
        elif peak["avg_corr"] > 0.1:
            verdict = "혼재"
        else:
            verdict = "기각 방향"
    else:
        verdict = "데이터 부족"

    return {
        "n_events": len(per_event),
        "max_lag": max_lag,
        "avg_ccf": avg_ccf,             # 차트용 (lag, avg_corr, n)
        "peak": peak,                    # 가장 강한 양의 상관
        "per_event": per_event,          # 사건별 best lag
        "verdict": verdict,
        "note": ("lag>0 = 유가가 주가에 선행 (가설 지지). "
                 "lag=0 = 동시 반응. lag<0 = 주가가 유가에 선행. "
                 "표본은 사건별 D±45 거래일 일별 로그수익률."),
    }


def h4_learning_effect(db, matrix):
    """가설 4: 유사 사건 반복 시 초기 하락폭이 줄어드는가?

    방법: 같은 sub_type 그룹(2건 이상) 내에서 시간순으로
    ^GSPC D+30 절대 하락폭이 줄어드는지 비교.
    """
    events = db.query(Event).filter(Event.scale == "macro").order_by(Event.event_date).all()
    by_subtype = defaultdict(list)
    for e in events:
        if e.sub_type:
            by_subtype[e.sub_type].append(e)

    groups = []
    support, total = 0, 0
    for sub, evts in sorted(by_subtype.items()):
        if len(evts) < 2:
            continue
        seq = []
        for e in evts:
            d30 = _ret(matrix, e.id, BENCH, "D+30")
            seq.append({
                "id": e.id, "name": e.name_ko[:24], "year": e.event_date.year,
                "d30": round(d30, 2) if d30 is not None else None,
            })
        with_data = [s for s in seq if s["d30"] is not None]
        trend = None
        if len(with_data) >= 2:
            first, last = with_data[0], with_data[-1]
            # 초기 충격(음수 방향)이 완화됐는가
            trend = "완화" if last["d30"] > first["d30"] else "심화"
            total += 1
            if trend == "완화":
                support += 1
        groups.append({"sub_type": sub, "events": seq, "trend": trend})

    if total >= 3:
        verdict = "지지" if support / total >= 0.6 else ("혼재" if support / total >= 0.4 else "기각 방향")
    else:
        verdict = "데이터 부족"
    return {"groups": groups, "support": support, "total": total, "verdict": verdict}


def h5_korea_premium(db, matrix):
    """가설 5: KOSPI가 S&P500보다 과잉반응하는 경향이 있는가?

    방법: 두 지수 모두 D+30 데이터가 있는 사건에서 |KOSPI| > |S&P| 비율.
    부호검정 관점: 50% 초과가 일관되게 나오면 과잉반응 지지.
    """
    events = db.query(Event).filter(Event.scale == "macro").all()
    rows = []
    for e in events:
        ks = _ret(matrix, e.id, KOSPI, "D+30")
        spx = _ret(matrix, e.id, BENCH, "D+30")
        if ks is None or spx is None:
            continue
        rows.append({
            "id": e.id, "name": e.name_ko[:22], "year": e.event_date.year,
            "kospi": round(ks, 2), "spx": round(spx, 2),
            "overreact": abs(ks) > abs(spx),
        })

    n = len(rows)
    over_n = sum(1 for r in rows if r["overreact"])
    pct = round(over_n / n * 100) if n else None
    ratio = None
    abs_pairs = [(abs(r["kospi"]), abs(r["spx"])) for r in rows if abs(r["spx"]) > 0.01]
    if abs_pairs:
        ratio = round(statistics.mean(k / s for k, s in abs_pairs), 2)

    if n >= 5 and pct is not None:
        verdict = "지지" if pct >= 60 else ("혼재" if pct >= 40 else "기각 방향")
    else:
        verdict = "데이터 부족"
    return {"rows": rows, "n": n, "over_n": over_n, "over_pct": pct,
            "amp_ratio": ratio, "verdict": verdict}


def m1_split_listing_discount(db, matrix):
    """마이크로 가설 1: 물적분할(자회사 분할 상장)이 모회사 주가를 디스카운트시키는가?

    방법: M010(LG화학)·M016(SK이노)·M026(에코프로머티리얼즈) 3건의 모회사가
    KOSPI 대비 D+1, D+7, D+30, D+180에서 얼마나 underperform 했는가.

    한국 자본시장의 핵심 논쟁 — 책의 킬러 챕터 후보.
    """
    cases = [
        ("M010", "051910.KS", "LG화학"),
        ("M016", "096770.KS", "SK이노베이션"),
        ("M026", "086520.KS", "에코프로"),
    ]
    benchmark = KOSPI

    rows = []
    excess_by_period = {p: [] for p in ("D+1", "D+7", "D+30", "D+180")}
    for eid, parent_sym, parent_name in cases:
        row = {"id": eid, "parent": parent_name, "symbol": parent_sym, "periods": {}}
        for p in ("D+1", "D+7", "D+30", "D+180"):
            r_parent = _ret(matrix, eid, parent_sym, p)
            r_bench = _ret(matrix, eid, benchmark, p)
            excess = (r_parent - r_bench) if (r_parent is not None and r_bench is not None) else None
            row["periods"][p] = {
                "parent": r_parent, "bench": r_bench, "excess": excess,
            }
            if excess is not None:
                excess_by_period[p].append(excess)
        rows.append(row)

    avg_excess = {p: _mean(xs) for p, xs in excess_by_period.items()}
    # D+30 평균 초과수익률이 음수면 가설 지지
    d30 = avg_excess.get("D+30")
    n_avail = len(excess_by_period.get("D+30", []))
    if n_avail >= 2 and d30 is not None:
        verdict = "지지" if d30 < -1.0 else ("기각 방향" if d30 > 1.0 else "혼재")
    else:
        verdict = "데이터 부족"
    return {
        "rows": rows, "avg_excess": avg_excess,
        "benchmark": benchmark, "verdict": verdict,
        "note": "음수 = 모회사가 KOSPI보다 약세. 책에서 다룰 '물적분할 디스카운트'의 정량 근거.",
    }


def m2_limit_up_trauma(db, matrix):
    """마이크로 가설 2: 따상 마감 IPO는 D+30에 평균 회귀하는가?

    가설: 첫날 따상으로 마감한 IPO 5건(M018·M019·M020·M021·M025)은
    D+30 시점에 KOSPI 대비 underperform. 따상 실패(M022 크래프톤)는 오히려 안정.
    """
    limit_up = [
        ("M018", "326030.KS", "SK바이오팜"),
        ("M019", "293490.KS", "카카오게임즈"),
        ("M020", "352820.KS", "하이브"),
        ("M021", "302440.KS", "SK바이오사이언스"),
        ("M025", "454910.KS", "두산로보틱스"),
    ]
    no_limit_up = [
        ("M022", "259960.KS", "크래프톤"),
    ]
    benchmark = KOSPI

    def _group(cases, label):
        out = []
        ex30 = []
        for eid, sym, name in cases:
            r30 = _ret(matrix, eid, sym, "D+30")
            b30 = _ret(matrix, eid, benchmark, "D+30")
            excess = (r30 - b30) if (r30 is not None and b30 is not None) else None
            out.append({"id": eid, "name": name, "symbol": sym,
                        "d30_stock": r30, "d30_bench": b30, "excess": excess})
            if excess is not None:
                ex30.append(excess)
        return {"label": label, "cases": out,
                "avg_excess_d30": _mean(ex30), "n": len(ex30)}

    g_up = _group(limit_up, "따상 마감")
    g_no = _group(no_limit_up, "따상 실패(대조군)")

    # 따상 그룹 평균이 대조군보다 낮으면 트라우마 지지
    if g_up["avg_excess_d30"] is not None and g_up["n"] >= 3:
        verdict = "지지" if g_up["avg_excess_d30"] < -2.0 else (
            "혼재" if g_up["avg_excess_d30"] < 0 else "기각 방향")
    else:
        verdict = "데이터 부족"

    return {
        "limit_up": g_up, "no_limit_up": g_no,
        "benchmark": benchmark, "verdict": verdict,
        "note": "음수 = KOSPI 대비 underperform. 책의 핵심 인사이트 후보 — '따상 마감 = 단기 고점 신호'.",
    }


def m3_owner_risk_recovery(db, matrix):
    """마이크로 가설 3: 오너 리스크 사건의 회복 시간 — D+30 충격 대비 D+180 회복률.

    오너/기업 책임 비중 70%+ 사건의 직접영향 자산이 어떻게 회복하는가.
    """
    cases = [
        ("M002", "180640.KS", "한진 조현민 (한진칼)"),
        ("M003", "003920.KS", "남양유업"),
        ("M004", "035720.KS", "카카오 데이터센터 화재 (카카오)"),
        ("M005", "005490.KS", "화물연대 (포스코홀딩스)"),
    ]
    benchmark = KOSPI

    rows = []
    excess_d30, excess_d180, recovered = [], [], 0
    for eid, sym, name in cases:
        d30 = _ret(matrix, eid, sym, "D+30")
        d180 = _ret(matrix, eid, sym, "D+180")
        b30 = _ret(matrix, eid, benchmark, "D+30")
        b180 = _ret(matrix, eid, benchmark, "D+180")
        ex30 = (d30 - b30) if (d30 is not None and b30 is not None) else None
        ex180 = (d180 - b180) if (d180 is not None and b180 is not None) else None
        # 회복 = D+180 초과수익률이 D+30보다 개선됨 + D+180 자체가 양수에 가까움
        recovery = None
        if ex30 is not None and ex180 is not None:
            recovery = "회복" if ex180 > ex30 + 2.0 else ("부진 지속" if ex180 < ex30 - 1.0 else "정체")
            if recovery == "회복":
                recovered += 1
            excess_d30.append(ex30)
            excess_d180.append(ex180)
        rows.append({"id": eid, "name": name, "symbol": sym,
                     "d30": d30, "d180": d180,
                     "ex30": ex30, "ex180": ex180, "recovery": recovery})

    n = len(excess_d30)
    avg_ex30 = _mean(excess_d30)
    avg_ex180 = _mean(excess_d180)
    if n >= 3 and avg_ex30 is not None and avg_ex180 is not None:
        verdict = "지지" if avg_ex180 > avg_ex30 + 2.0 else (
            "혼재" if avg_ex180 > avg_ex30 - 1.0 else "기각 방향")
    else:
        verdict = "데이터 부족"
    return {
        "rows": rows, "n": n, "recovered": recovered,
        "avg_ex30": avg_ex30, "avg_ex180": avg_ex180,
        "benchmark": benchmark, "verdict": verdict,
        "note": "초과수익률이 D+30 → D+180으로 개선되면 회복. KOSPI 기준.",
    }


def run_all(db):
    """5대 가설 + 마이크로 3대 가설 전체 실행 → 템플릿용 dict"""
    matrix = _load_matrix(db)
    macro_n = db.query(Event).filter(Event.scale == "macro").count()
    micro_n = db.query(Event).filter(Event.scale == "micro").count()
    return {
        "macro_n": macro_n,
        "micro_n": micro_n,
        "h1": h1_recovery_speed(db, matrix),
        "h2": h2_energy_dependence(db, matrix),
        "h3": h3_chain_lag(db, matrix),
        "h4": h4_learning_effect(db, matrix),
        "h5": h5_korea_premium(db, matrix),
        "m1": m1_split_listing_discount(db, matrix),
        "m2": m2_limit_up_trauma(db, matrix),
        "m3": m3_owner_risk_recovery(db, matrix),
    }
