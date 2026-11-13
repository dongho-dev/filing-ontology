"""가설(theses.json)의 조건을 최신 값에 비춰 판정한다.

쓰는 법: python scripts/thesis.py
입력: ontology/theses.json, actions.jsonl(사람의 판정 기록), companies.json, metrics.json, values_*.json
출력: ontology/judgments.json, 화면에 가설별 판정

조건의 종류(kind):
- increase, decrease: 최신 분기 값을 직전 분기 값과 비교한다. near가 있으면 변화율이 near% 이내일 때 "가까워짐".
- at_most, at_least: 최신 분기 값을 기준값(threshold, 지표의 단위 그대로)과 비교한다.
  near가 있으면 기준까지 near 이내일 때 "가까워짐".
- manual: 발언처럼 숫자로 판정할 수 없는 조건. 사람이 읽고 남긴 판정 기록(judge)을 쓴다.

판정 결과: held(지켜짐), near(가까워짐), broken(깨짐), unknown(판정 불가).
회사의 최신 분기에 해당하는 값이나 판정 기록이 없으면 지난 값으로 대신하지 않고 판정 불가로 둔다.
시스템은 사실과 기준 사이의 관계만 말한다. 추천하지 않는다.
"""
import datetime as dt
import json
import pathlib

import loop

ROOT = pathlib.Path(__file__).resolve().parent.parent
ONT = ROOT / "ontology"
RESULTS = {"held": "지켜짐", "near": "가까워짐", "broken": "깨짐", "unknown": "판정 불가"}
STATUS = {"broken": "깨짐", "near": "흔들림", "unknown": "판정 불가", "held": "유지"}
VALUE_KINDS = ("increase", "decrease", "at_most", "at_least")
QUARTER_GAP_DAYS = 120


def jkey(thesis: str, condition: str, period_end: str) -> str:
    return f"{thesis}|{condition}|{period_end}"


def judgments_from(actions: list[dict]) -> dict:
    """(가설, 조건, 기간) → 마지막 사람의 판정 기록."""
    out = {}
    for a in actions:
        if a.get("type") == "judge":
            t = a.get("target") or {}
            out[jkey(t.get("thesis"), t.get("condition"), t.get("period_end"))] = a
    return out


def basis_of(v: dict) -> dict:
    return {"period_end": v["period_end"], "value": v["value"], "label": v["source"].get("label"),
            "source_type": v["source"].get("type"), "review": v.get("review", {}).get("status", "n/a"),
            "filing": loop.latest_filing(v)}


def rule_text(cond: dict, unit: str) -> str:
    """조건의 기준을 사람이 읽는 말로 옮긴다."""
    k, near = cond["kind"], cond.get("near")
    if k == "manual":
        return "사람이 읽고 판정"
    if k in ("increase", "decrease"):
        s = "직전 분기보다 " + ("늘어남" if k == "increase" else "줄어듦")
        return s + (f" (변화가 {near:g}% 이내면 가까워짐)" if near else "")
    th = loop.fmt(cond["threshold"], unit)
    s = f"{th} " + ("이하" if k == "at_most" else "이상")
    if near:
        gap = f"{near:g}%p" if unit == "%" else loop.fmt(near, unit)
        s += f" (기준까지 {gap} 이내면 가까워짐)"
    return s


def judge_at(cond: dict, s: list[dict], i: int, unit: str) -> dict:
    """series s의 i번째 값으로 조건을 판정한다."""
    cur, k, near = s[i], cond["kind"], cond.get("near")
    out = {"period_end": cur["period_end"], "basis": [basis_of(cur)]}
    if k in ("at_most", "at_least"):
        th = cond["threshold"]
        gap = th - cur["value"] if k == "at_most" else cur["value"] - th
        gap_text = f"{abs(gap):g}%p" if unit == "%" else loop.fmt(abs(gap), unit)
        side = "아래" if k == "at_most" else "위"
        if gap < 0:
            ths = loop.fmt(th, unit)
            text = (f"기준 {ths}{loop.josa(ths, ('을', '를'))} {gap_text} 넘어섬." if k == "at_most"
                    else f"기준 {ths}에 {gap_text} 못 미침.")
            return dict(out, result="broken", text=f"{loop.fmt(cur['value'], unit)}. {text}")
        res = "near" if near is not None and gap <= near else "held"
        return dict(out, result=res, text=f"{loop.fmt(cur['value'], unit)}. 기준 {loop.fmt(th, unit)}보다 {gap_text} {side}.")
    # increase, decrease
    if i == 0:
        return dict(out, result="unknown", text="비교할 직전 분기 값이 없음.")
    prev = s[i - 1]
    days = (dt.date.fromisoformat(cur["period_end"]) - dt.date.fromisoformat(prev["period_end"])).days
    if days > QUARTER_GAP_DAYS:
        return dict(out, result="unknown", text=f"바로 앞 분기 값이 없음 (가장 가까운 값은 {prev['period_end']}).")
    out["basis"].append(basis_of(prev))
    diff = cur["value"] - prev["value"]
    if unit == "%":
        pct, change = None, f"{diff:+.1f}%p"
    elif prev["value"] and prev["value"] > 0:
        pct = (cur["value"] / prev["value"] - 1) * 100
        change = f"{pct:+.1f}%"
    else:
        pct, change = None, ("+" if diff > 0 else "-") + loop.fmt(abs(diff), unit)
    text = f"{loop.fmt(prev['value'], unit)} → {loop.fmt(cur['value'], unit)} ({change})."
    ok = diff > 0 if k == "increase" else diff < 0
    if not ok:
        return dict(out, result="broken", text=text)
    res = "near" if near is not None and pct is not None and abs(pct) <= near else "held"
    return dict(out, result=res, text=text)


def judge_value(cond: dict, values: list[dict], company: str, latest: str | None, unit: str) -> tuple[dict, dict | None]:
    """(최신 분기의 판정, 그 앞 분기의 판정)을 돌려준다."""
    s = loop.series(values, company, cond["metric"])
    if not s:
        return {"result": "unknown", "period_end": latest, "text": "이 지표의 값이 없음.", "basis": []}, None
    last = len(s) - 1
    if latest and s[last]["period_end"] < latest:
        v = s[last]
        cur = {"result": "unknown", "period_end": latest, "basis": [basis_of(v)],
               "text": f"최신 분기({latest})의 값이 없음. 마지막 값은 {v['period_end']}의 {loop.fmt(v['value'], unit)}."}
        prev = judge_at(cond, s, last, unit)
    else:
        cur = judge_at(cond, s, last, unit)
        prev = judge_at(cond, s, last - 1, unit) if last >= 1 else None
    return cur, ({"result": prev["result"], "period_end": prev["period_end"], "text": prev["text"]} if prev else None)


def judge_manual(thesis_id: str, cond: dict, latest: str | None, human: dict) -> tuple[dict, dict | None]:
    mine = sorted((a for a in human.values() if (a["target"].get("thesis"), a["target"].get("condition")) == (thesis_id, cond["id"])),
                  key=lambda a: a["target"].get("period_end") or "")
    now = next((a for a in mine if a["target"].get("period_end") == latest), None)
    older = [a for a in mine if (a["target"].get("period_end") or "") < (latest or "")]
    prev = None
    if older:
        a = older[-1]
        prev = {"result": a["verdict"], "period_end": a["target"]["period_end"], "text": a.get("note", "")}
    if now is None:
        return {"result": "unknown", "period_end": latest, "basis": [],
                "text": "사람이 읽고 판정해야 함. 이 분기에 대한 판정 기록이 없음."}, prev
    return {"result": now["verdict"], "period_end": latest, "basis": [], "by": "human", "ts": now.get("ts"),
            "text": now.get("note", "")}, prev


def compute(values: list[dict], theses: list[dict], actions: list[dict], metrics: list[dict]) -> list[dict]:
    """values는 행동 기록(정정)을 반영한 값이어야 한다."""
    M = {m["id"]: m for m in metrics}
    human = judgments_from(actions)
    out = []
    for t in theses:
        rev = loop.series(values, t["company"], "revenue")
        latest = rev[-1]["period_end"] if rev else None
        conds = []
        for c in t["conditions"]:
            unit = M.get(c.get("metric"), {}).get("unit")
            if c["kind"] == "manual":
                cur, prev = judge_manual(t["id"], c, latest, human)
            elif c["kind"] in VALUE_KINDS:
                cur, prev = judge_value(c, values, t["company"], latest, unit)
            else:
                raise ValueError(f"모르는 조건 종류: {c['kind']}")
            conds.append(dict(c, rule=rule_text(c, unit), unit=unit, verdict=cur, previous=prev))
        counts = {r: sum(1 for c in conds if c["verdict"]["result"] == r) for r in RESULTS}
        status = next(r for r in ("broken", "near", "unknown", "held") if counts[r] or r == "held")
        out.append({"id": t["id"], "company": t["company"], "statement": t["statement"], "created": t.get("created"),
                    "example": bool(t.get("example")), "note": t.get("note", ""), "period_end": latest,
                    "status": status, "counts": counts, "conditions": conds})
    return out


def validate_judge(a: dict, theses: list[dict]):
    """judge 행동에 문제가 있으면 한국어 이유를, 없으면 None을 돌려준다. theses는 compute의 결과."""
    target = a.get("target") or {}
    t = next((x for x in theses if x["id"] == target.get("thesis")), None)
    if t is None:
        return "그 가설이 없습니다."
    c = next((x for x in t["conditions"] if x["id"] == target.get("condition")), None)
    if c is None:
        return "그 조건이 없습니다."
    if c["kind"] != "manual":
        return "값으로 판정하는 조건은 사람이 판정하지 않습니다. 값이 틀렸다면 값을 정정하세요."
    if target.get("period_end") != t["period_end"]:
        return f"최신 분기({t['period_end']})에 대해서만 판정할 수 있습니다."
    if a.get("verdict") not in ("held", "near", "broken"):
        return "verdict는 held, near, broken 중 하나여야 합니다."
    if not (a.get("note") or "").strip():
        return "판정에는 이유(note)가 필요합니다."
    return None


def main() -> None:
    from build_dashboard import load, load_values  # 순환 import를 피하려고 여기서 부른다

    values, actions = load_values()
    result = compute(values, load("theses.json", []), actions, load("metrics.json"))
    (ONT / "judgments.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    names = {c["id"]: c["name"] for c in load("companies.json")}
    print(f"가설 {len(result)}개 → ontology/judgments.json")
    for t in result:
        tag = " (예시)" if t["example"] else ""
        print(f"- [{STATUS[t['status']]}] {names.get(t['company'], t['company'])}{tag}: {t['statement']} · 최신 분기 {t['period_end']}")
        for c in t["conditions"]:
            print(f"    [{RESULTS[c['verdict']['result']]}] {c['name']} · {c['verdict']['text']}")


if __name__ == "__main__":
    main()
