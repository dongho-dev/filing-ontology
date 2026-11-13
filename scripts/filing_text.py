"""SEC 공시 문서 하나를 내려받아 원본(html)과 글만 뽑은 판(txt)을 저장한다.

쓰는 법: python scripts/filing_text.py <문서 URL> <저장할 폴더>
결과: <폴더>/<파일이름>.html, <폴더>/<파일이름>.txt 경로를 출력한다.

txt는 한 줄에 한 문단(또는 표의 한 행)이고, 줄 안의 공백은 한 칸으로 줄인다.
근거 문장은 이 txt에서 글자 그대로 따온다(scripts/check_quotes.py가 검사한다).
"""
import html
import pathlib
import re
import sys

import sec_http

BLOCK = r"</?(p|div|tr|br|li|h[1-6]|table|title)\b[^>]*>"


def to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<ix:header>.*?</ix:header>", " ", raw)
    raw = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)" + BLOCK, "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html.unescape(raw).replace("\xa0", " ")
    lines = (re.sub(r"\s+", " ", ln).strip() for ln in raw.split("\n"))
    return "\n".join(ln for ln in lines if ln)


def main() -> None:
    url, out_dir = sys.argv[1], pathlib.Path(sys.argv[2])
    out_dir.mkdir(parents=True, exist_ok=True)
    name = url.rstrip("/").split("/")[-1] or "index"
    body = sec_http.get(url)
    (out_dir / name).write_bytes(body)
    txt = out_dir / (name + ".txt")
    txt.write_text(to_text(body.decode("utf-8", errors="replace")), encoding="utf-8")
    print(out_dir / name)
    print(txt)


if __name__ == "__main__":
    main()
