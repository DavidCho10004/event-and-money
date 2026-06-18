"""
DART 공시 자동 수집 → 사건 등록 후보 CSV 생성

용도:
    트랙D(자본 이벤트) 사건 등록을 반자동화.
    수기 일일이 찾던 액면분할·유증·CB·자사주매입·물적분할 공시를 한 번에 수집.

사전 준비:
    1. https://opendart.fss.or.kr/ 회원가입 → API 키 발급 (무료)
    2. .env 파일에 추가: DART_API_KEY=발급받은_키
    3. 필요 패키지: pip install requests

사용 예:
    # 시총 상위 기업의 최근 3년 액면분할만 수집
    python scripts/fetch_dart_disclosures.py --types B035 --from 20230101 --to 20251231

    # 자사주 매입·소각 + 유상증자 통합 수집
    python scripts/fetch_dart_disclosures.py --types B029,B030,B005 --from 20240101

    # 특정 종목만
    python scripts/fetch_dart_disclosures.py --stock 005930 --types B035

출력:
    data/dart_candidates_<timestamp>.csv  → 수기 검토 후 events.json에 추가
    data/dart_corp_codes.json             → corp_code 매핑 캐시
"""
import argparse
import csv
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.dart import (
    DartClient,
    DISCLOSURE_TYPES,
    disclosure_to_event_candidate,
    parse_disclosures,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "data"


def main():
    parser = argparse.ArgumentParser(description="DART 공시 수집 → 사건 후보 CSV")
    parser.add_argument("--from", dest="bgn_de", default=None, help="시작일 YYYYMMDD")
    parser.add_argument("--to", dest="end_de", default=None, help="종료일 YYYYMMDD")
    parser.add_argument("--types", default="B035,B005,B029,B030,B015,B026",
                        help=f"공시유형 콤마구분. 가능: {','.join(DISCLOSURE_TYPES.keys())}")
    parser.add_argument("--stock", default=None, help="특정 종목코드(6자리)만")
    parser.add_argument("--corp-codes-cache", action="store_true",
                        help="corp_code 전체를 다시 받고 캐시")
    args = parser.parse_args()

    client = DartClient(cache_dir=OUT_DIR / "dart_cache")

    if args.corp_codes_cache:
        logger.info("corp_code 전체 다운로드 + 캐시")
        codes = client.download_corp_codes(force=True)
        out = [{"corp_code": c.corp_code, "corp_name": c.corp_name,
                "stock_code": c.stock_code} for c in codes if c.stock_code]
        (OUT_DIR / "dart_corp_codes.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("상장사 %d건 저장 → data/dart_corp_codes.json", len(out))

    requested = [t.strip() for t in args.types.split(",") if t.strip()]
    unknown = [t for t in requested if t not in DISCLOSURE_TYPES]
    if unknown:
        logger.error("알 수 없는 공시유형: %s. 가능: %s", unknown, list(DISCLOSURE_TYPES.keys()))
        sys.exit(1)

    all_candidates = []
    for ty in requested:
        label = DISCLOSURE_TYPES[ty][2]
        logger.info("── %s (%s) 수집 ──", ty, label)
        page = 1
        while True:
            payload = client.list_disclosures(
                stock_code=args.stock,
                bgn_de=args.bgn_de,
                end_de=args.end_de,
                pblntf_detail_ty=ty,
                page_no=page,
                page_count=100,
            )
            disclosures = parse_disclosures(payload)
            if not disclosures:
                break

            for d in disclosures:
                cand = disclosure_to_event_candidate(d, ty)
                if cand:
                    all_candidates.append(cand)

            total_page = int(payload.get("total_page", 1) or 1)
            if page >= total_page:
                break
            page += 1
            time.sleep(0.15)  # DART 분당 한도 보호

        logger.info("  → 누적 %d건", len(all_candidates))

    if not all_candidates:
        logger.warning("수집된 공시 없음.")
        return

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = OUT_DIR / f"dart_candidates_{ts}.csv"
    json_path = OUT_DIR / f"dart_candidates_{ts}.json"

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["event_date", "name_ko", "category", "sub_type",
                    "stock_code", "rcept_no", "url"])
        for c in all_candidates:
            meta = c.get("_dart_meta", {})
            w.writerow([c["event_date"], c["name_ko"], c["category"], c["sub_type"],
                        c["affected_entities"][0] if c["affected_entities"] else "",
                        meta.get("rcept_no", ""), meta.get("url", "")])

    json_path.write_text(json.dumps(all_candidates, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    logger.info("=" * 60)
    logger.info("완료: %d건 수집", len(all_candidates))
    logger.info("  CSV (검토용): %s", csv_path)
    logger.info("  JSON (사건 등록 후보): %s", json_path)
    logger.info("")
    logger.info("다음 단계: CSV를 엑셀에서 검토 → 중요 사건만 골라")
    logger.info("  attribution·description·affected_entities 등을 수기 보강 후")
    logger.info("  data/events.json에 M0xx ID 부여해서 추가")


if __name__ == "__main__":
    main()
