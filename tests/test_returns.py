"""
수익률 계산 로직 단위 테스트 (CLAUDE.md 역할 5)

대상: scripts/calc_all_returns.py
- 기준가/종료가 탐색 (주말·공휴일·장기 폐장 대응)
- D+N / D-N 수익률 공식
- 경계 조건 (데이터 없음, 0원 가격, 미래 시점)

실행: pytest tests/ -v
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from calc_all_returns import (
    calc_returns_for_pair,
    find_price_on_or_after,
    find_price_on_or_before,
    SEARCH_RANGE,
)


# ── 테스트 픽스처 ──────────────────────────────

class FakePrice:
    """Price 모델 대역 — calc가 쓰는 필드만"""
    def __init__(self, symbol, trade_date, adj_close):
        self.symbol = symbol
        self.trade_date = trade_date
        self.adj_close = adj_close


class FakeEvent:
    """Event 모델 대역"""
    def __init__(self, event_id, event_date):
        self.id = event_id
        self.event_date = event_date


def make_price_dict(start, days, base=100.0, daily_step=1.0, skip_weekends=True,
                    closed_dates=()):
    """
    영업일 가격 딕셔너리 생성.
    base에서 시작해 거래일마다 daily_step씩 증가.
    closed_dates는 임시 폐장일 (9/11 시나리오용).
    """
    prices = {}
    price = base
    d = start
    for _ in range(days):
        is_weekend = skip_weekends and d.weekday() >= 5
        if not is_weekend and d not in closed_dates:
            prices[d] = FakePrice("TEST", d, price)
            price += daily_step
        d += timedelta(days=1)
    return prices


# ── find_price_on_or_before / after ──────────────────────────────

def test_find_on_or_before_exact_day():
    """거래일 당일이면 그대로 반환"""
    monday = date(2024, 1, 8)
    pd_ = make_price_dict(monday, 10)
    got = find_price_on_or_before(pd_, monday)
    assert got.trade_date == monday


def test_find_on_or_before_weekend_falls_back():
    """토요일 기준 → 직전 금요일 가격"""
    monday = date(2024, 1, 8)
    pd_ = make_price_dict(monday, 10)
    saturday = date(2024, 1, 13)
    got = find_price_on_or_before(pd_, saturday)
    assert got.trade_date == date(2024, 1, 12)  # 금요일


def test_find_on_or_after_weekend_rolls_forward():
    """일요일 기준 → 다음 월요일 가격"""
    monday = date(2024, 1, 8)
    pd_ = make_price_dict(monday, 10)
    sunday = date(2024, 1, 14)
    got = find_price_on_or_after(pd_, sunday)
    assert got.trade_date == date(2024, 1, 15)


def test_find_on_or_after_long_closure_911_style():
    """9/11형 시나리오: 6일 폐장 후 재개 첫 거래일을 찾는다"""
    start = date(2001, 9, 3)
    closed = tuple(date(2001, 9, 11) + timedelta(days=i) for i in range(6))
    pd_ = make_price_dict(start, 30, skip_weekends=True, closed_dates=closed)
    got = find_price_on_or_after(pd_, date(2001, 9, 11))
    assert got is not None
    assert got.trade_date == date(2001, 9, 17)  # 재개일 (월요일)


def test_find_returns_none_beyond_search_range():
    """SEARCH_RANGE(10일)를 넘는 공백이면 None"""
    monday = date(2024, 1, 8)
    pd_ = make_price_dict(monday, 5)  # 1/8~1/12만 존재
    too_far = date(2024, 2, 20)
    assert find_price_on_or_before(pd_, monday - timedelta(days=SEARCH_RANGE + 5)) is None
    assert find_price_on_or_after(pd_, too_far) is None


# ── calc_returns_for_pair: D+N ──────────────────────────────

def test_d_plus_return_formula():
    """D+N 수익률 = (end - base) / base * 100"""
    # 월요일 사건, 매 거래일 +1.0 상승, base=100
    event_date = date(2020, 1, 6)  # 월요일
    pd_ = make_price_dict(event_date - timedelta(days=40), 600)
    event = FakeEvent("T01", event_date)

    results = {r.period: r for r in calc_returns_for_pair(event, pd_)}

    d1 = results["D+1"]
    expected = (d1.price_end - d1.price_base) / d1.price_base * 100
    # calc는 소수 4자리 반올림 저장 → 허용 오차 1e-4
    assert d1.return_pct == pytest.approx(expected, abs=1e-4)
    # 기준일은 사건일 당일(거래일)
    assert d1.date_base == event_date
    # D+1 종료일은 다음 거래일
    assert d1.date_end == event_date + timedelta(days=1)
    # 상승 시나리오이므로 양수
    assert d1.return_pct > 0


def test_d_plus_returns_monotonic_in_uptrend():
    """지속 상승 시나리오면 D+30 수익률 > D+7 > D+1"""
    event_date = date(2020, 1, 6)
    pd_ = make_price_dict(event_date - timedelta(days=40), 600)
    event = FakeEvent("T02", event_date)
    r = {x.period: x.return_pct for x in calc_returns_for_pair(event, pd_)}
    assert r["D+30"] > r["D+7"] > r["D+1"]


# ── calc_returns_for_pair: D-N (사전반응) ──────────────────────────────

def test_d_minus_base_is_before_event():
    """D-N: base = 사건일-N, end = 사건일"""
    event_date = date(2020, 3, 2)  # 월요일
    pd_ = make_price_dict(event_date - timedelta(days=60), 600)
    event = FakeEvent("T03", event_date)
    r = {x.period: x for x in calc_returns_for_pair(event, pd_)}

    d30 = r["D-30"]
    assert d30.date_end == event_date          # 종료 = 사건일
    assert d30.date_base <= event_date - timedelta(days=30)  # 기준 = 30일 전 이하 거래일
    # 상승 시나리오 → 사전 30일간 양의 수익률
    assert d30.return_pct > 0


def test_d_minus_smaller_window_smaller_return():
    """상승 추세에서 |D-1| < |D-7| < |D-30|"""
    event_date = date(2020, 3, 2)
    pd_ = make_price_dict(event_date - timedelta(days=60), 600)
    event = FakeEvent("T04", event_date)
    r = {x.period: x.return_pct for x in calc_returns_for_pair(event, pd_)}
    assert r["D-1"] < r["D-7"] < r["D-30"]


# ── 경계 조건 ──────────────────────────────

def test_no_prices_returns_empty():
    """가격 데이터가 아예 없으면 빈 리스트"""
    event = FakeEvent("T05", date(2020, 1, 6))
    assert calc_returns_for_pair(event, {}) == []


def test_zero_base_price_skipped():
    """기준가 0이면 division 없이 빈 리스트 (가드)"""
    event_date = date(2020, 1, 6)
    pd_ = {event_date: FakePrice("TEST", event_date, 0.0)}
    event = FakeEvent("T06", event_date)
    assert calc_returns_for_pair(event, pd_) == []


def test_future_periods_skipped():
    """오늘 이후 시점(D+365가 미래)은 결과에서 제외"""
    event_date = date.today() - timedelta(days=10)
    pd_ = make_price_dict(event_date - timedelta(days=60), 70)
    event = FakeEvent("T07", event_date)
    periods = {x.period for x in calc_returns_for_pair(event, pd_)}
    assert "D+1" in periods
    assert "D+365" not in periods
    assert "D+180" not in periods


def test_price_history_too_short_for_d_minus30():
    """사건 전 데이터가 부족하면 D-30만 빠지고 나머지는 정상"""
    event_date = date(2020, 3, 2)
    # 사건 15일 전부터만 데이터 존재
    pd_ = make_price_dict(event_date - timedelta(days=15), 500)
    event = FakeEvent("T08", event_date)
    periods = {x.period for x in calc_returns_for_pair(event, pd_)}
    assert "D-30" not in periods
    assert "D-7" in periods
    assert "D+30" in periods


def test_known_numbers_hand_computed():
    """손으로 계산한 값과 일치 (golden test)"""
    # 거래일: 4일 연속 (수~토 대신 평일만): 100, 102, 104, 106
    d0 = date(2024, 7, 1)  # 월요일
    pd_ = {
        d0: FakePrice("T", d0, 100.0),
        d0 + timedelta(days=1): FakePrice("T", d0 + timedelta(days=1), 102.0),
        d0 + timedelta(days=2): FakePrice("T", d0 + timedelta(days=2), 104.0),
        d0 + timedelta(days=7): FakePrice("T", d0 + timedelta(days=7), 106.0),
    }
    event = FakeEvent("T09", d0)
    r = {x.period: x for x in calc_returns_for_pair(event, pd_)}
    # D+1: (102-100)/100 = +2.0%
    assert r["D+1"].return_pct == pytest.approx(2.0)
    # D+7: (106-100)/100 = +6.0%
    assert r["D+7"].return_pct == pytest.approx(6.0)
