"""네비우스 분기 비교 설명 페이지(dashboard/nebius-quarters.html)가 읽는 자료를 만든다(2026-11-14).

쓰는 법: python -I scripts/build_nebius_quarters.py [사건 파일.json]   (사건 파일을 안 주면 data/work/nbis_letters.json)
출력: dashboard/nebius-quarters.data.js  (window.NBQ = {...})

읽는 곳
- 손익계산서·현금흐름표·재무상태표: data/raw/filings/nbis/ 아래 분기 재무제표 4개와 2025년 연간보고서의 원문 txt.
  손익은 9개 분기(2024년 3분기~2026년 3분기), 현금흐름은 8개 분기(2024년 4분기~), 재무상태표는 6개 시점.
  - 4분기 값 = 연간 값 − 9개월 값. 현금흐름표는 연초부터 누계라서 분기 값 = 이번 누계 − 직전 누계.
  - 2025년 3월 말·6월 말 재무상태표는 받아 둔 공시에 없어서 비워 둔다(None).
- 부문 값·제휴 매출·수주 잔고·최대 고객 비중·계약 전력: ontology/values_text.json(이미 인용 검사를 거친 값).
- 분기별 사건: 인자로 준 JSON(주주 서한에서 뽑은 것). 인용문이 원문에 그대로 있는 것만 싣는다.

검산: 분기마다 매출 − 비용 네 가지 = 영업이익, 분기 합 = 누계, 같은 분기를 두 공시에서 읽은 값이 같은지 본다.
"""
import json
import pathlib
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE = ROOT / "data/raw/filings/nbis"
SEC = "https://www.sec.gov/Archives/edgar/data/1513845/"
FILES = {
    "q3_25": "0001104659-25-110831/tm2531034d1_ex99-2.htm.txt",
    "fy25": "0001104659-26-052948/nbis-20251231x20f.htm.txt",
    "q1_26": "0001104659-26-064092/nbis-20260331xex99d2.htm.txt",
    "q2_26": "0001104659-26-094844/nbis-20260812xex99d2.htm.txt",
    "q3_26": "0001104659-26-126238/nbis-ex99d2.htm.txt",
}
LETTERS = {"25Q3": "0001104659-25-109806", "25Q4": "0001104659-26-013946", "26Q1": "0001104659-26-059872",
           "26Q2": "0001104659-26-094568", "26Q3": "0001104659-26-126008"}
QS = ["24Q3", "24Q4", "25Q1", "25Q2", "25Q3", "25Q4", "26Q1", "26Q2", "26Q3"]
END = {"24Q3": "2024-09-30", "24Q4": "2024-12-31", "25Q1": "2025-03-31", "25Q2": "2025-06-30", "25Q3": "2025-09-30",
       "25Q4": "2025-12-31", "26Q1": "2026-03-31", "26Q2": "2026-06-30", "26Q3": "2026-09-30"}
VAL = r"\(?[\d,]+\.\d\)?|—"
NOTE = r"(?: \d{1,2}(?:, ?\d{1,2})*)?"


def flat(path: pathlib.Path) -> str:
    t = re.sub(r"\s+", " ", path.read_text(encoding="utf-8").replace("​", ""))
    return re.sub(r"\( ?([\d,.]+) ?\)", r"(\1)", t)


TEXT = {k: flat(BASE / f) for k, f in FILES.items()}


def num(s: str) -> float:
    if s == "—":
        return 0.0
    v = float(s.strip("()").replace(",", ""))
    return -v if s.startswith("(") else v


def row(doc: str, labels: list[str], n: int, after: str | None = None) -> list[float]:
    """doc에서 labels(정규식) 가운데 처음 맞는 행의 값 n개를 읽는다. after가 있으면 그 글자 뒤에서 찾는다."""
    t = TEXT[doc]
    start = 0
    if after:
        m = re.search(after, t)
        if not m:
            raise SystemExit(f"{doc}: 표 머리를 못 찾음 {after}")
        start = m.start()
    for lab in labels:
        m = re.compile(lab + NOTE + "".join(rf" ({VAL})" for _ in range(n)) + r"(?! ?%)").search(t, start)
        if m:
            return [num(x) for x in m.groups()]
    raise SystemExit(f"{doc}: 행을 못 찾음 {labels}")


IS = {
    "rev": [r"Revenues"],
    "cor": [r"Cost of revenues ?\(1\)"],
    "pd": [r"Product development ?\(1\)"],
    "sga": [r"Sales, general and administrative ?\(1\)"],
    "da": [r"Depreciation and amortization"],
    "op": [r"(?:Income / \(loss\)|Loss) from operations"],
    "int_inc": [r"Interest income"],
    "int_exp": [r"Interest expense"],
}
IS_AFTER = r"STATEMENTS OF OPERATIONS \(In millions"
CF = {
    "ocf": [r"Net cash (?:provided by / \(used in\)|used in) operating activities – continuing operations"],
    "capex": [r"Purchases of property and equipment and intangible assets"],
    "fin": [r"Net cash provided by financing activities ?– continuing operations",
            r"Net cash provided by ?/ ?\(used in\) financing activities"],
}
CF_AFTER = r"CASH FLOWS (?:FROM|PROVIDED BY|USED IN|PROVIDED BY / \(USED IN\))[^:]{0,40}OPERATING ACTIVITIES"
BS = {
    "cash": [r"Cash and cash equivalents"],
    "ppe": [r"Property and equipment, net", r"Property and equipment"],
    "ar": [r"Accounts receivable, less allowance for doubtful accounts of \$[\d.]+ and \$[\d.]+, respectively"],
    "debt_c": [r"Debt, current"],
    "debt_nc": [r"Debt, non-current"],
    "defrev_c": [r"Deferred revenue, current", r"Deferred revenue"],
    "defrev_nc": [r"Deferred revenue, non-current"],
}


def income() -> dict:
    out = {}
    for k, labs in IS.items():
        a = row("q3_25", labs, 4, IS_AFTER)   # 24Q3, 25Q3, 9M24, 9M25
        y = row("fy25", labs, 3, IS_AFTER)    # 2023, 2024, 2025
        b = row("q1_26", labs, 2, IS_AFTER)   # 25Q1, 26Q1
        c = row("q2_26", labs, 4, IS_AFTER)   # 25Q2, 26Q2, 6M25, 6M26
        d = row("q3_26", labs, 4, IS_AFTER)   # 25Q3, 26Q3, 9M25, 9M26
        v = {"24Q3": a[0], "24Q4": y[1] - a[2], "25Q1": b[0], "25Q2": c[0], "25Q3": d[0], "25Q4": y[2] - d[2],
             "26Q1": b[1], "26Q2": c[1], "26Q3": d[1]}
        for what, x, z in ((f"{k} 25Q3 두 공시", a[1], d[0]), (f"{k} 2025 상반기", b[0] + c[0], c[2]),
                           (f"{k} 2026 상반기", b[1] + c[1], c[3]), (f"{k} 2025 9개월", c[2] + d[0], d[2]),
                           (f"{k} 2026 9개월", c[3] + d[1], d[3])):
            if abs(x - z) > 0.25:
                raise SystemExit(f"검산 실패 {what}: {x} ≠ {z}")
        out[k] = {q: round(x, 1) for q, x in v.items()}
    for q in QS:
        got = out["rev"][q] - out["cor"][q] - out["pd"][q] - out["sga"][q] - out["da"][q]
        if abs(got - out["op"][q]) > 0.25:
            raise SystemExit(f"검산 실패 {q}: 매출 − 비용 = {got:.1f} ≠ 영업이익 {out['op'][q]}")
    out["int_exp"] = {q: abs(x) for q, x in out["int_exp"].items()}
    return out


def cashflow() -> dict:
    out = {}
    for k, labs in CF.items():
        y24_9 = row("q3_25", labs, 2, CF_AFTER)[0]
        fy = row("fy25", labs, 3, CF_AFTER)
        m3 = row("q1_26", labs, 2, CF_AFTER)
        m6 = row("q2_26", labs, 2, CF_AFTER)
        m9 = row("q3_26", labs, 2, CF_AFTER)
        v = {"24Q3": None, "24Q4": fy[1] - y24_9, "25Q1": m3[0], "25Q2": m6[0] - m3[0], "25Q3": m9[0] - m6[0],
             "25Q4": fy[2] - m9[0], "26Q1": m3[1], "26Q2": m6[1] - m3[1], "26Q3": m9[1] - m6[1]}
        out[k] = {q: (None if x is None else round(abs(x) if k == "capex" else x, 1)) for q, x in v.items()}
    return out


def balance() -> dict:
    out = {k: {q: None for q in QS} for k in BS}
    for doc, q0, q1 in (("q3_25", "24Q4", "25Q3"), ("q1_26", "25Q4", "26Q1"), ("q2_26", "25Q4", "26Q2"), ("q3_26", "25Q4", "26Q3")):
        for k, labs in BS.items():
            try:
                a, b = row(doc, labs, 2)
            except SystemExit:
                if k == "defrev_nc":   # 2025년 9월까지는 비유동 선수금 줄이 없다
                    a = b = 0.0
                else:
                    raise
            for q, x in ((q0, a), (q1, b)):
                if out[k][q] is not None and abs(out[k][q] - x) > 0.05:
                    raise SystemExit(f"검산 실패 {k} {q}: {out[k][q]} ≠ {x}")
                out[k][q] = x
    res = {"cash": out["cash"], "ppe": out["ppe"], "ar": out["ar"]}
    res["debt"] = {q: None if out["debt_c"][q] is None else round(out["debt_c"][q] + out["debt_nc"][q], 1) for q in QS}
    res["defrev"] = {q: None if out["defrev_c"][q] is None else round(out["defrev_c"][q] + out["defrev_nc"][q], 1) for q in QS}
    return res


def ontology() -> dict:
    vals = json.loads((ROOT / "ontology/values_text.json").read_text(encoding="utf-8"))
    vals = vals["values"] if isinstance(vals, dict) else vals
    rev = {v: k for k, v in END.items()}
    want = {"cloud_revenue": "c_rev", "cloud_adj_ebitda": "c_ebitda", "cloud_employee_cost": "c_emp", "cloud_other_cost": "c_oth",
            "partner_revenue": "partner", "adjusted_ebitda": "adj_ebitda", "rpo": "rpo", "top_customer_share": "top_cust"}
    out = {k: {q: None for q in QS} for k in want.values()}
    power = []
    for v in vals:
        if v["company"] != "nbis":
            continue
        if v["metric"] == "contracted_power_mw":
            power.append({"date": v["period_end"], "mw": v["value"]})
        k = want.get(v["metric"])
        q = rev.get(v["period_end"])
        if not k or not q:
            continue
        money = v.get("unit") == "USD"
        out[k][q] = round(v["value"] / 1e6, 1) if money else v["value"]
    out["power"] = sorted(power, key=lambda p: p["date"])
    return out


def events(path: str | None) -> dict:
    if not path:
        return {}
    raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    out, kept, dropped = {}, 0, 0
    for q, block in raw.items():
        folder = BASE / LETTERS[q]
        out[q] = {}
        for kind in ("events", "kpis", "drivers"):
            good = []
            for it in block.get(kind, []):
                f = folder / it.get("file", "")
                ok = f.is_file() and re.sub(r"\s+", " ", it.get("quote", "")).strip() in re.sub(
                    r"\s+", " ", f.read_text(encoding="utf-8").replace("​", ""))
                if ok and it.get("quote", "").strip():
                    it = dict(it, url=SEC + LETTERS[q].replace("-", "") + "/" + it["file"].removesuffix(".txt"))
                    good.append(it)
                    kept += 1
                else:
                    dropped += 1
            out[q][kind] = good
    print(f"사건 인용 {kept}건 실음, 원문과 안 맞아 뺀 것 {dropped}건")
    return out


def main() -> None:
    s = {}
    s.update(income())
    s.update(cashflow())
    s.update(balance())
    onto = ontology()
    power = onto.pop("power")
    s.update(onto)
    default = ROOT / "data/work/nbis_letters.json"
    ev = events(sys.argv[1] if len(sys.argv) > 1 else (str(default) if default.is_file() else None))
    arr = {q: None for q in QS}   # 발표문의 연환산 매출(ARR). "$551M", "$1.25B" 꼴을 백만 달러로 바꾼다
    for q, block in ev.items():
        for it in block.get("kpis", []):
            m = re.fullmatch(r"\$([\d.]+)([MB])", str(it.get("value", "")))
            if it.get("name") == "arr" and m:
                arr[q] = round(float(m.group(1)) * (1000 if m.group(2) == "B" else 1), 1)
    s["arr"] = arr
    series = {k: [v[q] for q in QS] for k, v in s.items()}
    data = {"quarters": QS, "ends": [END[q] for q in QS], "series": series, "power": power,
            "events": ev}
    dest = ROOT / "dashboard/nebius-quarters.data.js"
    dest.write_text("window.NBQ = " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + ";\n", encoding="utf-8")
    for k, xs in series.items():
        print(f"{k:10s}", " ".join("     —" if x is None else f"{x:8.1f}" for x in xs))
    print(f"{dest.relative_to(ROOT)} 을 썼습니다.")


if __name__ == "__main__":
    main()
