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

from backend.models import Event, Return

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


def h3_chain_lag(db, matrix):
    """가설 3: 1차→2차 변인 사이에 일관된 시차가 존재하는가?

    프록시: 전 사건에서 유가(CL=F) D+7과 S&P500 D+30의 상관.
    유가가 먼저 움직이고 주가가 따라온다면 양(+)의 상관 기대.
    ※ 일별 lead-lag 분석은 아니므로 참고용.
    """
    events = db.query(Event).filter(Event.scale == "macro").all()
    pairs = []
    for e in events:
        oil7 = _ret(matrix, e.id, OIL, "D+7")
        spx30 = _ret(matrix, e.id, BENCH, "D+30")
        if oil7 is not None and spx30 is not None:
            pairs.append((e.id, oil7, spx30))

    corr = _pearson([p[1] for p in pairs], [p[2] for p in pairs])
    return {
        "n": len(pairs),
        "corr_oil7_spx30": corr,
        "verdict": "보류 (정밀 lead-lag 분석 필요)",
        "note": "현재는 시점 수익률 간 단순 상관. 일별 시계열 교차상관(CCF) 분석은 추후 과제.",
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


def run_all(db):
    """5대 가설 전체 실행 → 템플릿용 dict"""
    matrix = _load_matrix(db)
    return {
        "h1": h1_recovery_speed(db, matrix),
        "h2": h2_energy_dependence(db, matrix),
        "h3": h3_chain_lag(db, matrix),
        "h4": h4_learning_effect(db, matrix),
        "h5": h5_korea_premium(db, matrix),
    }
