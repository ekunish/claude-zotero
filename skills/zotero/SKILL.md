---
name: zotero
description: >
  Use this skill when the user asks about academic references, papers, citations, bibliography,
  or mentions Zotero, DOI, BibTeX, or reference management. Trigger on keywords like
  "論文", "参考文献", "引用", "Zotero", "DOI", "BibTeX", "文献検索", "ライブラリ",
  "find papers", "import references", "search my library", "cite", "bibliography".
version: 1.0.0
---

# Zotero Integration Skill

You can interact with the user's Zotero library through two APIs:

## API Architecture

### 1. Local API (No Authentication Required)
Zotero must be running locally. Check with:
```bash
curl -s http://localhost:23119/connector/ping
```

**Connector endpoints (read/write):**
- `POST http://localhost:23119/connector/ping` — Check if Zotero is running
- `POST http://localhost:23119/connector/import` — Import BibTeX/RIS data
- `POST http://localhost:23119/connector/getSelectedCollection` — Get active collection

**Read-only endpoints:**
- `GET http://localhost:23119/api/users/0/items?format=json` — List all items
- `GET http://localhost:23119/api/users/0/items?q=<query>&format=json` — Search items
- `GET http://localhost:23119/api/users/0/collections?format=json` — List collections
- `GET http://localhost:23119/api/users/0/tags?format=json` — List tags
- `GET http://localhost:23119/api/users/0/items?tag=<tag>&format=json` — Filter by tag

### 2. REST API (Requires Authentication)
For cloud-based operations when Zotero is not running locally.

**Required environment variables:**
- `ZOTERO_API_KEY` — API key from https://www.zotero.org/settings/keys
- `ZOTERO_USER_ID` — Your Zotero user ID

**Base URL:** `https://api.zotero.org/users/{ZOTERO_USER_ID}/`

**Headers:**
```
Zotero-API-Key: {ZOTERO_API_KEY}
Content-Type: application/json
```

**Endpoints:**
- `GET /users/{id}/items?q=<query>&format=json` — Search items
- `GET /users/{id}/collections?format=json` — List collections
- `GET /users/{id}/items?collection=<key>&format=json` — Items in collection
- `POST /users/{id}/items` — Create items (batch up to 50)
- `PATCH /users/{id}/items/<key>` — Update item (requires `If-Unmodified-Since-Version` header)
- `DELETE /users/{id}/items/<key>` — Delete item

## Common Workflows

### Search the library
```bash
# Local API
curl -s "http://localhost:23119/api/users/0/items?q=machine+learning&format=json" | python3 -m json.tool

# REST API
curl -s -H "Zotero-API-Key: $ZOTERO_API_KEY" \
  "https://api.zotero.org/users/$ZOTERO_USER_ID/items?q=machine+learning&format=json"
```

### Import papers by DOI
Use the import script at `${CLAUDE_PLUGIN_ROOT}/scripts/zotero_import.sh`:
```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/zotero_import.sh" --dois "10.1038/s41586-021-03819-2,10.1126/science.abc4552"
```

Options:
- `--dois "doi1,doi2,..."` — Comma-separated DOIs
- `--file path/to/dois.txt` — File with one DOI per line
- `--bibtex path/to/refs.bib` — Import BibTeX file directly
- `--collection "Name"` — Target collection (default: current selection)

### List collections
```bash
curl -s "http://localhost:23119/api/users/0/collections?format=json" | \
  python3 -c "import sys,json; [print(f'{c[\"data\"][\"key\"]}: {c[\"data\"][\"name\"]}') for c in json.load(sys.stdin)]"
```

### Export BibTeX
```bash
# Single item
curl -s -H "Accept: application/x-bibtex" \
  "http://localhost:23119/api/users/0/items/<itemKey>?format=bibtex"

# All items in a collection
curl -s "http://localhost:23119/api/users/0/collections/<collKey>/items?format=bibtex"
```

## Decision Logic

1. **Always try Local API first** (port 23119) — faster, no auth needed
2. **Fall back to REST API** if local Zotero is not running and credentials are set
3. **Report clearly** if neither is available

## Important Notes

- Local API requires Zotero desktop to be running with "Allow other applications to communicate with Zotero" enabled in Settings > Advanced
- REST API has rate limits — respect `Backoff` and `Retry-After` headers
- Batch writes are limited to 50 items per request
- For write operations via REST API, always include `If-Unmodified-Since-Version` header to prevent conflicts
- Use unique `Zotero-Write-Token` (UUID) for each write to prevent duplicates

## Reference Documentation

See `${CLAUDE_PLUGIN_ROOT}/skills/zotero/references/api-endpoints.md` for the complete API reference.
