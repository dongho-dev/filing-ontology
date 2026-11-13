"""글에서 뽑은 값(data/work/)을 검토 규칙에 따라 걸러 ontology/에 옮긴다.

쓰는 법: python scripts/merge_text.py
입력: data/work/nbis_values.json, data/work/text_values.json, data/work/*_relations.json
출력: ontology/values_text.json, ontology/relations.json

검토하며 정한 규칙(2026-11-13):
- 코어위브 10-Q의 "undelivered" 전력은 특정 부지에 아직 인도되지 않은 전력이라 계약 전력 총량이 아니다. 뺀다.
- 같은 회사·지표·기간이 여러 문서에 있으면 나중에 제출된 문서의 값을 쓴다.
- 기간 말 시점의 값(전력, 현금, 수주 잔고)은 시작일을 비운다.
- 네비우스 2026년 3분기의 최대 고객 비중은 처음 뽑을 때 빠졌다(표의 열을 잘못 읽음). 표에서 다시 읽어 넣는다.
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


def table_row_quote(text_file: str, row_label: str, cells: int) -> str:
    """한 칸이 한 줄로 풀린 표에서, 행 이름부터 값 cells개까지를 원문 그대로 이어 붙인다."""
    lines = (ROOT / text_file).read_text(encoding="utf-8").split("\n")
    start = next(i for i, ln in enumerate(lines) if "accounted for 10% or more" in ln)
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


def main() -> None:
    values = load("nbis_values.json") + load("text_values.json") + [nbis_q3_2026_top_customer()]
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
