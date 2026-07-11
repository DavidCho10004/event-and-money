"""
analyze_supply_demand.py — 수급 지표 계산 (스크리너용)

fetch_supply_demand.py가 만든 supply_demand.csv를 읽어
종목별 수급 지표를 계산하고
data/processed/supply_demand_screener.csv 로 저장한다.

이 스크립트는 KRX 접속이 필요 없다 (로컬 CSV만 읽고 계산).

계산 지표 (종목당 1행):
    - foreign_streak : 외국인 연속 순매수 일수 (최근일부터, 음수면 연속 순매도)
    - inst_streak    : 기관 연속 순매수 일수
    - foreign_5d     : 외국인 최근 5거래일 누적 순매수
    - inst_5d        : 기관 최근 5거래일 누적 순매수
    - foreign_20d    : 외국인 최근 20거래일 누적 순매수
    - inst_20d       : 기관 최근 20거래일 누적 순매수
    - both_buying    : 외국인·기관 동반 순매수(최근 5일 누적 둘 다 양수) 여부
    - last_date      : 데이터 기준 최종일

실행 방법 (프로젝트 루트에서, fetch_supply_demand.py 실행 후):
    python scripts/analyze_supply_demand.py
"""
import sys
import logging
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PROCESSED_DIR = ROOT / "data" / "processed"
IN_FILE = PROCESSED_DIR / "supply_demand.csv"
OUT_FILE = PROCESSED_DIR / "supply_demand_screener.csv"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def current_streak(series):
    """최근일부터의 연속 순매수/순매도 일수.

    최근값이 양수면 연속 양수 일수를 양의 정수로,
    음수면 연속 음수 일수를 음의 정수로 반환. 0이면 0.
    series는 날짜 오름차순 정렬 상태를 가정.
    """
    vals = series.tolist()
    if not vals:
        return 0
    last = vals[-1]
    if last == 0:
        return 0
    sign = 1 if last > 0 else -1
    streak = 0
    for v in reversed(vals):
        if (v > 0 and sign > 0) or (v < 0 and sign < 0):
            streak += 1
        else:
            break
    return streak * sign


def analyze_one(g):
    """단일 종목 그룹(날짜 오름차순)에서 지표 한 줄 계산."""
    g = g.sort_values("date")
    has_foreign = "foreign_net" in g.columns
    has_inst = "inst_net" in g.columns

    foreign = g["foreign_net"] if has_foreign else pd.Series(dtype=float)
    inst = g["inst_net"] if has_inst else pd.Series(dtype=float)

    foreign_5d = float(foreign.tail(5).sum()) if has_foreign else 0.0
    inst_5d = float(inst.tail(5).sum()) if has_inst else 0.0

    return pd.Series({
        "name": g["name"].iloc[-1],
        "last_date": g["date"].iloc[-1],
        "foreign_streak": current_streak(foreign) if has_foreign else 0,
        "inst_streak": current_streak(inst) if has_inst else 0,
        "foreign_5d": foreign_5d,
        "inst_5d": inst_5d,
        "foreign_20d": float(foreign.tail(20).sum()) if has_foreign else 0.0,
        "inst_20d": float(inst.tail(20).sum()) if has_inst else 0.0,
        "both_buying": bool(foreign_5d > 0 and inst_5d > 0),
    })


def main():
    if not IN_FILE.exists():
        logger.error(
            "수급 데이터가 없습니다: %s\n"
            "먼저 fetch_supply_demand.py를 실행하세요:\n"
            "    python scripts/fetch_supply_demand.py",
            IN_FILE,
        )
        sys.exit(1)

    df = pd.read_csv(IN_FILE, dtype={"code": str})
    logger.info("입력: %s (%d행, %d종목)", IN_FILE, len(df), df["code"].nunique())

    # 종목코드 leading zero 보존 (예: 000660)
    df["code"] = df["code"].astype(str).str.zfill(6)

    result = (
        df.groupby("code", sort=False)
        .apply(analyze_one, include_groups=False)
        .reset_index()
    )

    # 외국인 5일 누적 순매수 내림차순 (스크리너 기본 정렬)
    result = result.sort_values("foreign_5d", ascending=False).reset_index(drop=True)

    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    result.to_csv(OUT_FILE, index=False, encoding="utf-8-sig")
    logger.info("저장 완료: %s (%d종목)", OUT_FILE, len(result))

    # 요약: 동반 매수 종목 수
    both = int(result["both_buying"].sum())
    logger.info("외국인·기관 동반 순매수(최근 5일): %d종목", both)


if __name__ == "__main__":
    main()
