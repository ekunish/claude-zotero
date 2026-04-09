# claude-zotero

Claude Code plugin for Zotero integration. Search, import, and manage academic references directly from Claude Code.

## Features

- **Search your library** — keyword search, tag filtering, collection browsing
- **Import by DOI** — resolve DOIs to BibTeX and import into Zotero
- **Import BibTeX/RIS** — direct file import
- **Export citations** — export as BibTeX, RIS, or CSL JSON
- **Auto PDF attachment** — fetch open-access PDFs via Unpaywall on import
- **Dual API support** — local Zotero API (no auth) + REST API (cloud)

## Installation

```
/plugin marketplace add ekunish/claude-zotero
/plugin install zotero@ekunish/claude-zotero
```

## Setup

### Local API (Recommended)

1. Open Zotero desktop
2. Go to **Settings > Advanced**
3. Enable **"Allow other applications to communicate with Zotero"**

No authentication required. Works immediately.

### REST API (Optional, for cloud access)

Set environment variables:
```bash
export ZOTERO_API_KEY="your_api_key"    # From https://www.zotero.org/settings/keys
export ZOTERO_USER_ID="your_user_id"
```

## Usage Examples

Once installed, Claude Code will automatically detect Zotero-related requests:

- "Zotero で machine learning の論文を検索して"
- "この DOI を Zotero に追加: 10.1038/s41586-021-03819-2"
- "参考文献リストを BibTeX でエクスポートして"
- "Search my Zotero library for papers about transformers"
- "Import these DOIs into my Zotero collection"

## Requirements

- Zotero 7+ (with local API enabled)
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) — Python package manager (manages dependencies automatically)
- `curl` — for DOI resolution and local Zotero connector
- Python 3.12+ (managed by `uv`)

### Optional

- `UNPAYWALL_EMAIL` — your email address for Unpaywall API (enables automatic open-access PDF attachment on DOI import)
- `ANTHROPIC_API_KEY` — for translating abstracts via Claude API
- `ZOTERO_TRANSLATE_LANG` — translation target language (default: Japanese)

## License

MIT
