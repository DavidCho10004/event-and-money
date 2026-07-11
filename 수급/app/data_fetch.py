"""
데이터 수집 모듈 — 네이버 금융에서 코스피/코스닥 지수 종가와
투자자별(개인/외국인/기관) 순매수를 수집한다.

- KRX(pykrx)는 최신 버전부터 로그인 계정을 요구하므로 사용하지 않는다.
- 네이버 금융은 로그인 없이 접근 가능하다.
- 수집한 데이터는 data/ 폴더에 CSV로 캐시한다.

단위:
- 지수: 포인트(체결가)
- 투자자별 순매수: 억원 (네이버 표기 그대로)
"""
from __future__ import annotations

import io
import time
import logging
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

# 네이버 금융 시장 코드
MARKETS = {
    "KOSPI": {"index_code": "KOSPI", "sosok": "01"},
    "KOSDAQ": {"index_code": "KOSDAQ", "sosok": "02"},
}


# ---------------------------------------------------------------------------
# 지수 종가 수집
# ---------------------------------------------------------------------------
def fetch_index_close(market: str, pages: int = 30) -> pd.DataFrame:
    """네이버 금융에서 지수 일별 종가를 수집한다.

    Args:
        market: "KOSPI" 또는 "KOSDAQ"
        pages: 조회할 페이지 수 (한 페이지 = 약 6영업일)

    Returns:
        columns=[date, close] 인 DataFrame (날짜 오름차순)
    """
    code = MARKETS[market]["index_code"]
    frames = []
    for page in range(1, pages + 1):
        url = f"https://finance.naver.com/sise/sise_index_day.naver?code={code}&page={page}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.encoding = "euc-kr"
            tables = pd.read_html(io.StringIO(r.text))
        except Exception as exc:  # noqa: BLE001
            logger.warning("지수 수집 실패 (%s p%d): %s", market, page, exc)
            continue

        # 날짜/체결가가 있는 테이블 선택
        candidates = [t for t in tables if t.shape[1] >= 6]
        if not candidates:
            continue
        t = candidates[0].dropna()
        if t.empty:
            continue
        t = t.rename(columns={"날짜": "date", "체결가": "close"})[["date", "close"]]
        frames.append(t)
        time.sleep(0.15)  # 네이버 서버 부담 완화

    if not frames:
        return pd.DataFrame(columns=["date", "close"])

    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"], format="%Y.%m.%d", errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df.dropna().drop_duplicates("date").sort_values("date").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# 투자자별 순매수 수집
# ---------------------------------------------------------------------------
def fetch_investor_flow(
    market: str, pages: int = 30, bizdate: str | None = None
) -> pd.DataFrame:
    """네이버 금융에서 투자자별 순매수(억원)를 수집한다.

    Args:
        market: "KOSPI" 또는 "KOSDAQ"
        pages: 조회할 페이지 수
        bizdate: 조회 기준일 'YYYYMMDD'. None이면 최근 영업일을 자동 사용.
            (네이버는 bizdate가 비면 데이터를 반환하지 않으므로 필수)

    Returns:
        columns=[date, individual, foreign, institution] 인 DataFrame (억원)
    """
    sosok = MARKETS[market]["sosok"]
    if bizdate is None:
        # 최근 영업일을 지수 종가에서 가져온다 (가장 확실한 방법)
        idx = fetch_index_close(market, pages=1)
        if not idx.empty:
            bizdate = idx["date"].max().strftime("%Y%m%d")
        else:
            bizdate = pd.Timestamp.today().strftime("%Y%m%d")

    frames = []
    for page in range(1, pages + 1):
        url = (
            "https://finance.naver.com/sise/investorDealTrendDay.naver"
            f"?bizdate={bizdate}&sosok={sosok}&page={page}"
        )
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.encoding = "euc-kr"
            tables = pd.read_html(io.StringIO(r.text))
        except Exception as exc:  # noqa: BLE001
            logger.warning("투자자 수집 실패 (%s p%d): %s", market, page, exc)
            continue

        if not tables:
            continue
        t = tables[0]
        # 멀티헤더 → 첫 컬럼 레벨만 사용
        if isinstance(t.columns, pd.MultiIndex):
            t.columns = [c[0] for c in t.columns]
        # 필요한 컬럼: 날짜, 개인, 외국인, 기관계
        need = {"날짜", "개인", "외국인", "기관계"}
        if not need.issubset(set(t.columns)):
            continue
        t = t[["날짜", "개인", "외국인", "기관계"]].copy()
        t = t.rename(
            columns={
                "날짜": "date",
                "개인": "individual",
                "외국인": "foreign",
                "기관계": "institution",
            }
        )
        frames.append(t)
        time.sleep(0.15)

    if not frames:
        return pd.DataFrame(
            columns=["date", "individual", "foreign", "institution"]
        )

    df = pd.concat(frames, ignore_index=True)
    # 날짜 형식: 'YY.MM.DD'
    df["date"] = pd.to_datetime(df["date"], format="%y.%m.%d", errors="coerce")
    for col in ["individual", "foreign", "institution"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna().drop_duplicates("date").sort_values("date").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# 병합 + 캐시
# ---------------------------------------------------------------------------
def _cache_path(market: str) -> Path:
    return DATA_DIR / f"supply_demand_{market}.csv"


def load_or_fetch(market: str, pages: int = 30, refresh: bool = False) -> pd.DataFrame:
    """캐시가 있으면 읽고, 없거나 refresh=True면 새로 수집한다.

    Returns:
        columns=[date, close, individual, foreign, institution]
    """
    path = _cache_path(market)
    if path.exists() and not refresh:
        df = pd.read_csv(path, parse_dates=["date"])
        return df

    idx = fetch_index_close(market, pages=pages)
    flow = fetch_investor_flow(market, pages=pages)
    if idx.empty or flow.empty:
        # 하나라도 실패하면 가용한 것만이라도 반환
        logger.warning("%s 데이터 일부 누락 (index=%d, flow=%d)", market, len(idx), len(flow))

    df = pd.merge(idx, flow, on="date", how="inner").sort_values("date").reset_index(drop=True)
    if not df.empty:
        df.to_csv(path, index=False)
    return df


# ---------------------------------------------------------------------------
# 코스피200 개별 종목
# ---------------------------------------------------------------------------
import re  # noqa: E402

_KPI200_PATTERN = re.compile(r'code=(\d{6})" target="_parent">([^<]+)</a>')
_KPI200_CACHE = DATA_DIR / "kospi200_list.csv"


def fetch_kospi200_list(refresh: bool = False) -> pd.DataFrame:
    """코스피200 구성종목 목록을 네이버 금융에서 수집한다.

    Returns:
        columns=[code, name] 인 DataFrame (종목코드 6자리, 종목명)
    """
    if _KPI200_CACHE.exists() and not refresh:
        return pd.read_csv(_KPI200_CACHE, dtype={"code": str})

    pairs: dict[str, str] = {}
    for page in range(1, 25):
        url = (
            "https://finance.naver.com/sise/entryJongmok.naver"
            f"?type=KPI200&page={page}"
        )
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.encoding = "euc-kr"
        except Exception as exc:  # noqa: BLE001
            logger.warning("코스피200 목록 수집 실패 (p%d): %s", page, exc)
            break
        found = _KPI200_PATTERN.findall(r.text)
        if not found:
            break
        for code, name in found:
            pairs[code] = name.strip()
        time.sleep(0.1)

    df = pd.DataFrame(
        [{"code": c, "name": n} for c, n in pairs.items()]
    )
    if not df.empty:
        df.to_csv(_KPI200_CACHE, index=False)
    return df


def fetch_stock_flow(code: str, pages: int = 20) -> pd.DataFrame:
    """개별 종목의 투자자별 순매수(수량, 주)를 네이버 금융에서 수집한다.

    개별 종목 페이지는 개인 데이터를 제공하지 않으며, 외국인·기관만 수량(주)
    기준으로 제공한다. 외국인 보유율(%)도 함께 수집한다.

    Args:
        code: 종목코드 6자리
        pages: 조회할 페이지 수 (1페이지 ≈ 30영업일)

    Returns:
        columns=[date, close, foreign, institution, foreign_ratio]
        (foreign/institution 단위: 주, foreign_ratio 단위: %)
    """
    frames = []
    for page in range(1, pages + 1):
        url = f"https://finance.naver.com/item/frgn.naver?code={code}&page={page}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.encoding = "euc-kr"
            tables = pd.read_html(io.StringIO(r.text))
        except Exception as exc:  # noqa: BLE001
            logger.warning("종목 수급 수집 실패 (%s p%d): %s", code, page, exc)
            continue

        # 날짜·종가·기관·외국인 순매매가 있는 테이블 찾기 (보통 index 3)
        target = None
        for t in tables:
            if t.shape[1] == 9:
                target = t
                break
        if target is None:
            continue

        target = target.copy()
        target.columns = [
            "date", "close", "diff", "rate", "volume",
            "institution", "foreign", "foreign_shares", "foreign_ratio",
        ]
        target = target[
            ["date", "close", "institution", "foreign", "foreign_ratio"]
        ].dropna(subset=["date"])
        frames.append(target)
        time.sleep(0.15)

    if not frames:
        return pd.DataFrame(
            columns=["date", "close", "foreign", "institution", "foreign_ratio"]
        )

    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"], format="%Y.%m.%d", errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["institution"] = pd.to_numeric(df["institution"], errors="coerce")
    df["foreign"] = pd.to_numeric(df["foreign"], errors="coerce")
    df["foreign_ratio"] = (
        df["foreign_ratio"].astype(str).str.replace("%", "", regex=False)
    )
    df["foreign_ratio"] = pd.to_numeric(df["foreign_ratio"], errors="coerce")
    df = (
        df.dropna(subset=["date", "close"])
        .drop_duplicates("date")
        .sort_values("date")
        .reset_index(drop=True)
    )
    return df


def _stock_cache_path(code: str) -> Path:
    return DATA_DIR / f"stock_{code}.csv"


def load_or_fetch_stock(code: str, pages: int = 20, refresh: bool = False) -> pd.DataFrame:
    """개별 종목 수급을 캐시 우선으로 로드한다.

    Returns:
        columns=[date, close, foreign, institution, foreign_ratio]
    """
    path = _stock_cache_path(code)
    if path.exists() and not refresh:
        return pd.read_csv(path, parse_dates=["date"])

    df = fetch_stock_flow(code, pages=pages)
    if not df.empty:
        df.to_csv(path, index=False)
    return df


if __name__ == "__main__":
    # 수동 테스트용
    logging.basicConfig(level=logging.INFO)
    for mkt in ["KOSPI", "KOSDAQ"]:
        out = load_or_fetch(mkt, pages=5, refresh=True)
        print(f"\n=== {mkt}: {len(out)} rows ===")
        print(out.tail(3).to_string())

    print("\n=== 코스피200 목록 ===")
    lst = fetch_kospi200_list(refresh=True)
    print(f"{len(lst)}종목, 샘플:")
    print(lst.head(3).to_string())

    print("\n=== 삼성전자(005930) 수급 ===")
    s = load_or_fetch_stock("005930", pages=3, refresh=True)
    print(f"{len(s)} rows")
    print(s.tail(3).to_string())
