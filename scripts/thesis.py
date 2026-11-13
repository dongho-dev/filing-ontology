"""가설(theses.json)의 조건을 최신 값에 비춰 판정한다.

쓰는 법: python scripts/thesis.py
입력: ontology/theses.json, actions.jsonl(사람의 판정 기록), companies.json, metrics.json, values_*.json
출력: ontology/judgments.json, 화면에 가설별 판정

조건의 종류(kind):
- increase, decrease: 최신 분기 값을 직전 분기 값과 비교한다. 가설과 반대로 움직이면 "반대로 움직임",
  break_after(기본 2)분기 연속이면 "깨짐". near가 있으면 변화율이 near% 이내일 때 "가까워짐".
- at_most, at_least: 최신 분기 값을 기준값(threshold, 지표의 단위 그대로)과 비교한다.
  near가 있으면 기준까지 near 이내일 때 "가까워짐".
- manual: 발언처럼 숫자로 판정할 수 없는 조건. 사람이 읽고 남긴 판정 기록(judge)을 쓴다.

판정 결과: held(지켜짐), near(가까워짐), reversed(반대로 움직임), broken(깨짐), unknown(판정 불가).
회사의 최신 분기에 해당하는 값이나 판정 기록이 없으면 지난 값으로 대신하지 않고 판정 불가로 둔다.
시스템은 사실과 기준 사이의 관계만 말한다. 추천하지 않는다.
"""
import datetime as dt
import json
import pathlib

import loop

ROOT = pathlib.Path(__file__).resolve().parent.parent
ONT = ROOT / "ontology"
RESULTS = {"held": "지켜짐", "near": "가까워짐", "reversed": "반대로 움직임", "broken": "깨짐", "unknown": "판정 불가"}
STATUS = {"broken": "깨짐", "reversed": "흔들림", "near": "흔들림", "unknown": "판정 불가", "held": "유지"}
VALUE_KINDS = ("increase", "decrease", "at_most", "at_least")
GAPS = {"not_read": "아직 안 읽음", "not_disclosed": "공시에 없음", "not_applicable": "해당 없음"}
QUARTER_GAP_DAYS = 120
BREAK_AFTER = 2  # 이만큼 연속으로 반대로 움직이면 깨짐
TRAIL_LEN = 4  # 화면에 보여 줄 최근 움직임의 수


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
        s += f" ({cond.get('break_after', BREAK_AFTER)}분기 연속 반대로 가면 깨짐)"
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
    if not adjacent(prev, cur):
        return dict(out, result="unknown", text=f"바로 앞 분기 값이 없음 (가장 가까운 값은 {prev['period_end']}).")
    out["basis"].append(basis_of(prev))
    trail = moves(cond, s, i, unit)
    out["trail"] = trail[-TRAIL_LEN:]
    last = trail[-1]
    text = f"{loop.fmt(prev['value'], unit)} → {loop.fmt(cur['value'], unit)} ({last['change']})."
    if not last["ok"]:
        streak = 0
        for m in reversed(trail):
            if m["ok"]:
                break
            streak += 1
        if streak >= cond.get("break_after", BREAK_AFTER):
            return dict(out, result="broken", text=f"{text} {streak}분기 연속 가설과 반대로 움직임.")
        return dict(out, result="reversed", text=f"{text} 가설과 반대로 움직임 ({streak}분기째).")
    res = "near" if near is not None and last["pct"] is not None and abs(last["pct"]) <= near else "held"
    return dict(out, result=res, text=text)


def adjacent(prev: dict, cur: dict) -> bool:
    """두 값이 바로 이어진 분기의 것인가."""
    return (dt.date.fromisoformat(cur["period_end"]) - dt.date.fromisoformat(prev["period_end"])).days <= QUARTER_GAP_DAYS


def moves(cond: dict, s: list[dict], i: int, unit: str) -> list[dict]:
    """i번째 값까지 끊기지 않고 이어진 분기들의 움직임을 오래된 것부터 돌려준다. ok는 가설이 말한 방향인지."""
    out = []
    j = i
    while j >= 1 and adjacent(s[j - 1], s[j]):
        a, b = s[j - 1], s[j]
        diff = b["value"] - a["value"]
        if unit == "%":
            pct, change = None, f"{diff:+.1f}%p"
        elif unit == "x":
            pct, change = None, f"{diff:+.2f}배"
        elif a["value"] and a["value"] > 0:
            pct = (b["value"] / a["value"] - 1) * 100
            change = f"{pct:+.1f}%"
        else:
            pct, change = None, ("+" if diff > 0 else "-") + loop.fmt(abs(diff), unit)
        ok = diff > 0 if cond["kind"] == "increase" else diff < 0
        out.append({"period_end": b["period_end"], "ok": ok, "change": change, "pct": pct})
        j -= 1
    return out[::-1]


def gap_of(gaps: dict | None, company: str, metric: str, period_end: str | None) -> tuple[str, str]:
    """값이 없는 이유를 (이유 코드, 사람이 읽는 말)로 돌려준다. 적힌 이유가 없으면 아직 안 읽음."""
    g = (gaps or {}).get(f"{company}|{metric}|{period_end}") or (gaps or {}).get(f"{company}|{metric}")
    if not g:
        return "not_read", GAPS["not_read"]
    return g["reason"], GAPS[g["reason"]] + (f": {g['note']}" if g.get("note") else "")


def judge_value(cond: dict, values: list[dict], company: str, latest: str | None, unit: str,
                gaps: dict | None = None) -> tuple[dict, dict | None]:
    """(최신 분기의 판정, 그 앞 분기의 판정)을 돌려준다."""
    s = loop.series(values, company, cond["metric"])
    if not s:
        code, why = gap_of(gaps, company, cond["metric"], latest)
        return {"result": "unknown", "period_end": latest, "gap": code, "text": f"이 지표의 값이 없음 ({why})", "basis": []}, None
    last = len(s) - 1
    if latest and s[last]["period_end"] < latest:
        v = s[last]
        code, why = gap_of(gaps, company, cond["metric"] + "|" + latest, None)
        cur = {"result": "unknown", "period_end": latest, "gap": code, "basis": [basis_of(v)],
               "text": f"최신 분기({latest})의 값이 없음 ({why}) 마지막 값은 {v['period_end']}의 {loop.fmt(v['value'], unit)}."}
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


def compute(values: list[dict], theses: list[dict], actions: list[dict], metrics: list[dict], gaps: dict | None = None) -> list[dict]:
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
                cur, prev = judge_value(c, values, t["company"], latest, unit, gaps)
            else:
                raise ValueError(f"모르는 조건 종류: {c['kind']}")
            conds.append(dict(c, rule=rule_text(c, unit), unit=unit, verdict=cur, previous=prev))
        counts = {r: sum(1 for c in conds if c["verdict"]["result"] == r) for r in RESULTS}
        status = next(r for r in ("broken", "reversed", "near", "unknown", "held") if counts[r] or r == "held")
        out.append({"id": t["id"], "company": t["company"], "statement": t["statement"], "created": t.get("created"),
                    "example": bool(t.get("example")), "note": t.get("note", ""), "period_end": latest,
                    "status": status, "counts": counts, "conditions": conds,
                    "decisions": decisions_for(t["id"], latest, actions)})
    return out


# ---------- 알림과 잇기 ----------

def inputs_of(metric: str, M: dict) -> set:
    """지표와, 그 지표를 계산하는 데 들어가는 재료 지표를 모두 모은다."""
    out, todo = set(), [metric]
    while todo:
        m = todo.pop()
        if m in out or m is None:
            continue
        out.add(m)
        d = M.get(m, {}).get("derive")
        if d:
            todo += [d["a"], d["b"]]
    return out


def link_alerts(alerts: list[dict], theses: list[dict], metrics: list[dict]) -> None:
    """감시 알림마다, 그 값이 닿는 가설의 조건을 붙인다(같은 회사, 조건의 지표나 그 재료 지표)."""
    M = {m["id"]: m for m in metrics}
    for a in alerts:
        a.setdefault("kind", "monitor")
        a["theses"] = [
            {"thesis": t["id"], "statement": t["statement"], "condition": c["id"], "name": c["name"], "result": c["verdict"]["result"]}
            for t in theses if t["company"] == a["company"]
            for c in t["conditions"] if c.get("metric") and a["metric"] in inputs_of(c["metric"], M)
        ]


def alerts_from(theses: list[dict], dismissals: dict) -> list[dict]:
    """가설의 조건에서 알림을 만든다. 지켜지고 있고 앞 분기와 같으면 알리지 않는다."""
    out = []
    for t in theses:
        for c in t["conditions"]:
            v, p = c["verdict"], c["previous"]
            changed = bool(p) and p["result"] != v["result"]
            if v["result"] == "held" and not changed:
                continue
            aid = f"thesis|{t['id']}|{c['id']}|{v['period_end']}"
            basis = v.get("basis") or []
            head = f"{RESULTS[p['result']]} → {RESULTS[v['result']]}" if changed else RESULTS[v["result"]]
            a = {"id": aid, "kind": "thesis", "company": t["company"], "thesis": t["id"], "statement": t["statement"],
                 "condition": c["id"], "rule_name": c["name"], "metric": c.get("metric"), "unit": c.get("unit"),
                 "period_end": v["period_end"], "result": v["result"], "previous_result": p["result"] if p else None,
                 "changed": changed, "change": f"{head}. {v['text']}", "trail": v.get("trail"), "basis": basis,
                 "filing": basis[0]["filing"] if basis else {"form": None, "filed": None, "url": None},
                 "impact": [], "dismissed": aid in dismissals}
            if aid in dismissals:
                a["dismissed_ts"] = dismissals[aid].get("ts")
                a["dismissed_note"] = dismissals[aid].get("note", "")
            out.append(a)
    return out


# ---------- 판단과 돌아보기 ----------

def snapshot(t: dict) -> dict:
    """판단하거나 돌아볼 때의 가설 모습. 나중에 "그때 무엇을 보고 있었나"를 알 수 있게 기록에 함께 남긴다."""
    return {"period_end": t["period_end"], "status": t["status"],
            "conditions": [{"id": c["id"], "name": c["name"], "result": c["verdict"]["result"], "text": c["verdict"]["text"]}
                           for c in t["conditions"]]}


def decisions_for(thesis_id: str, latest: str | None, actions: list[dict]) -> list[dict]:
    """가설에 대한 판단 기록과 거기에 달린 돌아보기. due는 판단(또는 마지막 돌아보기) 뒤에 새 분기가 들어왔다는 뜻."""
    ds = [dict(a, reflections=[]) for a in actions
          if a.get("type") == "decide" and (a.get("target") or {}).get("thesis") == thesis_id]
    by_id = {d.get("id"): d for d in ds}
    for a in actions:
        if a.get("type") == "reflect":
            d = by_id.get((a.get("target") or {}).get("decision"))
            if d is not None:
                d["reflections"].append(a)
    for d in ds:
        seen = max(((x.get("snapshot") or {}).get("period_end") or "") for x in [d] + d["reflections"])
        d["due"] = bool(latest) and latest > seen
    return ds


def validate(a: dict, theses: list[dict]):
    """judge, decide, reflect 행동에 문제가 있으면 한국어 이유를, 없으면 None을 돌려준다. theses는 compute의 결과."""
    target, kind = a.get("target") or {}, a.get("type")
    if kind == "reflect":
        if not any(d.get("id") == target.get("decision") for t in theses for d in t["decisions"]):
            return "그 판단 기록이 없습니다."
        if not (a.get("note") or "").strip():
            return "돌아보기에는 평가(note)가 필요합니다."
        return None
    t = next((x for x in theses if x["id"] == target.get("thesis")), None)
    if t is None:
        return "그 가설이 없습니다."
    if kind == "decide":
        if not isinstance(a.get("decision"), str) or not a["decision"].strip():
            return "하기로 한 것(decision)을 적어야 합니다."
        if not (a.get("note") or "").strip():
            return "판단에는 이유(note)가 필요합니다."
        return None
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


def enrich(rec: dict, a: dict, theses: list[dict]) -> None:
    """기록할 줄(rec)에 행동 종류별 내용을 채운다. 판단과 돌아보기에는 그때의 가설 모습을 함께 남긴다."""
    target = a.get("target") or {}
    if a["type"] == "judge":
        rec["verdict"] = a["verdict"]
    elif a["type"] == "decide":
        t = next(x for x in theses if x["id"] == target["thesis"])
        rec["id"] = "d-" + "".join(ch for ch in rec["ts"] if ch.isdigit())
        rec["decision"] = a["decision"].strip()
        rec["snapshot"] = snapshot(t)
    elif a["type"] == "reflect":
        t = next(x for x in theses if any(d.get("id") == target["decision"] for d in x["decisions"]))
        rec["snapshot"] = snapshot(t)


def main() -> None:
    from build_dashboard import load  # 순환 import를 피하려고 여기서 부른다

    import build_dashboard

    result = build_dashboard.build()["theses"]
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
