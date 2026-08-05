"""The site's visual layer: palette, type, page shell.

Kept apart from build_docs.py so that changing how the site looks and changing
how it is assembled stay separate jobs.

Two rules hold the design together, and both come from what loopgraph is.

**Colour only ever means a verdict.** Green is a criterion that closed, red is
one that failed, amber is one still unproven. Nothing is tinted to look nice.
A page that spent green on a heading would be lying in the site's own
vocabulary, so the palette leaves only cool ink and paper for everything else.

**Monospace is structure, not ornament.** Headings, nav labels, criterion ids
and exit codes are set in mono because in this subject they are all the same
kind of thing: literal strings a machine will compare. Prose is set in a sans
face so the difference is visible at a glance.
"""

CSS = """
:root {
  color-scheme: light dark;
  --paper:  #f4f6f8;   --panel:  #e8ecf1;   --raise:  #ffffff;
  --ink:    #10151c;   --muted:  #56616f;   --faint:  #7b8695;
  --rule:   #d5dbe3;   --line:   #b3bfcd;
  --link:   #2d4f86;   --link-h: #16305c;
  --pass:   #2e7d4f;   --fail:   #b23a2e;   --wait:   #8a6a17;
  --pass-bg:#e3f1e8;   --fail-bg:#f8e5e2;   --wait-bg:#f7eed6;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, Helvetica, Arial, sans-serif;
  --measure: 68ch;
}
:root[data-theme="dark"], :root:not([data-theme="light"]) {
  color-scheme: light;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --paper:  #0e1218;   --panel:  #161c25;   --raise:  #1b222c;
    --ink:    #dde4ee;   --muted:  #909cad;   --faint:  #6f7c8d;
    --rule:   #232c38;   --line:   #38444f;
    --link:   #8bb0ea;   --link-h: #b3ccf5;
    --pass:   #5cbf85;   --fail:   #e2796c;   --wait:   #d6b45f;
    --pass-bg:#16261d;   --fail-bg:#2a1a19;   --wait-bg:#282217;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --paper:  #0e1218;   --panel:  #161c25;   --raise:  #1b222c;
  --ink:    #dde4ee;   --muted:  #909cad;   --faint:  #6f7c8d;
  --rule:   #232c38;   --line:   #38444f;
  --link:   #8bb0ea;   --link-h: #b3ccf5;
  --pass:   #5cbf85;   --fail:   #e2796c;   --wait:   #d6b45f;
  --pass-bg:#16261d;   --fail-bg:#2a1a19;   --wait-bg:#282217;
}

* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; scroll-behavior: smooth; }
@media (prefers-reduced-motion: reduce) {
  html { scroll-behavior: auto; }
  * { animation-duration: .01ms !important; transition-duration: .01ms !important; }
}
body {
  margin: 0; background: var(--paper); color: var(--ink);
  font-family: var(--sans); font-size: 16px; line-height: 1.62;
  font-variant-numeric: tabular-nums;
}
a { color: var(--link); text-decoration-thickness: 1px; text-underline-offset: 2px; }
a:hover { color: var(--link-h); }
:focus-visible { outline: 2px solid var(--link); outline-offset: 2px; border-radius: 2px; }

/* ---- masthead ---------------------------------------------------------- */
.masthead { border-bottom: 1px solid var(--rule); background: var(--panel); }
.masthead .inner {
  max-width: 1400px; margin: 0 auto; padding: .55rem 1.5rem;
  display: flex; align-items: center; gap: 1rem;
}
.masthead .name {
  font-family: var(--mono); font-size: .95rem; font-weight: 600;
  letter-spacing: -.02em; color: var(--ink); text-decoration: none;
}
.masthead .name::before { content: "▸ "; color: var(--pass); }
.masthead .tag {
  color: var(--muted); font-size: .8rem; border-left: 1px solid var(--line);
  padding-left: 1rem;
}
.masthead .spacer { flex: 1 1 auto; }
.masthead a.util, .masthead button.util {
  font-family: var(--mono); font-size: .78rem; color: var(--muted);
  text-decoration: none; background: none; border: 1px solid transparent;
  padding: .2rem .5rem; border-radius: 4px; cursor: pointer;
}
.masthead a.util:hover, .masthead button.util:hover {
  color: var(--ink); border-color: var(--line);
}
@media (max-width: 620px) { .masthead .tag { display: none; } }

/* ---- shell ------------------------------------------------------------- */
.wrap {
  max-width: 1400px; margin: 0 auto; padding: 0 1.5rem;
  display: grid; grid-template-columns: 216px minmax(0, 1fr); gap: 3rem;
}
.wrap.has-toc { grid-template-columns: 216px minmax(0, 1fr) 190px; }
@media (max-width: 1180px) { .wrap.has-toc { grid-template-columns: 216px minmax(0, 1fr); } }

nav.side {
  padding: 2rem 0 4rem; align-self: start; position: sticky; top: 0;
  max-height: 100vh; overflow-y: auto;
}
nav.side .eyebrow {
  font-family: var(--mono); font-size: .68rem; letter-spacing: .08em;
  text-transform: uppercase; color: var(--faint); margin: 1.7rem 0 .45rem;
}
nav.side .eyebrow:first-child { margin-top: 0; }
nav.side ul { list-style: none; margin: 0; padding: 0; }
nav.side a {
  display: block; padding: .25rem 0 .25rem .7rem; font-size: .875rem;
  color: var(--muted); text-decoration: none;
  border-left: 2px solid var(--rule);
}
nav.side a:hover { color: var(--ink); border-left-color: var(--line); }
nav.side a.current {
  color: var(--ink); font-weight: 600; border-left-color: var(--pass);
  background: linear-gradient(90deg, var(--panel), transparent);
}

main { padding: 2.6rem 0 6rem; min-width: 0; }

/* ---- on-this-page ------------------------------------------------------ */
aside.toc {
  padding: 2.9rem 0 4rem; align-self: start; position: sticky; top: 0;
  max-height: 100vh; overflow-y: auto; font-size: .8rem;
}
@media (max-width: 1180px) { aside.toc { display: none; } }
aside.toc .eyebrow {
  font-family: var(--mono); font-size: .66rem; letter-spacing: .08em;
  text-transform: uppercase; color: var(--faint); margin: 0 0 .5rem;
}
aside.toc ul { list-style: none; margin: 0; padding: 0; }
aside.toc ul ul { display: none; }
aside.toc a {
  display: block; padding: .2rem 0; color: var(--muted); text-decoration: none;
  line-height: 1.4;
}
aside.toc a:hover { color: var(--ink); }

/* ---- prose ------------------------------------------------------------- */
main > *, .prose > * { max-width: var(--measure); }
main > .tablewrap, main > .ledger, main > .cards, main > .loopfig,
main > pre, main > .hero { max-width: none; }

h1, h2, h3, h4 { font-family: var(--mono); letter-spacing: -.02em; line-height: 1.25; }
h1 { font-size: 1.95rem; font-weight: 600; margin: 0 0 1.1rem; }
h2 {
  font-size: 1.12rem; font-weight: 600; margin: 3rem 0 .9rem;
  padding-top: .85rem; border-top: 1px solid var(--rule);
}
h3 { font-size: .98rem; font-weight: 600; margin: 2rem 0 .55rem; color: var(--ink); }
h4 { font-size: .88rem; font-weight: 600; margin: 1.5rem 0 .4rem; color: var(--muted); }
h1 .headerlink, h2 .headerlink, h3 .headerlink, h4 .headerlink {
  color: var(--faint); text-decoration: none; opacity: 0; padding-left: .4rem;
  font-weight: 400;
}
h1:hover .headerlink, h2:hover .headerlink,
h3:hover .headerlink, h4:hover .headerlink { opacity: 1; }

p, ul, ol { margin: 0 0 1.05rem; }
li { margin: .3rem 0; }
li > ul, li > ol { margin: .3rem 0; }
strong { font-weight: 640; }
blockquote {
  margin: 1.2rem 0; padding: .15rem 0 .15rem 1.1rem;
  border-left: 2px solid var(--line); color: var(--muted);
}
hr { border: 0; border-top: 1px solid var(--rule); margin: 2.4rem 0; }

code { font-family: var(--mono); font-size: .855em; }
p code, li code, td code, th code, h1 code, h2 code, h3 code, h4 code, aside code {
  background: var(--panel); border: 1px solid var(--rule);
  border-radius: 3px; padding: .07em .3em;
}
pre {
  background: var(--panel); border: 1px solid var(--rule);
  border-left: 2px solid var(--line);
  padding: .8rem 1rem; overflow-x: auto; margin: 0 0 1.2rem; line-height: 1.5;
}
pre code { background: none; border: 0; padding: 0; font-size: .82rem; }

.tablewrap { overflow-x: auto; margin: 0 0 1.3rem; }
table { border-collapse: collapse; font-size: .875rem; min-width: 100%; }
th, td {
  text-align: left; vertical-align: top; padding: .5rem .85rem;
  border-bottom: 1px solid var(--rule);
}
th {
  font-family: var(--mono); font-weight: 600; font-size: .78rem;
  letter-spacing: .01em; color: var(--muted); background: var(--panel);
  white-space: nowrap;
}
tbody tr:last-child td { border-bottom: 0; }

/* ---- hero -------------------------------------------------------------- */
.hero { margin: 0 0 2.6rem; }
.hero .eyebrow {
  font-family: var(--mono); font-size: .72rem; letter-spacing: .1em;
  text-transform: uppercase; color: var(--faint); margin: 0 0 .7rem;
}
.hero h1 { font-size: 2.35rem; margin: 0 0 .8rem; }
.hero .lede {
  font-size: 1.08rem; color: var(--muted); max-width: 56ch; margin: 0 0 1.6rem;
}
.hero .lede strong { color: var(--ink); font-weight: 600; }

/* the signature: a check run, rendered as the thing it is */
.ledger {
  border: 1px solid var(--rule); background: var(--raise);
  font-family: var(--mono); font-size: .82rem; margin: 0 0 1.4rem;
}
.ledger .bar {
  display: flex; gap: .6rem; align-items: center;
  padding: .45rem .8rem; border-bottom: 1px solid var(--rule);
  background: var(--panel); color: var(--faint); font-size: .74rem;
}
.ledger .bar .dot {
  width: 7px; height: 7px; border-radius: 50%; background: var(--line);
}
.ledger .row {
  display: grid; grid-template-columns: 4.2rem 6.4rem 1fr;
  gap: .8rem; padding: .5rem .8rem; border-bottom: 1px solid var(--rule);
  align-items: baseline;
}
.ledger .row:last-child { border-bottom: 0; }
.ledger .id { color: var(--faint); }
.ledger .stmt { color: var(--ink); font-family: var(--sans); font-size: .86rem; }
.ledger .verdict { font-weight: 600; }
.ledger .verdict.pass { color: var(--pass); }
.ledger .verdict.fail { color: var(--fail); }
.ledger .verdict.wait { color: var(--wait); }
.ledger .exit {
  padding: .55rem .8rem; background: var(--panel); color: var(--muted);
  border-top: 1px solid var(--rule); font-size: .78rem;
}
.ledger .exit b { color: var(--ink); font-weight: 600; }
@media (max-width: 620px) {
  .ledger .row { grid-template-columns: 3.4rem 5.6rem; }
  .ledger .stmt { grid-column: 1 / -1; }
}

/* ---- loop diagram ------------------------------------------------------ */
.loopfig { margin: 2.2rem 0 2.6rem; }
.loopfig svg { width: 100%; height: auto; display: block; }
.loopfig figcaption {
  font-size: .8rem; color: var(--muted); margin-top: .6rem; max-width: var(--measure);
}
.loopfig .stroke { stroke: var(--line); }
.loopfig .fill-panel { fill: var(--panel); }
.loopfig .fill-paper { fill: var(--paper); }
.loopfig .label { fill: var(--ink); font-family: var(--mono); font-size: 12px; }
.loopfig .sub { fill: var(--muted); font-family: var(--sans); font-size: 11px; }
.loopfig .pass { stroke: var(--pass); }
.loopfig .fail { stroke: var(--fail); }
.loopfig .pass-t { fill: var(--pass); font-family: var(--mono); font-size: 11px; }
.loopfig .fail-t { fill: var(--fail); font-family: var(--mono); font-size: 11px; }

/* ---- cards ------------------------------------------------------------- */
.cards {
  display: grid; grid-template-columns: repeat(3, 1fr);
  gap: 1px; margin: 1.6rem 0 2.2rem;
}
@media (max-width: 1080px) { .cards { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 620px)  { .cards { grid-template-columns: 1fr; } }
.card {
  background: var(--paper); padding: .95rem 1.05rem; text-decoration: none;
  color: inherit; display: block; outline: 1px solid var(--rule);
}
.card:hover { background: var(--panel); position: relative; z-index: 1;
  outline-color: var(--line); }
.card b {
  display: block; font-family: var(--mono); font-size: .88rem; font-weight: 600;
  margin-bottom: .28rem; color: var(--ink);
}
.card span { font-size: .84rem; color: var(--muted); line-height: 1.45; }

footer.site {
  border-top: 1px solid var(--rule); margin-top: 3.5rem; padding: 1.3rem 0 3rem;
  color: var(--faint); font-size: .8rem; font-family: var(--mono);
  max-width: none;
}
footer.site a { color: var(--muted); }

@media (max-width: 900px) {
  .wrap, .wrap.has-toc { grid-template-columns: 1fr; gap: 0; }
  nav.side {
    position: static; max-height: none; padding: 1rem 0 .6rem;
    border-bottom: 1px solid var(--rule);
  }
  nav.side .eyebrow { margin: .9rem 0 .35rem; }
  nav.side ul { display: flex; flex-wrap: wrap; gap: .1rem .35rem; }
  nav.side a { border-left: 0; padding: .15rem .4rem; }
  nav.side a.current { background: var(--panel); }
  main { padding-top: 1.8rem; }
  .hero h1 { font-size: 1.75rem; }
  h1 { font-size: 1.55rem; }
}

/* ---- pygments: code is ink, not a rainbow ------------------------------ */
.highlight .c, .highlight .c1, .highlight .cm, .highlight .ch { color: var(--faint); font-style: italic; }
.highlight .k, .highlight .kd, .highlight .kn, .highlight .kr, .highlight .kt { color: var(--ink); font-weight: 600; }
.highlight .s, .highlight .s1, .highlight .s2, .highlight .sb, .highlight .se { color: var(--link); }
.highlight .nb, .highlight .bp, .highlight .nf, .highlight .nc { color: var(--ink); }
.highlight .m, .highlight .mi, .highlight .mf { color: var(--link); }
.highlight .gp { color: var(--faint); }
.highlight .gi { color: var(--pass); }
.highlight .gd { color: var(--fail); }
"""

FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
<rect width="32" height="32" rx="5" fill="#10151c"/>
<path d="M8 10h7a6 6 0 0 1 0 12H8z" fill="none" stroke="#5cbf85" stroke-width="2.6"/>
<path d="M18 16l4 4 6-8" fill="none" stroke="#5cbf85" stroke-width="2.6"
      stroke-linecap="square"/>
</svg>
"""

THEME_SCRIPT = """
(function () {
  var k = 'loopgraph-theme', r = document.documentElement;
  try { var s = localStorage.getItem(k); if (s) r.setAttribute('data-theme', s); } catch (e) {}
  window.addEventListener('DOMContentLoaded', function () {
    var b = document.getElementById('theme-toggle');
    if (!b) return;
    b.addEventListener('click', function () {
      var dark = r.getAttribute('data-theme') === 'dark' ||
        (!r.getAttribute('data-theme') &&
         window.matchMedia('(prefers-color-scheme: dark)').matches);
      var next = dark ? 'light' : 'dark';
      r.setAttribute('data-theme', next);
      try { localStorage.setItem(k, next); } catch (e) {}
    });
  });
})();
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:type" content="website">
<link rel="icon" href="{root}assets/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="{root}assets/style.css">
<script>{theme_script}</script>
</head>
<body>
<header class="masthead"><div class="inner">
  <a class="name" href="{root}index.html">{site}</a>
  <span class="tag">{tagline}</span>
  <span class="spacer"></span>
  <button class="util" id="theme-toggle" type="button"
          aria-label="Switch between light and dark">theme</button>
  <a class="util" href="{repo}">github</a>
</div></header>
<div class="wrap{toc_class}">
<nav class="side" aria-label="Sections">
{nav}
</nav>
<main>
{body}
<footer class="site">
  MIT &middot; <a href="{repo}">source</a> &middot;
  <a href="{repo}/blob/main/docs-src/{stem}.md">edit this page</a>
</footer>
</main>
{toc}
</div>
</body>
</html>
"""
