# -*- coding: utf-8 -*-
"""
누적 저장소 공통 모듈 (로컬 전용 — Parquet은 git에 커밋하지 않는다)

구조:
    data/snapshots/snapshots_YYYY.parquet   # 시점별 전 종목 단면
    data/flows/flows_YYYY.parquet           # (날짜 × 종목 × 투자자) 순매수 롱포맷

flows 스키마 (단일 롱포맷, 2층 해상도):
    날짜(YYYY-MM-DD) | 시장 | 코드(6자리 문자열) | 투자자 | 거래대금 | 거래량 | 해상도
    - 해상도 'D': 일별 행 (해당 날짜 하루치)
    - 해상도 'M': 월별 백필 행 (날짜=그 달 마지막 영업일, 한 달치 합계)
    - 일별 커버 시작일 이전 구간만 M행이 존재 → 집계 시 이중계산 없음

snapshots 스키마:
    날짜 | 시장 | 코드 | 종가 | 시가총액 | 상장주식수 | PER | PBR | 외인지분율

모든 upsert는 키 중복 시 새 데이터로 교체 → 멱등성 보장.
"""

import json
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent   # 수급/
DATA_DIR = BASE_DIR / "data"
SNAP_DIR = DATA_DIR / "snapshots"
FLOW_DIR = DATA_DIR / "flows"
PROCESSED_DIR = DATA_DIR / "processed"
CHECKPOINT_DIR = DATA_DIR / "_checkpoints"

SNAP_KEYS = ["날짜", "시장", "코드"]
FLOW_KEYS = ["날짜", "시장", "코드", "투자자", "해상도"]

SNAP_COLS = SNAP_KEYS + ["종가", "시가총액", "상장주식수", "PER", "PBR", "외인지분율"]
FLOW_COLS = FLOW_KEYS[:4] + ["거래대금", "거래량", "해상도"]


def _ensure_dirs():
    for d in (SNAP_DIR, FLOW_DIR, PROCESSED_DIR, CHECKPOINT_DIR):
        d.mkdir(parents=True, exist_ok=True)


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """코드 6자리 zero-padding + 날짜 문자열(YYYY-MM-DD) 강제."""
    df = df.copy()
    df["코드"] = df["코드"].astype(str).str.zfill(6)
    df["날짜"] = pd.to_datetime(df["날짜"]).dt.strftime("%Y-%m-%d")
    return df


def _year_of(date_str: str) -> int:
    return int(str(date_str)[:4])


def _upsert(df_new: pd.DataFrame, dir_path: Path, prefix: str, keys: list,
            merge_na: bool = False) -> None:
    """연도별 parquet 파일에 upsert (키 중복 시 새 행으로 교체).

    merge_na=True: 새 행의 NA 셀은 기존 값으로 보존 (부분 컬럼 upsert 지원 —
    예: 일별 외인지분율만 추가할 때 월말 전체 스냅샷의 종가/PER를 지우지 않음).
    """
    _ensure_dirs()
    df_new = _normalize(df_new)
    for year, part in df_new.groupby(df_new["날짜"].map(_year_of)):
        path = dir_path / f"{prefix}_{year}.parquet"
        part = part.drop_duplicates(subset=keys, keep="last")
        if path.exists():
            old = pd.read_parquet(path)
            if merge_na:
                merged = (part.set_index(keys)
                          .combine_first(old.set_index(keys))
                          .reset_index())
            else:
                merged = pd.concat([old, part], ignore_index=True)
                merged = merged.drop_duplicates(subset=keys, keep="last")
        else:
            merged = part
        merged = merged.sort_values(keys).reset_index(drop=True)
        merged.to_parquet(path, index=False)
        logger.info("저장: %s (%d행)", path.name, len(merged))


def upsert_snapshots(df: pd.DataFrame) -> None:
    _upsert(df[SNAP_COLS], SNAP_DIR, "snapshots", SNAP_KEYS, merge_na=True)


def upsert_flows(df: pd.DataFrame) -> None:
    _upsert(df[FLOW_COLS], FLOW_DIR, "flows", FLOW_KEYS)


def _read_all(dir_path: Path, prefix: str, years=None) -> pd.DataFrame:
    """연도별 parquet을 읽어 결합. years=None이면 전체."""
    files = sorted(dir_path.glob(f"{prefix}_*.parquet"))
    if years is not None:
        years = {int(y) for y in years}
        files = [f for f in files if int(f.stem.split("_")[1]) in years]
    if not files:
        return pd.DataFrame()
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def read_snapshots(years=None) -> pd.DataFrame:
    return _read_all(SNAP_DIR, "snapshots", years)


def read_flows(years=None) -> pd.DataFrame:
    return _read_all(FLOW_DIR, "flows", years)


def last_flow_date(resolution: str = "D") -> str | None:
    """해당 해상도의 마지막 수집일 (없으면 None)."""
    df = read_flows()
    if df.empty:
        return None
    sub = df[df["해상도"] == resolution]
    return None if sub.empty else sub["날짜"].max()


def load_checkpoint(name: str) -> dict:
    path = CHECKPOINT_DIR / f"{name}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_checkpoint(name: str, data: dict) -> None:
    _ensure_dirs()
    path = CHECKPOINT_DIR / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
