"""가격(Price) 테이블 모델"""
from sqlalchemy import Column, String, Date, Numeric, BigInteger, ForeignKey, Index
from backend.db.database import Base


class Price(Base):
    __tablename__ = "prices"

    symbol = Column(String(20), ForeignKey("assets.symbol"), primary_key=True)
    trade_date = Column(Date, primary_key=True)
    adj_close = Column(Numeric(18, 6))                    # 수정 종가
    volume = Column(BigInteger)


# 사건일 기준 조회 최적화 인덱스
Index("idx_prices_date", Price.trade_date)
Index("idx_prices_symbol_date", Price.symbol, Price.trade_date)
