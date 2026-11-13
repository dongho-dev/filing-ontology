"""글에서 뽑은 값의 근거 문장이 원문에 글자 그대로 있는지 검사한다.

쓰는 법: python scripts/check_quotes.py <값 파일.json> [<값 파일.json> ...]
값 파일은 목록이고, 항목마다 source.quote(근거 문장)와 source.text_file(원문 txt 경로, 레포 기준)이 있어야 한다.
하나라도 어긋나면 종료 코드 1.
"""
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def main() -> None:
    bad = total = 0
    cache: dict[str, str] = {}
    for path in sys.argv[1:]:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict):  # 질의 화면 파일: 인용 블록의 항목을 값 파일과 같은 모양으로 바꾼다
            data = [
                {"company": it.get("company"), "metric": data.get("id"), "period_end": it.get("period_end"),
                 "source": {"type": "text", "quote": it.get("text"), "text_file": it.get("source", {}).get("text_file")}}
                for b in data.get("blocks", []) if b.get("type") == "quotes" for it in b["items"]
            ]
        for i, item in enumerate(data):
            src = item.get("source", {})
            if src.get("type") != "text":
                continue
            total += 1
            tf, quote = src.get("text_file"), src.get("quote")
            label = f"{path}[{i}] {item.get('company')} {item.get('metric') or item.get('relation')} {item.get('period_end', '')}"
            if not tf or not quote:
                bad += 1
                print("근거 없음:", label)
                continue
            if tf not in cache:
                fp = ROOT / tf
                cache[tf] = norm(fp.read_text(encoding="utf-8")) if fp.exists() else ""
            if norm(quote) not in cache[tf]:
                bad += 1
                print("원문에 없음:", label, "|", quote[:80])
    print(f"검사 {total}건, 어긋남 {bad}건")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
