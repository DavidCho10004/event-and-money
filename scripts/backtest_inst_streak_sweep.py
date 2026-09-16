"""
기관 연속 순매수 백테스트 — 파라미터 민감도 스윕

목적: 연속일수(streak) × 보유일수(hold) 격자를 한 번에 돌려
      "3일·5일이 우연히 나쁜 조합인지 / 수급 추종 자체가 안 되는지" 판별.

backtest_inst_streak.py의 로직을 그대로 재사용 (데이터는 1회만 로드).
대조군은 hold별로만 달라지므로 hold당 1회(20반복 평균) 계산.

실행 (프로젝트 루트):
    python scripts/backtest_inst_streak_sweep.py
    python scripts/backtest_inst_streak_sweep.py --streaks 2,3,4,5 --holds 1,3,5,10,20

출력: data/processed/backtest/inst_streak_sweep.csv
의존성: 표준 라이브러리만.
"""
import argparse
import csv
import logging
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backtest_inst_streak import (  # noqa: E402
    OUT_DIR, find_trades, load_market_map, load_series, random_control,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    ap = argparse.ArgumentParser(description="기관 연속 순매수 파라미터 스윕")
    ap.add_argument("--streaks", default="2,3,4,5")
    ap.add_argument("--holds", default="1,3,5,10,20")
    ap.add_argument("--cost", type=float, default=0.003)
    ap.add_argument("--from", dest="date_from", default=None)
    ap.add_argument("--to", dest="date_to", default=None)
    ap.add_argument("--ctrl-reps", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    streaks = [int(x) for x in args.streaks.split(",")]
    holds = [int(x) for x in args.holds.split(",")]

    logger.info("데이터 로드 (1회)...")
    by_code = load_series(args.date_from, args.date_to)
    market_of = load_market_map()
    all_dates = sorted({d for s in by_code.values() for d, _, _ in s})
    logger.info("종목 %d / %s ~ %s", len(by_code), all_dates[0], all_dates[-1])

    # 대조군: hold별 1회. 표본수는 streak=최소값 기준 트레이드수로 통일(보수적으로 큰 N)
    ctrl = {}
    for h in holds:
        n_ref = sum(len(find_trades(s, min(streaks), h, args.cost)) for s in by_code.values())
        reps = random_control(by_code, h, args.cost, n_ref, args.ctrl_reps, args.seed)
        ctrl[h] = (statistics.mean(c[0] for c in reps) * 100,
                   statistics.mean(c[1] for c in reps) * 100)
        logger.info("대조군 hold=%d: 승률 %.2f%% 평균 %.3f%%", h, *ctrl[h])

    rows = []
    for st in streaks:
        for h in holds:
            rets = []
            for code, s in by_code.items():
                rets.extend(t["ret"] for t in find_trades(s, st, h, args.cost))
            if not rets:
                rows.append({"연속일": st, "보유일": h, "N": 0})
                continue
            n = len(rets)
            mean = statistics.mean(rets)
            sd = statistics.pstdev(rets) if n > 1 else 0.0
            win = sum(1 for r in rets if r > 0) / n * 100
            cw, cm = ctrl[h]
            rows.append({
                "연속일": st, "보유일": h, "N": n,
                "승률%": round(win, 2),
                "평균%": round(mean * 100, 3),
                "비용전평균%": round((mean + args.cost) * 100, 3),
                "중앙값%": round(statistics.median(rets) * 100, 3),
                "t": round(mean / (sd / n ** 0.5), 2) if sd > 0 else None,
                "대조승률%": round(cw, 2), "대조평균%": round(cm, 3),
                "승률초과%p": round(win - cw, 2), "평균초과%p": round(mean * 100 - cm, 3),
            })
            logger.info("streak=%d hold=%2d N=%6d 승률 %.2f 평균 %+.3f 초과 %+.3f",
                        st, h, n, win, mean * 100, mean * 100 - cm)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "inst_streak_sweep.csv"
    hdr = ["연속일", "보유일", "N", "승률%", "평균%", "비용전평균%", "중앙값%", "t",
           "대조승률%", "대조평균%", "승률초과%p", "평균초과%p"]
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        f.write(f"# 기관 연속 순매수 스윕. D+1 종가 진입, 왕복비용 {args.cost*100:.2f}%, "
                f"기간 {all_dates[0]}~{all_dates[-1]}, 대조군 {args.ctrl_reps}회 평균. "
                f"종가 기준·상폐 생존편향·단일 장세 한계\n")
        w = csv.DictWriter(f, fieldnames=hdr, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    logger.info("저장: %s", out)

    # 격자 출력: 평균초과%p (전략 평균 − 랜덤 평균)
    print("\n[평균수익 − 랜덤 대조군 (%p)]  양수=우위 / 음수=열위")
    print("연속\\보유 " + "".join(f"{h:>8}" for h in holds))
    for st in streaks:
        line = f"{st:>4}일     "
        for h in holds:
            r = next(x for x in rows if x["연속일"] == st and x["보유일"] == h)
            line += f"{r.get('평균초과%p', 'n/a'):>8}"
        print(line)
    print("\n[승률 %]")
    print("연속\\보유 " + "".join(f"{h:>8}" for h in holds))
    for st in streaks:
        line = f"{st:>4}일     "
        for h in holds:
            r = next(x for x in rows if x["연속일"] == st and x["보유일"] == h)
            line += f"{r.get('승률%', 'n/a'):>8}"
        print(line)


if __name__ == "__main__":
    main()
