"""사건(Event) 테이블 모델"""
from sqlalchemy import Column, String, Date, Boolean, Text, DateTime, Integer, func
from backend.db.database import Base


class Event(Base):
    __tablename__ = "events"

    id = Column(String(4), primary_key=True)            # 'A01', 'M001' 등
    name_ko = Column(Text, nullable=False)               # '9/11 테러'
    name_en = Column(Text, nullable=False)               # 'September 11 Attacks'
    event_date = Column(Date, nullable=False)             # 2001-09-11 (D=0, 시장에 반영된 시점)
    announce_date = Column(Date)                          # 공시·소문 보도일 (선택, 트랙C 사전반응 분석용)
    category = Column(String(30), nullable=False)         # macro: 'war', 'financial' / micro: 'corporate_scandal' 등
    sub_type = Column(String(50))                         # 'terror', 'gapjil' 등
    description_ko = Column(Text)
    description_en = Column(Text)
    energy_impact = Column(Boolean, default=False)        # 에너지 공급 차질 여부
    created_at = Column(DateTime, server_default=func.now())

    # 마이크로 사건용 필드 (매크로는 scale='macro', 나머지 null)
    scale = Column(String(10), default="macro")           # 'macro' | 'micro'
    attr_political = Column(Integer)                      # 0-100, 정치 요인 가중치
    attr_corporate = Column(Integer)                      # 0-100, 오너/기업 요인
    attr_macro = Column(Integer)                          # 0-100, 거시/시장 요인
    attr_rationale = Column(Text)                         # 가중치 부여 근거
    affected_entities = Column(Text)                      # JSON list of symbols (직접 영향)
    comparable_universe = Column(Text)                    # JSON list of symbols (비교군, CAR 계산용)
