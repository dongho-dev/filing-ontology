"""대시보드를 열고 행동(검토·정정·잇기·닫기·조건 판정·판단·돌아보기)을 기록하는 작은 서버.

쓰는 법: python scripts/serve.py [포트]   (기본 8765, 127.0.0.1에만 연다)
- GET  /            dashboard/ 정적 파일
- GET  /api/ping    {"ok": true}
- POST /api/action  행동 하나(JSON)를 검증해 ontology/actions.jsonl에 덧붙이고 dashboard/data.js를 다시 만든다.
"""
import datetime as dt
import functools
import http.server
import json
import sys
import threading

import build_dashboard
import loop
import thesis

LOCK = threading.Lock()


class Handler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def reply(self, code: int, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == "/api/ping":
            return self.reply(200, {"ok": True})
        return super().do_GET()

    def do_POST(self):
        if self.path.split("?")[0] != "/api/action":
            return self.reply(404, {"ok": False, "error": "없는 주소입니다."})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if n <= 0 or n > 100_000:
                return self.reply(400, {"ok": False, "error": "요청 본문이 비었거나 너무 큽니다."})
            a = json.loads(self.rfile.read(n).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return self.reply(400, {"ok": False, "error": "JSON을 읽지 못했습니다."})
        with LOCK:
            data = build_dashboard.build()
            err = loop.validate_action(a, data["values"], data["companies"], data["alerts"], data["theses"])
            if err:
                return self.reply(400, {"ok": False, "error": err})
            rec = {"ts": dt.datetime.now().isoformat(timespec="seconds"), "type": a["type"], "target": a["target"],
                   "note": a.get("note", "").strip()}
            if a["type"] == "correct":
                rec["value"] = a["value"]
            if a["type"] == "link":
                rec["counterparty"] = a["counterparty"]
            thesis.enrich(rec, a, data["theses"])
            with loop.ACTIONS.open("a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            build_dashboard.main()
        return self.reply(200, {"ok": True, "action": rec})


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    handler = functools.partial(Handler, directory=str(build_dashboard.ROOT / "dashboard"))
    build_dashboard.main()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"http://127.0.0.1:{port}/ 에서 대시보드를 엽니다. 끄려면 Ctrl+C.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
