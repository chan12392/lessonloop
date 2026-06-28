#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_index.py — cards/*.md 스캔 → frontmatter 파싱 → id 채움(PENDING) → lessons_index.csv 빌드.
인덱스는 파생물(재빌드 가능). hot path = 이 csv.
"""
import csv, hashlib, io, re, sys
from pathlib import Path

# 경로 진실원천
SCRIPTS = Path(__file__).resolve().parent
try:
    from paths import cards_dir, cards_dir_cards, index_file
except ImportError:
    # 하위 호환: 단돈 실행 시 fallback
    ROOT = SCRIPTS.parent
    cards_dir = lambda: ROOT
    cards_dir_cards = lambda: ROOT / "cards"
    index_file = lambda: ROOT / "lessons_index.csv"

CARDS = cards_dir_cards()
INDEX_CSV = index_file()
INDEX_MD = cards_dir() / "lessons_index.md"

FIELDS = ["id", "l1", "l2", "trigger", "rule", "enforce", "severity", "sources"]


def parse_front(text):
    m = re.match(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        return None, None
    fm = {}
    for line in m.group(1).splitlines():
        mm = re.match(r"^([a-z0-9_]+):\s*(.*)$", line)
        if not mm:
            continue
        k, v = mm.group(1), mm.group(2).strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        fm[k] = v
    return fm, m


def main():
    rows = []
    for p in sorted(CARDS.glob("*.md")):
        text = io.open(p, encoding="utf-8").read()
        fm, m = parse_front(text)
        if fm is None:
            print("SKIP (no frontmatter):", p.name); continue
        rule = fm.get("rule", "")
        # id 채우기: PENDING 이거나 비면 rule 해시
        if fm.get("id", "PENDING") in ("PENDING", ""):
            new_id = hashlib.sha1(rule.encode("utf-8")).hexdigest()[:12]
            # [ \t]* 만 — \s* 는 \n 까지 먹어 다음 줄(l1 등)을 삼킴(과거 버그).
            text2 = re.sub(r"^id:[ \t]*.*$", f"id: {new_id}", text, count=1, flags=re.M)
            io.open(p, "w", encoding="utf-8", newline="\n").write(text2)
            fm["id"] = new_id
            print(f"  id filled: {p.name} -> {new_id}")
        rows.append({**{k: fm.get(k, "") for k in FIELDS}, "slug": p.stem})

    # CSV (hot path)
    with io.open(INDEX_CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "l1", "l2", "trigger", "rule", "slug"])
        for r in rows:
            w.writerow([r["id"], r["l1"], r["l2"], r["trigger"], r["rule"], r["slug"]])

    # MD 미러 (사람 읽기용)
    with io.open(INDEX_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("# LessonLoop Index\n\n| l1 | l2 | trigger | rule | slug |\n|---|---|---|---|---|\n")
        for r in sorted(rows, key=lambda x: (x["l1"], x["l2"])):
            trig = r["trigger"].replace("|", "\\|")
            rule = r["rule"].replace("|", "\\|")
            f.write(f"| {r['l1']} | {r['l2']} | `{trig}` | {rule} | {r['slug']} |\n")

    print(f"\nindexed {len(rows)} cards -> {INDEX_CSV.name} + {INDEX_MD.name}")
    by_l1 = {}
    for r in rows:
        by_l1[r["l1"]] = by_l1.get(r["l1"], 0) + 1
    print("by L1:", by_l1)


if __name__ == "__main__":
    main()
