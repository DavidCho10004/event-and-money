"""
사건별 분석 노트 자동 생성 — DB → analysis/by_event/*.md

용도:
    책 원고·블로그 글의 골격을 자동 생성.
    수치(수익률 테이블, CAR, 요약 통계)는 DB에서 채우고,
    해석·내러티브 섹션은 저자가 채울 빈칸으로 남긴다.

실행 (프로젝트 루트에서):
    python scripts/generate_event_notes.py            # 전체 사건
    python scripts/generate_event_notes.py --ids M007,M020   # 특정 사건만

출력: analysis/by_event/<ID>_<slug>.md
데이터 갱신 후 재실행하면 수치 섹션만 갱신됨 (— AUTHOR — 이후는 보존).
"""
import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db.database import SessionLocal
from backend.models import Asset, Event, Return

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "analysis" / "by_event"

PERIOD_ORDER = ["D-30", "D-7", "D-1", "D+1", "D+7", "D+30", "D+180", "D+365"]

CATEGORY_NAMES = {
    "war": "전쟁/군사", "financial": "금융위기", "pandemic": "팬데믹/재난",
    "policy": "정책/통화", "industry": "산업/기술",
    "corporate_scandal": "기업 스캔들", "owner_risk": "오너 리스크",
    "product_safety": "제품/안전", "labor": "노동/파업",
    "succession": "승계/지배구조", "listing_event": "상장/IPO",
    "capital_event": "자본 이벤트",
}

AUTHOR_MARKER = "<!-- AUTHOR -->"

AUTHOR_TEMPLATE = """<!-- AUTHOR -->
<!-- 이 마커 아래는 재생성 시 보존됩니다. 자유롭게 집필하세요. -->

## 사건의 전개 (저자 작성)

_(사건이 어떻게 시작되고 확산됐는지, 시장이 어느 시점에 무엇을 반영했는지)_

## 해석 (저자 작성)

_(위 수치가 말하는 것. 예상과 달랐던 부분. 유사 사건과의 비교)_

## 투자자 행동 지침 (저자 작성)

_(이런 사건이 다시 온다면: 무엇을 보고, 무엇을 하지 말아야 하는가)_
"""


def fmt_pct(v):
    if v is None:
        return "—"
    return f"{v:+.2f}%"


def build_returns_table(returns_by_symbol, assets_map, affected, comparable, limit=15):
    """수익률 마크다운 테이블. affected → comparable → 대표 글로벌 순."""
    priority_globals = ["^KS11", "^GSPC", "^IXIC", "CL=F", "GC=F", "USDKRW=X", "^VIX"]
    ordered = (
        [s for s in affected if s in returns_by_symbol]
        + [s for s in comparable if s in returns_by_symbol]
        + [s for s in priority_globals
           if s in returns_by_symbol and s not in affected and s not in comparable]
    )
    ordered = ordered[:limit]
    if not ordered:
        return "_가격 데이터 미수집 — `fetch_all_prices.py` 실행 후 재생성_\n"

    lines = ["| 자산 | 구분 | " + " | ".join(PERIOD_ORDER) + " |",
             "|---|---|" + "---|" * len(PERIOD_ORDER)]
    for sym in ordered:
        asset = assets_map.get(sym)
        name = asset.name_ko if asset else sym
        role = "🎯직접" if sym in affected else ("⚖️비교" if sym in comparable else "")
        row = [fmt_pct(returns_by_symbol[sym].get(p)) for p in PERIOD_ORDER]
        lines.append(f"| {name} | {role} | " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


def build_car_section(returns_by_symbol, affected, comparable):
    """CAR(직접영향 평균 − 비교군 평균) 테이블."""
    def avg(symbols, p):
        vals = [returns_by_symbol[s][p] for s in symbols
                if s in returns_by_symbol and p in returns_by_symbol[s]]
        return sum(vals) / len(vals) if vals else None

    rows = []
    for p in PERIOD_ORDER:
        a, c = avg(affected, p), avg(comparable, p)
        car = (a - c) if (a is not None and c is not None) else None
        rows.append((p, a, c, car))
    if all(r[3] is None for r in rows):
        return None

    lines = ["| 시점 | 직접영향 평균 | 비교군 평균 | CAR |", "|---|---|---|---|"]
    for p, a, c, car in rows:
        lines.append(f"| {p} | {fmt_pct(a)} | {fmt_pct(c)} | **{fmt_pct(car)}** |")
    return "\n".join(lines) + "\n"


def generate_note(event, returns_by_symbol, assets_map):
    """단일 사건 노트 마크다운 (자동 수치 섹션)."""
    cat = CATEGORY_NAMES.get(event.category, event.category)
    scale_label = "🌍 매크로" if (event.scale or "macro") == "macro" else "🏢 마이크로"
    affected = json.loads(event.affected_entities) if event.affected_entities else []
    comparable = json.loads(event.comparable_universe) if event.comparable_universe else []

    parts = [
        f"# {event.id} {event.name_ko}",
        "",
        f"- **일자**: {event.event_date}"
        + (f" (📢 발표·공시일 {event.announce_date})"
           if event.announce_date and event.announce_date != event.event_date else ""),
        f"- **분류**: {scale_label} · {cat}" + (f" · {event.sub_type}" if event.sub_type else ""),
        f"- **웹**: https://event-and-money-production.up.railway.app/event/{event.slug or event.id}",
        "",
        "## 개요",
        "",
        event.description_ko or "_설명 없음_",
        "",
    ]

    # attribution (마이크로만)
    if event.scale == "micro" and event.attr_political is not None:
        parts += [
            "## 사건 성격 분해",
            "",
            f"| 정치 | 기업(오너) | 시장(거시) |",
            f"|---|---|---|",
            f"| {event.attr_political}% | {event.attr_corporate}% | {event.attr_macro}% |",
            "",
            f"근거: {event.attr_rationale or '—'}",
            "",
        ]

    parts += [
        "## 자산별 수익률",
        "",
        build_returns_table(returns_by_symbol, assets_map, affected, comparable),
    ]

    car = build_car_section(returns_by_symbol, affected, comparable)
    if car:
        parts += ["## 초과수익률 (CAR)", "", car,
                  "_CAR < 0: 사건 고유 충격으로 비교군 대비 추가 하락. 0 수렴: 영향 소멸._", ""]

    parts += [
        "---",
        "_수치 섹션은 `python scripts/generate_event_notes.py`로 자동 생성 "
        "(Source: Yahoo Finance, Adjusted Close). 아래는 저자 영역._",
        "",
    ]
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description="사건별 분석 노트 생성")
    parser.add_argument("--ids", default=None, help="특정 사건만 (콤마 구분, 예: M007,M020)")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    db = SessionLocal()

    q = db.query(Event).order_by(Event.id)
    if args.ids:
        wanted = [i.strip() for i in args.ids.split(",")]
        q = q.filter(Event.id.in_(wanted))
    events = q.all()

    assets_map = {a.symbol: a for a in db.query(Asset).all()}

    written, preserved = 0, 0
    for event in events:
        rows = db.query(Return).filter(Return.event_id == event.id).all()
        returns_by_symbol = {}
        for r in rows:
            returns_by_symbol.setdefault(r.symbol, {})[r.period] = float(r.return_pct)

        auto_part = generate_note(event, returns_by_symbol, assets_map)

        fp = OUT_DIR / f"{event.id}_{event.slug or 'note'}.md"
        author_part = AUTHOR_TEMPLATE
        if fp.exists():
            old = fp.read_text(encoding="utf-8")
            if AUTHOR_MARKER in old:
                author_part = old[old.index(AUTHOR_MARKER):]
                preserved += 1

        fp.write_text(auto_part + author_part, encoding="utf-8")
        written += 1

    db.close()
    logger.info("완료: %d건 생성 (%d건은 저자 영역 보존)", written, preserved)
    logger.info("출력: %s", OUT_DIR)


if __name__ == "__main__":
    main()
