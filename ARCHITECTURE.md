# Event & Money — 풀스택 아키텍처 설계서

> 최종 수정일: 2026-04-30
> 이 문서는 CLAUDE.md와 함께 클로드 코드에 제공합니다.

---

## 1. 아키텍처 개요

```
┌─────────────────────────────────────────────────────────────┐
│                    eventandmoney.com                         │
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌───────────────┐  │
│  │  프론트엔드    │    │   백엔드 API  │    │  데이터 파이프 │  │
│  │  Next.js      │◄──►│   FastAPI     │◄──►│  라인          │  │
│  │  (React)      │    │  (Python)     │    │  (Python)     │  │
│  └──────┬───────┘    └──────┬───────┘    └──────┬────────┘  │
│         │                   │                    │           │
│         │            ┌──────▼───────┐    ┌──────▼────────┐  │
│         │            │ PostgreSQL   │    │ Yahoo Finance │  │
│         │            │ (Supabase)   │    │ FRED API      │  │
│         │            └──────────────┘    └───────────────┘  │
│         │                                                    │
│  ┌──────▼───────┐                                           │
│  │  Vercel       │  ← 프론트 호스팅                          │
│  └──────────────┘                                           │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. 기술 스택 선정 이유

### 프론트엔드: Next.js (React + TypeScript)

**왜 Streamlit이 아닌가:**
- Streamlit은 프로토타입에는 좋지만, **도메인 배포 + 커스텀 디자인 + SEO + 모바일 반응형**에 한계가 있음
- 글로벌 확장(영문 버전)을 고려하면 제대로 된 웹 프레임워크가 필요
- Next.js는 Vercel에 무료~저렴하게 배포 가능

**핵심 라이브러리:**
- `recharts` 또는 `plotly.js` — 인터랙티브 차트
- `tailwindcss` — 스타일링
- `next-intl` — 한국어/영어 다국어 지원 (나중에)
- `framer-motion` — 파급 체인 애니메이션

### 백엔드: FastAPI (Python)

**왜 FastAPI인가:**
- 규훈님이 Python에 익숙 — 새 언어 배울 필요 없음
- 데이터 분석 로직(pandas, numpy)을 그대로 API로 노출 가능
- 비동기 지원으로 Yahoo Finance API 호출 시 성능 우수
- 자동 API 문서 생성 (Swagger UI)

### DB: PostgreSQL (Supabase)

**왜 Supabase인가:**
- PostgreSQL을 무료 티어로 호스팅 (500MB, 충분)
- REST API 자동 생성 — 프론트에서 직접 쿼리 가능
- 인증/사용자 관리 내장 (나중에 구독 모델 시 필요)
- 규훈님이 SQLite 경험 있으니 SQL 자체는 익숙

### 호스팅/배포

| 구성요소 | 서비스 | 비용 | 비고 |
|---------|--------|------|------|
| 프론트엔드 | Vercel | 무료~$20/월 | Next.js 최적화 |
| 백엔드 API | Railway 또는 Render | 무료~$7/월 | FastAPI 컨테이너 |
| DB | Supabase | 무료 (500MB) | PostgreSQL |
| 도메인 | Namecheap/가비아 | ~$12/년 | eventandmoney.com |
| **월 총 비용** | | **약 $7~27/월** (1~4만원) | |

---

## 3. 데이터베이스 스키마

```sql
-- ========================================
-- 사건 테이블
-- ========================================
CREATE TABLE events (
    id          VARCHAR(4) PRIMARY KEY,     -- 'A01', 'B05' 등
    name_ko     TEXT NOT NULL,               -- '9/11 테러'
    name_en     TEXT NOT NULL,               -- 'September 11 Attacks'
    event_date  DATE NOT NULL,               -- 2001-09-11
    category    VARCHAR(20) NOT NULL,        -- 'war', 'financial', 'pandemic', 'policy', 'industry'
    sub_type    VARCHAR(50),                 -- 'terror', 'energy_war', 'bank_crisis' 등
    description_ko TEXT,                     -- 한국어 설명 (2~3문장)
    description_en TEXT,                     -- 영어 설명
    energy_impact  BOOLEAN DEFAULT FALSE,    -- 에너지 공급 차질 여부
    created_at  TIMESTAMP DEFAULT NOW()
);

-- ========================================
-- 자산 테이블
-- ========================================
CREATE TABLE assets (
    symbol      VARCHAR(20) PRIMARY KEY,     -- '^GSPC', 'CL=F', 'USDKRW=X'
    name_ko     TEXT NOT NULL,               -- 'S&P 500'
    name_en     TEXT NOT NULL,               -- 'S&P 500'
    asset_class VARCHAR(20) NOT NULL,        -- 'equity_index', 'commodity', 'currency', 'bond', 'sector_etf'
    data_start  DATE,                        -- 데이터 시작일 (ETF는 상장일)
    yahoo_symbol VARCHAR(20),                -- yfinance용 심볼 (다를 수 있음)
    description TEXT
);

-- ========================================
-- 가격 데이터 테이블 (원본)
-- ========================================
CREATE TABLE prices (
    symbol      VARCHAR(20) REFERENCES assets(symbol),
    trade_date  DATE NOT NULL,
    adj_close   NUMERIC(18,6),               -- 수정 종가
    volume      BIGINT,
    PRIMARY KEY (symbol, trade_date)
);

-- 인덱스: 사건일 기준 조회 최적화
CREATE INDEX idx_prices_date ON prices(trade_date);
CREATE INDEX idx_prices_symbol_date ON prices(symbol, trade_date);

-- ========================================
-- 수익률 매트릭스 테이블 (계산 결과)
-- ========================================
CREATE TABLE returns (
    event_id    VARCHAR(4) REFERENCES events(id),
    symbol      VARCHAR(20) REFERENCES assets(symbol),
    period      VARCHAR(10) NOT NULL,        -- 'D+1', 'D+7', 'D+30', 'D+180', 'D+365'
    return_pct  NUMERIC(10,4),               -- 수익률 (%, 예: -5.2300)
    price_base  NUMERIC(18,6),               -- 기준일 가격
    price_end   NUMERIC(18,6),               -- 종료일 가격
    date_base   DATE,                        -- 실제 기준 거래일
    date_end    DATE,                        -- 실제 종료 거래일
    PRIMARY KEY (event_id, symbol, period)
);

-- ========================================
-- 파급 체인 테이블
-- ========================================
CREATE TABLE domino_chains (
    id          SERIAL PRIMARY KEY,
    category    VARCHAR(20) NOT NULL,        -- 사건 카테고리 (war, financial 등)
    sub_type    VARCHAR(50),                 -- 세부 유형 (energy_war 등)
    stage       INTEGER NOT NULL,            -- 1, 2, 3 (1차/2차/3차 변인)
    variable_ko TEXT NOT NULL,               -- 'WTI/Brent 급등'
    variable_en TEXT,
    affected_assets TEXT[],                   -- 관련 자산 심볼 배열
    typical_lag VARCHAR(30),                 -- '즉시~수일', '수일~수주', '수주~수개월'
    direction   VARCHAR(10),                 -- 'up', 'down', 'mixed'
    parent_id   INTEGER REFERENCES domino_chains(id), -- 상위 변인 연결
    notes       TEXT
);

-- ========================================
-- 블로그/투자 기록 (나중에)
-- ========================================
CREATE TABLE investment_log (
    id          SERIAL PRIMARY KEY,
    trade_date  DATE NOT NULL,
    action      VARCHAR(10),                 -- 'buy', 'sell', 'hold'
    symbol      VARCHAR(20),
    amount_krw  BIGINT,
    quantity    NUMERIC(10,4),
    price       NUMERIC(18,6),
    rationale   TEXT,                         -- 매매 근거 (어떤 사건/가설 기반)
    result_pct  NUMERIC(10,4),               -- 실현 수익률 (매도 시)
    created_at  TIMESTAMP DEFAULT NOW()
);
```

---

## 4. API 엔드포인트 설계

### 기본 구조
```
https://api.eventandmoney.com/v1/
```

### 엔드포인트 목록

#### 사건 관련
```
GET  /events                    전체 사건 목록
GET  /events/{id}               사건 상세 (A01, B05 등)
GET  /events?category=war       카테고리별 필터
GET  /events/{id}/returns       특정 사건의 전체 자산 수익률
GET  /events/{id}/chain         특정 사건 유형의 파급 체인
```

#### 자산 관련
```
GET  /assets                    전체 자산 목록
GET  /assets/{symbol}/history   자산 가격 히스토리
GET  /assets/{symbol}/events    특정 자산이 영향받은 사건들
```

#### 분석 관련
```
GET  /analysis/heatmap                  사건×자산 히트맵 데이터
GET  /analysis/compare?events=A04,A08   사건 간 비교
GET  /analysis/recovery                 회복 속도 분석
GET  /analysis/correlation?assets=CL=F,^GSPC  자산간 상관관계
```

#### 파급 체인
```
GET  /chains                    전체 파급 체인 목록
GET  /chains/{category}         카테고리별 파급 체인 (트리 구조)
```

---

## 5. 프론트엔드 페이지 구조

```
eventandmoney.com/
│
├── /                           랜딩 페이지 (프로젝트 소개 + 주요 인사이트)
│
├── /events                     사건 목록 (카드형, 필터/검색)
│   └── /events/[id]            사건 상세 페이지
│                                ├── 사건 개요 (날짜, 배경, 핵심 포인트)
│                                ├── 자산별 수익률 테이블 + 차트
│                                ├── 타임라인 차트 (D-30 ~ D+365)
│                                └── 파급 체인 다이어그램 (인터랙티브)
│
├── /dashboard                  대시보드 (히트맵, 비교 도구)
│   ├── 히트맵 뷰 (사건×자산, 색상으로 수익률)
│   ├── 사건 비교 (2개 사건 겹쳐서 보기)
│   └── 자산 비교 (동일 사건에서 자산 간 비교)
│
├── /chains                     파급 체인 시각화
│   └── /chains/[category]      유형별 도미노 체인 (인터랙티브 트리)
│
├── /insights                   분석 인사이트 (블로그형)
│   └── /insights/[slug]        개별 분석 글
│
├── /about                      프로젝트/저자 소개
│
└── (향후)
    ├── /simulator              포트폴리오 스트레스 테스트
    └── /en/                    영문 버전
```

---

## 6. 프론트엔드 핵심 컴포넌트

### 6-1. 히트맵 (HeatmapView)
```
         S&P500  KOSPI  WTI   금    USD/KRW  VIX
9/11     -11.6%  -12%  +5%  +6%   +8%     +130%
리먼      -28%   -25%  -35% +12%  +40%     +200%
코로나    -34%   -35%  -65% +3%   +10%     +400%
이란전쟁  -8%    -12%  +60% +15%  +12%     +80%
```
- 셀 색상: 빨강(상승) ~ 파랑(하락) 그라데이션
- 클릭하면 해당 사건 상세 페이지로 이동
- 시점(D+1, D+7...) 토글 가능

### 6-2. 타임라인 차트 (TimelineChart)
- X축: D-30 ~ D+365 (사건일 = D)
- Y축: 기준일 대비 수익률 (%)
- 복수 자산을 겹쳐서 표시
- 마우스 호버 시 정확한 날짜/수익률 표시

### 6-3. 파급 체인 다이어그램 (DominoChain)
- 트리형 인터랙티브 다이어그램
- 1차 → 2차 → 3차 노드, 클릭하면 관련 자산/수익률 표시
- 애니메이션으로 도미노가 넘어지는 효과 (framer-motion)

### 6-4. 사건 비교 (EventCompare)
- 2개 사건 선택 → 같은 차트에 겹쳐서 표시
- "9/11 vs 이란 전쟁" 같은 비교가 핵심 콘텐츠

---

## 7. 디렉토리 구조 (풀스택)

```
event-and-money/
├── README.md
├── CLAUDE.md
├── ARCHITECTURE.md              ← 이 파일
│
├── frontend/                    ── Next.js 프론트엔드
│   ├── package.json
│   ├── next.config.js
│   ├── tailwind.config.js
│   ├── public/
│   │   └── images/
│   ├── src/
│   │   ├── app/                 # App Router (Next.js 14+)
│   │   │   ├── layout.tsx       # 공통 레이아웃
│   │   │   ├── page.tsx         # 랜딩 페이지
│   │   │   ├── events/
│   │   │   │   ├── page.tsx     # 사건 목록
│   │   │   │   └── [id]/
│   │   │   │       └── page.tsx # 사건 상세
│   │   │   ├── dashboard/
│   │   │   │   └── page.tsx     # 대시보드
│   │   │   ├── chains/
│   │   │   │   └── page.tsx     # 파급 체인
│   │   │   └── insights/
│   │   │       └── page.tsx     # 인사이트/블로그
│   │   ├── components/
│   │   │   ├── charts/
│   │   │   │   ├── HeatmapView.tsx
│   │   │   │   ├── TimelineChart.tsx
│   │   │   │   └── EventCompare.tsx
│   │   │   ├── chains/
│   │   │   │   └── DominoChain.tsx
│   │   │   └── common/
│   │   │       ├── Header.tsx
│   │   │       ├── Footer.tsx
│   │   │       └── EventCard.tsx
│   │   ├── lib/
│   │   │   └── api.ts           # 백엔드 API 호출 함수
│   │   └── types/
│   │       └── index.ts         # TypeScript 타입 정의
│   └── .env.local               # API URL 등
│
├── backend/                     ── FastAPI 백엔드
│   ├── requirements.txt
│   ├── main.py                  # FastAPI 앱 엔트리
│   ├── config.py                # DB 연결, 환경변수
│   ├── routers/
│   │   ├── events.py            # /events 엔드포인트
│   │   ├── assets.py            # /assets 엔드포인트
│   │   ├── analysis.py          # /analysis 엔드포인트
│   │   └── chains.py            # /chains 엔드포인트
│   ├── models/
│   │   ├── event.py             # SQLAlchemy 모델
│   │   ├── asset.py
│   │   ├── price.py
│   │   └── returns.py
│   ├── services/
│   │   ├── data_fetcher.py      # Yahoo Finance 데이터 수집
│   │   ├── returns_calculator.py # 수익률 계산 로직
│   │   ├── correlation.py       # 상관관계 분석
│   │   └── chain_builder.py     # 파급 체인 데이터 생성
│   ├── db/
│   │   ├── database.py          # DB 연결/세션 관리
│   │   ├── init_db.py           # 테이블 생성 + 초기 데이터
│   │   └── seed_data.py         # events, assets 초기 입력
│   └── tests/
│       ├── test_returns.py
│       └── test_data_quality.py
│
├── data/                        ── 데이터 (로컬 작업용)
│   ├── events.json              # 사건 메타데이터 (seed 소스)
│   └── assets.json              # 자산 메타데이터 (seed 소스)
│
├── scripts/                     ── 유틸리티 스크립트
│   ├── fetch_all_prices.py      # 전체 가격 데이터 일괄 수집 → DB 적재
│   ├── calc_all_returns.py      # 전체 수익률 일괄 계산 → DB 적재
│   └── export_for_blog.py       # 블로그용 차트/데이터 내보내기
│
├── blog/                        ── 블로그 포스팅 초안
├── book/                        ── 책 원고
├── investment/                  ── 실전 투자 기록
│
├── docker-compose.yml           # 로컬 개발 환경 (PostgreSQL + API)
├── .env.example                 # 환경변수 템플릿
└── .gitignore
```

---

## 8. 개발 순서 (Phase별)

### Phase 0: 환경 세팅 (1~2일)
```
1. GitHub 레포 생성 (event-and-money)
2. Supabase 프로젝트 생성 → PostgreSQL 프로비저닝
3. 도메인 구매 (eventandmoney.com)
4. Vercel 프로젝트 생성 → 도메인 연결
5. 로컬에 docker-compose로 PostgreSQL 띄우기 (개발용)
```

### Phase 1: 데이터 + 백엔드 기초 (1~2주)
```
1. DB 스키마 생성 (events, assets, prices, returns 테이블)
2. events.json → DB 시딩 (30개 사건)
3. assets.json → DB 시딩 (20+ 자산)
4. Yahoo Finance → prices 테이블 적재 (5개 사건부터)
5. 수익률 계산 → returns 테이블 적재
6. FastAPI 기본 엔드포인트 (GET /events, GET /events/{id}/returns)
7. → 5개 사건으로 파이프라인 검증 후 30개로 확장
```

### Phase 2: 프론트엔드 MVP (2~3주)
```
1. Next.js 프로젝트 세팅 (tailwind, recharts)
2. 랜딩 페이지 (프로젝트 소개)
3. 사건 목록 페이지 (카드형)
4. 사건 상세 페이지 (수익률 테이블 + 타임라인 차트)
5. 히트맵 대시보드
6. Vercel 배포 → eventandmoney.com 라이브
```

### Phase 3: 핵심 기능 확장 (3~4주)
```
1. 파급 체인 인터랙티브 다이어그램
2. 사건 비교 기능
3. 가설 검증 결과 페이지
4. 블로그/인사이트 섹션
5. 모바일 반응형 최적화
```

### Phase 4: 고도화 (지속)
```
1. 영문 버전
2. 포트폴리오 시뮬레이터
3. 실시간 뉴스 연동 (사건 발생 시 자동 알림)
4. 사용자 계정 + 구독 모델
```

---

## 9. 로컬 개발 환경

### docker-compose.yml (PostgreSQL)
```yaml
version: '3.8'
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_DB: eventandmoney
      POSTGRES_USER: emuser
      POSTGRES_PASSWORD: empass2026
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
```

### 실행 방법
```bash
# 1. DB 시작
docker-compose up -d

# 2. 백엔드
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# 3. 프론트엔드
cd frontend
npm install
npm run dev  # localhost:3000
```

---

## 10. 환경변수 (.env.example)

```env
# Database
DATABASE_URL=postgresql://emuser:empass2026@localhost:5432/eventandmoney
SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_KEY=your-anon-key

# API
API_BASE_URL=http://localhost:8000/v1
NEXT_PUBLIC_API_URL=http://localhost:8000/v1

# External APIs
FRED_API_KEY=your-fred-key    # https://fred.stlouisfed.org/docs/api/api_key.html

# Deployment
VERCEL_URL=https://eventandmoney.com
```

---

*이 문서는 프로젝트가 진행되면서 업데이트됩니다.*
