#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from html import escape
from pathlib import Path
from urllib.parse import urlparse

import requests

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"]
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "betstorm-data")
BETSTORM_GITHUB_TOKEN = os.environ["BETSTORM_GITHUB_TOKEN"]

SITE_URL = os.getenv("BETSTORM_SITE_URL", "https://betstorm.it").rstrip("/")
REPO = os.getenv("BETSTORM_SITE_REPO", "Giuseppe83-sys/BETSTORM")
BRANCH = os.getenv("BETSTORM_SITE_BRANCH", "main")
ARTICLES_PATH = os.getenv("BETSTORM_ARTICLES_PATH", "articles.json")

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
ET.register_namespace("", NS)


def supabase_headers():
    return {
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "apikey": SUPABASE_KEY,
    }


def load_articles():
    url = f"{SUPABASE_URL}/storage/v1/object/{SUPABASE_BUCKET}/{ARTICLES_PATH}"
    r = requests.get(url, headers=supabase_headers(), timeout=60)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError("articles.json deve contenere un array JSON.")
    return data


def valid_article(article):
    return (
        isinstance(article, dict)
        and isinstance(article.get("slug"), str)
        and SLUG_RE.fullmatch(article["slug"]) is not None
        and bool(article.get("title"))
        and bool(article.get("content_html"))
    )


def safe_meta(text, limit):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text[:limit].rstrip()


def article_date(article):
    raw = article.get("target_date") or article.get("generated_at") or ""
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        try:
            return date.fromisoformat(str(raw)[:10]).isoformat()
        except ValueError:
            return datetime.now(timezone.utc).date().isoformat()


def article_html(article):
    slug = article["slug"]
    title = safe_meta(article["title"], 120)
    description = safe_meta(
        article.get("meta_description") or article.get("excerpt") or title, 155
    )
    keyword = safe_meta(article.get("primary_keyword"), 80)
    published = article_date(article)
    canonical = f"{SITE_URL}/blog/{slug}.html"
    content = str(article["content_html"])

    schema = {
        "@context": "https://schema.org",
        "@type": "Article",
        "headline": title,
        "description": description,
        "datePublished": published,
        "dateModified": published,
        "mainEntityOfPage": {"@type": "WebPage", "@id": canonical},
        "publisher": {"@type": "Organization", "name": "BetStorm", "url": SITE_URL},
    }

    return f"""<!doctype html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{escape(title)} | BetStorm</title>
  <meta name="description" content="{escape(description, quote=True)}">
  <link rel="canonical" href="{escape(canonical, quote=True)}">
  <meta property="og:type" content="article">
  <meta property="og:site_name" content="BetStorm">
  <meta property="og:title" content="{escape(title, quote=True)}">
  <meta property="og:description" content="{escape(description, quote=True)}">
  <meta property="og:url" content="{escape(canonical, quote=True)}">
  <meta name="twitter:card" content="summary">
  <script type="application/ld+json">{json.dumps(schema, ensure_ascii=False).replace("</", "<\\/")}</script>
  <style>
    :root{{--bg:#070b10;--card:#0d1218;--text:#f5f7fb;--muted:#9eb0c8;--green:#00e57b;--line:#26303b}}
    *{{box-sizing:border-box}}
    body{{margin:0;background:var(--bg);color:var(--text);font-family:Arial,sans-serif;line-height:1.7}}
    a{{color:var(--green)}} header,main,footer{{max-width:920px;margin:auto;padding:22px}}
    header{{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line)}}
    .brand{{font-weight:800;text-decoration:none;color:var(--text);font-size:1.3rem}}
    article{{margin:38px 0;background:var(--card);border:1px solid var(--line);border-radius:22px;padding:clamp(22px,5vw,48px)}}
    .date,.keyword{{color:var(--green);font-weight:700}} h1{{line-height:1.15;font-size:clamp(2rem,5vw,3.2rem)}}
    h2,h3{{line-height:1.25;margin-top:1.7em}} p,li{{color:#d6deea}} .excerpt{{font-size:1.15rem;color:var(--muted)}}
    .notice{{margin-top:36px;padding-top:22px;border-top:1px solid var(--line);color:var(--muted);font-size:.92rem}}
    footer{{color:var(--muted);font-size:.9rem;border-top:1px solid var(--line)}}
  </style>
</head>
<body>
<header>
  <a class="brand" href="/">BETSTORM</a>
  <nav><a href="/blog.html">Blog</a> &nbsp; <a href="/dashboard.html">Dashboard</a></nav>
</header>
<main>
  <article>
    <div class="date">{escape(published)}</div>
    <h1>{escape(title)}</h1>
    <p class="excerpt">{escape(description)}</p>
    {f'<p class="keyword">{escape(keyword)}</p>' if keyword else ''}
    <div class="content">{content}</div>
    <div class="notice">18+ | Il gioco può causare dipendenza. Gioca responsabilmente. I contenuti hanno finalità informative e non garantiscono vincite.</div>
  </article>
</main>
<footer>© BetStorm · <a href="/privacy.html">Privacy</a> · <a href="/termini.html">Termini</a></footer>
</body>
</html>
"""


def run(cmd, cwd=None, env=None):
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def update_sitemap(repo_dir, article_urls):
    sitemap = repo_dir / "sitemap.xml"
    if sitemap.exists():
        tree = ET.parse(sitemap)
        root = tree.getroot()
    else:
        root = ET.Element(f"{{{NS}}}urlset")
        tree = ET.ElementTree(root)

    existing = {}
    for node in root.findall(f"{{{NS}}}url"):
        loc = node.find(f"{{{NS}}}loc")
        if loc is not None and loc.text:
            existing[loc.text.strip()] = node

    today = datetime.now(timezone.utc).date().isoformat()

    def ensure_url(loc, lastmod, changefreq, priority):
        node = existing.get(loc)
        if node is None:
            node = ET.SubElement(root, f"{{{NS}}}url")
            ET.SubElement(node, f"{{{NS}}}loc").text = loc
            existing[loc] = node

        fields = {
            "lastmod": lastmod,
            "changefreq": changefreq,
            "priority": priority,
        }
        for tag, value in fields.items():
            child = node.find(f"{{{NS}}}{tag}")
            if child is None:
                child = ET.SubElement(node, f"{{{NS}}}{tag}")
            child.text = value

    ensure_url(f"{SITE_URL}/blog.html", today, "daily", "0.8")
    for loc, lastmod in article_urls:
        ensure_url(loc, lastmod, "monthly", "0.7")

    ET.indent(tree, space="  ")
    tree.write(sitemap, encoding="utf-8", xml_declaration=True)


def main():
    articles = [a for a in load_articles() if valid_article(a)]
    if not articles:
        print('{"status":"no_valid_articles"}')
        return

    # Pubblica tutti gli articoli validi presenti in articles.json.
    # In questo modo, se una pagina manca dal repo, viene ripristinata.
    with tempfile.TemporaryDirectory(prefix="betstorm-site-") as tmp:
        repo_dir = Path(tmp) / "BETSTORM"
        clone_url = f"https://x-access-token:{BETSTORM_GITHUB_TOKEN}@github.com/{REPO}.git"

        run(["git", "clone", "--depth", "1", "--branch", BRANCH, clone_url, str(repo_dir)])

        blog_dir = repo_dir / "blog"
        blog_dir.mkdir(parents=True, exist_ok=True)

        urls = []
        created_or_updated = 0

        for article in articles:
            slug = article["slug"]
            target = blog_dir / f"{slug}.html"
            html = article_html(article)
            old = target.read_text(encoding="utf-8") if target.exists() else None
            if old != html:
                target.write_text(html, encoding="utf-8")
                created_or_updated += 1
            urls.append((f"{SITE_URL}/blog/{slug}.html", article_date(article)))

        update_sitemap(repo_dir, urls)

        # Non pubblichiamo segreti: il token resta solo nell'URL remoto in memoria.
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        if not status:
            print(json.dumps({"status": "no_changes", "articles": len(articles)}))
            return

        run(["git", "config", "user.name", "BetStorm SEO Publisher"], cwd=repo_dir)
        run(["git", "config", "user.email", "seo-bot@users.noreply.github.com"], cwd=repo_dir)
        run(["git", "add", "blog", "sitemap.xml"], cwd=repo_dir)
        run(["git", "commit", "-m", "SEO: publish static BetStorm articles"], cwd=repo_dir)

        push_env = os.environ.copy()
        push_env["GIT_TERMINAL_PROMPT"] = "0"
        run(["git", "push", "origin", f"HEAD:{BRANCH}"], cwd=repo_dir, env=push_env)

        print(json.dumps({
            "status": "published",
            "articles": len(articles),
            "pages_changed": created_or_updated,
            "repo": REPO,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
