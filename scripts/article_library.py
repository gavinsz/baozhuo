#!/usr/bin/env python3
"""Index and retrieve the retained Baozhuo collection with Python's standard library."""

import argparse
import collections
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[1]
DOCX = ROOT / "references/sources/books/抱拙居士-公众号文章汇编.docx"
TXT = ROOT / "references/sources/books/baozhuo_articles_2025-08_to_2026-05.txt"
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
TOPICS = {
    "框架": r"体系|框架|总纲|GARP|四法则",
    "产业": r"资本支出|capex|产业|供需|渗透率|生命周期",
    "盈利": r"每股收益|eps|业绩|盈利|现金流|ROIC",
    "估值": r"估值|定价|安全边际|peg|dcf|ddm",
    "催化": r"催化|预期差|超预期",
    "组合": r"仓位|分散|集中|再平衡|剁肉|回撤",
    "退出": r"止盈|止损|卖出|清仓|纠错",
    "认知": r"贝叶斯|证伪|认知|哲学|复盘|概率",
}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_articles():
    with zipfile.ZipFile(DOCX) as archive:
        root = ET.fromstring(archive.read("word/document.xml"))
    paragraphs = [
        "".join(t.text or "" for t in p.findall(".//w:t", NS))
        for p in root.findall(".//w:p", NS)
    ]
    starts = []
    for i, paragraph in enumerate(paragraphs[:-1]):
        header = re.fullmatch(r"(\d+)\. (.+)", paragraph)
        meta = re.fullmatch(
            r"发布日期：(\d{4}-\d{2}-\d{2}) (.*?)    链接：(https?://\S+)",
            paragraphs[i + 1],
        )
        if header and meta:
            starts.append((i, header, meta))
    articles = []
    for k, (i, header, meta) in enumerate(starts):
        end = starts[k + 1][0] if k + 1 < len(starts) else len(paragraphs)
        body_start = i + 2
        if re.fullmatch(r"─+", paragraphs[body_start]):
            body_start += 1
        body = "\n".join(paragraphs[body_start:end]).strip()
        articles.append({
            "id": f"D{int(header[1]):04d}",
            "title": header[2], "date": meta[1], "time": meta[2], "url": meta[3],
            "paragraph_start": i + 1, "paragraph_end": end,
            "body_chars": len(body),
            "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
            "ai_footer": "（内容由AI生成，仅供参考）" in body,
            "topics": [name for name, pattern in TOPICS.items()
                       if re.search(pattern, body, re.I)],
            "body": body,
        })
    if not articles or len({a["id"] for a in articles}) != len(articles):
        raise ValueError("No articles found or duplicate sequence IDs")
    return articles, len(paragraphs)


def audit(articles, paragraph_count):
    text = TXT.read_text()
    txt_rows = re.findall(
        r"(?m)^序号: (\d+)\n标题: (.*?)\n发布日期: (.*?)\n", text
    )
    lookup = collections.defaultdict(list)
    for a in articles:
        lookup[(a["date"], a["title"])].append(a["id"])
    bodies = collections.defaultdict(list)
    for a in articles:
        bodies[a["body_sha256"]].append(a["id"])
    matches = [{"txt_id": int(n), "ids": lookup.get((d, t), [])}
               for n, t, d in txt_rows]
    return {
        "docx_sha256": sha256(DOCX), "txt_sha256": sha256(TXT),
        "docx_articles": len(articles), "docx_paragraphs": paragraph_count,
        "docx_dates": [min(a["date"] for a in articles), max(a["date"] for a in articles)],
        "docx_unique_urls": len({a["url"] for a in articles}),
        "months": dict(sorted(collections.Counter(a["date"][:7] for a in articles).items())),
        "exact_body_duplicate_groups": [v for v in bodies.values() if len(v) > 1],
        "ai_footer_ids": [a["id"] for a in articles if a["ai_footer"]],
        "txt_articles": len(txt_rows),
        "txt_dates": [min(d for _, _, d in txt_rows), max(d for _, _, d in txt_rows)],
        "txt_matched_by_title_date": sum(bool(m["ids"]) for m in matches),
        "txt_unmatched": [m for m in matches if not m["ids"]],
        "txt_ambiguous": [m for m in matches if len(m["ids"]) > 1],
    }


def write_index(articles, report):
    directory = ROOT / "references/research"
    directory.mkdir(parents=True, exist_ok=True)
    records = [{k: v for k, v in a.items() if k != "body"} for a in articles]
    (directory / "article-index.json").write_text(
        json.dumps({"audit": report, "articles": records}, ensure_ascii=False, indent=2) + "\n"
    )
    lines = [
        "# 抱拙文章索引", "",
        "主源为本地 Word 汇编。编号 D0001 等对应汇编序号；段落号为 document.xml 中从 1 开始的段落位置。",
        "链接是文集记录的原文地址，本次未逐篇联网认证。主题标签由关键词自动生成，仅供检索，不代表人工精读或观点认证。", "",
        "正文检索：`python3 scripts/article_library.py search '资本支出|ROIC' --limit 8`。",
        "原文提取：`python3 scripts/article_library.py show D0601 D1190`。", "",
        "| 编号 | 日期 | 标题及文集记录链接 | 段落范围 | 自动主题 |", "| --- | --- | --- | --- | --- |",
    ]
    for a in articles:
        title = a["title"].replace("|", "\\|")
        lines.append(f'| {a["id"]} | {a["date"]} | [{title}]({a["url"]}) | '
                     f'{a["paragraph_start"]}–{a["paragraph_end"]} | {"、".join(a["topics"])} |')
    (directory / "08-article-index.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("audit")
    sub.add_parser("index")
    show = sub.add_parser("show")
    show.add_argument("ids", nargs="+")
    search = sub.add_parser("search")
    search.add_argument("pattern")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--context", type=int, default=120)
    args = parser.parse_args()
    articles, paragraph_count = load_articles()
    if args.command in {"audit", "index"}:
        report = audit(articles, paragraph_count)
        if args.command == "index":
            write_index(articles, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.command == "show":
        by_id = {a["id"]: a for a in articles}
        missing = set(args.ids) - by_id.keys()
        if missing:
            parser.error(f"Unknown IDs: {', '.join(sorted(missing))}")
        for article_id in args.ids:
            a = by_id[article_id]
            print(f'\n## {a["id"]} {a["date"]} {a["title"]}\n{a["url"]}')
            print(f'文集段落 {a["paragraph_start"]}–{a["paragraph_end"]}\n{a["body"]}')
    else:
        if args.limit < 1 or args.context < 0:
            parser.error("limit must be positive and context nonnegative")
        pattern = re.compile(args.pattern, re.I)
        hits = [(a, list(pattern.finditer(a["body"]))) for a in articles]
        hits = [(a, matches) for a, matches in hits if matches]
        print(f"匹配文章 {len(hits)} 篇；显示前 {args.limit} 篇，每篇最多两处。")
        for a, matches in hits[:args.limit]:
            print(f'\n{a["id"]} {a["date"]} {a["title"]} {a["url"]}')
            for match in matches[:2]:
                print(a["body"][max(0, match.start() - args.context):match.end() + args.context])


if __name__ == "__main__":
    main()
