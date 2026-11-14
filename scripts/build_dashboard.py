"""정의와 값을 모아 대시보드가 읽는 dashboard/data.js를 만든다.

쓰는 법: python scripts/build_dashboard.py
입력: ontology/companies.json, metrics.json, values_xbrl.json, values_text.json(있으면), relations.json(있으면),
      monitors.json(감시 조건), theses.json(가설과 조건), absences.json(공시에 없다고 확인한 것), actions.jsonl(행동 기록), views/*.json(질의 화면)
출력: dashboard/data.js, ontology/alerts.json, ontology/judgments.json

행동 기록(검토·정정·잇기·닫기·조건 판정)을 값, 알림, 가설 판정에 다시 반영한다.
알림 계산은 scripts/loop.py, 가설 판정은 scripts/thesis.py의 것을 쓴다.
"""
import datetime as dt
import json
import pathlib

import loop
import thesis

ROOT = pathlib.Path(__file__).resolve().parent.parent
ONT = ROOT / "ontology"


def load(name: str, default=None):
    p = ONT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def normalize(v: dict) -> dict:
    """글에서 뽑은 값의 근거를 구조화 데이터의 근거와 같은 모양으로 맞춘다."""
    s = v["source"]
    if s.get("type") == "text":
        v = dict(v, source={
            "type": "text", "how": s.get("how") or "글에서 뽑음", "label": s.get("label"), "quote": s.get("quote"),
            "filings": [{"form": s.get("form"), "accession": s.get("accession"), "filed": s.get("filed"), "url": s.get("url")}],
        })
    return v


def add_yoy(values: list[dict]) -> None:
    """매출은 있는데 전년 대비 성장률이 없는 분기를 채운다."""
    have = {(v["company"], v["period_end"]) for v in values if v["metric"] == "revenue_yoy"}
    rev = [v for v in values if v["metric"] == "revenue" and v.get("period_start")]
    rev = [v for v in rev if 80 <= (dt.date.fromisoformat(v["period_end"]) - dt.date.fromisoformat(v["period_start"])).days <= 100]
    for v in rev:
        if (v["company"], v["period_end"]) in have:
            continue
        target = dt.date.fromisoformat(v["period_end"]) - dt.timedelta(days=365)
        prior = [p for p in rev if p["company"] == v["company"]
                 and abs((dt.date.fromisoformat(p["period_end"]) - target).days) <= 10]
        if prior and prior[0]["value"]:
            values.append({
                "company": v["company"], "metric": "revenue_yoy", "period_start": v["period_start"],
                "period_end": v["period_end"], "value": round((v["value"] / prior[0]["value"] - 1) * 100, 1), "unit": "%",
                "source": {"type": "derived", "how": f"{v['period_end']} 매출과 {prior[0]['period_end']} 매출에서 계산", "filings": []},
            })


def is_quarter(v: dict) -> bool:
    """기간 값이면 한 분기 길이인지 본다. 시점 값(시작일 없음)은 그대로 통과."""
    if not v.get("period_start"):
        return True
    return 80 <= (dt.date.fromisoformat(v["period_end"]) - dt.date.fromisoformat(v["period_start"])).days <= 100


def add_derived(values: list[dict], metrics: list[dict], skips: list[dict] | None = None) -> None:
    """metrics.json의 derive 정의대로 계산 지표를 채운다. 재료 값이 둘 다 있는 기간만 만든다.

    derive: {"op": "delta", "a": 지표}는 바로 앞 분기 값과의 차이(잔액의 한 분기 변화)를 만든다.
    derive: {"op": "ratio", "diff" 또는 "sum", "a": 지표, "b": 지표, "scale": 곱할 수(기본 1), "b_positive": 분모가 0 이하면 만들지 않음}
    분모 때문에 만들지 못한 기간은 skips에 이유와 함께 적는다.
    """
    names = {m["id"]: m["name"] for m in metrics}
    for m in metrics:
        d = m.get("derive")
        if not d:
            continue
        have = {(v["company"], v["period_end"]) for v in values if v["metric"] == m["id"]}
        if d["op"] == "delta":
            rows = sorted((v for v in values if v["metric"] == d["a"]), key=lambda v: (v["company"], v["period_end"]))
            for prev, cur in zip(rows, rows[1:]):
                gap = (dt.date.fromisoformat(cur["period_end"]) - dt.date.fromisoformat(prev["period_end"])).days
                if prev["company"] != cur["company"] or gap > 120 or (cur["company"], cur["period_end"]) in have:
                    continue
                raw = any(x.get("review", {}).get("status") in ("unreviewed", "inputs_unreviewed") for x in (prev, cur))
                values.append({
                    "company": cur["company"], "metric": m["id"],
                    "period_start": (dt.date.fromisoformat(prev["period_end"]) + dt.timedelta(days=1)).isoformat(),
                    "period_end": cur["period_end"], "value": cur["value"] - prev["value"], "unit": m["unit"],
                    "source": {"type": "derived", "how": f"{names[d['a']]}의 {prev['period_end']} 값과 {cur['period_end']} 값의 차이",
                               "filings": [f for f in (cur["source"].get("filings") or []) if f],
                               "inputs": [{"metric": d["a"], "value": cur["value"], "unit": cur["unit"]}]},
                    "review": {"status": "inputs_unreviewed" if raw else "n/a"},
                })
            continue
        side = {k: {(v["company"], v["period_end"]): v for v in values if v["metric"] == d[k] and is_quarter(v)} for k in ("a", "b")}
        for key in sorted(side["a"].keys() & side["b"].keys()):
            a, b = side["a"][key], side["b"][key]
            if key in have:
                continue
            if d["op"] in ("diff", "sum"):
                value = a["value"] - b["value"] if d["op"] == "diff" else a["value"] + b["value"]
            else:
                if not b["value"] or (d.get("b_positive") and b["value"] <= 0):
                    if skips is not None:
                        skips.append({"company": key[0], "metric": m["id"], "period_end": key[1], "reason": "not_applicable",
                                      "note": f"{names[d['b']]}{loop.josa(names[d['b']], ('이', '가'))} 0 이하({loop.fmt(b['value'], b['unit'])})라 계산할 수 없다."})
                    continue
                value = round(a["value"] / b["value"] * d.get("scale", 1), 1 if m["unit"] == "%" else 2)
            filings, seen = [], set()
            for f in (a["source"].get("filings") or []) + (b["source"].get("filings") or []):
                if f and f.get("url") not in seen:
                    seen.add(f.get("url"))
                    filings.append(f)
            raw = any(x.get("review", {}).get("status") in ("unreviewed", "inputs_unreviewed") for x in (a, b))
            values.append({
                "company": key[0], "metric": m["id"], "period_start": a.get("period_start") if b.get("period_start") else None,
                "period_end": key[1], "value": value, "unit": m["unit"],
                "source": {"type": "derived", "how": f"{names[d['a']]}, {names[d['b']]} 값에서 계산", "filings": filings,
                           "inputs": [{"metric": x["metric"], "value": x["value"], "unit": x["unit"]} for x in (a, b)]},
                "review": {"status": "inputs_unreviewed" if raw else "n/a"},
            })


ROLE_NAMES = {"gpu_cloud": "GPU 클라우드", "gpu_supplier": "GPU 공급사"}


def compute_gaps(values: list[dict], metrics: list[dict], companies: list[dict], skips: list[dict], absences: list[dict]) -> dict:
    """값이 없는 칸마다 이유를 붙인다. 열쇠는 "회사|지표" 또는 "회사|지표|기간 말".

    이유(reason): not_read(아직 안 읽음), not_disclosed(공시에 없음), not_applicable(해당 없음).
    - 공시에 없음은 사람이 원문을 확인하고 ontology/absences.json에 적은 것만 쓴다. 추측으로 붙이지 않는다.
    - 해당 없음은 지표의 applies_to(회사 역할)와, 계산할 수 없는 경우(분모가 0 이하)에서 나온다.
    - 나머지는 모두 아직 안 읽음이다.
    """
    have = {(v["company"], v["metric"]) for v in values}
    M = {m["id"]: m for m in metrics}
    gaps = {}
    for a in absences:
        key = "|".join(x for x in (a["company"], a["metric"], a.get("period_end")) if x)
        gaps[key] = {"reason": a["reason"], "note": a.get("note", "")}
    for s in skips:
        gaps.setdefault(f"{s['company']}|{s['metric']}|{s['period_end']}", {"reason": s["reason"], "note": s["note"]})

    def why(cid: str, role: str, mid: str) -> dict:
        if f"{cid}|{mid}" in gaps:
            return gaps[f"{cid}|{mid}"]
        m = M[mid]
        if m.get("only") and cid not in m["only"]:
            return {"reason": "not_applicable", "note": "회사가 따로 밝히는 부문 값이라 그 회사에만 있다."}
        if m.get("applies_to") and role not in m["applies_to"]:
            roles = ", ".join(ROLE_NAMES.get(r, r) for r in m["applies_to"])
            return {"reason": "not_applicable", "note": f"이 지표는 {roles} 회사에만 해당한다."}
        d = m.get("derive")
        if d:
            for k in ("a", "b"):
                if k in d and (cid, d[k]) not in have:
                    g = why(cid, role, d[k])
                    return {"reason": g["reason"], "note": f"재료 값({M[d[k]]['name']})이 없다. {g['note']}"}
            mine = sorted((s for s in skips if (s["company"], s["metric"]) == (cid, mid)), key=lambda s: s["period_end"])
            if mine:
                return {"reason": mine[-1]["reason"], "note": mine[-1]["note"]}
            return {"reason": "not_read", "note": "재료 값은 있지만 같은 분기의 것이 없다."}
        return {"reason": "not_read", "note": "원문에서 이 값을 아직 찾아보지 않았다."}

    for c in companies:
        for m in metrics:
            if (c["id"], m["id"]) not in have:
                gaps[f"{c['id']}|{m['id']}"] = why(c["id"], c["role"], m["id"])
    return gaps


def load_values(skips: list[dict] | None = None, keep=None, actions: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """값을 모으고 행동 기록(정정·검토)을 반영한 뒤 파생 값을 채운다. (값, 행동 기록)을 돌려준다."""
    values = [normalize(v) for v in load("values_xbrl.json", []) + load("values_text.json", []) if keep is None or keep(v)]
    actions = loop.load_actions() if actions is None else actions
    loop.apply_actions(values, actions)
    add_yoy(values)
    add_derived(values, load("metrics.json"), skips)
    for v in values:
        v.setdefault("review", {"status": "n/a"})
    return values, actions


def build(keep=None, actions: list[dict] | None = None) -> dict:
    """화면이 읽을 자료를 만든다. keep(값을 걸러 내는 함수)과 actions(기록 대신 쓸 목록)는 scripts/replay.py가 지난 때를 다시 볼 때 쓴다."""
    skips: list[dict] = []
    values, actions = load_values(skips, keep, actions)
    monitors = load("monitors.json", [])
    alerts = loop.compute_alerts(values, monitors, load("relations.json", []), load("companies.json"),
                                 load("metrics.json"), actions)
    metrics = load("metrics.json")
    gaps = compute_gaps(values, metrics, load("companies.json"), skips, load("absences.json", []))
    theses = thesis.compute(values, load("theses.json", []), actions, metrics, gaps)
    thesis.link_alerts(alerts, theses, metrics)
    alerts = thesis.alerts_from(theses, loop.dismissals_from(actions)) + alerts
    return {
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "companies": load("companies.json"),
        "metrics": load("metrics.json"),
        "values": values,
        "relations": load("relations.json", []),
        "views": [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "views").glob("*.json"))],
        "monitors": monitors,
        "alerts": alerts,
        "theses": theses,
        "paused_theses": [{"company": t["company"], "statement": t["statement"], "note": t.get("note", "")}
                          for t in load("theses.json", []) if t.get("paused")],
        "gaps": gaps,
        "actions": actions,
    }


def main() -> None:
    data = build()
    (ONT / "alerts.json").write_text(json.dumps(data["alerts"], ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (ONT / "judgments.json").write_text(json.dumps(data["theses"], ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    out = ROOT / "dashboard" / "data.js"
    out.parent.mkdir(exist_ok=True)
    out.write_text("window.DATA = " + json.dumps(data, ensure_ascii=False) + ";\n", encoding="utf-8")
    n_open = sum(1 for a in data["alerts"] if not a["dismissed"])
    print(f"값 {len(data['values'])}개, 관계 {len(data['relations'])}개, 질의 화면 {len(data['views'])}개, "
          f"알림 {len(data['alerts'])}건(열림 {n_open}), 가설 {len(data['theses'])}개, 행동 기록 {len(data['actions'])}줄을 dashboard/data.js에 썼습니다.")


if __name__ == "__main__":
    main()
