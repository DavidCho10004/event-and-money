"""
사건별 OG 이미지(1200x630 PNG) 동적 생성

용도: 카카오톡·트위터·페이스북 공유 시 미리보기 카드.
디자인: 카테고리 색 그라데이션 배경 + 사건 ID/이름/날짜 + 마이크로 사건은 attribution 바.

폰트: Pretendard (backend/static/fonts/) — 한글·영문 동시 지원, 1.6MB × 2.
시스템 폰트 미보장이므로 임베드 폰트만 사용.
"""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONTS_DIR = Path(__file__).resolve().parent.parent / "static" / "fonts"
FONT_BOLD = FONTS_DIR / "Pretendard-Bold.otf"
FONT_REG = FONTS_DIR / "Pretendard-Regular.otf"

# OG 표준 크기
W, H = 1200, 630

# 카테고리별 배경 색상 (그라데이션 시작/끝)
CATEGORY_COLORS = {
    "war":               ("#c0392b", "#641e16"),
    "financial":         ("#2980b9", "#1b4f72"),
    "pandemic":          ("#8e44ad", "#4a235a"),
    "policy":            ("#d68910", "#7d6608"),
    "industry":          ("#16a085", "#0b5345"),
    "corporate_scandal": ("#e67e22", "#784212"),
    "owner_risk":        ("#8e44ad", "#4a235a"),
    "product_safety":    ("#d35400", "#6e2c00"),
    "labor":             ("#34495e", "#1c2833"),
    "succession":        ("#16a085", "#0b5345"),
    "listing_event":     ("#2980b9", "#1b4f72"),
    "capital_event":     ("#7f8c8d", "#34495e"),
}
CATEGORY_LABELS = {
    "war": "전쟁/군사", "financial": "금융위기",
    "pandemic": "팬데믹/재난", "policy": "정책/통화",
    "industry": "산업/기술",
    "corporate_scandal": "기업 스캔들", "owner_risk": "오너 리스크",
    "product_safety": "제품/안전", "labor": "노동/파업",
    "succession": "승계/지배구조", "listing_event": "상장/IPO",
    "capital_event": "자본 이벤트",
}


def _gradient(color1: str, color2: str) -> Image.Image:
    """좌상→우하 대각선 그라데이션 1200x630 생성."""
    img = Image.new("RGB", (W, H), color1)
    draw = ImageDraw.Draw(img)
    c1 = tuple(int(color1[i:i+2], 16) for i in (1, 3, 5))
    c2 = tuple(int(color2[i:i+2], 16) for i in (1, 3, 5))
    for y in range(H):
        t = y / H
        r = int(c1[0] * (1 - t) + c2[0] * t)
        g = int(c1[1] * (1 - t) + c2[1] * t)
        b = int(c1[2] * (1 - t) + c2[2] * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))
    return img


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = FONT_BOLD if bold else FONT_REG
    return ImageFont.truetype(str(path), size)


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """문자 단위로 줄바꿈 (한글은 띄어쓰기 보존 + 글자 단위 fallback)."""
    lines: list[str] = []
    current = ""
    for ch in text:
        test = current + ch
        bbox = font.getbbox(test)
        if bbox[2] - bbox[0] > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = test
        if len(lines) >= 3:  # 최대 3줄
            break
    if current and len(lines) < 3:
        lines.append(current)
    if len(lines) >= 3 and len(text) > sum(len(l) for l in lines):
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def render_event_og(event) -> bytes:
    """단일 사건 → PNG 바이트.

    event는 backend.models.Event SQLAlchemy 인스턴스.
    """
    c1, c2 = CATEGORY_COLORS.get(event.category, ("#1a1a2e", "#0d0d17"))
    img = _gradient(c1, c2)
    draw = ImageDraw.Draw(img, "RGBA")

    # 좌상단 브랜드
    draw.text((60, 50), "Event & Money", font=_font(28, bold=True), fill=(255, 255, 255, 230))
    draw.text((60, 88), "사건과 돈의 데이터", font=_font(18), fill=(255, 255, 255, 160))

    # 우상단 사건 ID + 스케일 뱃지
    scale_emoji = "🌍" if (event.scale or "macro") == "macro" else "🏢"
    id_text = f"{scale_emoji} {event.id}"
    bbox = _font(34, bold=True).getbbox(id_text)
    id_w = bbox[2] - bbox[0]
    draw.text((W - 60 - id_w, 60), id_text, font=_font(34, bold=True), fill=(255, 255, 255, 240))

    # 중앙 이름 (자동 줄바꿈, 최대 3줄)
    title_font = _font(68, bold=True)
    lines = _wrap_text(event.name_ko, title_font, W - 120)
    y_start = (H - len(lines) * 88) // 2 - 20
    for i, line in enumerate(lines):
        draw.text((60, y_start + i * 88), line, font=title_font, fill=(255, 255, 255, 255))

    # 하단 좌측: 날짜 + 카테고리
    date_str = str(event.event_date)
    cat_label = CATEGORY_LABELS.get(event.category, event.category)
    draw.text((60, H - 110), date_str, font=_font(32, bold=True), fill=(255, 255, 255, 220))
    # 카테고리 뱃지 박스
    cat_font = _font(22, bold=True)
    cb = cat_font.getbbox(cat_label)
    cw, ch = cb[2] - cb[0] + 28, cb[3] - cb[1] + 18
    draw.rounded_rectangle((60, H - 65, 60 + cw, H - 65 + ch),
                           radius=14, fill=(255, 255, 255, 50))
    draw.text((74, H - 60), cat_label, font=cat_font, fill=(255, 255, 255, 240))

    # 우하단: attribution 바 (마이크로만)
    if event.scale == "micro" and event.attr_political is not None:
        bar_x = 700
        bar_y = H - 90
        bar_w = W - bar_x - 60
        bar_h = 32
        segments = [
            ("정치", event.attr_political or 0, (192, 57, 43, 230)),
            ("기업", event.attr_corporate or 0, (142, 68, 173, 230)),
            ("시장", event.attr_macro or 0, (41, 128, 185, 230)),
        ]
        cur_x = bar_x
        for label, pct, color in segments:
            if pct <= 0:
                continue
            seg_w = int(bar_w * pct / 100)
            draw.rectangle((cur_x, bar_y, cur_x + seg_w, bar_y + bar_h), fill=color)
            seg_font = _font(14, bold=True)
            txt = f"{label} {pct}"
            tb = seg_font.getbbox(txt)
            tw = tb[2] - tb[0]
            if seg_w > tw + 12:
                draw.text((cur_x + (seg_w - tw) // 2, bar_y + 8),
                          txt, font=seg_font, fill=(255, 255, 255, 255))
            cur_x += seg_w
        draw.text((bar_x, bar_y - 22), "사건 성격 분해", font=_font(13), fill=(255, 255, 255, 180))

    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue()
