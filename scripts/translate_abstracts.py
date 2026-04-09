#!/usr/bin/env python3
"""Fetch abstracts for all papers in a Zotero collection, translate them, and add as notes.

Target language is configurable via ZOTERO_TRANSLATE_LANG (default: Japanese).
"""

import html
import os
import re
import sys
import time
import urllib.parse
import anthropic

from zotero_api import api_get, api_post, http_get_json, is_error

COLLECTION_KEY = os.environ.get("ZOTERO_COLLECTION_KEY", "")
TRANSLATE_MODEL = os.environ.get("ZOTERO_TRANSLATE_MODEL", "claude-haiku-4-5-20251001")
TRANSLATE_LANG = os.environ.get("ZOTERO_TRANSLATE_LANG", "Japanese")

# Tag applied to translation notes, derived from language (e.g. "abstract-ja", "abstract-zh")
_LANG_TAG_MAP = {
    "Japanese": "abstract-ja",
    "Chinese": "abstract-zh",
    "Korean": "abstract-ko",
    "French": "abstract-fr",
    "German": "abstract-de",
    "Spanish": "abstract-es",
}
TRANSLATE_TAG = _LANG_TAG_MAP.get(TRANSLATE_LANG, f"abstract-{TRANSLATE_LANG[:2].lower()}")


def fetch_abstract_crossref(doi):
    """Fetch abstract from CrossRef by DOI."""
    if not doi:
        return None
    data = http_get_json(f"https://api.crossref.org/works/{urllib.parse.quote(doi, safe='')}")
    if not data:
        return None
    abstract = data.get("message", {}).get("abstract", "")
    abstract = re.sub(r"</?jats:[^>]*/?>", "", abstract)
    return abstract.strip() or None


def fetch_abstract_semantic_scholar(title):
    """Fetch abstract from Semantic Scholar by title."""
    q = urllib.parse.quote(title[:200])
    data = http_get_json(
        f"https://api.semanticscholar.org/graph/v1/paper/search?query={q}&limit=1&fields=abstract"
    )
    if not data:
        return None
    papers = data.get("data", [])
    if papers and papers[0].get("abstract"):
        return papers[0]["abstract"]
    return None


def translate_abstract(text, title):
    """Translate abstract to the configured language using Claude API."""
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=TRANSLATE_MODEL,
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": (
                f"Translate the following paper abstract into {TRANSLATE_LANG}. "
                f"Output only the translation, no explanations.\n\n"
                f"Paper title: {title}\n\nAbstract:\n{text}"
            ),
        }],
    )
    if not resp.content:
        return "(translation failed: empty response)"
    return resp.content[0].text


def get_existing_notes(item_key):
    """Check if item already has a translation note (by tag)."""
    children = api_get(f"/items/{item_key}/children?format=json")
    if not children or is_error(children):
        return []
    return [c for c in children if c["data"]["itemType"] == "note"
            and any(t.get("tag") == TRANSLATE_TAG for t in c["data"].get("tags", []))]


def add_note(parent_key, note_html) -> bool:
    """Add a note to an item. Returns True on success."""
    data = [{
        "itemType": "note",
        "parentItem": parent_key,
        "note": note_html,
        "tags": [{"tag": TRANSLATE_TAG}],
    }]
    result = api_post("/items", data)
    if is_error(result) or result is None:
        return False
    if isinstance(result, dict) and result.get("failed"):
        return False
    return True


def _get_all_subcollection_keys(root_key):
    """Recursively collect all subcollection keys under root_key."""
    from collections import deque

    # Paginate to fetch all collections
    all_colls = []
    start = 0
    while True:
        page = api_get(f"/collections?format=json&limit=100&start={start}")
        if is_error(page) or not page:
            break
        all_colls.extend(page)
        start += 100

    if not all_colls:
        return [root_key]

    keys = [root_key]
    seen = {root_key}
    queue = deque([root_key])
    while queue:
        parent = queue.popleft()
        for c in all_colls:
            child_key = c["data"]["key"]
            if c["data"].get("parentCollection") == parent and child_key not in seen:
                keys.append(child_key)
                seen.add(child_key)
                queue.append(child_key)
    return keys


def get_all_papers():
    """Get all papers in the collection and all subcollections (recursive)."""
    sub_keys = _get_all_subcollection_keys(COLLECTION_KEY)

    all_items = []
    seen = set()
    for ck in sub_keys:
        start = 0
        while True:
            items = api_get(f"/collections/{ck}/items?format=json&limit=100&start={start}")
            if is_error(items):
                print(f"  Warning: API error fetching items from collection {ck}, skipping", file=sys.stderr)
                break
            if not items:
                break
            for item in items:
                d = item["data"]
                if d["key"] not in seen and d["itemType"] not in ("note", "attachment"):
                    seen.add(d["key"])
                    all_items.append(d)
            start += 100
    return all_items


def main():
    if not COLLECTION_KEY:
        print("Error: ZOTERO_COLLECTION_KEY environment variable is not set.", file=sys.stderr)
        sys.exit(1)

    print(f"Translating abstracts to {TRANSLATE_LANG} (tag: {TRANSLATE_TAG})")
    papers = get_all_papers()
    print(f"Total papers: {len(papers)}")

    no_abstract = []
    success = 0
    skipped = 0

    for i, paper in enumerate(papers):
        key = paper["key"]
        title = paper.get("title", "(no title)")
        doi = paper.get("DOI", "")
        abstract = paper.get("abstractNote", "").strip()

        print(f"\n[{i+1}/{len(papers)}] {title[:60]}")

        # Check if already has translation note
        existing = get_existing_notes(key)
        if existing:
            print("  -> SKIP (already has translation)")
            skipped += 1
            continue

        # Fetch abstract if missing
        if not abstract:
            print("  -> Fetching abstract from CrossRef...", end=" ")
            abstract = fetch_abstract_crossref(doi)
            if not abstract:
                print("trying Semantic Scholar...", end=" ")
                abstract = fetch_abstract_semantic_scholar(title)
            if abstract:
                print("OK")
            else:
                print("NOT FOUND")
                creators = paper.get("creators") or [{}]
                no_abstract.append(f"{creators[0].get('lastName', '?')} ({paper.get('date', '?')}): {title}")
                continue

        # Translate
        print("  -> Translating...", end=" ")
        try:
            translation = translate_abstract(abstract, title)
            print("OK")
        except Exception as e:
            print(f"FAILED ({e})")
            continue

        # Add note
        note_html = (
            f"<h2>Abstract ({TRANSLATE_LANG})</h2>"
            f"<p>{html.escape(translation)}</p>"
            f"<hr/>"
            f"<h2>Original Abstract</h2>"
            f"<p>{html.escape(abstract)}</p>"
        )
        if add_note(key, note_html):
            success += 1
            print("  -> Note added")
        else:
            print("  -> FAILED (note creation failed)")
        time.sleep(0.3)

    print(f"\n{'='*60}")
    print(f"DONE: {success} translated, {skipped} skipped, {len(no_abstract)} no abstract")
    if no_abstract:
        print("\nNo abstract found:")
        for p in no_abstract:
            print(f"  - {p}")


if __name__ == "__main__":
    main()
