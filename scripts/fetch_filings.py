"""공시 목록(data/raw/sec/<ticker>_submissions.json)에서 기간 안의 공시를 골라 본문과 첨부(.htm)를 내려받는다.

쓰는 법: python scripts/fetch_filings.py <ticker> <시작일> <끝일> [양식,양식...]
   예: python scripts/fetch_filings.py nbis 2024-07-01 2025-10-31 6-K,20-F
결과: data/raw/filings/<ticker>/<접수번호>/ 아래 index.json, <문서>.htm, <문서>.htm.txt
이미 있는 문서는 다시 받지 않는다. SEC에 부담을 주지 않도록 요청 사이에 잠깐 쉰다.
"""
import json
import pathlib
import re
import sys
import time

import sec_http
from filing_text import to_text

ROOT = pathlib.Path(__file__).resolve().parent.parent


def main() -> None:
    ticker, start, end = sys.argv[1:4]
    forms = set((sys.argv[4] if len(sys.argv) > 4 else "6-K,20-F,10-Q,10-K").split(","))
    sub = json.loads((ROOT / f"data/raw/sec/{ticker}_submissions.json").read_text(encoding="utf-8"))
    cik = str(int(sub["cik"]))
    r = sub["filings"]["recent"]
    got = 0
    for i, acc in enumerate(r["accessionNumber"]):
        if r["form"][i] not in forms or not (start <= r["filingDate"][i] <= end):
            continue
        folder = ROOT / "data/raw/filings" / ticker / acc
        folder.mkdir(parents=True, exist_ok=True)
        base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/"
        idx = folder / "index.json"
        if not idx.is_file():
            idx.write_bytes(sec_http.get(base + "index.json"))
            time.sleep(0.25)
        names = [it["name"] for it in json.loads(idx.read_text(encoding="utf-8"))["directory"]["item"]]
        for name in names:
            if not name.endswith(".htm") or name.endswith("-index.htm") or re.fullmatch(r"R\d+\.htm", name) or (folder / name).is_file():
                continue
            body = sec_http.get(base + name)
            (folder / name).write_bytes(body)
            (folder / (name + ".txt")).write_text(to_text(body.decode("utf-8", errors="replace")), encoding="utf-8")
            got += 1
            time.sleep(0.25)
        print(r["filingDate"][i], r["form"][i], acc, [n for n in names if n.endswith(".htm") and not n.endswith("-index.htm")])
    print(f"새로 받은 문서 {got}개")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
