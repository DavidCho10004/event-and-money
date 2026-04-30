"""모든 모델을 임포트하여 Base.metadata에 등록"""
from backend.models.event import Event
from backend.models.asset import Asset
from backend.models.price import Price
from backend.models.returns import Return
from backend.models.domino_chain import DominoChain

__all__ = ["Event", "Asset", "Price", "Return", "DominoChain"]
