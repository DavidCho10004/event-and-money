"""사건(Event) 테이블 모델"""
from sqlalchemy import Column, String, Date, Boolean, Text, DateTime, func
from backend.db.database import Base


class Event(Base):
    __tablename__ = "events"

    id = Column(String(4), primary_key=True)            # 'A01', 'B05' 등
    name_ko = Column(Text, nullable=False)               # '9/11 테러'
    name_en = Column(Text, nullable=False)               # 'September 11 Attacks'
    event_date = Column(Date, nullable=False)             # 2001-09-11
    category = Column(String(20), nullable=False)         # 'war', 'financial', 'pandemic', 'policy', 'industry'
    sub_type = Column(String(50))                         # 'terror', 'energy_war' 등
    description_ko = Column(Text)
    description_en = Column(Text)
    energy_impact = Column(Boolean, default=False)        # 에너지 공급 차질 여부
    created_at = Column(DateTime, server_default=func.now())
