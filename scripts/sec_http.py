"""SEC 요청에 쓰는 공통 함수.

SEC는 공시 본문(www.sec.gov)을 연락처를 밝힌 프로그램에만 준다.
User-Agent는 레포 맨 위 .env 파일의 SEC_USER_AGENT에서 읽는다(.env는 커밋하지 않는다).
예: SEC_USER_AGENT=research-script/0.1 you@example.com

쓰는 법(단독 실행): python scripts/sec_http.py <URL> <저장할 파일>
"""
import os
import pathlib
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent


def user_agent() -> str:
    value = os.environ.get("SEC_USER_AGENT")
    env = ROOT / ".env"
    if not value and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("SEC_USER_AGENT="):
                value = line.split("=", 1)[1].strip()
    return value or "research-script/0.1"


def get(url: str, timeout: int = 120) -> bytes:
    """문서 하나를 받는다. 실패하면 urllib.error.HTTPError가 난다. 요청 뒤 0.3초 쉰다."""
    req = urllib.request.Request(url, headers={"User-Agent": user_agent()})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
    time.sleep(0.3)
    return body


if __name__ == "__main__":
    out = pathlib.Path(sys.argv[2])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(get(sys.argv[1]))
    print(out, out.stat().st_size, "bytes")
