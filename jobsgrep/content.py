"""Server-rendered SEO pages: category landing pages + blog.

All pages reuse the main stylesheet (/static/styles.css) for visual consistency
and add proper meta/canonical/OG tags + JSON-LD so they're indexable. Category
pages double as the browsable, link-worthy entry points for each downloadable
job pack.
"""
from __future__ import annotations

import html as _html
import json as _json

from .insights import ROLE_SLUGS
from .taxonomy import ROLE_ORDER, classify_level, classify_role_family, filter_tech

SITE = "JobsGrep"


def esc(s: str) -> str:
    return _html.escape(str(s or ""))


# ─── Shared shell ────────────────────────────────────────────────────────────

def page_shell(title: str, description: str, canonical: str, body: str,
               base_url: str, ld_json: str = "") -> str:
    ld = f'<script type="application/ld+json">{ld_json}</script>' if ld_json else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{esc(title)}</title>
  <meta name="description" content="{esc(description)}">
  <meta name="robots" content="index, follow">
  <link rel="canonical" href="{esc(canonical)}">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{esc(canonical)}">
  <meta property="og:title" content="{esc(title)}">
  <meta property="og:description" content="{esc(description)}">
  <meta property="og:image" content="{esc(base_url)}/favicon.png">
  <meta name="twitter:card" content="summary">
  <link rel="icon" type="image/svg+xml" href="/favicon.ico">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/styles.css">
  {ld}
  <style>
    .article {{ width: 100%; max-width: 760px; margin: 0 auto; }}
    .article h1 {{ font-size: 2.1rem; font-weight: 800; line-height: 1.15; margin-bottom: 0.5rem; }}
    .article .meta {{ color: var(--muted); font-size: 0.85rem; margin-bottom: 1.75rem; }}
    .article h2 {{ font-size: 1.35rem; margin: 2rem 0 0.75rem; }}
    .article p, .article li {{ color: #c8cbe0; line-height: 1.75; font-size: 1rem; }}
    .article ul, .article ol {{ margin: 0.5rem 0 1rem 1.25rem; }}
    .article li {{ margin-bottom: 0.4rem; }}
    .article a {{ color: var(--accent); }}
    .article code, .prompt-box {{ background: var(--bg); border: 1px solid var(--border); border-radius: 8px; }}
    .prompt-box {{ display: block; white-space: pre-wrap; padding: 1rem; font-size: 0.85rem; color: #c8d8f0; margin: 1rem 0; line-height: 1.6; }}
    .landing-list {{ list-style: none; margin: 0; padding: 0; display: grid; gap: 0.5rem; }}
    .landing-list li {{ background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 0.7rem 0.9rem; display: flex; justify-content: space-between; gap: 1rem; align-items: baseline; }}
    .landing-list a {{ color: var(--text); text-decoration: none; font-weight: 600; font-size: 0.92rem; }}
    .landing-list a:hover {{ color: var(--accent); }}
    .landing-list .ll-meta {{ color: var(--muted); font-size: 0.8rem; white-space: nowrap; }}
    .pill {{ font-size: 0.72rem; color: var(--accent); background: rgba(91,141,238,0.1); border-radius: 20px; padding: 1px 8px; margin-left: 0.4rem; }}
    .crumbs {{ font-size: 0.82rem; color: var(--muted); margin-bottom: 1rem; }}
    .crumbs a {{ color: var(--muted); }}
    .related a {{ display: inline-block; margin: 0.2rem 0.4rem 0.2rem 0; color: var(--accent); font-size: 0.9rem; }}
  </style>
</head>
<body>
<header>
  <a class="logo" href="/" style="text-decoration:none">⚡ JobsGrep</a>
  <nav class="header-nav">
    <a href="/#downloads">Downloads</a>
    <a href="/#sources">Sources</a>
    <a href="/blog">Guides</a>
    <a href="https://github.com/awesomefanda" target="_blank" rel="noopener">GitHub</a>
  </nav>
</header>
<main>
{body}
</main>
<footer>
  JobsGrep — every open tech job in one spreadsheet, free.
  &nbsp;|&nbsp; <a href="/">Home</a> &nbsp;|&nbsp; <a href="/blog">Guides</a> &nbsp;|&nbsp;
  <a href="https://github.com/awesomefanda" target="_blank" rel="noopener">GitHub</a>
</footer>
</body>
</html>"""


_RANKING_PROMPT = (
    "You are my job-search assistant. Below is a spreadsheet of open jobs "
    "(Company, Title, Level, Location, Remote, Salary, Posted, Source, URL) and "
    "my resume.\n"
    "1. Score every job 0-100 for fit with my resume.\n"
    "2. Return a ranked table: Rank | Company | Title | Level | Location | Score | reason.\n"
    "3. For the top 10, add why I fit, my biggest gap, and a tailored hook.\n"
    "MY RESUME:\n[PASTE YOUR RESUME HERE]"
)


# ─── Category landing pages ──────────────────────────────────────────────────

CATEGORY_INTROS: dict[str, str] = {
    "Software Engineering":
        "Backend, frontend, full-stack, mobile, and systems roles from across the tech industry — junior through principal.",
    "Data & ML":
        "Data engineering, data science, machine learning, and applied-AI roles, including LLM and MLOps positions.",
    "Infrastructure & DevOps":
        "DevOps, SRE, platform, cloud, and infrastructure engineering roles keeping production running.",
    "Security":
        "Security engineering, application security, and security operations roles across the industry.",
    "Hardware & Semiconductor":
        "Chip and hardware roles — ASIC/FPGA/RTL design, physical design, verification, analog/mixed-signal, PCB, and electrical engineering.",
    "QA & Test":
        "Quality engineering, test automation, and SDET roles.",
    "Solutions & Sales Engineering":
        "Solutions engineering, sales engineering, and forward-deployed roles bridging product and customers.",
    "Developer Relations":
        "Developer advocacy, developer experience, and developer-relations roles.",
    "IT & Support":
        "Technical support engineering, IT, and systems administration roles.",
    "Technical Writing":
        "Technical writing and documentation engineering roles.",
    "Design":
        "Product design, UX, and UI roles for technology teams.",
    "Engineering Management":
        "Engineering manager, director, and VP of engineering roles.",
    "Product Management":
        "Product manager roles from associate to head of product.",
    "Program & Project Management":
        "Technical program and project management roles.",
    "Other":
        "Tech-adjacent roles that don't fit a single discipline.",
    "Non-Tech":
        "Non-engineering roles posted at tech companies — sales, marketing, operations, finance, HR, legal, and more.",
}


def render_category_page(family: str, slug: str, all_jobs: list, base_url: str) -> str:
    jobs = [j for j in filter_tech(all_jobs) if classify_role_family(j.title) == family]
    count = len(jobs)
    title = f"{family} Jobs — Download {count:,} Open Roles (Free Excel) | {SITE}"
    desc = (
        f"Download {count:,} open {family} jobs as a free Excel sheet aggregated from "
        f"Greenhouse, Lever, Ashby, SmartRecruiters, Adzuna and more — then rank them "
        f"against your resume with your own AI."
    )
    canonical = f"{base_url}/categories/{slug}"

    sample = jobs[:50]
    items = ""
    for j in sample:
        loc = esc(j.location or "Remote")
        lvl = esc(classify_level(j.title))
        items += (
            f'<li><a href="{esc(j.url or "#")}" target="_blank" rel="noopener nofollow">'
            f'{esc(j.title)} <span class="pill">{lvl}</span></a>'
            f'<span class="ll-meta">{esc(j.company)} · {loc}</span></li>'
        )
    if not items:
        items = '<li>No listings cached right now — check back after the next refresh.</li>'

    related = " ".join(
        f'<a href="/categories/{ROLE_SLUGS[f]}">{esc(f)}</a>'
        for f in ROLE_ORDER if f != family and f in ROLE_SLUGS
    )

    ld = _json.dumps({
        "@context": "https://schema.org",
        "@type": "ItemList",
        "name": f"{family} Jobs",
        "description": desc,
        "url": canonical,
        "numberOfItems": count,
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": esc(j.title), "url": j.url or base_url}
            for i, j in enumerate(sample[:20], 1)
        ],
    }, ensure_ascii=False)

    body = f"""
  <section class="hero" style="text-align:left">
    <div class="crumbs"><a href="/">Home</a> › <a href="/#downloads">Categories</a> › {esc(family)}</div>
    <h1>{esc(family)} Jobs</h1>
    <p class="hero-sub" style="margin-left:0">{esc(CATEGORY_INTROS.get(family, ''))}
    <strong>{count:,} open roles</strong> right now.</p>
    <div class="hero-cta" style="justify-content:flex-start">
      <a class="btn btn-primary" href="/api/packs/{esc(slug)}">⬇ Download {count:,} {esc(family)} jobs (Excel)</a>
      <a class="btn btn-ghost" href="/api/packs/all">Download everything</a>
    </div>
  </section>

  <section class="section">
    <h2 class="section-title">Rank these against your resume with AI</h2>
    <p class="section-sub">The download includes a Level column and a Start Here tab. Paste the rows plus your resume into ChatGPT, Claude, or Gemini with this prompt:</p>
    <code class="prompt-box">{esc(_RANKING_PROMPT)}</code>
  </section>

  <section class="section">
    <h2 class="section-title">Sample {esc(family)} listings</h2>
    <p class="section-sub">A preview of what's in the sheet. The Excel download has all {count:,}.</p>
    <ul class="landing-list">{items}</ul>
  </section>

  <section class="section related">
    <h2 class="section-title">Other categories</h2>
    <p>{related}</p>
    <p style="margin-top:1rem"><a href="/blog">Read the guides →</a></p>
  </section>
"""
    return page_shell(title, desc, canonical, body, base_url, ld)


# ─── Blog ────────────────────────────────────────────────────────────────────

BLOG_POSTS: list[dict] = [
    {
        "slug": "rank-jobs-with-ai-resume",
        "title": "How to Use AI to Rank Job Listings Against Your Resume (Free)",
        "date": "2026-06-18",
        "description": "A simple, free workflow: download open jobs as a spreadsheet, then have ChatGPT, Claude, or Gemini score every role against your resume and hand back a ranked shortlist.",
        "body": """
<p>Applying to jobs one posting at a time is slow, and job boards bury good roles
under noise. A faster approach: get <em>all</em> the relevant jobs as a
spreadsheet, then let an AI model do the ranking against your actual resume. No
subscriptions, no uploading your resume to a third party — the ranking happens in
your own ChatGPT, Claude, or Gemini session.</p>

<h2>Step 1 — Download the jobs for your role</h2>
<p>Grab the Excel pack for your discipline from the
<a href="/#downloads">downloads section</a> — for example
<a href="/categories/software-engineering">Software Engineering</a>,
<a href="/categories/data-ml">Data &amp; ML</a>, or
<a href="/categories/product-management">Product Management</a>. Each pack has a
<strong>Level</strong> column so you can filter to your seniority in Excel before
you even involve an AI.</p>

<h2>Step 2 — Paste the rows and your resume into an AI</h2>
<p>Open ChatGPT, Claude, or Gemini. Paste in the job rows (or attach the sheet),
then paste your resume and use a prompt like this:</p>
<code class="prompt-box">""" + _RANKING_PROMPT + """</code>

<h2>Step 3 — Get a ranked shortlist</h2>
<p>The model returns a ranked table with a fit score and a one-line reason for
each role, plus a deeper look at the top matches — where you're strong, where
you're short, and a tailored hook you can reuse in your application. Because the
model sees your whole resume at once, the ranking reflects <em>your</em>
experience, not keyword matching.</p>

<h2>Why this beats a normal job search</h2>
<ul>
<li><strong>Breadth:</strong> you review hundreds of roles in minutes instead of scrolling endless boards.</li>
<li><strong>Personalization:</strong> the ranking is against your real resume, not a generic relevance score.</li>
<li><strong>Privacy:</strong> your resume stays in your own AI session — JobsGrep only ships the public job data.</li>
</ul>

<p>Ready? <a href="/#downloads">Download a job pack</a> and try it now.</p>
""",
    },
    {
        "slug": "download-all-tech-jobs-spreadsheet",
        "title": "How to Download Every Open Tech Job as a Spreadsheet",
        "date": "2026-06-18",
        "description": "Where JobsGrep's data comes from, how fresh it is, and how to download all open tech jobs as a clean Excel file segmented by role.",
        "body": """
<p>JobsGrep aggregates open tech roles from public and licensed job APIs into a
single, downloadable spreadsheet — updated continuously and segmented by role so
you only grab what's relevant.</p>

<h2>Where the data comes from</h2>
<p>Every source is a public or licensed API — no scraping of consumer job boards.
That includes company applicant-tracking systems (Greenhouse, Lever, Ashby,
SmartRecruiters), the licensed <a href="https://developer.adzuna.com" target="_blank" rel="noopener">Adzuna</a>
aggregator, Hacker News "Who's Hiring", Y Combinator companies, and USAJobs. You
can see the full list and live per-source counts in the
<a href="/#sources">sources section</a>.</p>

<h2>How fresh is it?</h2>
<p>The homepage shows a "Job data updated …" badge so you always know how old the
data is before you download. The corpus is refreshed on a schedule, and each
download reflects the latest refresh.</p>

<h2>Download by role</h2>
<p>Instead of one giant file, JobsGrep splits jobs into role packs so they're small
enough to paste into an AI and easy to scan:</p>
<ul>
<li><a href="/categories/software-engineering">Software Engineering</a></li>
<li><a href="/categories/data-ml">Data &amp; ML</a></li>
<li><a href="/categories/infrastructure-devops">Infrastructure &amp; DevOps</a></li>
<li><a href="/categories/engineering-management">Engineering Management</a></li>
<li><a href="/categories/product-management">Product Management</a></li>
</ul>
<p>Or grab <a href="/api/packs/all">the full workbook</a> with every role on its own tab.</p>

<h2>Then rank them with AI</h2>
<p>Each pack is built to drop straight into an LLM. See
<a href="/blog/rank-jobs-with-ai-resume">how to rank jobs against your resume with AI</a>.</p>
""",
    },
    {
        "slug": "remote-vs-onsite-tech-jobs",
        "title": "Remote vs Onsite Tech Jobs: How to Read the Market",
        "date": "2026-06-18",
        "description": "How to use JobsGrep's live charts to gauge remote availability, in-demand role families, and the top hiring companies before you start applying.",
        "body": """
<p>Before you start applying, it helps to know what the market actually looks
like: how many roles are remote-friendly, which disciplines are hiring most, and
who's posting the most jobs. JobsGrep's homepage shows all of this as live charts
built from the current job corpus.</p>

<h2>Remote availability</h2>
<p>The "Remote vs onsite" chart shows what share of current openings are
remote-friendly. It's a quick reality check: if you're remote-only, it tells you
how much of the market you're realistically addressing.</p>

<h2>Which roles are hiring</h2>
<p>The "Jobs by role family" breakdown shows where the volume is —
<a href="/categories/software-engineering">Software Engineering</a> is usually the
largest, but <a href="/categories/data-ml">Data &amp; ML</a> and
<a href="/categories/infrastructure-devops">Infrastructure &amp; DevOps</a> are
consistently deep too. If you're flexible, follow the volume.</p>

<h2>Seniority mix</h2>
<p>The "By seniority level" chart shows the spread from junior to executive.
Pairing this with the Level column in each download lets you target the rungs
where the most roles exist.</p>

<h2>Who's hiring</h2>
<p>The "Top hiring companies" chart surfaces the employers posting the most open
roles right now — a useful shortlist for targeted outreach.</p>

<p>Have a look at the <a href="/">live dashboard</a>, then
<a href="/#downloads">download the pack</a> for your role and
<a href="/blog/rank-jobs-with-ai-resume">rank it with AI</a>.</p>
""",
    },
]

_POSTS_BY_SLUG = {p["slug"]: p for p in BLOG_POSTS}


def render_blog_index(base_url: str) -> str:
    title = f"Guides — Job Search with AI | {SITE}"
    desc = "Practical guides on downloading open tech jobs and ranking them against your resume with AI."
    canonical = f"{base_url}/blog"
    cards = ""
    for p in BLOG_POSTS:
        cards += (
            f'<a class="dl-card" href="/blog/{esc(p["slug"])}">'
            f'<div class="dl-card-top"><span class="dl-name">{esc(p["title"])}</span></div>'
            f'<span class="dl-action" style="color:var(--muted)">{esc(p["description"])}</span></a>'
        )
    body = f"""
  <section class="hero" style="text-align:left">
    <h1>Guides</h1>
    <p class="hero-sub" style="margin-left:0">Get more out of JobsGrep — and your job search.</p>
  </section>
  <section class="section">
    <div class="download-grid">{cards}</div>
  </section>
"""
    return page_shell(title, desc, canonical, body, base_url)


def render_blog_post(slug: str, base_url: str) -> str | None:
    post = _POSTS_BY_SLUG.get(slug)
    if not post:
        return None
    title = f"{post['title']} | {SITE}"
    canonical = f"{base_url}/blog/{slug}"
    ld = _json.dumps({
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": post["title"],
        "description": post["description"],
        "datePublished": post["date"],
        "url": canonical,
        "publisher": {"@type": "Organization", "name": SITE},
    }, ensure_ascii=False)
    body = f"""
  <article class="article">
    <div class="crumbs"><a href="/">Home</a> › <a href="/blog">Guides</a> › {esc(post['title'])}</div>
    <h1>{esc(post['title'])}</h1>
    <div class="meta">Updated {esc(post['date'])}</div>
    {post['body']}
    <p style="margin-top:2rem"><a class="btn btn-primary" href="/#downloads">⬇ Browse job packs</a></p>
  </article>
"""
    return page_shell(title, post["description"], canonical, body, base_url, ld)


def all_seo_paths() -> list[str]:
    """Relative URLs for the sitemap."""
    paths = ["/blog"]
    paths += [f"/blog/{p['slug']}" for p in BLOG_POSTS]
    paths += [f"/categories/{s}" for s in ROLE_SLUGS.values()]
    return paths
