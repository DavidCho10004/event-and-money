"""
데이터 확인용 차트 생성
1. 히트맵: 30사건 × 8자산, D+30 수익률
2. 타임라인: 9/11 vs 이란전쟁, S&P 500 D-10~D+30

실행: python scripts/quick_charts.py
"""
import sys
from pathlib import Path
from datetime import timedelta

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.db.database import SessionLocal
from backend.models import Event, Price, Return

OUT_DIR = Path(__file__).resolve().parent.parent / "outputs" / "charts"

# 한글 폰트 설정 (Windows)
plt.rcParams["font.family"] = "Malgun Gothic"
plt.rcParams["axes.unicode_minus"] = False

HEATMAP_ASSETS = [
    ("^GSPC", "S&P 500"),
    ("^KS11", "KOSPI"),
    ("CL=F", "WTI"),
    ("GC=F", "Gold"),
    ("DX-Y.NYB", "DXY"),
    ("^VIX", "VIX"),
    ("XLE", "Energy"),
    ("ITA", "Defense"),
]


def make_heatmap():
    """30사건 × 8자산 D+30 수익률 히트맵"""
    db = SessionLocal()
    events = db.query(Event).order_by(Event.event_date).all()
    symbols = [s for s, _ in HEATMAP_ASSETS]
    labels = [l for _, l in HEATMAP_ASSETS]

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
        row_labels.append(f"{e.id} {e.name_ko[:18]}")

    db.close()

    data = np.array(matrix)

    fig, ax = plt.subplots(figsize=(14, 18))

    # NaN은 회색으로 표시
    cmap = plt.cm.RdBu_r.copy()
    cmap.set_bad(color="#cccccc")

    vmax = min(np.nanmax(np.abs(data[~np.isnan(data)])), 100)
    im = ax.imshow(data, cmap=cmap, aspect="auto", vmin=-vmax, vmax=vmax)

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, fontsize=11, rotation=45, ha="right")
    ax.set_yticks(range(len(row_labels)))
    ax.set_yticklabels(row_labels, fontsize=9)

    # 셀에 수치 표시
    for i in range(len(row_labels)):
        for j in range(len(labels)):
            val = data[i, j]
            if np.isnan(val):
                ax.text(j, i, "N/A", ha="center", va="center", fontsize=7, color="#888888")
            else:
                color = "white" if abs(val) > vmax * 0.6 else "black"
                ax.text(j, i, f"{val:+.1f}", ha="center", va="center", fontsize=7, color=color)

    cbar = fig.colorbar(im, ax=ax, shrink=0.6, pad=0.02)
    cbar.set_label("수익률 (%)", fontsize=11)

    ax.set_title("Event & Money — D+30 수익률 히트맵 (30사건 × 8자산)", fontsize=14, pad=15)
    fig.tight_layout()

    path = OUT_DIR / "heatmap_d30.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"히트맵 저장: {path}")
    return path


def make_timeline():
    """9/11 vs 이란전쟁 — S&P 500 D-10~D+30 타임라인 비교"""
    db = SessionLocal()

    events = {
        "A04": db.query(Event).filter(Event.id == "A04").first(),
        "A08": db.query(Event).filter(Event.id == "A08").first(),
    }

    fig, ax = plt.subplots(figsize=(12, 6))

    colors = {"A04": "#d62728", "A08": "#1f77b4"}
    styles = {"A04": "-", "A08": "--"}

    for eid, event in events.items():
        start = event.event_date - timedelta(days=15)
        end = event.event_date + timedelta(days=45)

        prices = (
            db.query(Price)
            .filter(Price.symbol == "^GSPC", Price.trade_date >= start, Price.trade_date <= end)
            .order_by(Price.trade_date)
            .all()
        )

        if not prices:
            continue

        # 기준가 = event_date 직전 거래일
        base_price = None
        for p in prices:
            if p.trade_date <= event.event_date:
                base_price = float(p.adj_close)
            else:
                break

        if not base_price:
            continue

        # X축: 사건일 기준 상대 일수, Y축: 기준가 대비 수익률(%)
        x_days = [(p.trade_date - event.event_date).days for p in prices]
        y_returns = [(float(p.adj_close) / base_price - 1) * 100 for p in prices]

        label = f"{event.id} {event.name_ko} ({event.event_date})"
        ax.plot(x_days, y_returns, styles[eid], color=colors[eid], linewidth=2, label=label)

    ax.axvline(x=0, color="gray", linestyle=":", alpha=0.7, label="사건일 (D=0)")
    ax.axhline(y=0, color="gray", linewidth=0.5)
    ax.set_xlabel("사건일 기준 경과일 (D)", fontsize=11)
    ax.set_ylabel("수익률 (%)", fontsize=11)
    ax.set_title("S&P 500: 9/11 테러 vs 미국-이란 전쟁 (D-10 ~ D+30)", fontsize=13)
    ax.legend(fontsize=10)
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%+.1f%%"))
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    db.close()

    path = OUT_DIR / "timeline_a04_vs_a08.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"타임라인 저장: {path}")
    return path


if __name__ == "__main__":
    make_heatmap()
    make_timeline()
    print("완료!")
