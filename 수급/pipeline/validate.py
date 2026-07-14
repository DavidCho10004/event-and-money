# -*- coding: utf-8 -*-
"""
데이터 감시자 — 통과 못 하면 exit 1 → run_weekly.bat이 push를 차단한다.

검증 항목 (임계값은 전부 아래 상수):
  V1 종목 수 범위      : 최신 거래일의 시장별 수집 종목 수가 예상 범위인가
  V2 데이터 신선도     : 일별(D) 최신 날짜가 실행일 기준 MAX_STALE_DAYS일 이내인가
  V3 순매수 항등식     : 전 투자주체 합 ≈ 0 (사모 제외 — 기관합계의 부분집합)
                         최근 IDENTITY_CHECK_DAYS 거래일 각각 검사
  V4 결측/이상치 비율  : 최신일 외인지분율 결측률, 순매수 이상치 비율

주의: 로그인 정보(KRX_ID/KRX_PW)는 이 파일을 포함해 파이프라인 어디서도
출력·기록하지 않는다.
"""
import logging
import sys
from datetime import datetime

import store

logging.basicConfig(level=logging.WARNING, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("validate")
logger.setLevel(logging.INFO)

# ── 임계값 상수 ──────────────────────────────────────────
EXPECTED_COUNT = {"KOSPI": (800, 1000), "KOSDAQ": (1600, 2000)}  # 종목 수 900±100 / 1800±200
MAX_STALE_DAYS = 5          # 최신 데이터가 실행일로부터 며칠 이내여야 하는가 (주말+공휴일 여유)
IDENTITY_CHECK_DAYS = 5     # 항등식을 검사할 최근 거래일 수
IDENTITY_TOL_EOK = 1.0      # 항등식 허용 오차 (억원) — 실측 0.0억이므로 보수적으로 1억
MISSING_FRGN_MAX = 0.15     # 최신일 외인지분율 결측률 상한 (15%)
OUTLIER_NETBUY_EOK = 50000  # 종목·주체·일 순매수 이상치 기준 (5조원)
OUTLIER_MAX_RATIO = 0.0005  # 이상치 행 비율 상한 (0.05%)

IDENTITY_INVESTORS = ["개인", "외국인", "기타외국인", "기관합계", "기타법인"]


def run_checks(flows, snapshots, today: datetime) -> list:
    """검증 실행. 실패 메시지 리스트 반환 (빈 리스트 = 전부 통과)."""
    failures = []
    d_flows = flows[flows["해상도"] == "D"]
    if d_flows.empty:
        return ["일별(D) 데이터가 저장소에 없습니다."]
    latest = d_flows["날짜"].max()

    # V2 신선도
    stale = (today - datetime.strptime(latest, "%Y-%m-%d")).days
    if stale > MAX_STALE_DAYS:
        failures.append(f"V2 신선도: 최신 데이터 {latest} — {stale}일 경과 (허용 {MAX_STALE_DAYS}일)")
    else:
        logger.info("V2 통과: 최신 %s (%d일 전)", latest, stale)

    # V1 종목 수 (최신일, 시장별 — '개인' 주체 기준)
    day = d_flows[(d_flows["날짜"] == latest) & (d_flows["투자자"] == "개인")]
    for market, (lo, hi) in EXPECTED_COUNT.items():
        n = day[day["시장"] == market]["코드"].nunique()
        if not (lo <= n <= hi):
            failures.append(f"V1 종목수: {market} {n}종목 — 허용 범위 {lo}~{hi} 벗어남")
        else:
            logger.info("V1 통과: %s %d종목 (%d~%d)", market, n, lo, hi)

    # V3 항등식 (최근 N거래일, 시장 합산)
    recent_days = sorted(d_flows["날짜"].unique())[-IDENTITY_CHECK_DAYS:]
    ident = d_flows[d_flows["투자자"].isin(IDENTITY_INVESTORS)]
    for d in recent_days:
        total_eok = ident[ident["날짜"] == d]["거래대금"].sum() / 1e8
        if abs(total_eok) > IDENTITY_TOL_EOK:
            failures.append(f"V3 항등식: {d} 전 주체 순매수 합 {total_eok:+.1f}억 (허용 ±{IDENTITY_TOL_EOK}억)")
    if not any(f.startswith("V3") for f in failures):
        logger.info("V3 통과: 최근 %d거래일 항등식 합 ≤ ±%.1f억", len(recent_days), IDENTITY_TOL_EOK)

    # V4-a 최신일 외인지분율 결측률
    if snapshots is not None and not snapshots.empty:
        snap_latest = snapshots[snapshots["날짜"] == snapshots["날짜"].max()]
        if len(snap_latest):
            miss = snap_latest["외인지분율"].isna().mean()
            if miss > MISSING_FRGN_MAX:
                failures.append(f"V4 결측: 최신일 외인지분율 결측률 {miss:.1%} (허용 {MISSING_FRGN_MAX:.0%})")
            else:
                logger.info("V4-a 통과: 외인지분율 결측률 %.1f%%", miss * 100)
    else:
        failures.append("V4 결측: 스냅샷 데이터가 없습니다.")

    # V4-b 순매수 이상치 비율 (최근 N거래일)
    recent = d_flows[d_flows["날짜"].isin(recent_days)]
    if len(recent):
        outlier_ratio = (recent["거래대금"].abs() > OUTLIER_NETBUY_EOK * 1e8).mean()
        if outlier_ratio > OUTLIER_MAX_RATIO:
            failures.append(
                f"V4 이상치: |순매수|>{OUTLIER_NETBUY_EOK:,}억 행 비율 {outlier_ratio:.4%} (허용 {OUTLIER_MAX_RATIO:.2%})")
        else:
            logger.info("V4-b 통과: 이상치 비율 %.4f%%", outlier_ratio * 100)

    return failures


def main():
    flows = store.read_flows()
    snapshots = store.read_snapshots()
    failures = run_checks(flows, snapshots, datetime.now())

    if failures:
        for f in failures:
            logger.error("검증 실패 — %s", f)
        print("VALIDATE FAILED")
        sys.exit(1)
    print("VALIDATE OK")


if __name__ == "__main__":
    main()
