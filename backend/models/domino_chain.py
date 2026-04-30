"""파급 체인(Domino Chain) 테이블 모델"""
from sqlalchemy import Column, Integer, String, Text, ForeignKey
from backend.db.database import Base


class DominoChain(Base):
    __tablename__ = "domino_chains"

    id = Column(Integer, primary_key=True, autoincrement=True)
    category = Column(String(20), nullable=False)         # 사건 카테고리
    sub_type = Column(String(50))                         # 세부 유형
    stage = Column(Integer, nullable=False)               # 1, 2, 3 (1차/2차/3차 변인)
    variable_ko = Column(Text, nullable=False)            # 'WTI/Brent 급등'
    variable_en = Column(Text)
    affected_assets = Column(Text)                        # JSON 문자열로 저장 (SQLite 호환)
    typical_lag = Column(String(30))                      # '즉시~수일', '수일~수주'
    direction = Column(String(10))                        # 'up', 'down', 'mixed'
    parent_id = Column(Integer, ForeignKey("domino_chains.id"))
    notes = Column(Text)
