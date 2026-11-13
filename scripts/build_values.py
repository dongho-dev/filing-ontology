"""SEC 구조화 재무 데이터(XBRL)에서 분기별 지표 값을 만든다.

쓰는 법: python scripts/build_values.py
입력: data/raw/sec/<id>_facts.json, data/raw/sec/<id>_submissions.json
출력: ontology/values_xbrl.json

분기 값이 직접 없으면(현금흐름표처럼 누적으로만 보고되는 항목) 누적 값의 차이로 구한다.
"""
import datetime as dt
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "sec"
ONT = ROOT / "ontology"
QUARTERS_SHOWN = 5
PERIODIC_FORMS = {"10-Q", "10-K", "10-Q/A", "10-K/A"}


def d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def load_facts(cid: str, tag: str) -> list[dict]:
    facts = json.loads((RAW / f"{cid}_facts.json").read_text(encoding="utf-8"))["facts"]
    node = facts.get("us-gaap", {}).get(tag)
    if not node or "USD" not in node["units"]:
        return []
    return [dict(f, tag=tag) for f in node["units"]["USD"] if f["form"] in PERIODIC_FORMS]


def latest_by_period(facts: list[dict]) -> dict[tuple, dict]:
    """같은 기간이 여러 공시에 나오면 가장 나중에 제출된 값을 쓴다."""
    out: dict[tuple, dict] = {}
    for f in facts:
        key = (f.get("start"), f["end"])
        if key not in out or f["filed"] > out[key]["filed"]:
            out[key] = f
    return out


def quarter_values(facts: list[dict]) -> dict[str, dict]:
    """기간 말 날짜 -> {value, start, parts}. parts는 값을 만드는 데 쓴 원 자료."""
    by = latest_by_period([f for f in facts if f.get("start")])
    out: dict[str, dict] = {}
    for end in sorted({e for _, e in by}):
        direct = [f for (s, e), f in by.items() if e == end and 80 <= (d(e) - d(s)).days <= 100]
        if direct:
            f = direct[0]
            out[end] = {"value": f["val"], "start": f["start"], "how": "분기 값 그대로", "parts": [f]}
            continue
        longer = sorted(
            (f for (s, e), f in by.items() if e == end and (d(e) - d(s)).days > 100),
            key=lambda f: (d(f["end"]) - d(f["start"])).days,
        )
        for f in longer:
            prev = [
                g for (s, e), g in by.items()
                if s == f["start"] and 80 <= (d(end) - d(e)).days <= 100
            ]
            if prev:
                g = prev[0]
                start = (d(g["end"]) + dt.timedelta(days=1)).isoformat()
                out[end] = {"value": f["val"] - g["val"], "start": start, "how": "누적 값의 차이", "parts": [f, g]}
                break
    return out


def instant_values(facts: list[dict]) -> dict[str, dict]:
    by = latest_by_period([f for f in facts if not f.get("start")])
    return {e: {"value": f["val"], "start": None, "how": "기간 말 값 그대로", "parts": [f]} for (_, e), f in by.items()}


def merged_values(cid: str, tags: list[str], kind: str) -> tuple[dict, list]:
    """항목 여러 개를 앞에 적힌 순서대로 합친다. 같은 기간에 두 항목의 값이 다르면 앞의 것을 쓰고 알린다.

    회사가 중간에 표시 항목을 바꾸는 경우가 있어서(예: 총매출 항목이 바뀜) 항목 하나만 보면 분기가 빈다.
    """
    by: dict[tuple, dict] = {}
    clashes = []
    for tag in tags:
        for key, f in latest_by_period(load_facts(cid, tag)).items():
            if key in by:
                a = by[key]["val"]
                if a and abs(f["val"] - a) / abs(a) > 0.01:
                    clashes.append((key[1], by[key]["tag"], a, tag, f["val"]))
            else:
                by[key] = f
    facts = list(by.values())
    vals = quarter_values(facts) if kind == "duration" else instant_values(facts)
    return vals, clashes


def doc_urls(cid: str, cik: int) -> dict[str, str]:
    rec = json.loads((RAW / f"{cid}_submissions.json").read_text(encoding="utf-8"))["filings"]["recent"]
    return {
        a: f"https://www.sec.gov/Archives/edgar/data/{cik}/{a.replace('-', '')}/{doc}"
        for a, doc in zip(rec["accessionNumber"], rec["primaryDocument"])
    }


def source(parts: list[dict], how: str, urls: dict[str, str]) -> dict:
    return {
        "type": "xbrl",
        "tag": " / ".join(dict.fromkeys(p["tag"] for p in parts)),
        "how": how,
        "filings": [
            {"form": p["form"], "accession": p["accn"], "filed": p["filed"], "url": urls.get(p["accn"])}
            for p in parts
        ],
    }


def main() -> None:
    companies = json.loads((ONT / "companies.json").read_text(encoding="utf-8"))
    metrics = json.loads((ONT / "metrics.json").read_text(encoding="utf-8"))
    values, notes = [], []
    for c in companies:
        cid = c["id"]
        if "10-Q" not in c["forms"]:
            notes.append(f"{cid}: 10-Q를 내지 않아 구조화 분기 데이터가 없음")
            continue
        urls = doc_urls(cid, c["cik"])
        rev, _ = merged_values(cid, metrics[0]["xbrl"], "duration")
        ends_all = sorted(rev)
        ends = ends_all[-QUARTERS_SHOWN:]
        for m in metrics:
            if m.get("derive"):
                continue  # 다른 지표에서 계산하는 지표는 build_dashboard.py가 채운다
            if m["kind"] == "derived":
                for e in ends:
                    target = d(e) - dt.timedelta(days=365)
                    prior = [p for p in ends_all if abs((d(p) - target).days) <= 10]
                    if prior and rev[prior[0]]["value"]:
                        pct = (rev[e]["value"] / rev[prior[0]]["value"] - 1) * 100
                        values.append({
                            "company": cid, "metric": m["id"], "period_start": rev[e]["start"], "period_end": e,
                            "value": round(pct, 1), "unit": "%",
                            "source": {"type": "derived", "how": f"{e} 매출과 {prior[0]} 매출에서 계산", "filings": []},
                        })
                continue
            if m["kind"] not in ("duration", "instant"):
                continue
            vals, clashes = merged_values(cid, m["xbrl"], m["kind"])
            missing = [e for e in ends if e not in vals]
            if missing:
                notes.append(f"{cid} {m['id']}: 값이 없는 분기 {missing}")
            recent = [c for c in clashes if c[0] >= ends[0]]
            if recent:
                e, t1, v1, t2, v2 = recent[-1]
                notes.append(f"{cid} {m['id']}: 항목 두 개의 값이 다름 {len(recent)}건. 예: {e} {t1}={v1:,} / {t2}={v2:,}. 앞의 것을 씀")
            for e in ends:
                if e in vals:
                    v = vals[e]
                    values.append({
                        "company": cid, "metric": m["id"], "period_start": v["start"], "period_end": e,
                        "value": v["value"], "unit": m["unit"], "source": source(v["parts"], v["how"], urls),
                    })
    (ONT / "values_xbrl.json").write_text(json.dumps(values, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"값 {len(values)}개를 ontology/values_xbrl.json에 썼습니다.")
    for n in notes:
        print("알림:", n)
    for c in companies:
        rows = [v for v in values if v["company"] == c["id"]]
        for e in sorted({v["period_end"] for v in rows}):
            cells = {v["metric"]: v["value"] for v in rows if v["period_end"] == e}
            fmt = lambda x: "-" if x is None else (f"{x/1e6:,.0f}M" if abs(x) >= 1e5 else f"{x}")
            print(c["id"], e, " ".join(f"{k}={fmt(cells.get(k))}" for k in ("revenue", "revenue_yoy", "operating_income", "capex", "rpo", "cash")))


if __name__ == "__main__":
    main()
