# 수급 분석 (Supply & Demand) — 독립 실행 앱

Event & Money 본체와 **분리된 별도 프로그램**입니다. 코스피/코스닥 투자자별
순매수(개인·외국인·기관)를 일/주/월 시계열로 보여주고, 지수 등락과의 관계를
누적 순매수선 + 상관계수로 확인합니다.

> 본체(FastAPI 사건 분석 앱)와 코드·배포가 완전히 분리되어 있어, 이 앱을 고쳐도
> 운영 중인 Event & Money 사이트에는 영향이 없습니다.

## 로컬 실행

```bash
# 저장소 루트에서
pip install -r supply_demand_app/requirements.txt
python -m uvicorn supply_demand_app.app:app --reload --port 8090
# → http://localhost:8090
```

## 데이터

- **실데이터**: `data/processed/supply_demand_{KOSPI,KOSDAQ}_{D,W,M}.csv` 가 있으면 자동 사용.
  - 생성: 로컬에서 `python scripts/fetch_supply_demand.py` → `python scripts/analyze_supply_demand.py`
  - 폴더 위치를 바꾸려면 환경변수 `SUPPLY_DEMAND_DATA_DIR` 지정.
- **데모(샘플)**: 실데이터가 없으면 결정론적 합성 데이터로 UI를 표시하고 상단에 "데모" 배너 노출.

## 배포 (Railway, 별도 서비스)

본체와 **다른 Railway 서비스**로 띄웁니다:

1. Railway 프로젝트에서 **New Service → GitHub Repo**(같은 저장소) 추가
2. 서비스 설정 **Root Directory = `supply_demand_app`**
3. 자동으로 `requirements.txt` / `railway.json` / `Procfile` 인식 → 배포
4. 새 서비스에 도메인이 발급되면 그 URL로 접속 (본체와 별개 주소)

> Root Directory 를 `supply_demand_app` 로 두면 이 폴더의 `requirements.txt`(가벼움)만
> 설치되므로 본체 빌드와 서로 간섭하지 않습니다.

## 구성

| 파일 | 역할 |
|------|------|
| `app.py` | FastAPI 진입점 (`/`, `/api/supply-demand`, `/healthz`) |
| `service.py` | 집계·상관 계산 (파이썬 표준 라이브러리만) |
| `templates/index.html` | 자체 nav + Chart.js 2단 차트 + 상관표 |
| `requirements.txt` / `Procfile` / `railway.json` / `runtime.txt` | 독립 배포 설정 |
