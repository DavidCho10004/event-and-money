# -*- coding: utf-8 -*-
"""
이벤트×수급 결합 1단계 — 데이터 감사 + 기술 통계 (지도 그리기, 검증 아님)

① 감사: 사건 DB(events.json) × 수급 커버리지 (월별 2016-01~ / 일별 2025-07-14~)
② 사건별 수급 프로필: 사건 월(월별 커버) 또는 D-5~D+5(일별 커버)의
   시장 전체 투자자별 순매수 — "누가 팔고 누가 받았나"
③ 단면 예비 (일별 커버 사건만): D-2~D+2 외인 강도 상위 30 vs 하위 30의
   이후 1개월(21거래일) 수익률 차이 — 사건별 개별 출력 (합산·평균 금지)

산출: data/processed/event_supply/ 아래 CSV 3종. 전부 기술 통계.
참고: 2026-07-13 폭락은 사건 DB 미등재 — ②/③에 수동 참고 행으로 포함하고 표기.
"""
import json
import logging

import pandas as pd

import store

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger("event_supply_map")
logger.setLevel(logging.INFO)

ROOT = store.BASE_DIR.parent
OUT = ROOT / "data" / "processed" / "event_supply"

M_START, M_END = "2016-01", "2025-06"     # 월별 flows 커버
INVESTORS = ["외국인", "기관합계", "개인"]
WINDOW_D = 5      # ② 일별 프로필 창 D±5
XS_WINDOW = 2     # ③ 단면 창 D±2
XS_TOP = 30
FWD_DAYS = 21     # ③ 이후 1개월 (거래일)

# 사건 DB에 없는 참고 사례 (표기 필수)
EXTRA_EVENTS = [{"id": "(미등재)", "name_ko": "2026-07-13 폭락", "event_date": "2026-07-13",
                 "category": "-", "scale": "-"}]


def load_events():
    evs = json.loads((ROOT / "data" / "events.json").read_text(encoding="utf-8"))
    return evs


def daily_days():
    fl = store.read_flows()
    d = fl[fl["해상도"] == "D"]
    return sorted(d["날짜"].unique()), fl


def nearest_day_idx(days, date):
    """date 당일 또는 직후 첫 거래일 인덱스 (없으면 None)."""
    for i, d in enumerate(days):
        if d >= date:
            return i
    return None


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    events = load_events()
    days, fl = daily_days()
    d_start, d_end = days[0], days[-1]

    # ① 감사
    audit = []
    for e in events:
        dt = e["event_date"]
        ym = dt[:7]
        m_cov = M_START <= ym <= M_END or (d_start[:7] <= ym <= d_end[:7])
        d_cov = d_start <= dt <= d_end
        note = ""
        if dt < "2016-01-01":
            note = "수급 커버 이전"
        if e["scale"] == "micro" and any(k in e["name_ko"] for k in
                                         ["스타벅스", "테슬라", "애플", "보잉", "디즈니"]):
            note = (note + " · " if note else "") + "해외 기업 — 국내 증시 직접 연관 낮음(수동 확인 필요)"
        audit.append({"ID": e["id"], "사건": e["name_ko"], "날짜": dt,
                      "분류": f'{e["category"]}/{e["scale"]}',
                      "월별커버": "O" if m_cov else "X",
                      "일별커버": "O" if d_cov else "X", "비고": note})
    audit_df = pd.DataFrame(audit)
    audit_df.to_csv(OUT / "audit.csv", index=False, encoding="utf-8-sig")

    # ② 수급 프로필
    m_rows = fl[(fl["해상도"] == "M") & (fl["날짜"] <= M_END + "-31")]
    d_rows = fl[fl["해상도"] == "D"]
    profiles = []
    for e in events + EXTRA_EVENTS:
        dt = e["event_date"]
        ym = dt[:7]
        d_cov = d_start <= dt <= d_end
        if d_cov:
            i = nearest_day_idx(days, dt)
            win = days[max(0, i - WINDOW_D): i + WINDOW_D + 1]
            src = d_rows[d_rows["날짜"].isin(win)]
            basis = f"D±{WINDOW_D} ({win[0]}~{win[-1]})"
        elif M_START <= ym <= M_END:
            src = m_rows[m_rows["날짜"].str[:7] == ym]
            basis = f"사건 월 ({ym})"
        elif ym > M_END:   # 일별 커버 이전이지만 D 시작 후 월 (해당 없음 방어)
            src = d_rows[d_rows["날짜"].str[:7] == ym]
            basis = f"사건 월 D합산 ({ym})"
        else:
            continue
        row = {"ID": e["id"], "사건": e["name_ko"], "날짜": dt, "집계기준": basis}
        for mkt in ["KOSPI", "KOSDAQ"]:
            for inv in INVESTORS:
                v = src[(src["시장"] == mkt) & (src["투자자"] == inv)]["거래대금"].sum()
                label = "기관" if inv == "기관합계" else inv
                row[f"{mkt}_{label}_조"] = round(v / 1e12, 2)
        profiles.append(row)
    prof_df = pd.DataFrame(profiles)
    prof_df.to_csv(OUT / "profiles.csv", index=False, encoding="utf-8-sig")

    # ③ 단면 예비 (일별 커버 사건만, 사건별 개별)
    sn = store.read_snapshots()
    xs_out = []
    d_events = [e for e in events + EXTRA_EVENTS if d_start <= e["event_date"] <= d_end]
    for e in d_events:
        i = nearest_day_idx(days, e["event_date"])
        win = days[max(0, i - XS_WINDOW): i + XS_WINDOW + 1]
        entry = win[-1]                       # 창 마지막 거래일 종가 진입 기준
        if i + XS_WINDOW + FWD_DAYS >= len(days):
            xs_out.append({"ID": e["id"], "사건": e["name_ko"], "비고": "이후 1개월 데이터 부족"})
            continue
        exit_day = days[i + XS_WINDOW + FWD_DAYS]
        for mkt in ["KOSPI", "KOSDAQ"]:
            f = d_rows[(d_rows["시장"] == mkt) & (d_rows["날짜"].isin(win)) & (d_rows["투자자"] == "외국인")]
            vol = f.groupby("코드")["거래량"].sum()
            s_entry = sn[(sn["시장"] == mkt) & (sn["날짜"] == entry)].set_index("코드")
            strength = (vol / s_entry["상장주식수"]).dropna()
            c_in = s_entry["종가"]
            c_out = sn[(sn["시장"] == mkt) & (sn["날짜"] == exit_day)].set_index("코드")["종가"]
            ret = (c_out / c_in - 1).dropna()
            top = strength.nlargest(XS_TOP).index
            bot = strength.nsmallest(XS_TOP).index
            r_top = ret.reindex(top).dropna()
            r_bot = ret.reindex(bot).dropna()
            xs_out.append({
                "ID": e["id"], "사건": e["name_ko"], "시장": mkt,
                "창": f"{win[0]}~{win[-1]}", "만기": exit_day,
                "상위30_1개월%": round(r_top.mean() * 100, 2),
                "하위30_1개월%": round(r_bot.mean() * 100, 2),
                "차이%p": round((r_top.mean() - r_bot.mean()) * 100, 2),
                "유효종목": f"{len(r_top)}/{len(r_bot)}",
            })
    xs_df = pd.DataFrame(xs_out)
    xs_df.to_csv(OUT / "cross_section.csv", index=False, encoding="utf-8-sig")

    print(f"① 감사: 사건 {len(audit_df)}건 — 월별커버 {(audit_df['월별커버']=='O').sum()} / "
          f"일별커버 {(audit_df['일별커버']=='O').sum()} / 커버 이전 {(audit_df['비고'].str.contains('이전')).sum()}")
    print()
    print("② 프로필 (최근 10건 + 참고):")
    print(prof_df.tail(10).to_string(index=False))
    print()
    print("③ 단면 (사건별 개별 — 합산 없음):")
    print(xs_df.to_string(index=False))


if __name__ == "__main__":
    main()
