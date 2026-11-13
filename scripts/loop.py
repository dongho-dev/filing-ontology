"""감시 조건(monitors.json)에 걸린 변화를 찾아 알림(alerts.json)을 만든다.

쓰는 법: python scripts/loop.py
입력: ontology/monitors.json, actions.jsonl(행동 기록), relations.json, companies.json, metrics.json, values_*.json
출력: ontology/alerts.json, 화면에 알림 개수와 목록

회사마다 지표의 최신 기간 값과 바로 앞 기간 값을 비교한다.
값은 행동 기록(정정)을 반영한 뒤의 값이다. 영향 범위는 relations.json과 "잇기" 기록만 따라가고, 연결이 없으면 모름으로 둔다.
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
ONT = ROOT / "ontology"
ACTIONS = ONT / "actions.jsonl"
ACTION_TYPES = ("review", "correct", "link", "dismiss", "judge", "decide", "reflect")


def vkey(company: str, metric: str, period_end: str) -> str:
    return f"{company}|{metric}|{period_end}"


# ---------- 행동 기록 ----------

def load_actions(path: pathlib.Path = ACTIONS) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def apply_actions(values: list[dict], actions: list[dict]) -> None:
    """검토·정정 기록을 값에 반영한다. text 값에만 review.status를 붙이고 나머지는 n/a."""
    by_key = {vkey(v["company"], v["metric"], v["period_end"]): v for v in values}
    for v in values:
        v["review"] = {"status": "unreviewed"} if v["source"].get("type") == "text" else {"status": "n/a"}
    for a in actions:
        if a.get("type") not in ("review", "correct"):
            continue
        t = a.get("target") or {}
        v = by_key.get(vkey(t.get("company"), t.get("metric"), t.get("period_end")))
        if v is None or v["source"].get("type") != "text":
            continue
        r = v["review"]
        if a["type"] == "review":
            if r["status"] == "unreviewed":
                v["review"] = {"status": "reviewed", "ts": a.get("ts"), "note": a.get("note", "")}
            else:
                r["last_review_ts"] = a.get("ts")
        else:
            original = r.get("original_value", v["value"])
            v["value"] = a["value"]
            v["review"] = {"status": "corrected", "ts": a.get("ts"), "note": a.get("note", ""),
                           "original_value": original}


def links_from(actions: list[dict]) -> dict:
    """(회사, 원문 표현) → 마지막 잇기 기록."""
    out = {}
    for a in actions:
        if a.get("type") == "link":
            t = a.get("target") or {}
            out[(t.get("company"), t.get("label"))] = a
    return out


def dismissals_from(actions: list[dict]) -> dict:
    out = {}
    for a in actions:
        if a.get("type") == "dismiss":
            out[(a.get("target") or {}).get("alert_id")] = a
    return out


# ---------- 글로 옮기기 ----------

def fmt(v, unit: str) -> str:
    if unit == "USD":
        x = v / 1e8
        a = abs(x)
        digits = 0 if a >= 100 else 1 if a >= 10 else 2
        return f"{x:,.{digits}f}억 달러"
    if unit == "%":
        return f"{v:g}%"
    if unit == "MW":
        return f"{round(v):,} MW"
    if unit == "x":
        return f"{v:,.2f}배"
    return str(v)


def josa(word: str, pair: tuple[str, str]) -> str:
    """받침이 있으면 pair[0], 없으면 pair[1]. 한글이 아니면 pair[1]."""
    ch = word[-1] if word else ""
    if "가" <= ch <= "힣":
        return pair[0] if (ord(ch) - 0xAC00) % 28 else pair[1]
    return pair[1]


# ---------- 감시 ----------

def series(values: list[dict], company: str, metric: str) -> list[dict]:
    return sorted((v for v in values if v["company"] == company and v["metric"] == metric), key=lambda v: v["period_end"])


def latest_filing(v: dict) -> dict:
    fs = [f for f in v["source"].get("filings") or [] if f]
    if not fs:
        return {"form": None, "filed": None, "url": None}
    f = max(fs, key=lambda f: f.get("filed") or "")
    return {"form": f.get("form"), "filed": f.get("filed"), "url": f.get("url")}


def check(rule: dict, cur: dict, prev: dict, mname: str, unit: str):
    """조건에 걸리면 사람이 읽는 문장을, 아니면 None을 돌려준다."""
    kind = rule["kind"]
    th = rule.get("threshold", 0)
    d = rule.get("direction", "any")
    span = f"({prev['period_end']} → {cur['period_end']})"
    if kind == "label_changed":
        a, b = prev["source"].get("label"), cur["source"].get("label")
        if a and b and a != b:
            return f"{mname} 원문 표현이 '{a}'에서 '{b}'{josa(b, ('으로', '로'))} 바뀜 {span}. 같은 회사인지는 원문만으로 알 수 없음."
        return None
    if kind == "pp_change":
        diff = cur["value"] - prev["value"]
        hit = (d == "up" and diff > th) or (d == "down" and -diff > th) or (d == "any" and abs(diff) > th)
        if not hit:
            return None
        way = "늘어남" if diff > 0 else "줄어듦"
        return f"{mname}{josa(mname, ('이', '가'))} {fmt(prev['value'], unit)}에서 {fmt(cur['value'], unit)}{josa(fmt(cur['value'], unit), ('으로', '로'))} {abs(diff):.1f}%p {way} {span}."
    if kind == "pct_change":
        if not prev["value"] or prev["value"] <= 0:
            return None
        p = (cur["value"] / prev["value"] - 1) * 100
        hit = (d == "up" and p > th) or (d == "down" and -p > th) or (d == "any" and abs(p) > th)
        if not hit:
            return None
        way = "늘어남" if p > 0 else "줄어듦"
        return f"{mname}{josa(mname, ('이', '가'))} {fmt(prev['value'], unit)}에서 {fmt(cur['value'], unit)}{josa(fmt(cur['value'], unit), ('으로', '로'))} {abs(p):.1f}% {way} {span}."
    raise ValueError(f"모르는 감시 종류: {kind}")


def impact(company: str, rule: dict, cur: dict, relations: list[dict], companies: list[dict], links: dict) -> list[dict]:
    order = {c["id"]: i for i, c in enumerate(companies)}
    out = []
    if rule["metric"] == "top_customer_share":
        label = cur["source"].get("label")
        ln = links.get((company, label))
        if ln and ln.get("counterparty"):
            out.append({"kind": "customer", "company": ln["counterparty"], "label": label, "note": ln.get("note", ""), "ts": ln.get("ts")})
        elif ln:
            out.append({"kind": "unknown", "label": label, "linked": True, "note": ln.get("note", ""), "ts": ln.get("ts"),
                        "text": f"확인했지만 모름 (원문 표현: {label})"})
        else:
            out.append({"kind": "unknown", "label": label, "linked": False,
                        "text": f"이름이 가려져 있어 누구인지 모름 (원문 표현: {label})"})
    sups = [r for r in relations if r.get("company") == company and r.get("relation") == "supplied_by"]
    sups.sort(key=lambda r: order.get(r["counterparty"], 99))
    for r in sups:
        out.append({"kind": "supplier", "company": r["counterparty"], "what": r.get("what")})
    seen = set()
    for r in sups:
        peers = [x for x in relations if x.get("relation") == "supplied_by" and x.get("counterparty") == r["counterparty"] and x.get("company") != company]
        peers.sort(key=lambda x: order.get(x["company"], 99))
        for x in peers:
            if x["company"] in seen:
                continue
            seen.add(x["company"])
            out.append({"kind": "same_supplier", "company": x["company"], "via": r["counterparty"]})
    return out


def compute_alerts(values: list[dict], monitors: list[dict], relations: list[dict], companies: list[dict],
                   metrics: list[dict], actions: list[dict]) -> list[dict]:
    """values는 apply_actions를 거친 값이어야 한다."""
    M = {m["id"]: m for m in metrics}
    links, dis = links_from(actions), dismissals_from(actions)
    alerts = []
    for c in companies:
        for rule in monitors:
            s = series(values, c["id"], rule["metric"])
            if len(s) < 2:
                continue
            prev, cur = s[-2], s[-1]
            m = M.get(rule["metric"], {"name": rule["metric"], "unit": cur.get("unit")})
            text = check(rule, cur, prev, m["name"], m.get("unit") or cur.get("unit"))
            if text is None:
                continue
            aid = f"{c['id']}|{rule['id']}|{cur['period_end']}"
            a = {
                "id": aid, "company": c["id"], "rule_id": rule["id"], "rule_name": rule["name"], "metric": rule["metric"],
                "period_end": cur["period_end"],
                "current": {"value": cur["value"], "label": cur["source"].get("label"), "source_type": cur["source"].get("type"),
                            "review": cur.get("review", {}).get("status", "n/a")},
                "previous": {"value": prev["value"], "label": prev["source"].get("label"), "period_end": prev["period_end"]},
                "change": text,
                "filing": latest_filing(cur),
                "impact": impact(c["id"], rule, cur, relations, companies, links),
                "dismissed": aid in dis,
            }
            if aid in dis:
                a["dismissed_ts"] = dis[aid].get("ts")
                a["dismissed_note"] = dis[aid].get("note", "")
            alerts.append(a)
    return alerts


# ---------- 행동 검증 (serve.py가 쓴다) ----------

def validate_action(a, values: list[dict], companies: list[dict], alerts: list[dict], theses: list[dict] | None = None):
    """문제가 있으면 한국어 이유를, 없으면 None을 돌려준다."""
    if not isinstance(a, dict):
        return "요청 본문은 JSON 객체여야 합니다."
    t = a.get("type")
    if t not in ACTION_TYPES:
        return f"type은 {', '.join(ACTION_TYPES)} 중 하나여야 합니다."
    target = a.get("target")
    if not isinstance(target, dict):
        return "target이 없습니다."
    note = a.get("note", "")
    if not isinstance(note, str):
        return "note는 글이어야 합니다."
    if t in ("judge", "decide", "reflect"):
        import thesis  # 순환 import를 피하려고 여기서 부른다

        return thesis.validate(a, theses or [])
    cids = {c["id"] for c in companies}
    if t in ("review", "correct"):
        k = vkey(target.get("company"), target.get("metric"), target.get("period_end"))
        v = next((x for x in values if vkey(x["company"], x["metric"], x["period_end"]) == k), None)
        if v is None:
            return "그 회사·지표·기간의 값이 없습니다."
        if v["source"].get("type") != "text":
            return "글에서 뽑은 값만 검토·정정할 수 있습니다. 이 값은 구조화 자료나 계산 값입니다."
        if t == "correct":
            val = a.get("value")
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                return "정정에는 새 값(value, 숫자)이 필요합니다."
            if not note.strip():
                return "정정에는 이유(note)가 필요합니다."
    elif t == "link":
        if target.get("company") not in cids:
            return "target.company가 회사 목록에 없습니다."
        if not isinstance(target.get("label"), str) or not target["label"].strip():
            return "target.label(원문 표현)이 필요합니다."
        if "counterparty" not in a:
            return "counterparty가 필요합니다. 모르면 null로 보내세요."
        cp = a["counterparty"]
        if cp is not None and cp not in cids:
            return "counterparty가 회사 목록에 없습니다."
        if cp == target.get("company"):
            return "자기 자신을 고객으로 이을 수 없습니다."
        if not note.strip():
            return "잇기에는 이유(note)가 필요합니다."
    elif t == "dismiss":
        if target.get("alert_id") not in {x["id"] for x in alerts}:
            return "그 알림이 없습니다."
    return None


# ---------- 실행 ----------

def build(write: bool = True) -> list[dict]:
    import build_dashboard

    alerts = build_dashboard.build()["alerts"]  # 감시 알림과 가설 알림을 함께
    if write:
        (ONT / "alerts.json").write_text(json.dumps(alerts, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return alerts


def load(name: str, default=None):
    p = ONT / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def main() -> None:
    alerts = build()
    names = {c["id"]: c["name"] for c in load("companies.json")}
    opened = [a for a in alerts if not a["dismissed"]]
    print(f"알림 {len(alerts)}건 (열림 {len(opened)}, 닫힘 {len(alerts) - len(opened)}) → ontology/alerts.json")
    for a in alerts:
        mark = "닫힘" if a["dismissed"] else "열림"
        kind = "가설" if a.get("kind") == "thesis" else "감시"
        print(f"- [{mark}] [{kind}] {names.get(a['company'], a['company'])} · {a['rule_name']} · {a['change']}")


if __name__ == "__main__":
    main()
