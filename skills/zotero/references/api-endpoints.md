# Zotero API Reference

## Local Connector API (Port 23119)

These endpoints are available when Zotero desktop is running locally.

### Ping
```
POST http://localhost:23119/connector/ping
```
Returns 200 if Zotero is running.

### Import
```
POST http://localhost:23119/connector/import
Content-Type: application/x-bibtex
```
Body: BibTeX or RIS formatted text. Returns 201 on success.

### Get Selected Collection
```
POST http://localhost:23119/connector/getSelectedCollection
```
Returns JSON with `id`, `name` of the currently selected collection.

## Local Read-Only API (Port 23119)

### Items
```
GET http://localhost:23119/api/users/0/items?format=json
GET http://localhost:23119/api/users/0/items?q=<query>&format=json
GET http://localhost:23119/api/users/0/items?tag=<tag>&format=json
GET http://localhost:23119/api/users/0/items?collection=<collKey>&format=json
GET http://localhost:23119/api/users/0/items/<itemKey>?format=json
```

Query parameters:
- `q` — Quick search (searches title, creator, year)
- `qmode` — `titleCreatorYear` (default) or `everything`
- `tag` — Filter by tag (use `||` for OR, prefix `-` for NOT)
- `collection` — Filter by collection key
- `itemType` — Filter by type: `journalArticle`, `book`, `conferencePaper`, etc.
- `sort` — `dateAdded`, `dateModified`, `title`, `creator`, `date`
- `direction` — `asc` or `desc`
- `limit` — Max results (default 25, max 100)
- `start` — Offset for pagination
- `format` — `json`, `bibtex`, `ris`, `csljson`

### Collections
```
GET http://localhost:23119/api/users/0/collections?format=json
GET http://localhost:23119/api/users/0/collections/<collKey>?format=json
GET http://localhost:23119/api/users/0/collections/<collKey>/items?format=json
```

### Tags
```
GET http://localhost:23119/api/users/0/tags?format=json
```

### Searches
```
GET http://localhost:23119/api/users/0/searches?format=json
```

## REST API v3 (api.zotero.org)

Base URL: `https://api.zotero.org`

### Authentication
All requests require: `Zotero-API-Key: {key}`

### Items
```
GET    /users/{userID}/items?format=json
GET    /users/{userID}/items/<itemKey>?format=json
POST   /users/{userID}/items                          # Create (body: JSON array, max 50)
PATCH  /users/{userID}/items/<itemKey>                 # Update (requires If-Unmodified-Since-Version)
DELETE /users/{userID}/items/<itemKey>                  # Delete (requires If-Unmodified-Since-Version)
```

### Collections
```
GET    /users/{userID}/collections?format=json
POST   /users/{userID}/collections                     # Create
PATCH  /users/{userID}/collections/<collKey>            # Update
DELETE /users/{userID}/collections/<collKey>            # Delete
```

### Item Types
Common `itemType` values:
- `journalArticle` — Journal paper
- `book` — Book
- `bookSection` — Book chapter
- `conferencePaper` — Conference paper
- `thesis` — Thesis/dissertation
- `report` — Report
- `webpage` — Web page
- `preprint` — Preprint

### Item Template
To get a blank template for creating items:
```
GET https://api.zotero.org/items/new?itemType=journalArticle
```

### Write Operations

**Create items:**
```bash
curl -X POST "https://api.zotero.org/users/$ZOTERO_USER_ID/items" \
  -H "Zotero-API-Key: $ZOTERO_API_KEY" \
  -H "Content-Type: application/json" \
  -H "Zotero-Write-Token: $(uuidgen)" \
  -d '[{"itemType":"journalArticle","title":"...","creators":[{"creatorType":"author","firstName":"...","lastName":"..."}]}]'
```

**Update item:**
```bash
curl -X PATCH "https://api.zotero.org/users/$ZOTERO_USER_ID/items/<key>" \
  -H "Zotero-API-Key: $ZOTERO_API_KEY" \
  -H "Content-Type: application/json" \
  -H "If-Unmodified-Since-Version: <version>" \
  -d '{"title":"Updated Title"}'
```

### Rate Limiting
- Check `Backoff` header in responses
- If present, wait that many seconds before next request
- Also check `Retry-After` for 429 responses

## DOI Resolution

### doi.org Content Negotiation
```bash
curl -sL -H "Accept: application/x-bibtex" "https://doi.org/<DOI>"
```

### CrossRef Fallback
```bash
curl -s "https://api.crossref.org/works/<DOI>" | python3 -m json.tool
```
