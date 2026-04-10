#!/usr/bin/env python3
"""Import BibTeX into Zotero via REST API.

Reads BibTeX from stdin, converts to Zotero item JSON, and POSTs to the REST API.
Requires ZOTERO_API_KEY and ZOTERO_USER_ID environment variables.

Usage:
    echo "@article{...}" | uv run --project . python3 zotero_rest_import.py
    cat refs.bib | uv run --project . python3 zotero_rest_import.py
"""

import os
import re
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from zotero_api import _HTTP_USER_AGENT, api_post, is_error

BIBTEX_TYPE_MAP = {
    "article": "journalArticle",
    "inproceedings": "conferencePaper",
    "conference": "conferencePaper",
    "book": "book",
    "incollection": "bookSection",
    "inbook": "bookSection",
    "phdthesis": "thesis",
    "mastersthesis": "thesis",
    "techreport": "report",
    "misc": "document",
    "unpublished": "manuscript",
}

# Regex for BibTeX field values: {braced}, "quoted", or bare (e.g. year = 2024)
_FIELD_RE = re.compile(
    r"(\w+)\s*=\s*(?:"
    r"\{((?:[^{}]|\{[^{}]*\})*)\}"  # {braced value} with one level of nesting
    r'|"([^"]*)"'                    # "quoted value"
    r"|(\w+)"                         # bare value (numbers, etc.)
    r")"
)


def split_entries(bibtex: str) -> list[str]:
    """Split a BibTeX string into individual entries."""
    entries = []
    depth = 0
    start = None
    for i, ch in enumerate(bibtex):
        if ch == "@" and depth == 0:
            start = i
        elif ch == "{" and start is not None:
            depth += 1
        elif ch == "}" and start is not None:
            depth -= 1
            if depth == 0:
                entries.append(bibtex[start : i + 1])
                start = None
    return entries


def parse_bibtex(entry: str) -> dict:
    """Parse a single BibTeX entry into a dict of fields."""
    type_match = re.match(r"\s*@(\w+)\s*\{", entry)
    bib_type = type_match.group(1).lower() if type_match else "misc"

    fields = {}
    for m in _FIELD_RE.finditer(entry):
        key = m.group(1).lower()
        value = next((g for g in (m.group(2), m.group(3), m.group(4)) if g is not None), "")
        # Strip inner braces used for capitalization protection
        value = re.sub(r"\{([^{}]*)\}", r"\1", value).strip()
        fields[key] = value

    fields["_type"] = bib_type
    return fields


def _parse_names(name_str: str, creator_type: str) -> list[dict]:
    """Parse a BibTeX name list into Zotero creators with the given type."""
    creators = []
    for name in re.split(r"\s+and\s+", name_str):
        name = name.strip()
        if not name:
            continue
        if "," in name:
            parts = [p.strip() for p in name.split(",", 1)]
            creators.append(
                {"creatorType": creator_type, "lastName": parts[0], "firstName": parts[1]}
            )
        else:
            names = name.rsplit(" ", 1)
            if len(names) == 2:
                creators.append(
                    {"creatorType": creator_type, "firstName": names[0], "lastName": names[1]}
                )
            else:
                creators.append({"creatorType": creator_type, "name": name})
    return creators


# Zotero field name for the publication venue depends on item type
_VENUE_FIELD = {
    "conferencePaper": "proceedingsTitle",
    "bookSection": "bookTitle",
}


def bibtex_to_zotero_item(fields: dict) -> dict:
    """Convert parsed BibTeX fields to a Zotero item dict."""
    bib_type = fields.get("_type", "misc")
    item_type = BIBTEX_TYPE_MAP.get(bib_type, "document")
    venue_field = _VENUE_FIELD.get(item_type, "publicationTitle")

    item = {
        "itemType": item_type,
        "title": fields.get("title", ""),
        "creators": _parse_names(fields.get("author", ""), "author")
                    + _parse_names(fields.get("editor", ""), "editor"),
        "DOI": fields.get("doi", ""),
        "url": fields.get("url", ""),
        "date": fields.get("year", ""),
        venue_field: fields.get("journal", fields.get("booktitle", "")),
        "volume": fields.get("volume", ""),
        "issue": fields.get("number", ""),
        "pages": fields.get("pages", ""),
        "publisher": fields.get("publisher", ""),
        "abstractNote": fields.get("abstract", ""),
    }

    if not item["url"] and item["DOI"]:
        item["url"] = f"https://doi.org/{item['DOI']}"

    return {k: v for k, v in item.items() if v or k == "creators"}


_ARXIV_NS = "{http://www.w3.org/2005/Atom}"


def _extract_arxiv_id(item: dict) -> str | None:
    """Extract arXiv ID from an item's DOI or URL. Returns None if not arXiv."""
    doi = (item.get("DOI") or "").lower()
    if doi.startswith("10.48550/arxiv."):
        return item["DOI"][len("10.48550/arxiv."):]
    url = item.get("url") or ""
    m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", url, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def fetch_arxiv_abstract(arxiv_id: str) -> str | None:
    """Fetch abstract from the arXiv API. Returns None on failure."""
    url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}"
    req = urllib.request.Request(url, headers={"User-Agent": _HTTP_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml_text = resp.read().decode()
    except urllib.error.URLError as e:
        print(f"arXiv API error for {arxiv_id}: {e}", file=sys.stderr)
        return None

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"arXiv XML parse error for {arxiv_id}: {e}", file=sys.stderr)
        return None

    entry = root.find(f"{_ARXIV_NS}entry")
    if entry is None:
        return None
    summary = entry.find(f"{_ARXIV_NS}summary")
    if summary is None or not summary.text:
        return None
    return re.sub(r"\s+", " ", summary.text).strip()


def enrich_arxiv_abstracts(items: list[dict]) -> None:
    """Fill in missing abstracts for arXiv items via the arXiv API (in place)."""
    targets = [i for i in items if not i.get("abstractNote") and _extract_arxiv_id(i)]
    if not targets:
        return
    print(f"Fetching abstracts for {len(targets)} arXiv paper(s)...", file=sys.stderr)
    for idx, item in enumerate(targets):
        if idx > 0:
            time.sleep(3)  # arXiv courtesy pause
        arxiv_id = _extract_arxiv_id(item)
        abstract = fetch_arxiv_abstract(arxiv_id)
        if abstract:
            item["abstractNote"] = abstract


def _post_items(items: list[dict]) -> dict[str, str]:
    """POST items to Zotero. Returns {index: item_key} for successfully created items."""
    created = {}
    for i in range(0, len(items), 50):
        batch = items[i : i + 50]
        result = api_post("/items", batch)
        if is_error(result) or not isinstance(result, dict):
            print(f"API error: {result}", file=sys.stderr)
            continue
        for idx, item_data in result.get("successful", {}).items():
            created[str(i + int(idx))] = item_data.get("key", item_data.get("data", {}).get("key", ""))
        for idx, err in result.get("failed", {}).items():
            print(f"  Failed item {idx}: {err.get('message', err)}", file=sys.stderr)
    return created


def main():
    bibtex = sys.stdin.read().strip()
    if not bibtex:
        print("Error: no BibTeX input", file=sys.stderr)
        sys.exit(1)

    entries = split_entries(bibtex)
    if not entries:
        print("Error: no BibTeX entries found", file=sys.stderr)
        sys.exit(1)

    items = []
    for entry in entries:
        fields = parse_bibtex(entry)
        item = bibtex_to_zotero_item(fields)
        if item.get("title"):
            items.append(item)

    if not items:
        print("Error: could not parse any items from BibTeX", file=sys.stderr)
        sys.exit(1)

    # Enrich arXiv items with abstracts (doi.org BibTeX omits them for arXiv)
    enrich_arxiv_abstracts(items)

    created = _post_items(items)
    if not created:
        sys.exit(1)

    for idx, item_key in created.items():
        item = items[int(idx)]
        doi = item.get("DOI", "N/A")
        print(f"Imported: {item['title'][:60]} (DOI: {doi})")

    # Attempt PDF attachment (arXiv works without UNPAYWALL_EMAIL)
    from pdf_attach import attach_pdf
    for idx, item_key in created.items():
        doi = items[int(idx)].get("DOI", "")
        if doi:
            attach_pdf(item_key, doi)


if __name__ == "__main__":
    main()
