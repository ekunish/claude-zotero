#!/usr/bin/env python3
"""Fetch abstracts for all papers in a Zotero collection, translate to Japanese, and add as notes."""

import json
import os
import subprocess
import time
import uuid
import urllib.parse
import anthropic

API_KEY = os.environ["ZOTERO_API_KEY"]
USER_ID = os.environ["ZOTERO_USER_ID"]
BASE = f"https://api.zotero.org/users/{USER_ID}"
COLLECTION_KEY = os.environ.get("ZOTERO_COLLECTION_KEY", "Y3U7B48A")

client = anthropic.Anthropic()


def api_get(path):
    r = subprocess.run(
        ["curl", "-s", "-H", f"Zotero-API-Key: {API_KEY}", f"{BASE}{path}"],
        capture_output=True, text=True,
    )
    try:
        return json.loads(r.stdout) if r.stdout.strip() else None
    except json.JSONDecodeError:
        return None


def api_post(path, data):
    token = uuid.uuid4().hex[:32]
    r = subprocess.run(
        ["curl", "-s", "-X", "POST", f"{BASE}{path}",
         "-H", f"Zotero-API-Key: {API_KEY}",
         "-H", "Content-Type: application/json",
         "-H", f"Zotero-Write-Token: {token}",
         "-d", json.dumps(data)],
        capture_output=True, text=True,
    )
    try:
        return json.loads(r.stdout) if r.stdout.strip() else None
    except json.JSONDecodeError:
        return None


def fetch_abstract_crossref(doi):
    """Fetch abstract from CrossRef by DOI."""
    if not doi:
        return None
    r = subprocess.run(
        ["curl", "-s", f"https://api.crossref.org/works/{doi}"],
        capture_output=True, text=True, timeout=15,
    )
    try:
        data = json.loads(r.stdout).get("message", {})
        abstract = data.get("abstract", "")
        # Strip JATS XML tags
        for tag in ["<jats:p>", "</jats:p>", "<jats:italic>", "</jats:italic>",
                     "<jats:bold>", "</jats:bold>", "<jats:sup>", "</jats:sup>",
                     "<jats:sub>", "</jats:sub>", "<jats:title>", "</jats:title>",
                     "<jats:sec>", "</jats:sec>"]:
            abstract = abstract.replace(tag, "")
        return abstract.strip() if abstract.strip() else None
    except Exception:
        return None


def fetch_abstract_semantic_scholar(title):
    """Fetch abstract from Semantic Scholar by title."""
    q = urllib.parse.quote(title[:200])
    r = subprocess.run(
        ["curl", "-s", f"https://api.semanticscholar.org/graph/v1/paper/search?query={q}&limit=1&fields=abstract"],
        capture_output=True, text=True, timeout=15,
    )
    try:
        data = json.loads(r.stdout)
        papers = data.get("data", [])
        if papers and papers[0].get("abstract"):
            return papers[0]["abstract"]
    except Exception:
        pass
    return None


def translate_to_japanese(text, title):
    """Translate abstract to Japanese using Claude API."""
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": f"以下の論文のアブストラクトを日本語に翻訳してください。翻訳のみを出力し、余計な説明は不要です。\n\n論文タイトル: {title}\n\nAbstract:\n{text}",
        }],
    )
    return resp.content[0].text


def get_existing_notes(item_key):
    """Check if item already has an abstract translation note."""
    children = api_get(f"/items/{item_key}/children?format=json")
    if not children:
        return []
    return [c for c in children if c["data"]["itemType"] == "note"
            and "アブストラクト" in c["data"].get("note", "")]


def add_note(parent_key, note_html):
    data = [{
        "itemType": "note",
        "parentItem": parent_key,
        "note": note_html,
        "tags": [{"tag": "abstract-ja"}],
    }]
    api_post("/items", data)


def get_all_papers():
    """Get all papers in the collection and subcollections."""
    colls = api_get("/collections?format=json&limit=100")
    sub_keys = [COLLECTION_KEY]
    if colls:
        for c in colls:
            if c["data"].get("parentCollection") == COLLECTION_KEY:
                sub_keys.append(c["data"]["key"])

    all_items = []
    seen = set()
    for ck in sub_keys:
        start = 0
        while True:
            items = api_get(f"/collections/{ck}/items?format=json&limit=100&start={start}")
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
            print(f"  -> SKIP (already has translation)")
            skipped += 1
            continue

        # Fetch abstract if missing
        if not abstract:
            print(f"  -> Fetching abstract from CrossRef...", end=" ")
            abstract = fetch_abstract_crossref(doi)
            if not abstract:
                print("trying Semantic Scholar...", end=" ")
                abstract = fetch_abstract_semantic_scholar(title)
            if abstract:
                print("OK")
            else:
                print("NOT FOUND")
                no_abstract.append(f"{paper.get('creators', [{}])[0].get('lastName', '?')} ({paper.get('date', '?')}): {title}")
                continue

        # Translate
        print(f"  -> Translating...", end=" ")
        try:
            translation = translate_to_japanese(abstract, title)
            print("OK")
        except Exception as e:
            print(f"FAILED ({e})")
            continue

        # Add note
        note_html = (
            f"<h2>アブストラクト（日本語訳）</h2>"
            f"<p>{translation}</p>"
            f"<hr/>"
            f"<h2>Original Abstract</h2>"
            f"<p>{abstract}</p>"
        )
        add_note(key, note_html)
        success += 1
        print(f"  -> Note added")
        time.sleep(0.3)

    print(f"\n{'='*60}")
    print(f"DONE: {success} translated, {skipped} skipped, {len(no_abstract)} no abstract")
    if no_abstract:
        print(f"\nNo abstract found:")
        for p in no_abstract:
            print(f"  - {p}")


if __name__ == "__main__":
    main()
