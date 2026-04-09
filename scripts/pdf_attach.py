#!/usr/bin/env python3
"""Fetch open-access PDFs and attach them to Zotero items.

Two modes:
  --pdf-url URL --item-key KEY   Download PDF from URL directly
  --doi DOI --item-key KEY       Resolve PDF URL via arXiv/Unpaywall, then download

Requires ZOTERO_API_KEY and ZOTERO_USER_ID.
UNPAYWALL_EMAIL is required for --doi mode.

Usage:
    uv run --project . python3 pdf_attach.py --item-key KEY --pdf-url "https://arxiv.org/pdf/1706.03762.pdf"
    uv run --project . python3 pdf_attach.py --item-key KEY --doi "10.48550/arXiv.1706.03762"
"""

import argparse
import os
import sys
import tempfile
import urllib.parse
import urllib.request

from zotero_api import (
    api_post, http_get_json, is_error, upload_file_to_item, _HTTP_USER_AGENT,
)

UNPAYWALL_EMAIL = os.environ.get("UNPAYWALL_EMAIL", "")


# --- PDF URL Resolution ---

def find_arxiv_pdf_url(doi: str) -> str | None:
    """If DOI is an arXiv DOI, return the direct PDF URL."""
    doi_lower = doi.lower()
    if doi_lower.startswith("10.48550/arxiv."):
        arxiv_id = doi[len("10.48550/arxiv."):]  # preserve original case of ID
        return f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    return None


def find_unpaywall_pdf_url(doi: str) -> str | None:
    """Query Unpaywall for an open-access PDF URL."""
    if not UNPAYWALL_EMAIL:
        return None

    encoded_doi = urllib.parse.quote(doi, safe="")
    data = http_get_json(
        f"https://api.unpaywall.org/v2/{encoded_doi}?email={urllib.parse.quote(UNPAYWALL_EMAIL)}"
    )
    if not data or not data.get("is_oa"):
        return None

    # Try best_oa_location first
    best = data.get("best_oa_location") or {}
    if best.get("url_for_pdf"):
        return best["url_for_pdf"]

    # Fall back to any oa_location with a PDF URL
    for loc in data.get("oa_locations", []):
        if loc.get("url_for_pdf"):
            return loc["url_for_pdf"]

    return None


def resolve_pdf_url(doi: str) -> str | None:
    """Resolve a DOI to a PDF URL. Tries arXiv pattern match, then Unpaywall."""
    # 1. arXiv pattern match (no API call)
    url = find_arxiv_pdf_url(doi)
    if url:
        print(f"  PDF source: arXiv", file=sys.stderr)
        return url

    # 2. Unpaywall
    url = find_unpaywall_pdf_url(doi)
    if url:
        print(f"  PDF source: Unpaywall", file=sys.stderr)
        return url

    print(f"  No open-access PDF found for {doi}", file=sys.stderr)
    return None


# --- Download and Attach ---

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


def attach_pdf_from_url(item_key: str, pdf_url: str) -> bool:
    """Download a PDF from URL and attach to a Zotero item."""
    print(f"  PDF URL: {pdf_url[:80]}")

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        if not download_pdf(pdf_url, tmp_path):
            return False

        filename = pdf_url.rsplit("/", 1)[-1].split("?")[0]
        if not filename.endswith(".pdf"):
            filename += ".pdf"
        # Sanitize filename
        filename = filename.replace("/", "_")[:100]

        att_key = create_attachment_item(item_key, filename)
        if not att_key:
            return False

        if upload_file_to_item(att_key, tmp_path, filename, "application/pdf"):
            print("  PDF attached")
            return True
        return False
    finally:
        os.unlink(tmp_path)


def attach_pdf(item_key: str, doi: str) -> bool:
    """Resolve PDF URL from DOI and attach to a Zotero item."""
    pdf_url = resolve_pdf_url(doi)
    if not pdf_url:
        return False
    return attach_pdf_from_url(item_key, pdf_url)


# --- CLI ---

def main():
    parser = argparse.ArgumentParser(description="Attach PDFs to Zotero items")
    parser.add_argument("--item-key", required=True, help="Zotero item key")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pdf-url", help="Direct PDF URL to download")
    group.add_argument("--doi", help="DOI to resolve via arXiv/Unpaywall")
    args = parser.parse_args()

    if args.doi and not UNPAYWALL_EMAIL:
        print("Warning: UNPAYWALL_EMAIL not set. Only arXiv DOIs will resolve.", file=sys.stderr)

    if args.pdf_url:
        ok = attach_pdf_from_url(args.item_key, args.pdf_url)
    else:
        ok = attach_pdf(args.item_key, args.doi)

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
