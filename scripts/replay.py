"""지난 분기로 되돌아가 고리를 한 바퀴 돌려 본다. 시연용이며 아무것도 저장하지 않는다.

쓰는 법: python scripts/replay.py [회사 id, 기본 nbis]

1. 최신 분기를 가린 상태(직전 분기까지만 들어온 때)에서 가설을 판정하고 알림을 본다.
2. 그 상태에서 판단을 하나 적는다. 판단의 글은 시연용으로 지어낸 것이며 "(시연)"으로 표시한다.
3. 최신 분기 값을 들여온다. 달라진 것, 가설의 판정, 알림, "돌아볼 때" 표시를 본다.
4. 돌아보기를 적는다.

값과 가설은 진짜 것을 쓴다. 판단과 돌아보기는 메모리 안에서만 만들고 ontology/actions.jsonl에는 쓰지 않는다.
"""
import sys

import build_dashboard
import loop
import thesis


def show_theses(d: dict, cid: str) -> None:
    for t in d["theses"]:
        if t["company"] != cid:
            continue
        print(f"   [{thesis.STATUS[t['status']]}] {t['statement']}")
        for c in t["conditions"]:
            v, p = c["verdict"], c["previous"]
            was = f" (그 앞 분기: {thesis.RESULTS[p['result']]})" if p and p["result"] != v["result"] else ""
            print(f"      - {thesis.RESULTS[v['result']]}: {c['name']} · {v['text']}{was}")


def show_alerts(d: dict, cid: str, kind: str) -> None:
    mine = [a for a in d["alerts"] if a["company"] == cid and a["kind"] == kind and not a["dismissed"]]
    if not mine:
        print("   없음")
    for a in mine:
        print(f"   - {a['rule_name']} · {a['change']}")


def record(a: dict, ts: str, d: dict, actions: list[dict]) -> dict:
    """서버가 하는 것과 같은 검증과 채우기를 거쳐 기록 줄을 만든다. 파일에는 쓰지 않는다."""
    err = loop.validate_action(a, d["values"], d["companies"], d["alerts"], d["theses"])
    if err:
        raise SystemExit(f"기록하지 못함: {err}")
    rec = {"ts": ts, "type": a["type"], "target": a["target"], "note": a["note"]}
    thesis.enrich(rec, a, d["theses"])
    actions.append(rec)
    return rec


def main() -> None:
    cid = sys.argv[1] if len(sys.argv) > 1 else "nbis"
    real = loop.load_actions()
    actions = list(real)
    now = build_dashboard.build(actions=actions)
    name = next(c["name"] for c in now["companies"] if c["id"] == cid)
    ends = [v["period_end"] for v in loop.series(now["values"], cid, "revenue")]
    if len(ends) < 3 or not any(t["company"] == cid for t in now["theses"]):
        raise SystemExit("분기 값이 셋 이상이고 가설이 있는 회사만 돌릴 수 있습니다.")
    latest, before = ends[-1], ends[-2]
    hide = lambda v: not (v["company"] == cid and v["period_end"] > before)

    print(f"== {name}: {before} 분기까지만 들어온 때로 되돌아감 ==")
    past = build_dashboard.build(keep=hide, actions=actions)
    print("1. 들어옴 → 3. 가설에 비춤")
    show_theses(past, cid)
    print("4. 알림 (가설에 닿은 것)")
    show_alerts(past, cid, "thesis")

    order = {"broken": 0, "reversed": 1, "near": 1, "unknown": 2, "held": 3}
    pick = min((t for t in past["theses"] if t["company"] == cid), key=lambda t: order[t["status"]])
    moved = [c["name"] for c in pick["conditions"] if c["verdict"]["result"] != "held"]
    filed = max((b["filing"]["filed"] or "" for c in pick["conditions"] for b in c["verdict"].get("basis", [])), default=before)
    dec = record({"type": "decide", "target": {"thesis": pick["id"]},
                  "decision": "(시연) 다음 분기 공시에서 이 가설의 조건을 다시 본다",
                  "note": "(시연) " + (f"지켜지지 않은 조건이 있다: {', '.join(moved)}" if moved else "조건이 모두 지켜지고 있다")},
                 f"{filed}T09:00:00", past, actions)
    print("5. 판단 → 6. 기록")
    print(f"   {dec['ts'][:10]} · \"{pick['statement']}\"에 대해: {dec['decision']}")
    print(f"   이유: {dec['note']}")
    print(f"   함께 남긴 그때 모습: {dec['snapshot']['period_end']} 분기, {thesis.STATUS[dec['snapshot']['status']]}")

    print()
    print(f"== {latest} 분기 공시가 들어옴 ==")
    now = build_dashboard.build(actions=actions)
    print("1. 들어옴 → 2. 탐지 (감시 조건에 걸린 것)")
    show_alerts(now, cid, "monitor")
    print("3. 가설에 비춤")
    show_theses(now, cid)
    print("4. 알림 (가설에 닿은 것)")
    show_alerts(now, cid, "thesis")
    t = next(x for x in now["theses"] if x["id"] == pick["id"])
    d = next(x for x in t["decisions"] if x["id"] == dec["id"])
    print(f"7. 돌아보기: 앞의 판단이 \"돌아볼 때\"로 표시됨 = {d['due']}")
    print(f"   그때: {thesis.STATUS[dec['snapshot']['status']]} → 지금: {thesis.STATUS[t['status']]}")

    changed = [f"{c['name']}: {thesis.RESULTS[s['result']]} → {thesis.RESULTS[c['verdict']['result']]}"
               for c in t["conditions"] for s in dec["snapshot"]["conditions"]
               if s["id"] == c["id"] and s["result"] != c["verdict"]["result"]]
    ref = record({"type": "reflect", "target": {"decision": dec["id"]},
                  "note": "(시연) " + ("판정이 달라진 조건: " + "; ".join(changed) if changed else "조건의 판정은 그대로다")},
                 f"{latest}T09:00:00", now, actions)
    after = build_dashboard.build(actions=actions)
    d = next(x for t in after["theses"] for x in t["decisions"] if x["id"] == dec["id"])
    print(f"   돌아보기 기록: {ref['note']}")
    print(f"   기록한 뒤 \"돌아볼 때\" 표시 = {d['due']}, 이 판단에 달린 돌아보기 {len(d['reflections'])}건")

    print()
    same = loop.load_actions() == real
    print(f"시연에서 만든 기록 {len(actions) - len(real)}줄은 저장하지 않았습니다. ontology/actions.jsonl은 그대로입니다: {same}")


if __name__ == "__main__":
    main()
