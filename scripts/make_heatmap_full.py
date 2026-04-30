"""
확장 히트맵: 30사건 × 전체 자산 (섹터별 그룹핑), D+30 수익률

실행: python scripts/make_heatmap_full.py
"""
import sys
from pathlib import Path
from collections import OrderedDict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db.database import SessionLocal
from backend.models import Event, Asset, Return

OUT_DIR = Path(__file__).resolve().parent.parent / "outputs" / "charts"

plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

# 섹터별로 그룹핑 — 파급 체인 분석에 유용한 순서
ASSET_GROUPS = OrderedDict([
    ("주가지수", [
        ("^GSPC", "S&P500"),
        ("^IXIC", "NASDAQ"),
        ("^KS11", "KOSPI"),
        ("^N225", "Nikkei"),
    ]),
    ("에너지", [
        ("CL=F", "WTI"),
        ("XLE", "에너지ETF"),
        ("OIH", "오일서비스"),
    ]),
    ("안전자산", [
        ("GC=F", "금"),
        ("DX-Y.NYB", "DXY"),
        ("^VIX", "VIX"),
        ("^TNX", "10Y금리"),
    ]),
    ("방산/항공", [
        ("ITA", "방산"),
        ("JETS", "항공"),
    ]),
    ("경기민감", [
        ("XLY", "소비재"),
        ("XLF", "금융"),
        ("KRE", "지역은행"),
        ("XHB", "주택건설"),
        ("XLRE", "리츠"),
        ("XLI", "산업재"),
    ]),
    ("경기방어", [
        ("XLP", "필수소비"),
        ("XLV", "헬스케어"),
        ("XLU", "유틸리티"),
    ]),
    ("소재/테마", [
        ("XLB", "소재"),
        ("HG=F", "구리"),
        ("COPX", "구리채굴"),
        ("URA", "우라늄"),
        ("LIT", "리튬배터리"),
    ]),
    ("기술/통신", [
        ("XLK", "기술"),
        ("XLC", "통신미디어"),
    ]),
])


def make_heatmap_full():
    db = SessionLocal()
    events = db.query(Event).order_by(Event.event_date).all()

    # 전체 자산 리스트 (그룹 순서 유지)
    all_assets = []
    group_boundaries = []  # (start_idx, group_name)
    idx = 0
    for group_name, assets in ASSET_GROUPS.items():
        group_boundaries.append((idx, group_name))
        for sym, label in assets:
            all_assets.append((sym, label))
            idx += 1

    symbols = [s for s, _ in all_assets]
    labels = [l for _, l in all_assets]

    # 매트릭스 구성
    matrix = []
    row_labels = []
    for e in events:
        row = []
        for sym in symbols:
            r = db.query(Return).filter(
                Return.event_id == e.id,
                Return.symbol == sym,
                Return.period == "D+30",
            ).first()
            row.append(float(r.return_pct) if r else np.nan)
        matrix.append(row)
        row_labels.append(f"{e.id} {e.name_ko[:16]}")

    db.close()

    data = np.array(matrix)
    n_rows, n_cols = data.shape

    fig, ax = plt.subplots(figsize=(22, 18))

    cmap = plt.cm.RdBu_r.copy()
    cmap.set_bad(color="#e0e0e0")

    vmax = min(np.nanmax(np.abs(data[~np.isnan(data)])), 80)
    im = ax.imshow(data, cmap=cmap, aspect="auto", vmin=-vmax, vmax=vmax)

    # X축 라벨
    ax.set_xticks(range(n_cols))
    ax.set_xticklabels(labels, fontsize=9, rotation=60, ha="right")

    # Y축 라벨
    ax.set_yticks(range(n_rows))
    ax.set_yticklabels(row_labels, fontsize=8)

    # 셀에 수치 표시
    for i in range(n_rows):
        for j in range(n_cols):
            val = data[i, j]
            if np.isnan(val):
                continue
            color = "white" if abs(val) > vmax * 0.55 else "black"
            ax.text(j, i, f"{val:+.0f}", ha="center", va="center", fontsize=6, color=color)

    # 그룹 구분선 (세로)
    for start_idx, group_name in group_boundaries[1:]:
        ax.axvline(x=start_idx - 0.5, color="#333", linewidth=1.5, linestyle="-")

    # 그룹 이름 (상단)
    for k, (start_idx, group_name) in enumerate(group_boundaries):
        if k + 1 < len(group_boundaries):
            end_idx = group_boundaries[k + 1][0]
        else:
            end_idx = n_cols
        mid = (start_idx + end_idx - 1) / 2
        ax.text(mid, -1.8, group_name, ha="center", va="bottom", fontsize=9, fontweight="bold", color="#1a1a2e")

    # 카테고리 구분선 (가로) — 같은 카테고리 내 사건 그룹핑
    prev_cat = None
    for i, e in enumerate(events):
        if prev_cat and e.category != prev_cat:
            ax.axhline(y=i - 0.5, color="#999", linewidth=0.8, linestyle="--")
        prev_cat = e.category

    cbar = fig.colorbar(im, ax=ax, shrink=0.5, pad=0.01)
    cbar.set_label("D+30 수익률 (%)", fontsize=11)

    ax.set_title("Event & Money — D+30 수익률 히트맵 (30사건 × 37자산, 섹터별 그룹)", fontsize=15, pad=35)
    fig.tight_layout()

    path = OUT_DIR / "heatmap_d30_full.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"저장: {path}")
    return path


if __name__ == "__main__":
    make_heatmap_full()
