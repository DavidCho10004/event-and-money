"""자산(Asset) 테이블 모델"""
from sqlalchemy import Column, String, Date, Text
from backend.db.database import Base


class Asset(Base):
    __tablename__ = "assets"

    symbol = Column(String(20), primary_key=True)         # '^GSPC', 'CL=F'
    name_ko = Column(Text, nullable=False)                # 'S&P 500'
    name_en = Column(Text, nullable=False)                # 'S&P 500'
    asset_class = Column(String(20), nullable=False)      # 'equity_index', 'commodity' 등
    data_start = Column(Date)                             # 데이터 시작일
    yahoo_symbol = Column(String(20))                     # yfinance 전용 심볼
    description = Column(Text)
