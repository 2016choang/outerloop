"""MkDocs hooks for the Outerloop site.

Adds repository files outside docs/ as pages, rewrites relative links so each
one points at a site page or at the file on GitHub, and blocks crawlers while
the site is in staging mode (OUTERLOOP_DOCS_STAGING, off with =false). Links
are rewritten on the parsed Markdown tree, so code is never touched. Links to
missing files are left as written so `mkdocs build --strict` reports them;
MkDocs does not check raw HTML, so the hook reports missing raw-HTML targets
itself.
"""

from __future__ import annotations

import logging
import os
import posixpath
import re
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[2]
GITHUB = "https://github.com/outerloop-science/outerloop"

# Repository file -> page path on the site.
EXTRA_PAGES = {
    "README.md": "overview.md",
    "CHANGELOG.md": "changelog.md",
    "CONTRIBUTING.md": "contributing.md",
    "SECURITY.md": "security.md",
    "RELEASING.md": "releasing.md",
}
REPO_OF_PAGE = {site: repo for repo, site in EXTRA_PAGES.items()}

SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
HTML_TAG = re.compile(r"<[a-zA-Z][^<>]*>")
HTML_ATTR = re.compile(r"""(\b(?:src|srcset|href)\s*=\s*(["']))(.+?)(\2)""")

# Warnings on a "mkdocs." logger fail `mkdocs build --strict`.
log = logging.getLogger("mkdocs.plugins.outerloop")

# repo path -> (site source path, site output URL), or None when not on the site
SiteLookup = Callable[[str], "tuple[str, str] | None"]


class Page(NamedTuple):
    """Where a page lives in the repo and on the site."""

    repo: str
    site: str
    url: str
    lookup: SiteLookup


def resolve(target: str, page: Page, html: bool) -> tuple[str | None, bool]:
    """Return (new target or None to keep it, whether the target is missing).

    Site pages become relative site links (Markdown links from the page's source
    path, raw HTML from its output URL), other repository files become GitHub URLs.
    """
    if SCHEME.match(target) or target.startswith(("#", "/")):
        return None, False
    path, sep, fragment = target.partition("#")
    repo_target = posixpath.normpath(posixpath.join(posixpath.dirname(page.repo), path))
    on_site = page.lookup(repo_target)
    if on_site is not None:
        source, url = on_site
        if html:
            new = posixpath.relpath(url, page.url or ".")
        else:
            new = posixpath.relpath(source, posixpath.dirname(page.site) or ".")
    elif (ROOT / repo_target).exists():
        kind = "tree" if (ROOT / repo_target).is_dir() else "blob"
        new = f"{GITHUB}/{kind}/main/{repo_target}"
    else:
        return None, True
    return f"{new}{sep}{fragment}", False


def rewrite_html(html: str, page: Page) -> str:
    """Rewrite src, srcset and href attributes inside the tags of a raw-HTML chunk."""

    def attr(match: re.Match) -> str:
        new, missing = resolve(match.group(3), page, html=True)
        if missing:
            log.warning(f"Doc file '{page.site}' contains a link to '{match.group(3)}', not found.")
        return match.group(0) if new is None else f"{match.group(1)}{new}{match.group(4)}"

    return HTML_TAG.sub(lambda tag: HTML_ATTR.sub(attr, tag.group(0)), html)


def link_extension(get_page: Callable[[], Page | None]):
    """A Markdown extension that rewrites links before MkDocs validates them."""
    from markdown import Extension
    from markdown.treeprocessors import Treeprocessor

    class Rewrite(Treeprocessor):
        def run(self, root):
            page = get_page()
            if page is None:
                return root
            for element in root.iter():
                key = {"a": "href", "img": "src"}.get(element.tag)
                if key is None:
                    continue
                new, _ = resolve(element.get(key, ""), page, html=False)
                if new is not None:
                    element.set(key, new)
            stash = self.md.htmlStash.rawHtmlBlocks
            for i, block in enumerate(stash):
                if isinstance(block, str):
                    stash[i] = rewrite_html(block, page)
            return root

    class LinkExtension(Extension):
        def extendMarkdown(self, md):
            # After inline parsing (20), before MkDocs resolves links (relpath, 0).
            md.treeprocessors.register(Rewrite(md), "outerloop_links", 10)

    return LinkExtension()


_page: Page | None = None


def on_config(config):
    staging = os.environ.get("OUTERLOOP_DOCS_STAGING", "true")
    config.extra["staging"] = staging.strip().lower() not in ("0", "false", "no", "off", "")
    config.markdown_extensions.append(link_extension(lambda: _page))
    return config


def on_files(files, config):
    from mkdocs.structure.files import File

    for repo_path, site_path in EXTRA_PAGES.items():
        content = (ROOT / repo_path).read_text(encoding="utf-8")
        files.append(File.generated(config, site_path, content=content))
    if config.extra["staging"]:
        files.append(File.generated(config, "robots.txt", content="User-agent: *\nDisallow: /\n"))
    return files


def on_page_markdown(markdown, page, config, files):
    global _page
    site = page.file.src_uri
    repo = REPO_OF_PAGE.get(site, "docs/" + site)
    if site in REPO_OF_PAGE:
        # Generated pages edit their source file, not a docs/ copy.
        page.edit_url = f"{GITHUB}/edit/main/{repo}"

    def lookup(repo_path: str) -> tuple[str, str] | None:
        source = EXTRA_PAGES.get(repo_path)
        if source is None and repo_path.startswith("docs/"):
            source = repo_path[5:]
        found = files.get_file_from_path(source) if source else None
        if found is None or found.inclusion.is_excluded():
            return None  # off the site: the link goes to GitHub
        return source, found.url

    # The page renders right after this hook; the link extension reads it then.
    _page = Page(repo, site, page.url, lookup)
    return markdown
