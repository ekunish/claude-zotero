#!/usr/bin/env bash
# zotero_import.sh — Import papers into Zotero via DOI or BibTeX
#
# Usage:
#   zotero_import.sh --dois "10.1038/xxx,10.2196/yyy"
#   zotero_import.sh --file dois.txt
#   zotero_import.sh --bibtex references.bib
#
# DOI formats accepted: 10.xxx/yyy, doi:10.xxx/yyy, https://doi.org/10.xxx/yyy
# Local connector: items go into the currently selected collection in Zotero.
# REST API: items go into "My Library" (unfiled).

set -euo pipefail

ZOTERO_URL="http://localhost:23119"
SESSION_ID="import-$(date +%s)-$(openssl rand -hex 4)"
TMPDIR_WORK=$(mktemp -d)
trap 'rm -rf "$TMPDIR_WORK"' EXIT
DOIS=""
DOI_FILE=""
BIBTEX_FILE=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dois)   [[ $# -ge 2 ]] || { echo "Error: $1 requires an argument"; exit 1; }; DOIS="$2"; shift 2 ;;
        --file)   [[ $# -ge 2 ]] || { echo "Error: $1 requires an argument"; exit 1; }; DOI_FILE="$2"; shift 2 ;;
        --bibtex) [[ $# -ge 2 ]] || { echo "Error: $1 requires an argument"; exit 1; }; BIBTEX_FILE="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: $0 [--dois DOI1,DOI2] [--file dois.txt] [--bibtex refs.bib]"
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Check Zotero is running; fall back to REST API if not
USE_REST_API=false
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if curl -sf "$ZOTERO_URL/connector/ping" > /dev/null 2>&1; then
    echo "Zotero is running (local connector)."
elif [[ -n "${ZOTERO_API_KEY:-}" && -n "${ZOTERO_USER_ID:-}" ]]; then
    echo "Zotero not running locally. Using REST API."
    USE_REST_API=true
else
    echo "Error: Zotero is not running and REST API credentials (ZOTERO_API_KEY, ZOTERO_USER_ID) are not set."
    echo "Either start Zotero or set ZOTERO_API_KEY and ZOTERO_USER_ID environment variables."
    exit 1
fi

# Import BibTeX via local connector
import_bibtex_local() {
    local bibtex="$1"
    local status
    status=$(printf '%s' "$bibtex" | curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$ZOTERO_URL/connector/import" \
        -H "Content-Type: application/x-bibtex" \
        -H "X-Zotero-Session-ID: $SESSION_ID" \
        --data-binary @-)
    [[ "$status" == "201" ]]
}

# Import BibTeX via REST API (converts to Zotero JSON internally)
import_bibtex_rest() {
    local bibtex="$1"
    echo "$bibtex" | uv run --project "$SCRIPT_DIR" python3 "$SCRIPT_DIR/zotero_rest_import.py"
}

# Import BibTeX using available method
import_bibtex() {
    if [[ "$USE_REST_API" == "true" ]]; then
        import_bibtex_rest "$1"
    else
        import_bibtex_local "$1"
    fi
}

# Normalize DOI: strip prefixes, URLs, and whitespace
normalize_doi() {
    local doi="$1"
    doi=$(echo "$doi" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')  # trim whitespace
    doi=${doi#doi:}             # strip doi: prefix
    doi=${doi#DOI:}             # strip DOI: prefix
    doi=${doi#https://doi.org/} # strip URL prefix
    doi=${doi#http://doi.org/}
    doi=${doi#https://dx.doi.org/}
    doi=${doi#http://dx.doi.org/}
    echo "$doi"
}

# Validate BibTeX response (doi.org/CrossRef return leading whitespace)
is_valid_bibtex() {
    local text="$1"
    local trimmed
    trimmed=$(echo "$text" | sed 's/^[[:space:]]*//')
    [[ "$trimmed" == @* ]]
}

# Resolve DOI to BibTeX
# URL-encode a DOI for safe use in HTTP requests
urlencode_doi() {
    uv run --project "$SCRIPT_DIR" python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$1"
}

resolve_doi() {
    local doi
    doi=$(normalize_doi "$1")
    local encoded_doi
    encoded_doi=$(urlencode_doi "$doi")
    local bibtex http_code
    local tmpfile="$TMPDIR_WORK/resolve_doi.tmp"

    # Try doi.org content negotiation
    http_code=$(curl -sL -o "$tmpfile" -w "%{http_code}" \
        -H "Accept: application/x-bibtex" "https://doi.org/$encoded_doi" 2>/dev/null)
    if [[ "$http_code" == "200" ]]; then
        bibtex=$(cat "$tmpfile")
        if is_valid_bibtex "$bibtex"; then
            echo "$bibtex"
            return 0
        fi
    fi

    # Fallback: try CrossRef
    http_code=$(curl -sL -o "$tmpfile" -w "%{http_code}" \
        "https://api.crossref.org/works/$encoded_doi/transform/application/x-bibtex" 2>/dev/null)
    if [[ "$http_code" == "200" ]]; then
        bibtex=$(cat "$tmpfile")
        if is_valid_bibtex "$bibtex"; then
            echo "$bibtex"
            return 0
        fi
    fi

    echo "DOI resolution failed: doi=$doi, last HTTP status=$http_code" >&2
    return 1
}

# Import from BibTeX file
if [[ -n "$BIBTEX_FILE" ]]; then
    if [[ ! -f "$BIBTEX_FILE" ]]; then
        echo "Error: File not found: $BIBTEX_FILE"
        exit 1
    fi
    echo "Importing BibTeX from $BIBTEX_FILE..."
    if import_bibtex "$(cat "$BIBTEX_FILE")"; then
        echo "Success: BibTeX imported."
    else
        echo "Error: Failed to import BibTeX."
        exit 1
    fi
    exit 0
fi

# Collect DOIs
doi_list=()
if [[ -n "$DOIS" ]]; then
    IFS=',' read -ra doi_list <<< "$DOIS"
fi
if [[ -n "$DOI_FILE" ]]; then
    if [[ ! -f "$DOI_FILE" ]]; then
        echo "Error: File not found: $DOI_FILE"
        exit 1
    fi
    while IFS= read -r line; do
        line=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')  # trim whitespace
        [[ -n "$line" && "$line" != \#* ]] && doi_list+=("$line")
    done < "$DOI_FILE"
fi

if [[ ${#doi_list[@]} -eq 0 ]]; then
    echo "Error: No DOIs provided. Use --dois, --file, or --bibtex."
    exit 1
fi

# Import DOIs
success=0
fail=0
for doi in "${doi_list[@]}"; do
    doi=$(echo "$doi" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')  # trim
    [[ -z "$doi" ]] && continue
    echo -n "Importing $doi... "
    resolve_err="$TMPDIR_WORK/resolve_err.tmp"
    bibtex=$(resolve_doi "$doi" 2>"$resolve_err") || true
    if [[ -z "$bibtex" ]]; then
        err_detail=$(cat "$resolve_err" 2>/dev/null)
        echo "FAILED (DOI resolution: ${err_detail:-unknown error})"
        fail=$((fail + 1))
        continue
    fi
    if import_bibtex "$bibtex"; then
        echo "OK"
        success=$((success + 1))
    else
        echo "FAILED (import rejected)"
        fail=$((fail + 1))
    fi
    sleep 0.5  # Rate limiting
done

echo ""
echo "Done: $success imported, $fail failed (total: ${#doi_list[@]})"
