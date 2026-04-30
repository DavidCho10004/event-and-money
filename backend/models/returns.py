"""수익률(Returns) 테이블 모델"""
from sqlalchemy import Column, String, Date, Numeric, ForeignKey
from backend.db.database import Base


class Return(Base):
    __tablename__ = "returns"

    event_id = Column(String(4), ForeignKey("events.id"), primary_key=True)
    symbol = Column(String(20), ForeignKey("assets.symbol"), primary_key=True)
    period = Column(String(10), primary_key=True)         # 'D+1', 'D+7', 'D+30', 'D+180', 'D+365'
    return_pct = Column(Numeric(10, 4))                   # 수익률 (%)
    price_base = Column(Numeric(18, 6))                   # 기준일 가격
    price_end = Column(Numeric(18, 6))                    # 종료일 가격
    date_base = Column(Date)                              # 실제 기준 거래일
    date_end = Column(Date)                               # 실제 종료 거래일
