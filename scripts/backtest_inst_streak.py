"""
기관 N일 연속 순매수 → 다음날 매수 → M일 보유 백테스트

질문: "기관이 3일 연속 순매수한 종목을 다음 날 사서 5일 보유하면 승률·평균수익률은?"

데이터: data/processed/detail/detail_{XX}.csv (종목별 일별 종가 + 기관_억)
        현재 저장소엔 2025-07 ~ 2026-07 (13개월)만 존재 — 단일 장세 한계 명시.
        3년치가 백필되면 --from/--to만 바꿔 재실행.

정의 (모두 종가 기준 — 시가 데이터 없음):
- 신호   : 기관_억 > 0 이 --streak 거래일 연속 (D-2, D-1, D0)
- 진입   : 다음 거래일 D+1 종가
- 청산   : 진입 후 --hold 거래일 뒤 종가 (D+1+hold)
- 수익률 : 청산가/진입가 - 1 - 왕복비용(--cost, 기본 0.3%)
- 중복   : 4일 연속이면 D0·D+1 각각 독립 트레이드 (겹침 인정, 표본수 표기)
- 대조군 : 같은 기간 임의(종목, 날짜) 같은 보유·비용, --ctrl-reps회 반복 평균
- 생존편향: 데이터에 있는 종목만 (상폐 누락 → 상향 편향 가능) 명시

실행 (프로젝트 루트):
    python scripts/backtest_inst_streak.py
    python scripts/backtest_inst_streak.py --streak 3 --hold 5 --from 2023-09-01

출력: data/processed/backtest/inst_streak_summary.csv, inst_streak_monthly.csv
      (트레이드 원장은 --trades-out 경로에 저장, 기본 저장 안 함)
의존성: 표준 라이브러리만.
"""
import argparse
import csv
import glob
import logging
import random
import statistics
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DETAIL_GLOB = str(ROOT / "data" / "processed" / "detail" / "detail_*.csv")
BUYREVIEW_DIR = ROOT / "data" / "processed"
OUT_DIR = ROOT / "data" / "processed" / "backtest"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_market_map():
    """코드 → 시장(KOSPI/KOSDAQ). buy_review_{MARKET}.csv에서 구성."""
    mp = {}
    for market in ("KOSPI", "KOSDAQ"):
        p = BUYREVIEW_DIR / f"buy_review_{market}.csv"
        if not p.exists():
            continue
        with open(p, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                mp[str(r["코드"]).zfill(6)] = market
    return mp


def load_series(date_from, date_to):
    """종목별 [(date, close, inst)] 시계열 (날짜 정렬). 범위 밖 행은 제외."""
    by_code = defaultdict(list)
    for path in sorted(glob.glob(DETAIL_GLOB)):
        with open(path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                d = r["날짜"][:10]
                if (date_from and d < date_from) or (date_to and d > date_to):
                    continue
                close = _num(r.get("종가"))
                if close is None or close <= 0:
                    continue
                by_code[str(r["코드"]).zfill(6)].append((d, close, _num(r.get("기관_억"))))
    for code in by_code:
        by_code[code].sort(key=lambda x: x[0])
    return by_code


def find_trades(series, streak, hold, cost):
    """신호 → 트레이드 리스트. 각 원소: dict(code, signal_date, entry_date, exit_date, ret)"""
    trades = []
    n = len(series)
    for i in range(streak - 1, n):
        # D0 = i. 연속 streak일 모두 기관_억 > 0 인가
        ok = True
        for k in range(streak):
            inst = series[i - k][2]
            if inst is None or inst <= 0:
                ok = False
                break
        if not ok:
            continue
        entry_i, exit_i = i + 1, i + 1 + hold
        if exit_i >= n:
            continue
        entry, exit_ = series[entry_i][1], series[exit_i][1]
        ret = exit_ / entry - 1 - cost
        trades.append({
            "signal_date": series[i][0],
            "entry_date": series[entry_i][0],
            "exit_date": series[exit_i][0],
            "ret": ret,
        })
    return trades


def random_control(by_code, hold, cost, n_trades, reps, seed):
    """임의(종목, 진입일) n_trades개 × reps회 → 각 회차의 (승률, 평균) 리스트."""
    rng = random.Random(seed)
    pool = [(code, i) for code, s in by_code.items()
            for i in range(0, len(s) - hold - 1)]
    if not pool:
        return []
    results = []
    for _ in range(reps):
        picks = rng.sample(pool, min(n_trades, len(pool)))
        rets = []
        for code, i in picks:
            s = by_code[code]
            rets.append(s[i + hold][1] / s[i][1] - 1 - cost)
        results.append((sum(1 for r in rets if r > 0) / len(rets), statistics.mean(rets)))
    return results


def summarize(label, rets):
    if not rets:
        return {"구분": label, "트레이드수": 0}
    n = len(rets)
    mean = statistics.mean(rets)
    sd = statistics.pstdev(rets) if n > 1 else 0.0
    tstat = (mean / (sd / n ** 0.5)) if sd > 0 else None
    return {
        "구분": label,
        "트레이드수": n,
        "승률%": round(sum(1 for r in rets if r > 0) / n * 100, 2),
        "평균수익%": round(mean * 100, 3),
        "중앙값%": round(statistics.median(rets) * 100, 3),
        "표준편차%": round(sd * 100, 3),
        "t통계량": round(tstat, 2) if tstat is not None else None,
    }


def main():
    ap = argparse.ArgumentParser(description="기관 N일 연속 순매수 백테스트")
    ap.add_argument("--streak", type=int, default=3, help="연속 순매수 거래일 수")
    ap.add_argument("--hold", type=int, default=5, help="보유 거래일 수")
    ap.add_argument("--cost", type=float, default=0.003, help="왕복 거래비용 (0.003=0.3%)")
    ap.add_argument("--from", dest="date_from", default=None, help="YYYY-MM-DD")
    ap.add_argument("--to", dest="date_to", default=None, help="YYYY-MM-DD")
    ap.add_argument("--ctrl-reps", type=int, default=20, help="랜덤 대조군 반복 횟수")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--trades-out", default=None, help="트레이드 원장 CSV 저장 경로(선택)")
    args = ap.parse_args()

    logger.info("데이터 로드 중 (범위 %s ~ %s)...", args.date_from or "처음", args.date_to or "끝")
    by_code = load_series(args.date_from, args.date_to)
    market_of = load_market_map()
    all_dates = sorted({d for s in by_code.values() for d, _, _ in s})
    logger.info("종목 %d / 거래일 %s ~ %s (%d일)", len(by_code), all_dates[0], all_dates[-1], len(all_dates))

    # 신호 트레이드
    trades = []
    for code, s in by_code.items():
        for t in find_trades(s, args.streak, args.hold, args.cost):
            t["code"] = code
            t["market"] = market_of.get(code, "UNKNOWN")
            trades.append(t)
    logger.info("신호 트레이드 %d건 (종목 %d개)", len(trades), len({t["code"] for t in trades}))

    # 대조군
    ctrl = random_control(by_code, args.hold, args.cost, len(trades), args.ctrl_reps, args.seed)
    ctrl_win = statistics.mean(c[0] for c in ctrl) * 100 if ctrl else None
    ctrl_mean = statistics.mean(c[1] for c in ctrl) * 100 if ctrl else None
    ctrl_win_sd = statistics.pstdev([c[0] for c in ctrl]) * 100 if len(ctrl) > 1 else 0
    ctrl_mean_sd = statistics.pstdev([c[1] for c in ctrl]) * 100 if len(ctrl) > 1 else 0

    # 요약
    rows = [summarize("전체", [t["ret"] for t in trades])]
    for m in ("KOSPI", "KOSDAQ"):
        rows.append(summarize(m, [t["ret"] for t in trades if t["market"] == m]))
    for r in rows:
        if r.get("트레이드수"):
            r["대조군승률%"] = round(ctrl_win, 2)
            r["대조군평균%"] = round(ctrl_mean, 3)
            r["승률초과%p"] = round(r["승률%"] - ctrl_win, 2)
            r["평균초과%p"] = round(r["평균수익%"] - ctrl_mean, 3)

    # 월별 (진입월 기준) — 단일 장세 편향 확인
    by_month = defaultdict(list)
    for t in trades:
        by_month[t["entry_date"][:7]].append(t["ret"])
    monthly = [summarize(mth, by_month[mth]) for mth in sorted(by_month)]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    hdr = ["구분", "트레이드수", "승률%", "평균수익%", "중앙값%", "표준편차%", "t통계량",
           "대조군승률%", "대조군평균%", "승률초과%p", "평균초과%p"]
    meta = (f"# 기관 {args.streak}일 연속 순매수 → D+1 종가 매수 → {args.hold}거래일 보유, "
            f"왕복비용 {args.cost*100:.2f}%, 기간 {all_dates[0]}~{all_dates[-1]}, "
            f"대조군 {args.ctrl_reps}회 평균 (승률 sd {ctrl_win_sd:.2f}, 평균 sd {ctrl_mean_sd:.3f}). "
            f"종가 기준(시가 없음)·상폐 생존편향·단일 장세 한계 있음")
    with open(OUT_DIR / "inst_streak_summary.csv", "w", encoding="utf-8-sig", newline="") as f:
        f.write(meta + "\n")
        w = csv.DictWriter(f, fieldnames=hdr, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    with open(OUT_DIR / "inst_streak_monthly.csv", "w", encoding="utf-8-sig", newline="") as f:
        f.write(meta + "\n")
        w = csv.DictWriter(f, fieldnames=hdr[:7], extrasaction="ignore")
        w.writeheader()
        w.writerows(monthly)
    if args.trades_out:
        with open(args.trades_out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["code", "market", "signal_date", "entry_date", "exit_date", "ret"])
            w.writeheader()
            w.writerows(trades)

    # 콘솔 보고
    print("\n" + meta)
    print(f"{'구분':<8}{'N':>8}{'승률%':>8}{'평균%':>9}{'중앙값%':>9}{'t':>7}{'대조승률':>9}{'대조평균':>9}{'승률초과':>9}{'평균초과':>9}")
    for r in rows:
        if not r.get("트레이드수"):
            print(f"{r['구분']:<8}{0:>8}")
            continue
        print(f"{r['구분']:<8}{r['트레이드수']:>8}{r['승률%']:>8}{r['평균수익%']:>9}{r['중앙값%']:>9}"
              f"{str(r['t통계량']):>7}{r['대조군승률%']:>9}{r['대조군평균%']:>9}{r['승률초과%p']:>9}{r['평균초과%p']:>9}")
    print("\n[월별 (진입월 기준)]")
    print(f"{'월':<10}{'N':>7}{'승률%':>8}{'평균%':>9}")
    for r in monthly:
        print(f"{r['구분']:<10}{r['트레이드수']:>7}{r.get('승률%',''):>8}{r.get('평균수익%',''):>9}")
    logger.info("저장: %s", OUT_DIR / "inst_streak_summary.csv")


if __name__ == "__main__":
    main()
