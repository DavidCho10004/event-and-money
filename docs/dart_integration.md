# DART OpenAPI 연동 — 자본 이벤트 자동 수집

트랙D(액면분할·유증·CB·자사주매입·물적분할 등) 사건을 수기 검색 대신 **DART 공시에서 일괄 수집**해 events.json 등록 후보를 만드는 파이프라인.

## 설치 (1회)

```cmd
pip install requests
```

## API 키 발급 (1회)

1. https://opendart.fss.or.kr/ 회원가입 (무료)
2. 마이페이지 → API 키 발급
3. 프로젝트 루트 `.env` 파일에 추가:
   ```
   DART_API_KEY=발급받은_키_40자
   ```

## 일반적인 사용 흐름

### 1. corp_code 캐시 (1회만 실행하면 됨)

```cmd
python scripts/fetch_dart_disclosures.py --corp-codes-cache --types B035
```

→ `data/dart_corp_codes.json`에 전체 상장사 매핑 저장.

### 2. 공시유형별 수집

```cmd
# 최근 5년 액면분할 모두 수집
python scripts/fetch_dart_disclosures.py --types B035 --from 20200101 --to 20251231

# 자사주 매입·소각·유증을 한 번에
python scripts/fetch_dart_disclosures.py --types B029,B030,B005 --from 20240101

# 삼성전자(005930)만
python scripts/fetch_dart_disclosures.py --stock 005930 --types B035,B005,B029
```

→ `data/dart_candidates_<timestamp>.csv` (엑셀 검토용) + `.json` (변환된 사건 후보) 생성.

### 3. 수기 검토 → events.json 등록

엑셀에서 CSV를 열어:
- 노이즈/소액 공시 제외
- 중요한 사건만 골라
- `attribution`, `description`, `affected_entities` 수기 보강
- `M0xx` ID 부여
- `data/events.json`에 추가

### 4. 시딩 + 계산

```cmd
python -m backend.db.seed_data
python scripts/calc_all_returns.py
git add data/events.json backend/db/eventandmoney.db
git commit -m "data: DART 수집 사건 N건 추가"
git push origin master
```

## 지원 공시유형 (12종)

| 코드 | 카테고리 | sub_type | 한글명 |
|---|---|---|---|
| B035 | capital_event | stock_split | 주식분할(액면분할) |
| B036 | capital_event | stock_consolidation | 주식병합 |
| B005 | capital_event | rights_offering | 유상증자결정 |
| B006 | capital_event | bonus_issue | 무상증자결정 |
| B015 | capital_event | cb_issuance | 전환사채(CB) 발행결정 |
| B016 | capital_event | bw_issuance | 신주인수권부사채(BW) 발행결정 |
| B029 | capital_event | buyback | 자기주식 취득결정 |
| B030 | capital_event | treasury_sale | 자기주식 처분결정 |
| B031 | capital_event | buyback_trust | 자기주식 신탁계약 체결 |
| B026 | capital_event | spin_off | 회사분할결정 |
| B028 | capital_event | merger | 회사합병결정 |
| B025 | capital_event | stock_swap | 주식교환·이전 결정 |

## DART API 한도

- 일일 10,000회 (계정당)
- 분당 1,000회 권장
- 본 스크립트는 페이지 호출 사이 0.15초 sleep 적용

## 주의사항

- DART는 **공시 메타데이터만** 제공. 주가 데이터는 별도(yfinance).
- 공시 본문 파싱은 미구현 — 유증 규모·자사주 비율 등은 수기 보강 필요.
- 자동 변환된 `attribution` 기본값(0/80/20)은 임시 — 사건마다 직관 가중치 조정 필수.
- 공시 ID(`DART-rcept_no`)는 임시 — events.json에 등록할 때 `M0xx`로 교체.

## 다음 단계

- 시총 상위 50종목 필터링 (소형주 노이즈 제거)
- 공시 본문 파싱 — 유증 규모, 자사주 매입 한도 등 자동 추출
- events.json 자동 머지 (수기 검토 단계 단축)
