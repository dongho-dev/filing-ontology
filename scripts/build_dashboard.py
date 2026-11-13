"""정의와 값을 모아 대시보드가 읽는 dashboard/data.js를 만든다.

쓰는 법: python scripts/build_dashboard.py
입력: ontology/companies.json, metrics.json, values_xbrl.json, values_text.json(있으면), relations.json(있으면),
      monitors.json(감시 조건), theses.json(가설과 조건), actions.jsonl(행동 기록), views/*.json(질의 화면)
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
            "type": "text", "how": "글에서 뽑음", "label": s.get("label"), "quote": s.get("quote"),
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


def load_values() -> tuple[list[dict], list[dict]]:
    """값을 모으고 행동 기록(정정·검토)을 반영한 뒤 파생 값을 채운다. (값, 행동 기록)을 돌려준다."""
    values = [normalize(v) for v in load("values_xbrl.json", []) + load("values_text.json", [])]
    actions = loop.load_actions()
    loop.apply_actions(values, actions)
    add_yoy(values)
    for v in values:
        v.setdefault("review", {"status": "n/a"})
    return values, actions


def build() -> dict:
    values, actions = load_values()
    monitors = load("monitors.json", [])
    alerts = loop.compute_alerts(values, monitors, load("relations.json", []), load("companies.json"),
                                 load("metrics.json"), actions)
    return {
        "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "companies": load("companies.json"),
        "metrics": load("metrics.json"),
        "values": values,
        "relations": load("relations.json", []),
        "views": [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "views").glob("*.json"))],
        "monitors": monitors,
        "alerts": alerts,
        "theses": thesis.compute(values, load("theses.json", []), actions, load("metrics.json")),
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
