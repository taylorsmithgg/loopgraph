#!/usr/bin/env python3
"""Render `docs-src/*.md` into the static site under `docs/`.

GitHub Pages is configured to serve `docs/` from the default branch, and a
`.nojekyll` marker there turns GitHub's own build off: what is committed is
exactly what is served, and it can be opened locally before it is pushed. A
site that only exists after a remote build is a site nobody has looked at.

    uv run --with markdown --with pygments tools/build_docs.py

Sources are ordinary markdown, readable in the repository as they are. Only
the navigation lives here, in NAV, because ordering is a property of the site
rather than of any one document.
"""

from __future__ import annotations

import html
import re
import shutil
import sys
from pathlib import Path

import markdown

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs-src"
OUT = ROOT / "docs"

SITE = "loopgraph"
TAGLINE = "Deterministic goal-state substrate for agent loops"
REPO = "https://github.com/taylorsmithgg/loopgraph"

# (section title, [(source stem, nav label)]).
NAV: list[tuple[str, list[tuple[str, str]]]] = [
    ("", [
        ("index", "Overview"),
    ]),
    ("Guide", [
        ("cli", "CLI &amp; exit codes"),
        ("gates", "Gates"),
        ("memory", "Memory"),
        ("audit", "Audit &amp; routing"),
    ]),
    ("Background", [
        ("design", "Design"),
        ("coordination", "Coordination design"),
        ("implementation-plan", "Implementation plan"),
        ("followups", "Open questions"),
    ]),
    ("Evidence", [
        ("evidence/index", "What was measured"),
        ("evidence/corpus-baseline", "Corpus baseline"),
        ("evidence/mechanism-benchmarks", "Mechanism benchmarks"),
        ("evidence/subagent-failure-taxonomy", "Subagent failure taxonomy"),
        ("evidence/prior-art-review", "Prior art review"),
    ]),
]

from design import CSS, FAVICON, PAGE, THEME_SCRIPT


def front_matter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    meta = {}
    for line in text[4:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[end + 5:]


def nav_html(current: str, root: str) -> str:
    out = []
    for section, items in NAV:
        if section:
            out.append(f'<div class="eyebrow">{section}</div>')
        out.append("<ul>")
        for stem, label in items:
            cls = ' class="current"' if stem == current else ""
            out.append(f'<li><a href="{root}{stem}.html"{cls}>{label}</a></li>')
        out.append("</ul>")
    return "\n".join(out)


def fix_links(body: str, root: str) -> str:
    """`[x](design.md)` in a source file must resolve in the built site."""
    def repl(m):
        href = m.group(1)
        if href.startswith(("http://", "https://", "#", "mailto:")):
            return m.group(0)
        return f'href="{href[:-3]}.html"' if href.endswith(".md") else m.group(0)
    body = re.sub(r'href="([^"]+)"', repl, body)
    return body.replace('href="assets/', f'href="{root}assets/')


def wrap_tables(body: str) -> str:
    # A markdown table with no header still emits a <thead> of empty cells,
    # which renders as a stray grey bar above the first row.
    body = re.sub(r"<thead>\s*<tr>(?:\s*<th[^>]*>\s*</th>)+\s*</tr>\s*</thead>",
                  "", body)
    return body.replace("<table>", '<div class="tablewrap"><table>') \
               .replace("</table>", "</table></div>")


def toc_html(md_toc: str, body: str) -> tuple[str, str]:
    """An 'on this page' rail, but only where a reader could get lost.

    Under six top-level sections the rail is noise next to the nav it sits
    across from, so short pages get the two-column shell instead.
    """
    if body.count("<h2") < 6:
        return "", ""
    inner = re.sub(r"^\s*<div class=\"toc\">|</div>\s*$", "", md_toc.strip())
    return (' has-toc',
            '<aside class="toc" aria-label="On this page">'
            '<div class="eyebrow">On this page</div>' + inner + "</aside>")


def build() -> int:
    if not SRC.is_dir():
        print(f"no sources at {SRC}", file=sys.stderr)
        return 1
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "assets").mkdir(parents=True)
    (OUT / ".nojekyll").write_text("")
    (OUT / "assets" / "style.css").write_text(CSS.lstrip())
    (OUT / "assets" / "favicon.svg").write_text(FAVICON)

    known = {stem for _, items in NAV for stem, _ in items}
    found = {str(p.relative_to(SRC).with_suffix("")) for p in SRC.rglob("*.md")}
    if missing := sorted(known - found):
        print(f"build_docs: NAV lists pages with no source: {missing}", file=sys.stderr)
        return 1
    if orphan := sorted(found - known):
        print(f"build_docs: sources missing from NAV: {orphan}", file=sys.stderr)
        return 1

    md = markdown.Markdown(
        extensions=["extra", "codehilite", "sane_lists", "admonition", "toc"],
        extension_configs={"codehilite": {"guess_lang": False},
                           "toc": {"permalink": "#", "toc_depth": "2-2"}})
    for path in sorted(SRC.rglob("*.md")):
        stem = str(path.relative_to(SRC).with_suffix(""))
        depth = stem.count("/")
        root = "../" * depth
        meta, text = front_matter(path.read_text())
        md.reset()
        body = wrap_tables(fix_links(md.convert(text), root))
        toc_class, toc = toc_html(md.toc, body)
        title = meta.get("title") or stem
        page = PAGE.format(
            title=html.escape(f"{title} · {SITE}" if stem != "index" else
                              f"{SITE} — {TAGLINE}"),
            description=html.escape(meta.get("description", TAGLINE)),
            site=SITE, tagline=html.escape(TAGLINE), repo=REPO,
            root=root, nav=nav_html(stem, root), body=body, stem=stem,
            toc=toc, toc_class=toc_class,
            theme_script=THEME_SCRIPT)
        dest = OUT / f"{stem}.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(page)
        print(f"  {stem}.html")
    print(f"built {len(found)} pages into {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
