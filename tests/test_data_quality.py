"""
데이터 품질 검증 (CLAUDE.md 역할 5)

DB 전체 불변식(invariant) 검사:
- returns 행의 무결성 (NULL, 날짜 순서, 시점 값)
- 이상치(|수익률| > 50%) 플래그 → 실패가 아닌 리포트
- 사건/자산 참조 무결성

실행: pytest tests/test_data_quality.py -v -s
(-s: 이상치 리포트 출력 보기)
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from backend.db.database import SessionLocal
from backend.models import Asset, Event, Price, Return

VALID_PERIODS = {"D-30", "D-7", "D-1", "D+1", "D+7", "D+30", "D+180", "D+365"}
OUTLIER_THRESHOLD = 50.0  # |수익률| > 50% → 수동 확인 대상


@pytest.fixture(scope="module")
def db():
    session = SessionLocal()
    yield session
    session.close()


def test_returns_no_null_pct(db):
    """return_pct가 NULL인 행이 없어야 함"""
    n = db.query(Return).filter(Return.return_pct.is_(None)).count()
    assert n == 0, f"return_pct NULL 행 {n}건"


def test_returns_date_order(db):
    """모든 행에서 date_end > date_base"""
    bad = db.query(Return).filter(Return.date_end <= Return.date_base).count()
    assert bad == 0, f"date_end <= date_base 행 {bad}건"


def test_returns_valid_periods(db):
    """period 값은 정의된 8개 중 하나"""
    rows = db.query(Return.period).distinct().all()
    got = {r[0] for r in rows}
    unknown = got - VALID_PERIODS
    assert not unknown, f"정의되지 않은 period: {unknown}"


def test_returns_reference_integrity(db):
    """returns의 event_id/symbol이 실제 events/assets에 존재"""
    event_ids = {e.id for e in db.query(Event.id).all()}
    symbols = {a.symbol for a in db.query(Asset.symbol).all()}

    ret_events = {r[0] for r in db.query(Return.event_id).distinct().all()}
    ret_symbols = {r[0] for r in db.query(Return.symbol).distinct().all()}

    orphan_events = ret_events - event_ids
    orphan_symbols = ret_symbols - symbols
    assert not orphan_events, f"고아 event_id: {orphan_events}"
    assert not orphan_symbols, f"고아 symbol: {orphan_symbols}"


def test_returns_formula_consistency(db):
    """저장된 return_pct가 price_base/price_end에서 재계산한 값과 일치 (샘플 500행)"""
    rows = (
        db.query(Return)
        .filter(Return.price_base.isnot(None), Return.price_end.isnot(None))
        .limit(500)
        .all()
    )
    assert rows, "검증할 행이 없음"
    mismatches = []
    for r in rows:
        base, end = float(r.price_base), float(r.price_end)
        if base == 0:
            continue
        expected = (end - base) / base * 100
        if abs(float(r.return_pct) - expected) > 0.01:  # 반올림 여유
            mismatches.append((r.event_id, r.symbol, r.period,
                               float(r.return_pct), round(expected, 4)))
    assert not mismatches, f"공식 불일치 {len(mismatches)}건 (예: {mismatches[:3]})"


# 음수 가격이 역사적으로 실재하는 예외 (docs/data_validation_log.md 참조)
# - CL=F 2020-04-20: WTI 선물 -$37.63 (사상 첫 마이너스 유가)
# - ^IRX 2020-03-19~27: 코로나 패닉기 미국 3개월물 마이너스 금리
NEGATIVE_PRICE_EXEMPT_SYMBOLS = {"CL=F", "^IRX", "^TNX"}


def test_prices_positive(db):
    """가격은 0 이하일 수 없음 — 단, 선물·금리 지표는 역사적 음수 실재로 예외"""
    rows = (
        db.query(Price)
        .filter(Price.adj_close <= 0)
        .filter(Price.symbol.notin_(NEGATIVE_PRICE_EXEMPT_SYMBOLS))
        .all()
    )
    detail = [(p.symbol, str(p.trade_date), float(p.adj_close)) for p in rows[:10]]
    assert not rows, f"예외 목록 외 0 이하 가격 {len(rows)}건: {detail}"


def test_events_have_required_fields(db):
    """모든 사건에 이름·날짜·카테고리·slug 존재"""
    for e in db.query(Event).all():
        assert e.name_ko and e.event_date and e.category, f"{e.id} 필수 필드 누락"
        assert e.slug, f"{e.id} slug 없음"


def test_micro_attribution_sums_to_100(db):
    """마이크로 사건의 3축 가중치 합 = 100"""
    bad = []
    for e in db.query(Event).filter(Event.scale == "micro").all():
        if e.attr_political is None:
            continue
        total = (e.attr_political or 0) + (e.attr_corporate or 0) + (e.attr_macro or 0)
        if total != 100:
            bad.append((e.id, total))
    assert not bad, f"가중치 합 ≠ 100: {bad}"


def test_report_outliers(db):
    """이상치(|수익률| > 50%) 리포트 — 실패시키지 않고 출력만.

    ±50% 초과 수익률은 오류가 아니라 실제 급변일 수 있으므로
    (예: 1973 오일쇼크 유가 4배), 수동 확인 대상으로 나열만 한다.
    """
    outliers = (
        db.query(Return)
        .filter((Return.return_pct > OUTLIER_THRESHOLD)
                | (Return.return_pct < -OUTLIER_THRESHOLD))
        .order_by(Return.return_pct.desc())
        .all()
    )
    print(f"\n[이상치 리포트] |수익률| > {OUTLIER_THRESHOLD}%: {len(outliers)}건")
    for r in outliers[:20]:
        print(f"  {r.event_id} × {r.symbol} {r.period}: {float(r.return_pct):+.1f}%"
              f"  ({r.date_base} → {r.date_end})")
    if len(outliers) > 20:
        print(f"  ... 외 {len(outliers) - 20}건")
    # 이상치 비율이 전체 5%를 넘으면 데이터 오염 의심 → 실패
    total = db.query(Return).count()
    ratio = len(outliers) / total if total else 0
    assert ratio < 0.05, f"이상치 비율 {ratio:.1%} — 데이터 오염 의심"
