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

CSS = """
:root {
  color-scheme: light dark;
  --bg: #fbfaf8;      --panel: #f2efea;   --ink: #1c1a17;    --muted: #6a635a;
  --rule: #ddd7cd;    --accent: #8a4b2a;  --accent-soft: #f0e5dc;
  --code-bg: #f5f2ed; --mark: #b3541e;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Helvetica, Arial, sans-serif;
  --serif: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16151a;    --panel: #1e1d23;   --ink: #e6e2db;    --muted: #9b948a;
    --rule: #302e37;  --accent: #d99a6c;  --accent-soft: #2a2229;
    --code-bg: #1b1a20; --mark: #e8b98f;
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: var(--sans); font-size: 16.5px; line-height: 1.65;
}
a { color: var(--accent); text-decoration-thickness: 1px; text-underline-offset: 2px; }
a:hover { text-decoration-thickness: 2px; }

.masthead {
  border-bottom: 1px solid var(--rule); background: var(--panel);
  position: sticky; top: 0; z-index: 20;
}
.masthead .inner {
  max-width: 1180px; margin: 0 auto; padding: .7rem 1.4rem;
  display: flex; align-items: baseline; gap: .9rem; flex-wrap: wrap;
}
.masthead .name {
  font-family: var(--mono); font-size: 1.05rem; font-weight: 600;
  color: var(--ink); text-decoration: none; letter-spacing: -.02em;
}
.masthead .tag { color: var(--muted); font-size: .85rem; }
.masthead .spacer { flex: 1 1 auto; }
.masthead .gh { font-size: .85rem; }

.wrap { max-width: 1180px; margin: 0 auto; padding: 0 1.4rem;
        display: grid; grid-template-columns: 232px minmax(0, 1fr); gap: 2.6rem; }
nav.side { padding: 2rem 0 4rem; align-self: start; position: sticky; top: 3.4rem;
           max-height: calc(100vh - 4rem); overflow-y: auto; }
nav.side h2 {
  font-size: .68rem; text-transform: uppercase; letter-spacing: .11em;
  color: var(--muted); margin: 1.6rem 0 .5rem; font-weight: 600;
}
nav.side h2:first-child { margin-top: 0; }
nav.side ul { list-style: none; margin: 0; padding: 0; }
nav.side li { margin: 0; }
nav.side a {
  display: block; padding: .26rem .55rem; margin-left: -.55rem;
  border-radius: 5px; font-size: .9rem; color: var(--ink);
  text-decoration: none; border-left: 2px solid transparent;
}
nav.side a:hover { background: var(--accent-soft); }
nav.side a.current {
  background: var(--accent-soft); color: var(--accent);
  font-weight: 600; border-left-color: var(--accent);
}

main { padding: 2.4rem 0 6rem; min-width: 0; }
main > h1:first-child { margin-top: 0; }
h1, h2, h3, h4 { font-family: var(--serif); line-height: 1.22; letter-spacing: -.01em; }
h1 { font-size: 2.15rem; margin: 0 0 1.2rem; }
h2 { font-size: 1.5rem; margin: 2.6rem 0 .8rem; padding-top: .5rem; border-top: 1px solid var(--rule); }
h3 { font-size: 1.17rem; margin: 1.9rem 0 .6rem; }
h4 { font-size: 1rem; margin: 1.4rem 0 .4rem; font-family: var(--sans); }
p, ul, ol { margin: 0 0 1rem; }
li { margin: .28rem 0; }
strong { font-weight: 650; }
blockquote {
  margin: 1.2rem 0; padding: .1rem 0 .1rem 1.1rem;
  border-left: 3px solid var(--rule); color: var(--muted);
}
hr { border: 0; border-top: 1px solid var(--rule); margin: 2.2rem 0; }

code { font-family: var(--mono); font-size: .87em; }
p code, li code, td code, h1 code, h2 code, h3 code, h4 code {
  background: var(--code-bg); border: 1px solid var(--rule);
  border-radius: 4px; padding: .06em .32em;
}
pre {
  background: var(--code-bg); border: 1px solid var(--rule); border-radius: 7px;
  padding: .85rem 1rem; overflow-x: auto; margin: 0 0 1.2rem; line-height: 1.5;
}
pre code { background: none; border: 0; padding: 0; font-size: .845rem; }

.tablewrap { overflow-x: auto; margin: 0 0 1.3rem; }
table { border-collapse: collapse; font-size: .9rem; min-width: 100%; }
th, td { text-align: left; vertical-align: top; padding: .5rem .8rem;
         border-bottom: 1px solid var(--rule); }
th { font-weight: 600; background: var(--panel); white-space: nowrap; }
tbody tr:last-child td { border-bottom: 0; }

.lede { font-size: 1.12rem; color: var(--muted); margin: -.4rem 0 1.8rem; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(215px, 1fr));
         gap: .9rem; margin: 1.4rem 0 2rem; }
.card {
  border: 1px solid var(--rule); border-radius: 8px; padding: .9rem 1rem;
  background: var(--panel); text-decoration: none; color: inherit; display: block;
}
.card:hover { border-color: var(--accent); }
.card b { display: block; font-size: .96rem; margin-bottom: .2rem; color: var(--accent); }
.card span { font-size: .86rem; color: var(--muted); line-height: 1.45; }

footer.site {
  border-top: 1px solid var(--rule); margin-top: 3rem; padding: 1.4rem 0 3rem;
  color: var(--muted); font-size: .84rem;
}
.toc { display: none; }

@media (max-width: 860px) {
  .wrap { grid-template-columns: 1fr; gap: 0; }
  nav.side {
    position: static; max-height: none; padding: 1.2rem 0 .4rem;
    border-bottom: 1px solid var(--rule);
  }
  nav.side ul { display: flex; flex-wrap: wrap; gap: .25rem; }
  nav.side a { border-left: 0; padding: .2rem .5rem; margin-left: 0; }
  main { padding-top: 1.6rem; }
  h1 { font-size: 1.8rem; }
}

/* pygments, tuned to both themes */
.highlight .c, .highlight .c1, .highlight .cm, .highlight .ch { color: var(--muted); font-style: italic; }
.highlight .k, .highlight .kd, .highlight .kn, .highlight .kr, .highlight .kt { color: var(--mark); }
.highlight .s, .highlight .s1, .highlight .s2, .highlight .sb, .highlight .se { color: #3f7d58; }
.highlight .nb, .highlight .bp { color: var(--accent); }
.highlight .nf, .highlight .nc { color: var(--ink); font-weight: 600; }
.highlight .m, .highlight .mi, .highlight .mf { color: #7a5ea8; }
.highlight .gp { color: var(--muted); }
@media (prefers-color-scheme: dark) {
  .highlight .s, .highlight .s1, .highlight .s2, .highlight .sb, .highlight .se { color: #8fbf9f; }
  .highlight .m, .highlight .mi, .highlight .mf { color: #b39ddb; }
}
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="stylesheet" href="{root}assets/style.css">
</head>
<body>
<header class="masthead"><div class="inner">
  <a class="name" href="{root}index.html">{site}</a>
  <span class="tag">{tagline}</span>
  <span class="spacer"></span>
  <a class="gh" href="{repo}">GitHub</a>
</div></header>
<div class="wrap">
<nav class="side">
{nav}
</nav>
<main>
{body}
<footer class="site">
  {site} &middot; MIT &middot; <a href="{repo}">source</a>
  &middot; <a href="{repo}/blob/main/docs-src/{stem}.md">edit this page</a>
</footer>
</main>
</div>
</body>
</html>
"""


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
            out.append(f"<h2>{section}</h2>")
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


def build() -> int:
    if not SRC.is_dir():
        print(f"no sources at {SRC}", file=sys.stderr)
        return 1
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "assets").mkdir(parents=True)
    (OUT / ".nojekyll").write_text("")
    (OUT / "assets" / "style.css").write_text(CSS.lstrip())

    known = {stem for _, items in NAV for stem, _ in items}
    found = {str(p.relative_to(SRC).with_suffix("")) for p in SRC.rglob("*.md")}
    if missing := sorted(known - found):
        print(f"build_docs: NAV lists pages with no source: {missing}", file=sys.stderr)
        return 1
    if orphan := sorted(found - known):
        print(f"build_docs: sources missing from NAV: {orphan}", file=sys.stderr)
        return 1

    md = markdown.Markdown(extensions=["extra", "codehilite", "sane_lists",
                                       "admonition", "toc"],
                           extension_configs={"codehilite": {"guess_lang": False}})
    for path in sorted(SRC.rglob("*.md")):
        stem = str(path.relative_to(SRC).with_suffix(""))
        depth = stem.count("/")
        root = "../" * depth
        meta, text = front_matter(path.read_text())
        md.reset()
        body = wrap_tables(fix_links(md.convert(text), root))
        title = meta.get("title") or stem
        page = PAGE.format(
            title=html.escape(f"{title} · {SITE}" if stem != "index" else
                              f"{SITE} — {TAGLINE}"),
            description=html.escape(meta.get("description", TAGLINE)),
            site=SITE, tagline=html.escape(TAGLINE), repo=REPO,
            root=root, nav=nav_html(stem, root), body=body, stem=stem)
        dest = OUT / f"{stem}.html"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(page)
        print(f"  {stem}.html")
    print(f"built {len(found)} pages into {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
