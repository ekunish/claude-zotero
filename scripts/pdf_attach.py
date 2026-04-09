#!/usr/bin/env python3
"""Fetch open-access PDFs via Unpaywall and attach them to Zotero items.

Called after DOI import via REST API. Requires ZOTERO_API_KEY, ZOTERO_USER_ID,
and UNPAYWALL_EMAIL environment variables.

Usage:
    echo "ITEM_KEY DOI" | uv run --project . python3 pdf_attach.py
    echo "ITEM_KEY1 DOI1\nITEM_KEY2 DOI2" | uv run --project . python3 pdf_attach.py
"""

import os
import sys
import tempfile
import urllib.parse
import urllib.request

from zotero_api import (
    api_post, http_get_json, is_error, upload_file_to_item, _HTTP_USER_AGENT,
)

UNPAYWALL_EMAIL = os.environ.get("UNPAYWALL_EMAIL", "")


def find_oa_pdf_url(doi: str) -> str | None:
    """Query Unpaywall for an open-access PDF URL. Returns URL or None."""
    if not UNPAYWALL_EMAIL:
        print("UNPAYWALL_EMAIL not set, skipping PDF lookup", file=sys.stderr)
        return None

    encoded_doi = urllib.parse.quote(doi, safe="")
    data = http_get_json(
        f"https://api.unpaywall.org/v2/{encoded_doi}?email={urllib.parse.quote(UNPAYWALL_EMAIL)}"
    )
    if not data or not data.get("is_oa"):
        return None

    loc = data.get("best_oa_location") or {}
    return loc.get("url_for_pdf") or None


def download_pdf(url: str, dest: str) -> bool:
    """Download a PDF from URL to dest path. Returns True on success."""
    req = urllib.request.Request(url, headers={"User-Agent": _HTTP_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "pdf" not in content_type and not url.endswith(".pdf"):
                print(f"  Warning: response is {content_type}, may not be PDF", file=sys.stderr)
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(8192)
                    if not chunk:
                        break
                    f.write(chunk)
        return os.path.getsize(dest) > 0
    except urllib.error.URLError as e:
        print(f"  PDF download failed: {e}", file=sys.stderr)
        return False


def create_attachment_item(parent_key: str, filename: str) -> str | None:
    """Create a child attachment item in Zotero. Returns the new item key or None."""
    data = [{
        "itemType": "attachment",
        "parentItem": parent_key,
        "linkMode": "imported_file",
        "title": filename,
        "contentType": "application/pdf",
        "filename": filename,
    }]
    result = api_post("/items", data)
    if is_error(result) or not isinstance(result, dict):
        print(f"  Failed to create attachment item: {result}", file=sys.stderr)
        return None
    successful = result.get("successful", {})
    if successful:
        first = next(iter(successful.values()))
        return first.get("key") or first.get("data", {}).get("key")
    failed = result.get("failed", {})
    if failed:
        for k, err in failed.items():
            print(f"  Attachment creation failed: {err.get('message', err)}", file=sys.stderr)
    return None


def attach_pdf(parent_key: str, doi: str) -> bool:
    """Find OA PDF for a DOI and attach to a Zotero item. Returns True on success."""
    pdf_url = find_oa_pdf_url(doi)
    if not pdf_url:
        return False

    print(f"  PDF found: {pdf_url[:80]}")

    # Download to temp file
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        if not download_pdf(pdf_url, tmp_path):
            return False

        # Create attachment item
        filename = f"{doi.replace('/', '_')}.pdf"
        att_key = create_attachment_item(parent_key, filename)
        if not att_key:
            return False

        # Upload the file
        if upload_file_to_item(att_key, tmp_path, filename, "application/pdf"):
            print("  PDF attached")
            return True
        return False
    finally:
        os.unlink(tmp_path)


def main():
    """Read 'ITEM_KEY DOI' lines from stdin and attach PDFs."""
    if not UNPAYWALL_EMAIL:
        print("Error: UNPAYWALL_EMAIL environment variable is not set.", file=sys.stderr)
        print("Get one at https://unpaywall.org/products/api (any valid email works)", file=sys.stderr)
        sys.exit(1)

    lines = sys.stdin.read().strip().splitlines()
    if not lines:
        print("Error: no input. Expected 'ITEM_KEY DOI' per line.", file=sys.stderr)
        sys.exit(1)

    success = 0
    for line in lines:
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        item_key, doi = parts
        print(f"Looking up PDF for {doi}...")
        if attach_pdf(item_key, doi):
            success += 1

    print(f"\nDone: {success}/{len(lines)} PDFs attached")


if __name__ == "__main__":
    main()
