#!/usr/bin/env python3
"""Tests for claude-zotero plugin modules.

Run: uv run --project . python3 test_zotero.py
Does NOT require Zotero/API credentials for unit tests.
"""

import io
import os
import sys
import unittest
from unittest.mock import patch, MagicMock
import urllib.error

# Ensure scripts dir is on path
sys.path.insert(0, os.path.dirname(__file__))


# ============================================================
# BibTeX Parser Tests
# ============================================================

class TestSplitEntries(unittest.TestCase):

    def setUp(self):
        from zotero_rest_import import split_entries
        self.split_entries = split_entries

    def test_single_entry(self):
        entries = self.split_entries('@article{key, title = {Test}}')
        self.assertEqual(len(entries), 1)

    def test_multiple_entries(self):
        bib = '@article{a, title = {A}}\n@book{b, title = {B}}\n@misc{c, title = {C}}'
        self.assertEqual(len(self.split_entries(bib)), 3)

    def test_empty_input(self):
        self.assertEqual(self.split_entries(""), [])
        self.assertEqual(self.split_entries("no entries here"), [])

    def test_nested_braces(self):
        bib = '@article{key, title = {Machine {Learning} for {NLP}}}'
        self.assertEqual(len(self.split_entries(bib)), 1)

    def test_at_sign_in_field_value(self):
        bib = '@article{key, note = {email: user@example.com}}'
        entries = self.split_entries(bib)
        self.assertEqual(len(entries), 1)


class TestParseBibtex(unittest.TestCase):

    def setUp(self):
        from zotero_rest_import import parse_bibtex
        self.parse_bibtex = parse_bibtex

    def test_braced_values(self):
        fields = self.parse_bibtex('@article{key, title = {Test}, doi = {10.1234/test}}')
        self.assertEqual(fields["_type"], "article")
        self.assertEqual(fields["title"], "Test")
        self.assertEqual(fields["doi"], "10.1234/test")

    def test_bare_year(self):
        fields = self.parse_bibtex('@article{key, year = 2024}')
        self.assertEqual(fields["year"], "2024")

    def test_quoted_values(self):
        fields = self.parse_bibtex('@article{key, title = "Quoted Title"}')
        self.assertEqual(fields["title"], "Quoted Title")

    def test_nested_braces_stripped(self):
        fields = self.parse_bibtex('@article{key, title = {Machine {Learning}}}')
        self.assertEqual(fields["title"], "Machine Learning")

    def test_unknown_type(self):
        fields = self.parse_bibtex('@online{key, title = {Web}}')
        self.assertEqual(fields["_type"], "online")


class TestParseNames(unittest.TestCase):

    def setUp(self):
        from zotero_rest_import import _parse_names
        self._parse_names = _parse_names

    def test_last_first_format(self):
        creators = self._parse_names("Smith, John and Doe, Jane", "author")
        self.assertEqual(len(creators), 2)
        self.assertEqual(creators[0], {"creatorType": "author", "lastName": "Smith", "firstName": "John"})

    def test_first_last_format(self):
        creators = self._parse_names("John Smith", "author")
        self.assertEqual(creators[0], {"creatorType": "author", "firstName": "John", "lastName": "Smith"})

    def test_single_name(self):
        creators = self._parse_names("NVIDIA", "author")
        self.assertEqual(creators[0], {"creatorType": "author", "name": "NVIDIA"})

    def test_editor_type(self):
        creators = self._parse_names("Smith, John", "editor")
        self.assertEqual(creators[0]["creatorType"], "editor")

    def test_empty(self):
        self.assertEqual(self._parse_names("", "author"), [])


class TestBibtexToZoteroItem(unittest.TestCase):

    def setUp(self):
        from zotero_rest_import import bibtex_to_zotero_item
        self.convert = bibtex_to_zotero_item

    def test_journal_article(self):
        item = self.convert({"_type": "article", "title": "T", "doi": "10.1/x", "journal": "Nature", "year": "2024"})
        self.assertEqual(item["itemType"], "journalArticle")
        self.assertEqual(item["DOI"], "10.1/x")
        self.assertEqual(item["publicationTitle"], "Nature")
        self.assertEqual(item["url"], "https://doi.org/10.1/x")

    def test_conference_paper_venue(self):
        item = self.convert({"_type": "inproceedings", "title": "T", "booktitle": "NeurIPS"})
        self.assertEqual(item["itemType"], "conferencePaper")
        self.assertEqual(item["proceedingsTitle"], "NeurIPS")
        self.assertNotIn("publicationTitle", item)

    def test_book_section_venue(self):
        item = self.convert({"_type": "incollection", "title": "Ch", "booktitle": "Book"})
        self.assertEqual(item["bookTitle"], "Book")

    def test_editors_combined(self):
        item = self.convert({"_type": "book", "title": "B", "author": "A, B", "editor": "E, F"})
        authors = [c for c in item["creators"] if c["creatorType"] == "author"]
        editors = [c for c in item["creators"] if c["creatorType"] == "editor"]
        self.assertEqual(len(authors), 1)
        self.assertEqual(len(editors), 1)

    def test_empty_fields_filtered(self):
        item = self.convert({"_type": "article", "title": "T"})
        self.assertNotIn("DOI", item)
        self.assertNotIn("volume", item)
        self.assertIn("creators", item)

    def test_url_not_overwritten(self):
        item = self.convert({"_type": "article", "title": "T", "doi": "10.1/x", "url": "https://example.com"})
        self.assertEqual(item["url"], "https://example.com")

    def test_unknown_type(self):
        item = self.convert({"_type": "online", "title": "W"})
        self.assertEqual(item["itemType"], "document")


# ============================================================
# API Helpers Tests
# ============================================================

class TestApiError(unittest.TestCase):

    def test_falsy(self):
        from zotero_api import ApiError, is_error
        err = ApiError("test")
        self.assertFalse(err)
        self.assertTrue(is_error(err))

    def test_non_errors(self):
        from zotero_api import is_error
        for val in [None, [], {}, "str", 0]:
            self.assertFalse(is_error(val))

    def test_repr(self):
        from zotero_api import ApiError
        self.assertEqual(repr(ApiError("msg")), "ApiError('msg')")


class TestGetCredentials(unittest.TestCase):

    def test_raises_without_both(self):
        from zotero_api import _get_credentials
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ZOTERO_USER_ID", None)
            os.environ.pop("ZOTERO_API_KEY", None)
            with self.assertRaises(RuntimeError) as ctx:
                _get_credentials()
            self.assertIn("ZOTERO_USER_ID", str(ctx.exception))

    def test_raises_without_api_key(self):
        from zotero_api import _get_credentials
        with patch.dict(os.environ, {"ZOTERO_USER_ID": "123"}, clear=False):
            os.environ.pop("ZOTERO_API_KEY", None)
            with self.assertRaises(RuntimeError) as ctx:
                _get_credentials()
            self.assertIn("ZOTERO_API_KEY", str(ctx.exception))

    def test_raises_without_user_id(self):
        from zotero_api import _get_credentials
        with patch.dict(os.environ, {"ZOTERO_API_KEY": "key"}, clear=False):
            os.environ.pop("ZOTERO_USER_ID", None)
            with self.assertRaises(RuntimeError) as ctx:
                _get_credentials()
            self.assertIn("ZOTERO_USER_ID", str(ctx.exception))

    def test_success(self):
        from zotero_api import _get_credentials
        with patch.dict(os.environ, {"ZOTERO_USER_ID": "123", "ZOTERO_API_KEY": "key"}):
            user_id, api_key = _get_credentials()
            self.assertEqual(user_id, "123")
            self.assertEqual(api_key, "key")


class TestRequest(unittest.TestCase):

    def test_http_error_returns_api_error(self):
        from zotero_api import _request, is_error
        error = urllib.error.HTTPError("http://test", 403, "Forbidden", {}, io.BytesIO(b"forbidden"))
        with patch("urllib.request.urlopen", side_effect=error):
            result = _request("GET", "http://test", "fake-key")
            self.assertTrue(is_error(result))
            self.assertIn("403", result.message)

    def test_url_error_returns_api_error(self):
        from zotero_api import _request, is_error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            result = _request("GET", "http://test", "fake-key")
            self.assertTrue(is_error(result))

    def test_json_decode_error(self):
        from zotero_api import _request, is_error
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _request("GET", "http://test", "fake-key")
            self.assertTrue(is_error(result))

    def test_empty_body_returns_none(self):
        from zotero_api import _request
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"   "
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _request("GET", "http://test", "fake-key")
            self.assertIsNone(result)

    def test_valid_json_returned(self):
        from zotero_api import _request
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"key": "value"}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = _request("GET", "http://test", "fake-key")
            self.assertEqual(result, {"key": "value"})


class TestApiPostItems(unittest.TestCase):

    @patch("zotero_api.api_post")
    def test_success(self, mock_post):
        from zotero_api import api_post_items
        mock_post.return_value = {"successful": {"0": {}}, "failed": {}}
        self.assertTrue(api_post_items([{"itemType": "document", "title": "T"}]))

    @patch("zotero_api.api_post")
    def test_failure(self, mock_post):
        from zotero_api import api_post_items
        mock_post.return_value = {"successful": {}, "failed": {"0": {"message": "bad field"}}}
        self.assertFalse(api_post_items([{"itemType": "document", "title": "T"}]))

    @patch("zotero_api.api_post")
    def test_api_error(self, mock_post):
        from zotero_api import api_post_items, ApiError
        mock_post.return_value = ApiError("network error")
        self.assertFalse(api_post_items([{"itemType": "document", "title": "T"}]))

    @patch("zotero_api.api_post")
    def test_empty_list(self, mock_post):
        from zotero_api import api_post_items
        self.assertTrue(api_post_items([]))
        mock_post.assert_not_called()

    @patch("zotero_api.api_post")
    def test_batching(self, mock_post):
        from zotero_api import api_post_items
        mock_post.return_value = {"successful": {}, "failed": {}}
        items = [{"itemType": "document", "title": f"T{i}"} for i in range(75)]
        self.assertTrue(api_post_items(items))
        self.assertEqual(mock_post.call_count, 2)  # 50 + 25


# ============================================================
# translate_abstracts Tests
# ============================================================

class TestFetchAbstractCrossref(unittest.TestCase):

    @patch("translate_abstracts.http_get_json")
    def test_strips_jats_tags(self, mock_get):
        from translate_abstracts import fetch_abstract_crossref
        mock_get.return_value = {
            "message": {"abstract": '<jats:p>Hello <jats:italic>world</jats:italic></jats:p>'}
        }
        self.assertEqual(fetch_abstract_crossref("10.1/x"), "Hello world")

    @patch("translate_abstracts.http_get_json")
    def test_strips_jats_with_attributes(self, mock_get):
        from translate_abstracts import fetch_abstract_crossref
        mock_get.return_value = {
            "message": {"abstract": '<jats:ext-link xlink:href="http://example.com">link</jats:ext-link>'}
        }
        self.assertEqual(fetch_abstract_crossref("10.1/x"), "link")

    @patch("translate_abstracts.http_get_json")
    def test_strips_self_closing_jats(self, mock_get):
        from translate_abstracts import fetch_abstract_crossref
        mock_get.return_value = {
            "message": {"abstract": "before<jats:break/>after"}
        }
        self.assertEqual(fetch_abstract_crossref("10.1/x"), "beforeafter")

    @patch("translate_abstracts.http_get_json")
    def test_returns_none_for_empty(self, mock_get):
        from translate_abstracts import fetch_abstract_crossref
        mock_get.return_value = {"message": {"abstract": ""}}
        self.assertIsNone(fetch_abstract_crossref("10.1/x"))

    def test_returns_none_for_no_doi(self):
        from translate_abstracts import fetch_abstract_crossref
        self.assertIsNone(fetch_abstract_crossref(""))


class TestFetchAbstractSemanticScholar(unittest.TestCase):

    @patch("translate_abstracts.http_get_json")
    def test_returns_abstract(self, mock_get):
        from translate_abstracts import fetch_abstract_semantic_scholar
        mock_get.return_value = {"data": [{"abstract": "Test abstract"}]}
        self.assertEqual(fetch_abstract_semantic_scholar("Test Paper"), "Test abstract")

    @patch("translate_abstracts.http_get_json")
    def test_returns_none_for_no_results(self, mock_get):
        from translate_abstracts import fetch_abstract_semantic_scholar
        mock_get.return_value = {"data": []}
        self.assertIsNone(fetch_abstract_semantic_scholar("Unknown"))


class TestAddNote(unittest.TestCase):

    @patch("translate_abstracts.api_post")
    def test_returns_true_on_success(self, mock_post):
        from translate_abstracts import add_note
        mock_post.return_value = {"successful": {"0": {}}, "failed": {}}
        self.assertTrue(add_note("KEY123", "<p>Note</p>"))

    @patch("translate_abstracts.api_post")
    def test_returns_false_on_error(self, mock_post):
        from translate_abstracts import add_note
        from zotero_api import ApiError
        mock_post.return_value = ApiError("fail")
        self.assertFalse(add_note("KEY123", "<p>Note</p>"))


class TestGetAllSubcollectionKeys(unittest.TestCase):

    @patch("translate_abstracts.api_get")
    def test_recursive_traversal(self, mock_get):
        from translate_abstracts import _get_all_subcollection_keys
        mock_get.side_effect = [
            [  # first page
                {"data": {"key": "SUB1", "parentCollection": "ROOT"}},
                {"data": {"key": "SUB2", "parentCollection": "ROOT"}},
                {"data": {"key": "SUBSUB", "parentCollection": "SUB1"}},
            ],
            [],  # second page (empty = done)
        ]
        keys = _get_all_subcollection_keys("ROOT")
        self.assertIn("ROOT", keys)
        self.assertIn("SUB1", keys)
        self.assertIn("SUB2", keys)
        self.assertIn("SUBSUB", keys)

    @patch("translate_abstracts.api_get")
    def test_returns_root_on_error(self, mock_get):
        from translate_abstracts import _get_all_subcollection_keys
        from zotero_api import ApiError
        mock_get.return_value = ApiError("fail")
        keys = _get_all_subcollection_keys("ROOT")
        self.assertEqual(keys, ["ROOT"])


# ============================================================
# zotero_rest_import.main() Tests
# ============================================================

class TestRestImportMain(unittest.TestCase):

    @patch("pdf_attach.attach_pdf")
    @patch("zotero_rest_import.api_post")
    def test_main_with_valid_bibtex(self, mock_post, mock_attach):
        from zotero_rest_import import main
        mock_post.return_value = {"successful": {"0": {"key": "ABC123"}}, "failed": {}}
        with patch("sys.stdin", io.StringIO('@article{test, title = {Test Paper}, doi = {10.1/x}}')):
            main()
        mock_post.assert_called_once()

    def test_main_empty_stdin(self):
        from zotero_rest_import import main
        with patch("sys.stdin", io.StringIO("")):
            with self.assertRaises(SystemExit) as ctx:
                main()
            self.assertEqual(ctx.exception.code, 1)


class TestHttpGetJson(unittest.TestCase):

    def test_success(self):
        from zotero_api import http_get_json
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"key": "value"}'
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = http_get_json("http://test")
            self.assertEqual(result, {"key": "value"})

    def test_url_error(self):
        from zotero_api import http_get_json
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            self.assertIsNone(http_get_json("http://test"))

    def test_json_error(self):
        from zotero_api import http_get_json
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            self.assertIsNone(http_get_json("http://test"))


class TestArxivEnrichment(unittest.TestCase):

    def test_extract_arxiv_id_from_doi(self):
        from zotero_rest_import import _extract_arxiv_id
        self.assertEqual(
            _extract_arxiv_id({"DOI": "10.48550/arXiv.1706.03762"}),
            "1706.03762",
        )

    def test_extract_arxiv_id_from_url(self):
        from zotero_rest_import import _extract_arxiv_id
        self.assertEqual(
            _extract_arxiv_id({"url": "https://arxiv.org/abs/1706.03762"}),
            "1706.03762",
        )

    def test_extract_arxiv_id_none(self):
        from zotero_rest_import import _extract_arxiv_id
        self.assertIsNone(_extract_arxiv_id({"DOI": "10.1038/nature14539"}))
        self.assertIsNone(_extract_arxiv_id({}))

    def test_fetch_arxiv_abstract(self):
        from zotero_rest_import import fetch_arxiv_abstract
        xml_resp = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Test Paper</title>
    <summary>  This is a
    multi-line
    abstract.  </summary>
  </entry>
</feed>"""
        mock_resp = MagicMock()
        mock_resp.read.return_value = xml_resp
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            result = fetch_arxiv_abstract("1706.03762")
            self.assertEqual(result, "This is a multi-line abstract.")

    def test_fetch_arxiv_abstract_error(self):
        from zotero_rest_import import fetch_arxiv_abstract
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("fail")):
            self.assertIsNone(fetch_arxiv_abstract("1706.03762"))

    @patch("zotero_rest_import.fetch_arxiv_abstract")
    def test_enrich_fills_missing_abstract(self, mock_fetch):
        from zotero_rest_import import enrich_arxiv_abstracts
        mock_fetch.return_value = "fetched abstract"
        items = [
            {"title": "arXiv paper", "DOI": "10.48550/arXiv.1706.03762", "abstractNote": ""},
            {"title": "non-arxiv", "DOI": "10.1038/nature14539", "abstractNote": ""},
            {"title": "already has", "DOI": "10.48550/arXiv.1234.5678", "abstractNote": "existing"},
        ]
        enrich_arxiv_abstracts(items)
        self.assertEqual(items[0]["abstractNote"], "fetched abstract")
        self.assertEqual(items[1]["abstractNote"], "")  # not arxiv, unchanged
        self.assertEqual(items[2]["abstractNote"], "existing")  # already has, unchanged
        mock_fetch.assert_called_once_with("1706.03762")


class TestPdfResolve(unittest.TestCase):

    def test_arxiv_doi(self):
        from pdf_attach import find_arxiv_pdf_url
        self.assertEqual(
            find_arxiv_pdf_url("10.48550/arXiv.1706.03762"),
            "https://arxiv.org/pdf/1706.03762.pdf",
        )

    def test_arxiv_doi_lowercase(self):
        from pdf_attach import find_arxiv_pdf_url
        self.assertEqual(
            find_arxiv_pdf_url("10.48550/arxiv.1706.03762"),
            "https://arxiv.org/pdf/1706.03762.pdf",
        )

    def test_non_arxiv_doi(self):
        from pdf_attach import find_arxiv_pdf_url
        self.assertIsNone(find_arxiv_pdf_url("10.1038/nature14539"))

    @patch("pdf_attach.http_get_json")
    def test_unpaywall_best_location(self, mock_get):
        from pdf_attach import find_unpaywall_pdf_url
        mock_get.return_value = {
            "is_oa": True,
            "best_oa_location": {"url_for_pdf": "https://example.com/paper.pdf"},
            "oa_locations": [],
        }
        with patch("pdf_attach.UNPAYWALL_EMAIL", "test@example.com"):
            self.assertEqual(find_unpaywall_pdf_url("10.1/x"), "https://example.com/paper.pdf")

    @patch("pdf_attach.http_get_json")
    def test_unpaywall_fallback_location(self, mock_get):
        from pdf_attach import find_unpaywall_pdf_url
        mock_get.return_value = {
            "is_oa": True,
            "best_oa_location": {"url_for_pdf": None},
            "oa_locations": [
                {"url_for_pdf": None},
                {"url_for_pdf": "https://repo.com/paper.pdf"},
            ],
        }
        with patch("pdf_attach.UNPAYWALL_EMAIL", "test@example.com"):
            self.assertEqual(find_unpaywall_pdf_url("10.1/x"), "https://repo.com/paper.pdf")

    @patch("pdf_attach.http_get_json")
    def test_unpaywall_not_oa(self, mock_get):
        from pdf_attach import find_unpaywall_pdf_url
        mock_get.return_value = {"is_oa": False}
        with patch("pdf_attach.UNPAYWALL_EMAIL", "test@example.com"):
            self.assertIsNone(find_unpaywall_pdf_url("10.1/x"))

    def test_unpaywall_no_email(self):
        from pdf_attach import find_unpaywall_pdf_url
        with patch("pdf_attach.UNPAYWALL_EMAIL", ""):
            self.assertIsNone(find_unpaywall_pdf_url("10.1/x"))

    @patch("pdf_attach.find_unpaywall_pdf_url")
    @patch("pdf_attach.find_arxiv_pdf_url")
    def test_resolve_prefers_arxiv(self, mock_arxiv, mock_uw):
        from pdf_attach import resolve_pdf_url
        mock_arxiv.return_value = "https://arxiv.org/pdf/1234.pdf"
        mock_uw.return_value = "https://unpaywall.org/paper.pdf"
        self.assertEqual(resolve_pdf_url("10.48550/arXiv.1234"), "https://arxiv.org/pdf/1234.pdf")
        mock_uw.assert_not_called()


class TestBibtexTypeMap(unittest.TestCase):

    def test_common_types_covered(self):
        from zotero_rest_import import BIBTEX_TYPE_MAP
        for btype in ["article", "inproceedings", "book", "phdthesis", "misc"]:
            self.assertIn(btype, BIBTEX_TYPE_MAP)


if __name__ == "__main__":
    unittest.main()
