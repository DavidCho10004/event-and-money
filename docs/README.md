# 이 프로젝트를 다시 여는 사람에게

한국 주식 수급(투자자별 순매수) 분석 프로젝트의 입구 문서.
2026-07-13 ~ 07-17 5일간 구축·검증을 마치고 **주간 무인 운영 모드**로 전환된 상태다.
결론 먼저: **약 80개 검증 조합에서 매수 신호 0개, 회피 신호 4개.** 도구의 정체성은
"매수 신호 생성기"가 아니라 **종목 발굴 필터 + 회피 경보기**다.

---

## ① 읽는 순서

1. **[수급프로젝트_중간점검_보고서-26.07.17.md](수급프로젝트_중간점검_보고서-26.07.17.md)** — 전체 서사.
   무엇을 만들었고, 무엇을 검증했고, 왜 그 결론에 도달했는지 (요약 → 시스템 → 검증 총괄 → 교훈 → 한계 → 다음 단계)
2. **[hypotheses.md](hypotheses.md)** — 가설 검증 대장 (공식 기록).
   가설별 사전 예상·결과·기각 근거 한 줄씩. 재현 명령어 포함. **2026-07-14부로 수급 전략 탐색 영구 종료** — 이후 열람용
3. **운영 화면 [/backtest](https://event-and-money-production.up.railway.app/backtest)** — 전 검증 성적표 + 강건성 열 + 결론 배너.
   숫자를 눈으로 확인하는 곳 (코드: [backend/templates/backtest.html](../backend/templates/backtest.html), [backend/services/backtest_view.py](../backend/services/backtest_view.py))

보조 문서: [data_validation_log.md](data_validation_log.md) (외부 교차검증 기록), [supply_demand.md](supply_demand.md), [dart_integration.md](dart_integration.md)

---

## ② 데이터 지도

### 원본 저장소 — Parquet, 로컬 전용 (gitignore, **커밋되지 않음**)

위치: `수급/data/` (스키마 정의: [수급/pipeline/store.py](../수급/pipeline/store.py))

| 경로 | 내용 |
|---|---|
| `수급/data/snapshots/snapshots_YYYY.parquet` | 시점별 전 종목 단면: 날짜·시장·코드·종가·시가·시가총액·상장주식수·PER·PBR·외인지분율 |
| `수급/data/flows/flows_YYYY.parquet` | (날짜 × 종목 × 투자자) 순매수 롱포맷: 거래대금·거래량·**해상도** |
| `수급/data/_checkpoints/` | 백필 진행 체크포인트 |

**D/M 해상도 (이중계산 원천 차단 장치)**:
- `해상도='M'`: 월별 백필 행 — 2016-01 ~ 2025-06 (114개월), 날짜는 그 달 마지막 영업일, 한 달치 합계
- `해상도='D'`: 일별 행 — 2025-07 ~ 현재, 하루치
- 일별 커버 시작일 이전 구간에만 M행이 존재하므로 집계 시 겹치지 않는다
- 투자자 6종: 개인 / 외국인 / 기타외국인 / 기관합계 / 사모 / 기타법인
- 모든 upsert는 키 중복 시 교체 → 멱등성 보장

### 검증 결과 CSV — `data/processed/backtest/` (커밋됨, /backtest 화면의 원자료)

파일명 규칙: `*_monthly`(진입월 단위 전체 기록) / `*_summary`(전략×보유기간 요약) / `*_robustness`(강건성 판정)

| CSV 접두어 | 어느 검증인가 | 생성 엔진 |
|---|---|---|
| `backtest_*.csv` | 전략 a~d′ (삼박자·강도·교집합·미반응 매집 + 대조군) | [backtest.py](../수급/pipeline/backtest.py) |
| `backtest_hyp_*.csv` | H1 폭락 레짐 / H2 지속성 / H3 개인 쏠림 역신호 | [backtest_hypotheses.py](../수급/pipeline/backtest_hypotheses.py) |
| `backtest_factor_*.csv` | F1 저PBR / F2 모멘텀 / F3 퀄리티 프록시 | [backtest_factors.py](../수급/pipeline/backtest_factors.py) |
| `backtest_f1_stress*.csv` | F1 실전화 스트레스 (유동성 컷·상폐 -100%·비용 1.0%) — F1 기각의 결정타 | [backtest_f1_stress.py](../수급/pipeline/backtest_f1_stress.py) |
| `backtest_daily*.csv` | A 익일 감쇠 곡선 (일별 외인 상위 → t+1 진입) | [backtest_daily.py](../수급/pipeline/backtest_daily.py) |
| `backtest_freq_*.csv` | B 빈도 가설 (직전 20거래일 순위 진입 일수) | [backtest_freq.py](../수급/pipeline/backtest_freq.py) |
| `backtest_event_*.csv` | E1/E2 사건 월 개인·외인 쏠림 (사건 36건) | [backtest_event.py](../수급/pipeline/backtest_event.py) |
| `backtest_cond_*.csv` | C1~C3 조건부 1라운드 (베이스 이등분: 밸류/위치/변동성) | [backtest_conditional.py](../수급/pipeline/backtest_conditional.py) |
| `backtest_*_test2020.csv` | 파이프라인 시험 실행분 (2020년 한정) — 분석에 사용하지 말 것 | backtest.py `--start/--end` |

### 화면용 CSV — `data/processed/` (커밋됨, 주간 갱신 대상)

| 경로 | 용도 |
|---|---|
| `buy_review_KOSPI.csv` / `buy_review_KOSDAQ.csv` / `buy_review_meta.json` | 매수 검토 스크리너 (/buy-review) |
| `market_flows_{시장}_{W,M}.csv` | 수급 분석 화면 (/supply-demand) 주간/월간 추이 |
| `detail/` | 종목 상세 (/buy-review/{코드}) |
| `event_supply/` | 사건×수급 지도 ([event_supply_map.py](../수급/pipeline/event_supply_map.py) 산출) |
| `cowalk/` | 수급동행 진단 — 종목별 52주 상관·분포·검산 낱장, 상세 배지 원자료 ([cowalk.py](../수급/pipeline/cowalk.py), 검증 아닌 기술 통계) |

`수급/data/*.csv` (stock_*.csv, supply_demand_*.csv 등)는 구버전 Streamlit 로컬 앱의 캐시로, 현재 검증·운영과 무관하다.

---

## ③ 재현 방법

```bash
cd 수급
python pipeline/backtest.py              # 전략 a~d′ (전체: 2016-04~, 시험: --start 2020-01 --end 2020-12)
python pipeline/backtest_hypotheses.py   # H1~H3
python pipeline/backtest_factors.py      # F1~F3
python pipeline/backtest_f1_stress.py    # F1 스트레스
python pipeline/backtest_daily.py        # A (일별)
python pipeline/backtest_freq.py         # B (빈도)
python pipeline/backtest_event.py        # E1/E2 (사건)
python pipeline/backtest_conditional.py  # C1~C3 (조건부 1라운드)
```

전제: 로컬에 원본 Parquet이 있어야 한다 (git에 없음 — ⑤ 백업 항목 참조).
결과는 `data/processed/backtest/`에 덮어쓰기 저장된다.

**검증 규율 (모든 엔진 공통 — 데이터 마이닝과 검증을 가르는 선)**:
1. **사전 고정**: 가설·사전 예상·판정 기준을 각 스크립트 상단 docstring에 먼저 기록, 결과가 달라도 그대로 보고
2. **튜닝 금지**: 파라미터는 전부 코드 상단 상수 ([backtest.py](../수급/pipeline/backtest.py) 재사용), 사후 조정 금지. 변경은 성과를 깎는 방향만 허용 (f1_stress 방식)
3. **강건성 자동**: ①기간 분할(2016~20 / 2021~26) 양쪽 양수 ②최고 승리 달 3개 제거 후 양수 ③거래비용(왕복 0.3%) 후 양수 — 셋 다 통과해야 생존. 벤치마크는 시총가중 시장 프록시 + 같은 시점·같은 개수 랜덤(시드 42 고정, 100회 평균). 룩어헤드 차단(각 시점 t까지의 데이터만 사용)

---

## ④ 운영 루틴 (매주 금요일 저녁)

[수급/run_weekly.bat](../수급/run_weekly.bat) 더블클릭 → 마지막 줄 확인 (30초):

```
[1/5] 증분 수집 (fetch_incremental.py)   ← KRX 계정 입력 (비밀번호 숨김 입력)
[2/5] 데이터 검증 (validate.py)          ← 실패 시 push 차단
[3/5] 스크리너/상세 데이터 생성 (analyze.py)
[4/5] git 커밋 (data/processed)
[5/5] git push → Railway 자동 배포 (1~2분 후 운영 반영)
```

- 성공: `✅ VALIDATED & PUSHED` / 변경 없음: `✅ VALIDATED (변경 없음)` / 실패: `❌` + `수급/logs/run_weekly_YYYYMMDD.log` 확인
- [validate.py](../수급/pipeline/validate.py) 5종 검증: 종목 수 범위 / 신선도 / 전 주체 순매수 합 항등식(±1억) / 결측률 / 이상치(5조 임계)
- **수집은 반드시 이 PC에서**: KRX가 클라우드 IP를 차단(400 LOGOUT)하므로 원격/클라우드 실행 불가
- 주말 실사용: 스크리너에서 삼박자+지속형으로 후보 발굴, ⚠배지(상장주식수 변동·장외 지분변동·개인 몰림)는 회피, 판단은 사람

---

## ⑤ 미완 목록

| 항목 | 상태 | 트리거 / 메모 |
|---|---|---|
| **원본 Parquet 외장 백업** | ✅ 드라이브 사본 1개 (2026-07-18) | `수급/data/` 전체(43파일, 75.6MB)를 zip 무결성 검증 후 구글 드라이브 업로드. 단 주간 수집으로 데이터가 계속 늘어남 — **주기적 재백업 필요** (월 1회 권장, 자동화 미구축) |
| H5 특정 외인 실명 추적 (DART 5% 공시) | 보류 | 트리거: DART 공시 수집 동기가 쌓이면 F3 재무 데이터와 세트로. 함정 3개(5영업일 지연·5% 문턱 아래 불가시·발표일 갭 선취)는 [hypotheses.md](hypotheses.md#미검증-보류-탐색-종료-대상-아님--별도-승인-시에만) 참조 |
| F3 퀄리티 재검토 | 보류 | 프록시(PBR/PER)로는 기각. 진짜 재무 데이터(DART) 수집 시에만 재검토 |
| 2단계 유사율 진단 | **완료 (2026-07-18)** | 수급동행 배지로 구현 — [hypotheses.md](hypotheses.md) '진단 도구' 섹션, 엔진 [cowalk.py](../수급/pipeline/cowalk.py) |
| H4 연기금 | **종결 (2026-07-17)** | 검증 안 함으로 종결 — 기대값이 수집 비용 미달 ([hypotheses.md](hypotheses.md) 기록) |
| 주간 운영 리허설 | 진행 중 | 7/17 첫 무인 실행 성공. 7/13 폭락일 데이터의 V4-b 오탐 여부 관찰 계속 |

---

*작성: 2026-07-18 | 수급 프로젝트는 Event & Money 본편의 한 챕터로 콘텐츠화 예정 — "수급 따라 사기를 80번 검증한 기록"*
