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
curl -s "http://localhost:23119/api/users/0/items?q=machine+learning&format=json" | uv run --project "${CLAUDE_PLUGIN_ROOT}/scripts" python3 -m json.tool

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

### List collections
```bash
curl -s "http://localhost:23119/api/users/0/collections?format=json" | \
  python3 -c "import sys,json; [print(f'{c[\"data\"][\"key\"]}: {c[\"data\"][\"name\"]}') for c in json.load(sys.stdin)]"
```

### Read PDF content of a paper
```bash
# 1. Find PDF attachment for an item (get child items)
curl -s -H "Zotero-API-Key: $ZOTERO_API_KEY" \
  "https://api.zotero.org/users/$ZOTERO_USER_ID/items/<parentItemKey>/children?format=json" | \
  python3 -c "import sys,json; [print(f'{i[\"data\"][\"key\"]}: {i[\"data\"].get(\"filename\",\"\")}') for i in json.load(sys.stdin) if i['data'].get('contentType')=='application/pdf']"

# 2. Download and extract text
curl -sL -H "Zotero-API-Key: $ZOTERO_API_KEY" \
  "https://api.zotero.org/users/$ZOTERO_USER_ID/items/<attachmentKey>/file" \
  -o /tmp/zotero_paper.pdf && pdftotext /tmp/zotero_paper.pdf -
```

Alternatively, search for PDF attachments directly:
```bash
curl -s -H "Zotero-API-Key: $ZOTERO_API_KEY" \
  "https://api.zotero.org/users/$ZOTERO_USER_ID/items?itemType=attachment&q=<query>&format=json&limit=5"
```

**Important:** Always clean up temp files after reading: `rm -f /tmp/zotero_paper.pdf`

### Add a note to an item
Use this to attach summaries, annotations, or memos to papers. Notes support HTML formatting.
```bash
WRITE_TOKEN=$(openssl rand -hex 16)
curl -s -X POST "https://api.zotero.org/users/$ZOTERO_USER_ID/items" \
  -H "Zotero-API-Key: $ZOTERO_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Zotero-Write-Token: $WRITE_TOKEN" \
  -d '[{
    "itemType": "note",
    "parentItem": "<parentItemKey>",
    "note": "<h1>Title</h1><p>Content in HTML</p>",
    "tags": [{"tag": "AI-generated-summary"}]
  }]'
```

When the user asks to summarize a paper and save it:
1. Search for the item and get its key
2. Download and read the PDF (see above)
3. Generate a detailed summary
4. Add the summary as a child note with the `AI-generated-summary` tag

### Translate abstracts
Use the translation script at `${CLAUDE_PLUGIN_ROOT}/scripts/translate_abstracts.py`:
```bash
ZOTERO_COLLECTION_KEY="<collectionKey>" uv run --project "${CLAUDE_PLUGIN_ROOT}/scripts" python3 "${CLAUDE_PLUGIN_ROOT}/scripts/translate_abstracts.py"
```

**Required environment variables:**
- `ZOTERO_API_KEY` — Zotero API key
- `ZOTERO_USER_ID` — Zotero user ID
- `ZOTERO_COLLECTION_KEY` — Target collection key (use "List collections" to find it)
- `ANTHROPIC_API_KEY` — Anthropic API key for Claude translation

**Optional environment variables:**
- `ZOTERO_TRANSLATE_LANG` — Target language (default: `Japanese`). Examples: `Chinese`, `Korean`, `French`, `German`, `Spanish`
- `ZOTERO_TRANSLATE_MODEL` — Claude model to use (default: `claude-haiku-4-5-20251001`)

The script:
1. Fetches all papers in the collection (including subcollections, recursively)
2. For papers without abstracts, tries CrossRef then Semantic Scholar
3. Translates each abstract to the target language using Claude
4. Saves the translation as a child note with a language-specific tag (e.g. `abstract-ja`, `abstract-zh`)
5. Skips papers that already have a translation note (identified by the tag)

### Export BibTeX
```bash
# Single item
curl -s -H "Accept: application/x-bibtex" \
  "http://localhost:23119/api/users/0/items/<itemKey>?format=bibtex"

# All items in a collection
curl -s "http://localhost:23119/api/users/0/collections/<collKey>/items?format=bibtex"
```

## Decision Logic

1. **When DOI is known, always use `zotero_import.sh --dois`** — imports via BibTeX, preserving DOI and all metadata. The script auto-detects local Zotero vs REST API
2. **When creating items directly via REST API, always include the `DOI` field** — field name is uppercase `"DOI"` (e.g. `"DOI": "10.1038/s41586-021-03819-2"`). Also set `"url"` to `https://doi.org/...`
3. **Try Local API first** (port 23119) — faster, no auth needed
4. **Fall back to REST API** if local Zotero is not running and credentials are set
5. **Report clearly** if neither is available

## Important Notes

- Python scripts require [`uv`](https://docs.astral.sh/uv/) — all script invocations use `uv run --project "${CLAUDE_PLUGIN_ROOT}/scripts"` to automatically manage the virtual environment and dependencies
- Local API requires Zotero desktop to be running with "Allow other applications to communicate with Zotero" enabled in Settings > Advanced
- REST API has rate limits — respect `Backoff` and `Retry-After` headers
- Batch writes are limited to 50 items per request
- For update/delete operations (PATCH/DELETE) via REST API, include `If-Unmodified-Since-Version` header to prevent conflicts (not needed for POST/create)
- Use unique `Zotero-Write-Token` (UUID) for each write to prevent duplicates

## Reference Documentation

See `${CLAUDE_PLUGIN_ROOT}/skills/zotero/references/api-endpoints.md` for the complete API reference.
