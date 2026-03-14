"""Tests for docs_operations.py search and comment features."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# Import the script as a module
_scripts_dir = str(Path(__file__).resolve().parents[2] / "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)
import docs_operations


# =============================================================================
# Fixtures - realistic Google Docs API response structures
# =============================================================================


@pytest.fixture()
def simple_doc_body():
    """Realistic Google Docs body with headings, paragraphs, and sections.

    Layout (character indices):
      0-1:     section break
      1-17:    H1 "Document title\\n"
      17-54:   normal "Hello world, this is a test document.\\n"
      54-66:   H2 "Section one\\n"
      66-120:  normal "This is the content of section one with some text.\\n"
      120-133: H2 "Section two\\n"
      133-180: normal "Content under section two is different from one.\\n"
      180-192: H3 "Subsection\\n"
      192-230: normal "Nested subsection content lives here.\\n"
    """
    return [
        {"sectionBreak": {}, "startIndex": 0, "endIndex": 1},
        {
            "startIndex": 1,
            "endIndex": 17,
            "paragraph": {
                "paragraphStyle": {"namedStyleType": "HEADING_1"},
                "elements": [
                    {
                        "startIndex": 1,
                        "endIndex": 17,
                        "textRun": {"content": "Document title\n"},
                    }
                ],
            },
        },
        {
            "startIndex": 17,
            "endIndex": 54,
            "paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [
                    {
                        "startIndex": 17,
                        "endIndex": 54,
                        "textRun": {"content": "Hello world, this is a test document.\n"},
                    }
                ],
            },
        },
        {
            "startIndex": 54,
            "endIndex": 66,
            "paragraph": {
                "paragraphStyle": {"namedStyleType": "HEADING_2"},
                "elements": [
                    {
                        "startIndex": 54,
                        "endIndex": 66,
                        "textRun": {"content": "Section one\n"},
                    }
                ],
            },
        },
        {
            "startIndex": 66,
            "endIndex": 120,
            "paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [
                    {
                        "startIndex": 66,
                        "endIndex": 120,
                        "textRun": {
                            "content": "This is the content of section one with some text.\n"
                        },
                    }
                ],
            },
        },
        {
            "startIndex": 120,
            "endIndex": 133,
            "paragraph": {
                "paragraphStyle": {"namedStyleType": "HEADING_2"},
                "elements": [
                    {
                        "startIndex": 120,
                        "endIndex": 133,
                        "textRun": {"content": "Section two\n"},
                    }
                ],
            },
        },
        {
            "startIndex": 133,
            "endIndex": 180,
            "paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [
                    {
                        "startIndex": 133,
                        "endIndex": 180,
                        "textRun": {
                            "content": "Content under section two is different from one.\n"
                        },
                    }
                ],
            },
        },
        {
            "startIndex": 180,
            "endIndex": 192,
            "paragraph": {
                "paragraphStyle": {"namedStyleType": "HEADING_3"},
                "elements": [
                    {
                        "startIndex": 180,
                        "endIndex": 192,
                        "textRun": {"content": "Subsection\n"},
                    }
                ],
            },
        },
        {
            "startIndex": 192,
            "endIndex": 230,
            "paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [
                    {
                        "startIndex": 192,
                        "endIndex": 230,
                        "textRun": {"content": "Nested subsection content lives here.\n"},
                    }
                ],
            },
        },
    ]


@pytest.fixture()
def doc_with_duplicate_headings():
    """Body with two H2 headings both named 'Details'."""
    return [
        {"sectionBreak": {}, "startIndex": 0, "endIndex": 1},
        {
            "startIndex": 1,
            "endIndex": 10,
            "paragraph": {
                "paragraphStyle": {"namedStyleType": "HEADING_2"},
                "elements": [
                    {"startIndex": 1, "endIndex": 10, "textRun": {"content": "Details\n"}}
                ],
            },
        },
        {
            "startIndex": 10,
            "endIndex": 30,
            "paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [
                    {
                        "startIndex": 10,
                        "endIndex": 30,
                        "textRun": {"content": "First details body.\n"},
                    }
                ],
            },
        },
        {
            "startIndex": 30,
            "endIndex": 39,
            "paragraph": {
                "paragraphStyle": {"namedStyleType": "HEADING_2"},
                "elements": [
                    {"startIndex": 30, "endIndex": 39, "textRun": {"content": "Details\n"}}
                ],
            },
        },
        {
            "startIndex": 39,
            "endIndex": 60,
            "paragraph": {
                "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                "elements": [
                    {
                        "startIndex": 39,
                        "endIndex": 60,
                        "textRun": {"content": "Second details body.\n"},
                    }
                ],
            },
        },
    ]


@pytest.fixture()
def comments_response():
    """Realistic Drive API comments response."""
    return {
        "comments": [
            {
                "id": "c1",
                "author": {"displayName": "Roman S"},
                "content": "Please review this section",
                "resolved": False,
                "createdTime": "2026-03-01T10:00:00Z",
                "modifiedTime": "2026-03-01T10:00:00Z",
                "quotedFileContent": {
                    "value": "some quoted text",
                    "mimeType": "text/html",
                },
                "anchor": "kix.abc123",
            },
            {
                "id": "c2",
                "author": {"displayName": "Anna K"},
                "content": "Done",
                "resolved": True,
                "createdTime": "2026-03-02T14:00:00Z",
                "modifiedTime": "2026-03-02T15:00:00Z",
            },
            {
                "id": "c3",
                "author": {"displayName": "Roman S"},
                "content": "What about this?",
                "resolved": False,
                "createdTime": "2026-03-03T09:00:00Z",
                "modifiedTime": "2026-03-03T09:30:00Z",
                "replies": [
                    {
                        "author": {"displayName": "Anna K"},
                        "content": "Good point, will fix",
                        "createdTime": "2026-03-03T09:30:00Z",
                    }
                ],
            },
        ]
    }


def _mock_docs_api(monkeypatch, body, inline_objects=None):
    """Set up mock docs service returning the given body."""
    mock_service = MagicMock()
    mock_doc = {
        "body": {"content": body},
        "inlineObjects": inline_objects or {},
    }
    mock_service.documents().get().execute.return_value = mock_doc
    monkeypatch.setattr(docs_operations, "get_docs_service", lambda **kw: mock_service)
    monkeypatch.setattr(docs_operations, "_resolve_project", lambda p: p or "anaro-labs")
    return mock_service


def _mock_drive_api(monkeypatch, *pages):
    """Set up mock drive service returning paginated comment responses.

    Each argument is a comments response dict. For single-page results,
    pass one dict. For paginated results, pass multiple.
    """
    mock_service = MagicMock()
    side_effects = []
    for i, page in enumerate(pages):
        resp = dict(page)
        if i < len(pages) - 1:
            resp["nextPageToken"] = f"token_{i + 1}"
        else:
            resp.pop("nextPageToken", None)
        side_effects.append(resp)
    mock_service.comments().list().execute.side_effect = side_effects
    monkeypatch.setattr(docs_operations, "get_drive_service", lambda **kw: mock_service)
    monkeypatch.setattr(docs_operations, "_resolve_project", lambda p: p or "anaro-labs")
    return mock_service


# =============================================================================
# TestBuildHeadingMap - pure function, no mocking needed
# =============================================================================


class TestBuildHeadingMap:
    """Tests for _build_heading_map helper."""

    def test_basic_heading_extraction(self, simple_doc_body):
        result = docs_operations._build_heading_map(simple_doc_body)
        assert "Document title" in result
        assert "Section one" in result
        assert "Section two" in result
        assert "Subsection" in result

        assert result["Document title"]["style"] == "HEADING_1"
        assert result["Section one"]["style"] == "HEADING_2"
        assert result["Section two"]["style"] == "HEADING_2"
        assert result["Subsection"]["style"] == "HEADING_3"

        assert result["Document title"]["startIndex"] == 1
        assert result["Document title"]["endIndex"] == 17

    def test_section_boundaries(self, simple_doc_body):
        result = docs_operations._build_heading_map(simple_doc_body)
        # H1 ends at next same-or-higher level - none exists, so doc end
        assert result["Document title"]["sectionEnd"] == 230
        # H2 "Section one" ends at next H2 "Section two"
        assert result["Section one"]["sectionEnd"] == 120
        # H2 "Section two" has no next H2, so doc end
        assert result["Section two"]["sectionEnd"] == 230

    def test_last_heading_section_end(self, simple_doc_body):
        result = docs_operations._build_heading_map(simple_doc_body)
        # Last heading (Subsection H3) should end at doc endIndex
        assert result["Subsection"]["sectionEnd"] == 230

    def test_h3_nested_in_h2(self, simple_doc_body):
        result = docs_operations._build_heading_map(simple_doc_body)
        # H3 "Subsection" is nested in H2 "Section two"
        # H3's sectionEnd = doc end (no next H3 or higher)
        assert result["Subsection"]["sectionEnd"] == 230
        # H2 "Section two" sectionEnd skips over the H3 to doc end
        assert result["Section two"]["sectionEnd"] == 230

    def test_duplicate_heading_names(self, doc_with_duplicate_headings):
        result = docs_operations._build_heading_map(doc_with_duplicate_headings)
        assert "Details" in result
        assert "Details (2)" in result
        assert result["Details"]["startIndex"] == 1
        assert result["Details (2)"]["startIndex"] == 30

    def test_empty_body(self):
        result = docs_operations._build_heading_map([])
        assert result == {}

    def test_no_headings(self):
        body = [
            {"sectionBreak": {}, "startIndex": 0, "endIndex": 1},
            {
                "startIndex": 1,
                "endIndex": 20,
                "paragraph": {
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    "elements": [
                        {"startIndex": 1, "endIndex": 20, "textRun": {"content": "Just a paragraph.\n"}}
                    ],
                },
            },
        ]
        result = docs_operations._build_heading_map(body)
        assert result == {}


# =============================================================================
# TestExtractParagraphElement - pure function, no mocking needed
# =============================================================================


class TestExtractParagraphElement:
    """Tests for extract_paragraph_element helper."""

    def test_text_run(self):
        elem = {"textRun": {"content": "Hello world"}}
        assert docs_operations.extract_paragraph_element(elem) == "Hello world"

    def test_inline_object_with_title(self):
        inline_objects = {
            "obj1": {
                "inlineObjectProperties": {
                    "embeddedObject": {"title": "My diagram"}
                }
            }
        }
        elem = {"inlineObjectElement": {"inlineObjectId": "obj1"}}
        result = docs_operations.extract_paragraph_element(elem, inline_objects)
        assert result == "[Image: My diagram]"

    def test_inline_object_without_context(self):
        elem = {"inlineObjectElement": {"inlineObjectId": "unknown"}}
        assert docs_operations.extract_paragraph_element(elem) == "[Image]"

    def test_rich_link(self):
        elem = {
            "richLink": {
                "richLinkProperties": {
                    "title": "Google Doc",
                    "uri": "https://docs.google.com/doc/123",
                }
            }
        }
        assert docs_operations.extract_paragraph_element(elem) == "[Link: Google Doc]"

    def test_rich_link_no_title(self):
        elem = {
            "richLink": {
                "richLinkProperties": {
                    "title": "",
                    "uri": "https://docs.google.com/doc/123",
                }
            }
        }
        assert docs_operations.extract_paragraph_element(elem) == "[Link: https://docs.google.com/doc/123]"

    def test_person_mention(self):
        elem = {"person": {"personProperties": {"name": "Roman S"}}}
        assert docs_operations.extract_paragraph_element(elem) == "@Roman S"

    def test_horizontal_rule(self):
        elem = {"horizontalRule": {}}
        assert docs_operations.extract_paragraph_element(elem) == "\n---\n"

    def test_unknown_element(self):
        elem = {"someNewType": {"data": "value"}}
        assert docs_operations.extract_paragraph_element(elem) == ""


# =============================================================================
# TestFindText - mocks API, captures stdout
# =============================================================================


class TestFindText:
    """Tests for find_text function."""

    def test_single_match(self, monkeypatch, simple_doc_body, capsys):
        _mock_docs_api(monkeypatch, simple_doc_body)
        docs_operations.find_text("doc123", "Hello world", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        assert result["matchCount"] == 1
        assert result["matches"][0]["startIndex"] == 17
        # "Hello world" is 11 chars, endIndex = 17 + 11 = 28
        assert result["matches"][0]["endIndex"] == 28

    def test_multiple_matches(self, monkeypatch, simple_doc_body, capsys):
        _mock_docs_api(monkeypatch, simple_doc_body)
        # "section" appears in "Section one", "section one", "Section two", "section two"
        # and in body text. Let's search for "one" which appears in multiple places.
        docs_operations.find_text("doc123", "one", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        # "one" in "Section one\n" (heading), "section one" (body text), "from one." (body text)
        assert result["matchCount"] >= 2

    def test_case_insensitive(self, monkeypatch, simple_doc_body, capsys):
        _mock_docs_api(monkeypatch, simple_doc_body)
        docs_operations.find_text("doc123", "hello world", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        assert result["matchCount"] == 1
        assert result["matches"][0]["matchedText"] == "Hello world"

    def test_no_match(self, monkeypatch, simple_doc_body, capsys):
        _mock_docs_api(monkeypatch, simple_doc_body)
        docs_operations.find_text("doc123", "nonexistent phrase xyz", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        assert result["matchCount"] == 0
        assert result["matches"] == []

    def test_match_spanning_text_runs(self, monkeypatch, capsys):
        """Text split across two textRun elements should still be found."""
        body = [
            {"sectionBreak": {}, "startIndex": 0, "endIndex": 1},
            {
                "startIndex": 1,
                "endIndex": 20,
                "paragraph": {
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    "elements": [
                        {"startIndex": 1, "endIndex": 8, "textRun": {"content": "Hello w"}},
                        {"startIndex": 8, "endIndex": 20, "textRun": {"content": "orld, test!\n"}},
                    ],
                },
            },
        ]
        _mock_docs_api(monkeypatch, body)
        docs_operations.find_text("doc123", "Hello world", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        assert result["matchCount"] == 1
        assert result["matches"][0]["startIndex"] == 1
        assert result["matches"][0]["endIndex"] == 12

    def test_context_extraction(self, monkeypatch, simple_doc_body, capsys):
        _mock_docs_api(monkeypatch, simple_doc_body)
        docs_operations.find_text("doc123", "test document", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        assert result["matchCount"] == 1
        ctx = result["matches"][0]["context"]
        # Context should include surrounding text, newlines escaped
        assert "\\n" in ctx or "test document" in ctx

    def test_match_at_document_start(self, monkeypatch, capsys):
        """Context should not go negative when match is at start."""
        body = [
            {"sectionBreak": {}, "startIndex": 0, "endIndex": 1},
            {
                "startIndex": 1,
                "endIndex": 10,
                "paragraph": {
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    "elements": [
                        {"startIndex": 1, "endIndex": 10, "textRun": {"content": "Start up\n"}},
                    ],
                },
            },
        ]
        _mock_docs_api(monkeypatch, body)
        docs_operations.find_text("doc123", "Start", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        assert result["matchCount"] == 1
        # Should not crash - context starts at position 0

    def test_match_at_document_end(self, monkeypatch, capsys):
        """Context should not overflow when match is at end."""
        body = [
            {"sectionBreak": {}, "startIndex": 0, "endIndex": 1},
            {
                "startIndex": 1,
                "endIndex": 5,
                "paragraph": {
                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                    "elements": [
                        {"startIndex": 1, "endIndex": 5, "textRun": {"content": "end\n"}},
                    ],
                },
            },
        ]
        _mock_docs_api(monkeypatch, body)
        docs_operations.find_text("doc123", "end", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        assert result["matchCount"] == 1

    def test_index_mapping_accuracy(self, monkeypatch, simple_doc_body, capsys):
        """startIndex/endIndex should match actual Google Docs character positions."""
        _mock_docs_api(monkeypatch, simple_doc_body)
        docs_operations.find_text("doc123", "Section one", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        assert result["matchCount"] >= 1
        match = result["matches"][0]
        # "Section one" starts at index 54 (the H2 heading)
        assert match["startIndex"] == 54
        assert match["endIndex"] == 65  # 54 + 11


# =============================================================================
# TestFindHeading - mocks API, captures stdout
# =============================================================================


class TestFindHeading:
    """Tests for find_heading function."""

    def test_exact_match(self, monkeypatch, simple_doc_body, capsys):
        _mock_docs_api(monkeypatch, simple_doc_body)
        docs_operations.find_heading("doc123", "Section one", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        assert result["matchCount"] == 1
        assert result["matches"][0]["headingText"] == "Section one"

    def test_substring_match(self, monkeypatch, simple_doc_body, capsys):
        _mock_docs_api(monkeypatch, simple_doc_body)
        docs_operations.find_heading("doc123", "Section", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        # "Section" matches "Section one", "Section two", and "Subsection"
        assert result["matchCount"] == 3
        texts = {m["headingText"] for m in result["matches"]}
        assert texts == {"Section one", "Section two", "Subsection"}

    def test_case_insensitive(self, monkeypatch, simple_doc_body, capsys):
        _mock_docs_api(monkeypatch, simple_doc_body)
        docs_operations.find_heading("doc123", "section one", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        assert result["matchCount"] == 1
        assert result["matches"][0]["headingText"] == "Section one"

    def test_no_match(self, monkeypatch, simple_doc_body, capsys):
        _mock_docs_api(monkeypatch, simple_doc_body)
        docs_operations.find_heading("doc123", "Nonexistent heading", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        assert result["matchCount"] == 0

    def test_returns_section_boundaries(self, monkeypatch, simple_doc_body, capsys):
        _mock_docs_api(monkeypatch, simple_doc_body)
        docs_operations.find_heading("doc123", "Section one", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        match = result["matches"][0]
        assert "startIndex" in match
        assert "endIndex" in match
        assert "sectionEnd" in match
        assert "style" in match
        assert match["style"] == "HEADING_2"
        assert match["sectionEnd"] == 120

    def test_duplicate_heading_match(self, monkeypatch, doc_with_duplicate_headings, capsys):
        _mock_docs_api(monkeypatch, doc_with_duplicate_headings)
        docs_operations.find_heading("doc123", "Details", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        assert result["matchCount"] == 2
        texts = {m["headingText"] for m in result["matches"]}
        assert "Details" in texts
        assert "Details (2)" in texts


# =============================================================================
# TestCheckComments - mocks Drive API, captures stdout
# =============================================================================


class TestCheckComments:
    """Tests for check_comments function."""

    def test_basic_comment_listing(self, monkeypatch, comments_response, capsys):
        _mock_drive_api(monkeypatch, comments_response)
        docs_operations.check_comments("doc123", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        assert result["totalComments"] == 3
        assert result["activeComments"] == 2
        assert result["resolvedComments"] == 1

    def test_comment_with_quoted_content(self, monkeypatch, comments_response, capsys):
        _mock_drive_api(monkeypatch, comments_response)
        docs_operations.check_comments("doc123", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        c1 = result["comments"][0]
        assert c1["quotedText"] == "some quoted text"
        assert c1["quotedMimeType"] == "text/html"

    def test_comment_without_quoted_content(self, monkeypatch, comments_response, capsys):
        _mock_drive_api(monkeypatch, comments_response)
        docs_operations.check_comments("doc123", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        # Second comment (resolved) has no quotedFileContent
        c2 = result["comments"][1]
        assert "quotedText" not in c2

    def test_comment_with_anchor(self, monkeypatch, comments_response, capsys):
        _mock_drive_api(monkeypatch, comments_response)
        docs_operations.check_comments("doc123", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        c1 = result["comments"][0]
        assert c1["anchor"] == "kix.abc123"

    def test_comment_with_replies(self, monkeypatch, comments_response, capsys):
        _mock_drive_api(monkeypatch, comments_response)
        docs_operations.check_comments("doc123", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        c3 = result["comments"][2]
        assert len(c3["replies"]) == 1
        assert c3["replies"][0]["author"] == "Anna K"
        assert c3["replies"][0]["content"] == "Good point, will fix"
        assert c3["replies"][0]["createdTime"] == "2026-03-03T09:30:00Z"

    def test_resolved_vs_active(self, monkeypatch, comments_response, capsys):
        _mock_drive_api(monkeypatch, comments_response)
        docs_operations.check_comments("doc123", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        active_ids = [c["id"] for c in result["comments"] if not c["resolved"]]
        resolved_ids = [c["id"] for c in result["comments"] if c["resolved"]]
        assert set(active_ids) == {"c1", "c3"}
        assert resolved_ids == ["c2"]

    def test_pagination(self, monkeypatch, capsys):
        """Two pages of comments should be combined."""
        page1 = {
            "comments": [
                {
                    "id": "c1",
                    "author": {"displayName": "Roman S"},
                    "content": "First page comment",
                    "resolved": False,
                    "createdTime": "2026-03-01T10:00:00Z",
                }
            ],
        }
        page2 = {
            "comments": [
                {
                    "id": "c2",
                    "author": {"displayName": "Anna K"},
                    "content": "Second page comment",
                    "resolved": False,
                    "createdTime": "2026-03-02T10:00:00Z",
                }
            ],
        }
        _mock_drive_api(monkeypatch, page1, page2)
        docs_operations.check_comments("doc123", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        assert result["totalComments"] == 2
        ids = [c["id"] for c in result["comments"]]
        assert ids == ["c1", "c2"]

    def test_empty_document(self, monkeypatch, capsys):
        _mock_drive_api(monkeypatch, {"comments": []})
        docs_operations.check_comments("doc123", project="anaro-labs")
        result = json.loads(capsys.readouterr().out)
        assert result["totalComments"] == 0
        assert result["activeComments"] == 0
        assert result["resolvedComments"] == 0
        assert result["comments"] == []
