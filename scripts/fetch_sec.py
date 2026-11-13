"""SEC EDGAR에서 회사별 공시 목록과 구조화 재무 데이터(XBRL)를 내려받는다.

쓰는 법: python scripts/fetch_sec.py
결과: data/raw/sec/<ticker>_submissions.json, data/raw/sec/<ticker>_facts.json
"""
import json
import pathlib
import urllib.error

import sec_http

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw" / "sec"


def get(url: str) -> bytes | None:
    try:
        return sec_http.get(url, timeout=60)
    except urllib.error.HTTPError as e:
        print(f"  실패 {e.code}: {url}")
        return None


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    companies = json.loads((ROOT / "ontology" / "companies.json").read_text(encoding="utf-8"))
    for c in companies:
        cik = f"{int(c['cik']):010d}"
        for kind, url in (
            ("submissions", f"https://data.sec.gov/submissions/CIK{cik}.json"),
            ("facts", f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"),
        ):
            body = get(url)
            if body is not None:
                (OUT / f"{c['id']}_{kind}.json").write_bytes(body)
                print(f"{c['id']} {kind}: {len(body):,} bytes")


if __name__ == "__main__":
    main()
