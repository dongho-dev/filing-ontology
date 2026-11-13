"""글에서 뽑은 값(data/work/)을 검토 규칙에 따라 걸러 ontology/에 옮긴다.

쓰는 법: python scripts/merge_text.py
입력: data/work/nbis_values.json, nbis_values2.json, text_values.json, data/work/*_relations.json
출력: ontology/values_text.json, ontology/relations.json

검토하며 정한 규칙(2026-11-13):
- 코어위브 10-Q의 "undelivered" 전력은 특정 부지에 아직 인도되지 않은 전력이라 계약 전력 총량이 아니다. 뺀다.
- 같은 회사·지표·기간이 여러 문서에 있으면 나중에 제출된 문서의 값을 쓴다.
- 기간 말 시점의 값(전력, 현금, 수주 잔고)은 시작일을 비운다.
- 네비우스 2026년 3분기의 최대 고객 비중은 처음 뽑을 때 빠졌다(표의 열을 잘못 읽음). 표에서 다시 읽어 넣는다.
- 차입금은 다섯 분기 모두 재무상태표의 두 줄(Debt, current / Debt, non-current)로 맞춘다(2026-11-13).
  2026년 3분기는 주석의 합계(Total debt 14,162.8)로 뽑혔는데, 재무상태표 합(14,190.8)과 28.0 차이가 나고
  앞 분기들과 기준이 달라서 쓰지 않고, 재무상태표의 두 줄을 표에서 읽어 넣는다. 합계는 계산 지표(debt)가 만든다.
- 선수금(Deferred revenue)은 재무상태표의 유동·비유동 두 줄을 표에서 읽어 넣는다. 2025년 3분기에는 한 줄뿐이라 합계로 넣는다.
"""
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORK = ROOT / "data" / "work"
ONT = ROOT / "ontology"


def load(name: str) -> list[dict]:
    p = WORK / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def table_row_quote(text_file: str, row_label: str, cells: int, after: str = "accounted for 10% or more") -> str:
    """한 칸이 한 줄로 풀린 표에서, 행 이름부터 값 cells개까지를 원문 그대로 이어 붙인다. after가 든 줄 뒤에서 찾는다."""
    lines = (ROOT / text_file).read_text(encoding="utf-8").split("\n")
    start = next(i for i, ln in enumerate(lines) if after in ln)
    i = next(k for k in range(start, len(lines)) if lines[k].replace("​", "").strip() == row_label)
    j, seen = i + 1, 0
    while seen < cells:
        if lines[j].replace("​", "").strip():
            seen += 1
        j += 1
    return re.sub(r"\s+", " ", "\n".join(lines[i:j])).strip()


def nbis_q3_2026_top_customer() -> dict:
    tf = "data/raw/filings/nbis/0001104659-26-126238/nbis-ex99d2.htm.txt"
    return {
        "company": "nbis", "metric": "top_customer_share", "period_start": "2026-07-01", "period_end": "2026-09-30",
        "value": 33, "unit": "%",
        "source": {
            "type": "text", "form": "6-K", "accession": "0001104659-26-126238", "filed": "2026-11-12",
            "url": "https://www.sec.gov/Archives/edgar/data/1513845/000110465926126238/nbis-ex99d2.htm",
            "label": "Customer E",
            "quote": table_row_quote(tf, "Customer E", 4),
            "text_file": tf,
        },
    }


def nbis_q3_2026_debt() -> list[dict]:
    """재무상태표의 차입금 두 줄. 열 순서는 (주석 번호, 2025-12-31, 2026-09-30)이다."""
    tf = "data/raw/filings/nbis/0001104659-26-126238/nbis-ex99d2.htm.txt"
    out = []
    for metric, label, value in (("debt_current", "Debt, current", 187.5e6), ("debt_noncurrent", "Debt, non-current", 14003.3e6)):
        out.append({
            "company": "nbis", "metric": metric, "period_start": None, "period_end": "2026-09-30", "value": round(value), "unit": "USD",
            "source": {
                "type": "text", "form": "6-K", "accession": "0001104659-26-126238", "filed": "2026-11-12",
                "url": "https://www.sec.gov/Archives/edgar/data/1513845/000110465926126238/nbis-ex99d2.htm",
                "label": label, "quote": table_row_quote(tf, label, 3, after="CONSOLIDATED BALANCE SHEET"), "text_file": tf,
            },
        })
    return out


def second_batch() -> list[dict]:
    """두 번째로 뽑은 값(조정 EBITDA, 이자비용, 영업현금흐름, 유형자산, 차입금)을 지표 이름에 맞춘다."""
    out = []
    for v in load("nbis_values2.json"):
        if v["metric"] == "debt":
            continue  # 주석의 합계. 위 규칙대로 쓰지 않는다
        if v["metric"] == "debt_component":
            v = dict(v, metric="debt_noncurrent" if "non-current" in v["source"]["label"] else "debt_current")
        out.append(v)
    return out + nbis_q3_2026_debt()


# 재무상태표의 선수금 줄. (원문 파일, 기간 말, 줄 이름, 지표, 줄 이름 뒤에 이어 붙일 칸 수, 값(백만 달러))
# 칸 순서는 (주석 번호가 있으면 주석 번호, 앞 기간 말, 이번 기간 말)이다. 2026-11-13에 머리줄과 함께 읽고,
# 뒤 공시들의 비교 열(2025-12-31의 275.5와 1,302.0)이 서로 같은 것으로 열을 확인했다.
DEFERRED_ROWS = [
    ("0001104659-25-110831/tm2531034d1_ex99-2.htm.txt", "2025-09-30", "Deferred revenue", "deferred_revenue", 3, 15.6),
    ("0001104659-26-013946/tm266173d1_ex99-1.htm.txt", "2025-12-31", "Deferred revenue, current", "deferred_revenue_current", 2, 275.5),
    ("0001104659-26-013946/tm266173d1_ex99-1.htm.txt", "2025-12-31", "Deferred revenue, non-current", "deferred_revenue_noncurrent", 2, 1302.0),
    ("0001104659-26-064092/nbis-20260331xex99d2.htm.txt", "2026-03-31", "Deferred revenue, current", "deferred_revenue_current", 3, 685.6),
    ("0001104659-26-064092/nbis-20260331xex99d2.htm.txt", "2026-03-31", "Deferred revenue, non-current", "deferred_revenue_noncurrent", 2, 4092.5),
    ("0001104659-26-094844/nbis-20260812xex99d2.htm.txt", "2026-06-30", "Deferred revenue, current", "deferred_revenue_current", 3, 979.4),
    ("0001104659-26-094844/nbis-20260812xex99d2.htm.txt", "2026-06-30", "Deferred revenue, non-current", "deferred_revenue_noncurrent", 2, 4995.8),
    ("0001104659-26-126238/nbis-ex99d2.htm.txt", "2026-09-30", "Deferred revenue, current", "deferred_revenue_current", 3, 1445.5),
    ("0001104659-26-126238/nbis-ex99d2.htm.txt", "2026-09-30", "Deferred revenue, non-current", "deferred_revenue_noncurrent", 2, 7963.7),
]


def nbis_deferred_revenue(known: list[dict]) -> list[dict]:
    """재무상태표에서 선수금 줄을 읽는다. 공시의 제출일과 주소는 이미 뽑아 둔 값(known)에서 같은 파일의 것을 쓴다."""
    meta = {v["source"]["text_file"]: v["source"] for v in known if v["source"].get("text_file")}
    out = []
    for rel, period_end, label, metric, cells, millions in DEFERRED_ROWS:
        tf = "data/raw/filings/nbis/" + rel
        lines = (ROOT / tf).read_text(encoding="utf-8").split("\n")
        clean = [ln.replace("\u200b", "").strip() for ln in lines]
        start = next(i for i, ln in enumerate(clean) if "BALANCE SHEET" in ln.upper() and "as of" not in ln.lower())
        i = next(k for k in range(start, len(clean)) if clean[k] == label)
        j, seen, last = i + 1, 0, ""
        while seen < cells:
            if clean[j]:
                seen, last = seen + 1, clean[j]
            j += 1
        if last.replace(",", "") != f"{millions:.1f}":
            raise SystemExit(f"{rel} {label}: 표에서 읽은 값 {last!r}가 기대한 {millions}와 다르다")
        m = meta[tf]
        out.append({
            "company": "nbis", "metric": metric, "period_start": None, "period_end": period_end, "value": round(millions * 1e6), "unit": "USD",
            "source": {"type": "text", "form": m["form"], "accession": m["accession"], "filed": m["filed"], "url": m["url"],
                       "label": label, "quote": re.sub(r"\s+", " ", "\n".join(lines[i:j])).strip(), "text_file": tf},
        })
    return out


def main() -> None:
    values = load("nbis_values.json") + load("text_values.json") + [nbis_q3_2026_top_customer()] + second_batch()
    values += nbis_deferred_revenue(values)
    kept: dict[tuple, dict] = {}
    dropped = 0
    for v in values:
        if "undelivered" in (v["source"].get("label") or ""):
            dropped += 1
            continue
        if v.get("period_start") == v["period_end"]:
            v = dict(v, period_start=None)
        key = (v["company"], v["metric"], v["period_end"])
        if key not in kept or v["source"]["filed"] > kept[key]["source"]["filed"]:
            kept[key] = v
    out = sorted(kept.values(), key=lambda v: (v["company"], v["metric"], v["period_end"]))
    (ONT / "values_text.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    relations = load("nbis_relations.json") + load("text_relations.json")
    (ONT / "relations.json").write_text(json.dumps(relations, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"값 {len(out)}개(뺀 것 {dropped}개, 겹쳐서 합친 것 {len(values) - dropped - len(out)}개), 관계 {len(relations)}개")


if __name__ == "__main__":
    main()
