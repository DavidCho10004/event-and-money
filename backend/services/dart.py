"""
DART (전자공시시스템) OpenAPI 클라이언트

용도:
- 트랙D(자본 이벤트) 자동 수집: 액면분할·유증·CB·자사주 매입/소각·물적분할
- 사용자가 로컬에서 실행해 후보 사건 CSV 생성 → 수기 검토 후 events.json에 등록

요구사항:
- DART OpenAPI 키 (https://opendart.fss.or.kr/ 무료 회원가입)
- .env 파일에 DART_API_KEY=xxx 설정
- requests, python-dotenv 패키지

샌드박스 제약:
- 외부 호출이 막혀있어 로컬(Windows)에서만 실행 가능
- 본 모듈은 호출/파싱 로직만 제공
"""
from __future__ import annotations

import logging
import os
import time
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

logger = logging.getLogger(__name__)

DART_BASE = "https://opendart.fss.or.kr/api"

# pblntf_detail_ty → (category, sub_type, 한글 설명)
# 트랙D 자본 이벤트 핵심 공시 매핑
DISCLOSURE_TYPES = {
    "B035": ("capital_event", "stock_split",          "주식분할(액면분할)"),
    "B036": ("capital_event", "stock_consolidation",  "주식병합"),
    "B005": ("capital_event", "rights_offering",      "유상증자결정"),
    "B006": ("capital_event", "bonus_issue",          "무상증자결정"),
    "B015": ("capital_event", "cb_issuance",          "전환사채(CB) 발행결정"),
    "B016": ("capital_event", "bw_issuance",          "신주인수권부사채(BW) 발행결정"),
    "B029": ("capital_event", "buyback",              "자기주식 취득결정"),
    "B030": ("capital_event", "treasury_sale",        "자기주식 처분결정"),
    "B031": ("capital_event", "buyback_trust",        "자기주식 신탁계약 체결"),
    "B026": ("capital_event", "spin_off",             "회사분할결정"),
    "B028": ("capital_event", "merger",               "회사합병결정"),
    "B025": ("capital_event", "stock_swap",           "주식교환·이전 결정"),
}


# ───────────────────────────────────────────
# 데이터 클래스
# ───────────────────────────────────────────

@dataclass
class CorpCode:
    """corpCode.xml의 개별 엔트리"""
    corp_code: str          # 8자리 DART 고유번호
    corp_name: str          # 한글 회사명
    stock_code: str         # 종목코드 (상장사만)
    modify_date: str        # YYYYMMDD


@dataclass
class Disclosure:
    """공시 1건"""
    corp_code: str
    corp_name: str
    stock_code: str         # ".KS" suffix는 없음 (DART는 6자리)
    report_nm: str          # 공시명
    rcept_no: str           # 접수번호 (URL용)
    flr_nm: str             # 제출인
    rcept_dt: str           # 접수일자 YYYYMMDD
    rm: str                 # 비고

    @property
    def yahoo_symbol(self) -> str | None:
        if not self.stock_code:
            return None
        return f"{self.stock_code}.KS"

    @property
    def url(self) -> str:
        return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={self.rcept_no}"


# ───────────────────────────────────────────
# 클라이언트
# ───────────────────────────────────────────

class DartClient:
    """DART OpenAPI 호출 래퍼.

    requests 패키지 사용. 호출 한도 = 일일 10,000회 (1분당 1,000회 권장 한도).
    """

    def __init__(self, api_key: str | None = None, cache_dir: Path | None = None):
        self.api_key = api_key or os.getenv("DART_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "DART_API_KEY가 설정되지 않았습니다. "
                ".env 파일에 DART_API_KEY=xxx 또는 환경변수로 지정하세요."
            )
        self.cache_dir = Path(cache_dir or "data/dart_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # lazy import — 모듈 import 시점에 requests가 없어도 무사히 통과
        import requests
        self._requests = requests
        self._session = requests.Session()

    def _get(self, endpoint: str, **params):
        params["crtfc_key"] = self.api_key
        url = f"{DART_BASE}/{endpoint}"
        for attempt in range(3):
            try:
                resp = self._session.get(url, params=params, timeout=20)
                resp.raise_for_status()
                return resp
            except self._requests.RequestException as e:
                logger.warning("DART %s 시도 %d 실패: %s", endpoint, attempt + 1, e)
                time.sleep(2 ** attempt)
        raise RuntimeError(f"DART {endpoint} 호출 실패")

    # ── 회사 코드 매핑 ──
    def download_corp_codes(self, force: bool = False) -> list[CorpCode]:
        """전체 corp_code 목록 다운로드 (DART corpCode.xml).

        결과는 cache에 저장. force=True면 재다운로드.
        """
        cache = self.cache_dir / "corp_codes.xml"
        if not cache.exists() or force:
            logger.info("DART corp_code 전체 다운로드 중...")
            resp = self._get("corpCode.xml")
            with zipfile.ZipFile(BytesIO(resp.content)) as zf:
                xml_bytes = zf.read("CORPCODE.xml")
            cache.write_bytes(xml_bytes)
        return parse_corp_codes(cache.read_bytes())

    def list_disclosures(
        self,
        corp_code: str | None = None,
        stock_code: str | None = None,
        bgn_de: str | None = None,
        end_de: str | None = None,
        pblntf_detail_ty: str | None = None,
        page_no: int = 1,
        page_count: int = 100,
    ) -> dict:
        """공시 목록 조회 (list.json).

        - corp_code: 8자리 DART 고유번호 (corp_code/stock_code 중 하나는 필수)
        - stock_code: 6자리 종목코드
        - bgn_de, end_de: 'YYYYMMDD'
        - pblntf_detail_ty: 'B035' 등 세부 공시유형
        """
        params = {"page_no": page_no, "page_count": page_count}
        if corp_code:
            params["corp_code"] = corp_code
        if stock_code:
            params["stock_code"] = stock_code
        if bgn_de:
            params["bgn_de"] = bgn_de
        if end_de:
            params["end_de"] = end_de
        if pblntf_detail_ty:
            params["pblntf_detail_ty"] = pblntf_detail_ty

        resp = self._get("list.json", **params)
        return resp.json()


# ───────────────────────────────────────────
# 파싱 헬퍼 (모듈 함수: API 키 없이도 단위테스트 가능)
# ───────────────────────────────────────────

def parse_corp_codes(xml_bytes: bytes) -> list[CorpCode]:
    """corpCode.xml 바이트 → CorpCode 리스트."""
    root = ET.fromstring(xml_bytes)
    out: list[CorpCode] = []
    for el in root.findall(".//list"):
        out.append(CorpCode(
            corp_code=(el.findtext("corp_code") or "").strip(),
            corp_name=(el.findtext("corp_name") or "").strip(),
            stock_code=(el.findtext("stock_code") or "").strip(),
            modify_date=(el.findtext("modify_date") or "").strip(),
        ))
    return out


def parse_disclosures(payload: dict) -> list[Disclosure]:
    """list.json 응답 → Disclosure 리스트."""
    if payload.get("status") != "000":
        msg = payload.get("message", payload.get("status"))
        if payload.get("status") == "013":
            return []  # 조회된 데이터가 없음
        raise RuntimeError(f"DART API 오류: {msg}")
    return [
        Disclosure(
            corp_code=row.get("corp_code", ""),
            corp_name=row.get("corp_name", ""),
            stock_code=row.get("stock_code", ""),
            report_nm=row.get("report_nm", ""),
            rcept_no=row.get("rcept_no", ""),
            flr_nm=row.get("flr_nm", ""),
            rcept_dt=row.get("rcept_dt", ""),
            rm=row.get("rm", ""),
        )
        for row in payload.get("list", [])
    ]


def disclosure_to_event_candidate(d: Disclosure, detail_ty: str) -> dict | None:
    """공시 1건 → events.json 등록 후보 dict.

    수기 검토용 — attribution이나 description은 사람이 채워야 함.
    """
    mapping = DISCLOSURE_TYPES.get(detail_ty)
    if not mapping:
        return None
    category, sub_type, ty_label = mapping

    # YYYYMMDD → YYYY-MM-DD
    rd = d.rcept_dt
    iso_date = f"{rd[:4]}-{rd[4:6]}-{rd[6:8]}" if len(rd) == 8 else None
    if not iso_date:
        return None

    return {
        "id": f"DART-{d.rcept_no}",          # 임시 ID, 수기 검토 후 M0xx로 교체
        "name_ko": f"{d.corp_name} {ty_label}",
        "name_en": f"{d.corp_name} {sub_type}",   # 영문명은 수기로 보강 권장
        "event_date": iso_date,
        "announce_date": iso_date,
        "category": category,
        "sub_type": sub_type,
        "description_ko": d.report_nm,
        "energy_impact": False,
        "scale": "micro",
        "attribution": {"political": 0, "corporate": 80, "macro": 20},  # 기본값 — 수기 조정 필수
        "attribution_rationale": "DART 자동 수집 (수기 검토 필요)",
        "affected_entities": [d.yahoo_symbol] if d.yahoo_symbol else [],
        "comparable_universe": ["^KS11"],
        "_dart_meta": {
            "rcept_no": d.rcept_no,
            "url": d.url,
            "flr_nm": d.flr_nm,
            "report_nm": d.report_nm,
            "rm": d.rm,
        },
    }
