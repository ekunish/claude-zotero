#!/usr/bin/env bash
# zotero_import.sh — Import papers into Zotero via DOI or BibTeX
#
# Usage:
#   zotero_import.sh --dois "10.1038/xxx,10.2196/yyy"
#   zotero_import.sh --file dois.txt
#   zotero_import.sh --bibtex references.bib
#   zotero_import.sh --dois "10.1038/xxx" --collection "My Collection"

set -euo pipefail

ZOTERO_URL="http://localhost:23119"
SESSION_ID="import-$(date +%s)-$(openssl rand -hex 4)"
COLLECTION=""
DOIS=""
DOI_FILE=""
BIBTEX_FILE=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dois)      DOIS="$2"; shift 2 ;;
        --file)      DOI_FILE="$2"; shift 2 ;;
        --bibtex)    BIBTEX_FILE="$2"; shift 2 ;;
        --collection) COLLECTION="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: $0 [--dois DOI1,DOI2] [--file dois.txt] [--bibtex refs.bib] [--collection name]"
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Check Zotero is running
if ! curl -sf "$ZOTERO_URL/connector/ping" > /dev/null 2>&1; then
    echo "Error: Zotero is not running or local API is disabled."
    echo "Please start Zotero and enable: Settings > Advanced > Allow other applications to communicate with Zotero"
    exit 1
fi
echo "Zotero is running."

# Import BibTeX directly
import_bibtex() {
    local bibtex="$1"
    local status
    status=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$ZOTERO_URL/connector/import" \
        -H "Content-Type: application/x-bibtex" \
        -H "X-Zotero-Session-ID: $SESSION_ID" \
        -d "$bibtex")
    if [[ "$status" == "201" ]]; then
        return 0
    else
        return 1
    fi
}

# Resolve DOI to BibTeX
resolve_doi() {
    local doi="$1"
    local bibtex
    # Try doi.org content negotiation
    bibtex=$(curl -sL -H "Accept: application/x-bibtex" "https://doi.org/$doi" 2>/dev/null)
    if [[ "$bibtex" == @* ]]; then
        echo "$bibtex"
        return 0
    fi
    # Fallback: try CrossRef
    local crossref
    crossref=$(curl -s "https://api.crossref.org/works/$doi/transform/application/x-bibtex" 2>/dev/null)
    if [[ "$crossref" == @* ]]; then
        echo "$crossref"
        return 0
    fi
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
        line=$(echo "$line" | xargs)  # trim whitespace
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
    doi=$(echo "$doi" | xargs)  # trim
    echo -n "Importing $doi... "
    bibtex=$(resolve_doi "$doi" 2>/dev/null) || true
    if [[ -z "$bibtex" ]]; then
        echo "FAILED (DOI resolution)"
        ((fail++))
        continue
    fi
    if import_bibtex "$bibtex"; then
        echo "OK"
        ((success++))
    else
        echo "FAILED (Zotero import)"
        ((fail++))
    fi
    sleep 0.5  # Rate limiting
done

echo ""
echo "Done: $success imported, $fail failed (total: ${#doi_list[@]})"
