#!/usr/bin/env python3
"""
Google Slides operations with EstateMate brand system support.
Uses service account with domain-wide delegation.

Usage:
    # Create / full rebuild
    python3 slides_operations.py --create-from-json /path/to/deck.json --project estate-mate
    python3 slides_operations.py --create --title "Deck Title" --project estate-mate
    python3 slides_operations.py --update-from-json /path/to/deck.json --pres-id PRES_ID --project estate-mate
    python3 slides_operations.py --update-from-json deck.json --pres-id ID --slides "0,3,5" --project estate-mate

    # Surgical editing (tier 1)
    python3 slides_operations.py --inspect PRES_ID --slide 3 --project estate-mate
    python3 slides_operations.py --patch PRES_ID --ops /tmp/fixes.json --project estate-mate

    # Single-slide rebuild (tier 2)
    python3 slides_operations.py --rebuild-slide PRES_ID --slide 4 --ops slide_def.json --project estate-mate

    # Utilities
    python3 slides_operations.py --list PRESENTATION_ID --project estate-mate
    python3 slides_operations.py --get PRESENTATION_ID --project estate-mate
    python3 slides_operations.py --move PRESENTATION_ID --folder FOLDER_ID --project estate-mate
    python3 slides_operations.py --export-pdf PRESENTATION_ID --project estate-mate
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from google_client import get_slides_service, get_drive_service, _resolve_project


# =============================================================================
# Units and color helpers
# =============================================================================

def _emu(pt):
    """Convert points to EMU. 1 pt = 12700 EMU."""
    return int(pt * 12700)

def _rgb(hex_color):
    """Convert #RRGGBB to Slides API RGB dict."""
    h = hex_color.lstrip("#")
    return {
        "red": int(h[0:2], 16) / 255.0,
        "green": int(h[2:4], 16) / 255.0,
        "blue": int(h[4:6], 16) / 255.0,
    }

def _sid():
    """Generate a unique element ID."""
    return f"s{uuid.uuid4().hex[:8]}"


# =============================================================================
# Slide dimensions (16:9 widescreen)
# =============================================================================

SLIDE_W = 720  # 10 inches in pt
SLIDE_H = 405  # 5.625 inches in pt
MARGIN_X = 48
MARGIN_Y = 48
CONTENT_W = SLIDE_W - (MARGIN_X * 2)
HEADER_H = 28  # Height for wordmark bar
CONTENT_TOP = 50  # Tight spacing below wordmark (was 88, too much dead space)


# =============================================================================
# Low-level element builders
# =============================================================================

def _create_textbox(reqs, page_id, eid, x, y, w, h):
    reqs.append({
        "createShape": {
            "objectId": eid,
            "shapeType": "TEXT_BOX",
            "elementProperties": {
                "pageObjectId": page_id,
                "size": {
                    "height": {"magnitude": _emu(h), "unit": "EMU"},
                    "width": {"magnitude": _emu(w), "unit": "EMU"},
                },
                "transform": {
                    "scaleX": 1, "scaleY": 1,
                    "translateX": _emu(x), "translateY": _emu(y),
                    "unit": "EMU",
                },
            },
        }
    })

def _create_rect(reqs, page_id, eid, x, y, w, h, fill_color, no_outline=True, corner_radius=None):
    shape_type = "ROUND_RECTANGLE" if corner_radius else "RECTANGLE"
    reqs.append({
        "createShape": {
            "objectId": eid,
            "shapeType": shape_type,
            "elementProperties": {
                "pageObjectId": page_id,
                "size": {
                    "height": {"magnitude": _emu(h), "unit": "EMU"},
                    "width": {"magnitude": _emu(w), "unit": "EMU"},
                },
                "transform": {
                    "scaleX": 1, "scaleY": 1,
                    "translateX": _emu(x), "translateY": _emu(y),
                    "unit": "EMU",
                },
            },
        }
    })
    props = {
        "shapeBackgroundFill": {
            "solidFill": {"color": {"rgbColor": _rgb(fill_color)}}
        },
    }
    fields = "shapeBackgroundFill.solidFill.color"
    if no_outline:
        props["outline"] = {"propertyState": "NOT_RENDERED"}
        fields += ",outline.propertyState"

    reqs.append({
        "updateShapeProperties": {
            "objectId": eid,
            "shapeProperties": props,
            "fields": fields,
        }
    })

def _insert_text(reqs, eid, text, index=0):
    reqs.append({
        "insertText": {
            "objectId": eid,
            "text": text,
            "insertionIndex": index,
        }
    })

def _style_text(reqs, eid, start, end, font_size=None, bold=None, color=None,
                font_family=None, italic=None, underline=None):
    style = {}
    fields = []
    if font_size is not None:
        style["fontSize"] = {"magnitude": font_size, "unit": "PT"}
        fields.append("fontSize")
    if bold is not None:
        style["bold"] = bold
        fields.append("bold")
    if italic is not None:
        style["italic"] = italic
        fields.append("italic")
    if underline is not None:
        style["underline"] = underline
        fields.append("underline")
    if color is not None:
        style["foregroundColor"] = {"opaqueColor": {"rgbColor": _rgb(color)}}
        fields.append("foregroundColor")
    if font_family is not None:
        style["fontFamily"] = font_family
        fields.append("fontFamily")
    if fields:
        reqs.append({
            "updateTextStyle": {
                "objectId": eid,
                "textRange": {"type": "FIXED_RANGE", "startIndex": start, "endIndex": end},
                "style": style,
                "fields": ",".join(fields),
            }
        })

def _style_para(reqs, eid, start, end, alignment=None, space_above=None,
                space_below=None, line_spacing=None, bullet_preset=None):
    style = {}
    fields = []
    if alignment:
        style["alignment"] = alignment
        fields.append("alignment")
    if space_above is not None:
        style["spaceAbove"] = {"magnitude": space_above, "unit": "PT"}
        fields.append("spaceAbove")
    if space_below is not None:
        style["spaceBelow"] = {"magnitude": space_below, "unit": "PT"}
        fields.append("spaceBelow")
    if line_spacing is not None:
        style["lineSpacing"] = line_spacing
        fields.append("lineSpacing")
    if fields:
        reqs.append({
            "updateParagraphStyle": {
                "objectId": eid,
                "textRange": {"type": "FIXED_RANGE", "startIndex": start, "endIndex": end},
                "style": style,
                "fields": ",".join(fields),
            }
        })
    if bullet_preset:
        reqs.append({
            "createParagraphBullets": {
                "objectId": eid,
                "textRange": {"type": "FIXED_RANGE", "startIndex": start, "endIndex": end},
                "bulletPreset": bullet_preset,
            }
        })


# =============================================================================
# Brand-aware element builders
# =============================================================================

def _resolve_colors(slide_def, theme):
    """Return (bg, title_color, body_color, overline_color, muted_color) based on mode."""
    mode = slide_def.get("mode", "light")
    if mode == "dark":
        return (
            theme.get("primary", "#1A332F"),
            theme.get("text_on_dark", "#FFFFFF"),
            theme.get("text_on_dark", "#FFFFFF"),
            theme.get("gold", "#D4A853"),
            theme.get("text_on_dark_muted", "#A3B8B3"),
        )
    else:
        return (
            theme.get("background", "#F9F9F7"),
            theme.get("text_heading", "#1F2937"),
            theme.get("text_body", "#374151"),
            theme.get("gold", "#D4A853"),
            theme.get("text_secondary", "#6B7280"),
        )


def _add_chrome(reqs, slide_id, theme, mode="light", side_strip=False):
    """Add template chrome: top accent bar + wordmark + optional right side strip."""
    primary = theme.get("primary", "#1A332F")
    font = theme.get("font", "Inter")
    gold = theme.get("gold", "#D4A853")

    # Top accent bar (thin Forest Green line across full width)
    bar_id = f"{slide_id}_topbar"
    _create_rect(reqs, slide_id, bar_id, 0, 0, SLIDE_W, 5, primary)

    # Bottom accent bar (thin gold line)
    bottom_id = f"{slide_id}_bottom"
    bar_color = theme.get("gold", "#D4A853")
    _create_rect(reqs, slide_id, bottom_id, 0, SLIDE_H - 4, SLIDE_W, 4, bar_color)

    # Wordmark top-left
    if mode == "dark":
        text_color = theme.get("text_on_dark", "#FFFFFF")
    else:
        text_color = theme.get("text_heading", "#1F2937")

    wid = f"{slide_id}_wm"
    _create_textbox(reqs, slide_id, wid, MARGIN_X, 14, 160, 18)

    wordmark = "ESTATE | MATE"
    _insert_text(reqs, wid, wordmark)
    _style_text(reqs, wid, 0, len(wordmark),
                font_size=8, bold=True, color=text_color, font_family=font)
    pipe_idx = wordmark.index("|")
    _style_text(reqs, wid, pipe_idx, pipe_idx + 1, color=gold)

    # URL top-right (like the template pattern)
    uid = f"{slide_id}_url"
    url_x = SLIDE_W - 200 - MARGIN_X
    _create_textbox(reqs, slide_id, uid, url_x, 14, 180, 18)
    url_text = "estatemate.io"
    _insert_text(reqs, uid, url_text)
    _style_text(reqs, uid, 0, len(url_text),
                font_size=8, color=theme.get("text_muted", "#9CA3AF"), font_family=font)
    _style_para(reqs, uid, 0, len(url_text), alignment="END")


def _add_overline(reqs, slide_id, overline_text, y, theme, mode="light"):
    """Add a gold uppercase overline label."""
    _, _, _, overline_color, _ = _resolve_colors({"mode": mode}, theme)
    font = theme.get("font", "Inter")

    oid = f"{slide_id}_ol"
    _create_textbox(reqs, slide_id, oid, MARGIN_X, y, CONTENT_W, 18)
    _insert_text(reqs, oid, overline_text)
    _style_text(reqs, oid, 0, len(overline_text),
                font_size=10, bold=True, color=overline_color, font_family=font)


def _add_slide_bg(reqs, slide_id, bg_color):
    """Set slide background color."""
    reqs.append({
        "updatePageProperties": {
            "objectId": slide_id,
            "pageProperties": {
                "pageBackgroundFill": {
                    "solidFill": {"color": {"rgbColor": _rgb(bg_color)}}
                }
            },
            "fields": "pageBackgroundFill.solidFill.color",
        }
    })


# =============================================================================
# Layout: TITLE (dark hero slide)
# =============================================================================

def _build_title_slide(reqs, slide_id, slide_def, theme):
    bg, title_c, body_c, gold_c, muted_c = _resolve_colors(slide_def, theme)
    font = theme.get("font", "Inter")
    gold = theme.get("gold", "#D4A853")
    mode = slide_def.get("mode", "light")

    _add_slide_bg(reqs, slide_id, bg)
    _add_chrome(reqs, slide_id, theme, mode, side_strip=False)

    title = slide_def.get("title", "")
    subtitle = slide_def.get("subtitle", "")
    accent_words = slide_def.get("title_accent_words", [])

    # Title - large, positioned lower-left for visual weight
    if title:
        tid = f"{slide_id}_title"
        _create_textbox(reqs, slide_id, tid, MARGIN_X, 120, CONTENT_W * 0.75, 160)
        _insert_text(reqs, tid, title)
        _style_text(reqs, tid, 0, len(title),
                     font_size=40, bold=True, color=title_c, font_family=font)
        _style_para(reqs, tid, 0, len(title), line_spacing=115)

        # Gold accent words
        for word in accent_words:
            idx = title.find(word)
            if idx >= 0:
                _style_text(reqs, tid, idx, idx + len(word), color=gold)

    # Subtitle
    if subtitle:
        sid = f"{slide_id}_sub"
        _create_textbox(reqs, slide_id, sid, MARGIN_X, 280, CONTENT_W * 0.7, 30)
        _insert_text(reqs, sid, subtitle)
        _style_text(reqs, sid, 0, len(subtitle),
                     font_size=16, color=muted_c, font_family=font)

    # Footer (e.g. "Edge Tech - März 2026")
    footer = slide_def.get("footer", "")
    if footer:
        fid = f"{slide_id}_footer"
        _create_textbox(reqs, slide_id, fid, MARGIN_X, SLIDE_H - 40, CONTENT_W * 0.5, 20)
        _insert_text(reqs, fid, footer)
        _style_text(reqs, fid, 0, len(footer),
                     font_size=10, color=muted_c, font_family=font)


# =============================================================================
# Layout: SECTION (dark divider)
# =============================================================================

def _build_section_slide(reqs, slide_id, slide_def, theme):
    bg, title_c, _, gold_c, muted_c = _resolve_colors(slide_def, theme)
    font = theme.get("font", "Inter")
    mode = slide_def.get("mode", "light")

    _add_slide_bg(reqs, slide_id, bg)
    _add_chrome(reqs, slide_id, theme, mode, side_strip=False)

    overline = slide_def.get("overline", "")
    title = slide_def.get("title", "")
    subtitle = slide_def.get("subtitle", "")

    y_start = 130 if not overline else 110

    if overline:
        oid = f"{slide_id}_ol"
        _create_textbox(reqs, slide_id, oid, MARGIN_X, y_start, CONTENT_W, 20)
        _insert_text(reqs, oid, overline)
        _style_text(reqs, oid, 0, len(overline),
                     font_size=10, bold=True, color=gold_c, font_family=font)
        _style_para(reqs, oid, 0, len(overline), alignment="CENTER")
        y_start += 28

    if title:
        tid = f"{slide_id}_title"
        _create_textbox(reqs, slide_id, tid, MARGIN_X, y_start, CONTENT_W, 100)
        _insert_text(reqs, tid, title)
        _style_text(reqs, tid, 0, len(title),
                     font_size=32, bold=True, color=title_c, font_family=font)
        _style_para(reqs, tid, 0, len(title), alignment="CENTER", line_spacing=120)

    if subtitle:
        sid = f"{slide_id}_sub"
        _create_textbox(reqs, slide_id, sid, MARGIN_X + 60, 280, CONTENT_W - 120, 40)
        _insert_text(reqs, sid, subtitle)
        _style_text(reqs, sid, 0, len(subtitle),
                     font_size=14, color=muted_c, font_family=font)
        _style_para(reqs, sid, 0, len(subtitle), alignment="CENTER")


# =============================================================================
# Layout: DIFFERENTIATOR (dark, centered impact statement)
# =============================================================================

def _build_differentiator_slide(reqs, slide_id, slide_def, theme):
    bg, title_c, body_c, gold_c, muted_c = _resolve_colors(slide_def, theme)
    font = theme.get("font", "Inter")
    gold = theme.get("gold", "#D4A853")
    mode = slide_def.get("mode", "dark")

    _add_slide_bg(reqs, slide_id, bg)
    _add_chrome(reqs, slide_id, theme, mode, side_strip=False)

    overline = slide_def.get("overline", "")
    title = slide_def.get("title", "")
    subtitle = slide_def.get("subtitle", "")

    # Overline centered
    if overline:
        oid = f"{slide_id}_ol"
        _create_textbox(reqs, slide_id, oid, MARGIN_X, 100, CONTENT_W, 20)
        _insert_text(reqs, oid, overline)
        _style_text(reqs, oid, 0, len(overline),
                     font_size=10, bold=True, color=gold_c, font_family=font)
        _style_para(reqs, oid, 0, len(overline), alignment="CENTER")

    # Title - big centered statement
    if title:
        tid = f"{slide_id}_title"
        _create_textbox(reqs, slide_id, tid, MARGIN_X + 40, 130, CONTENT_W - 80, 150)
        _insert_text(reqs, tid, title)
        _style_text(reqs, tid, 0, len(title),
                     font_size=30, bold=True, color=title_c, font_family=font)
        _style_para(reqs, tid, 0, len(title), alignment="CENTER", line_spacing=130)

        # Gold accent for key phrases
        for word in slide_def.get("title_accent_words", []):
            idx = title.find(word)
            if idx >= 0:
                _style_text(reqs, tid, idx, idx + len(word), color=gold)

    # Subtitle
    if subtitle:
        sid = f"{slide_id}_sub"
        _create_textbox(reqs, slide_id, sid, MARGIN_X + 60, 300, CONTENT_W - 120, 40)
        _insert_text(reqs, sid, subtitle)
        _style_text(reqs, sid, 0, len(subtitle),
                     font_size=14, color=muted_c, font_family=font)
        _style_para(reqs, sid, 0, len(subtitle), alignment="CENTER")


# =============================================================================
# Layout: TITLE_AND_BODY (content slide with bullets)
# =============================================================================

def _build_title_body_slide(reqs, slide_id, slide_def, theme):
    bg, title_c, body_c, gold_c, muted_c = _resolve_colors(slide_def, theme)
    font = theme.get("font", "Inter")
    mode = slide_def.get("mode", "light")

    _add_slide_bg(reqs, slide_id, bg)
    _add_chrome(reqs, slide_id, theme, mode, side_strip=False)

    overline = slide_def.get("overline", "")
    title = slide_def.get("title", "")
    body_items = slide_def.get("body", [])
    body_fs = slide_def.get("body_font_size", 15)

    y = CONTENT_TOP

    # Overline
    if overline:
        _add_overline(reqs, slide_id, overline, y, theme, mode)
        y += 24

    # Title - allow two lines for longer titles
    if title:
        tid = f"{slide_id}_title"
        title_h = 64 if len(title) > 35 else 42  # Taller box for wrapping titles
        _create_textbox(reqs, slide_id, tid, MARGIN_X, y, CONTENT_W * 0.85, title_h)
        _insert_text(reqs, tid, title)
        _style_text(reqs, tid, 0, len(title),
                     font_size=28, bold=True, color=title_c, font_family=font)
        y += title_h + 10

    # Gold accent bar under title
    bar_id = f"{slide_id}_bar"
    _create_rect(reqs, slide_id, bar_id, MARGIN_X, y, 40, 3,
                 theme.get("gold", "#D4A853"))
    y += 20

    # Body
    if body_items:
        bid = f"{slide_id}_body"
        body_w = CONTENT_W * 0.75  # Don't fill full width - leave breathing room
        body_h = SLIDE_H - y - MARGIN_Y
        _create_textbox(reqs, slide_id, bid, MARGIN_X, y, body_w, body_h)

        full_text = "\n".join(body_items)
        _insert_text(reqs, bid, full_text)
        _style_text(reqs, bid, 0, len(full_text),
                     font_size=body_fs, color=body_c, font_family=font)
        _style_para(reqs, bid, 0, len(full_text),
                     line_spacing=115, space_below=12)
        _style_para(reqs, bid, 0, len(full_text),
                     bullet_preset="BULLET_DISC_CIRCLE_SQUARE")

        # Bold "Label:" patterns
        offset = 0
        for item in body_items:
            if ": " in item and not item.startswith('"'):
                colon_pos = item.index(": ")
                _style_text(reqs, bid, offset, offset + colon_pos + 1, bold=True)
            offset += len(item) + 1


# =============================================================================
# Layout: TITLE_AND_TWO_COLUMNS
# =============================================================================

def _build_two_column_slide(reqs, slide_id, slide_def, theme):
    bg, title_c, body_c, gold_c, muted_c = _resolve_colors(slide_def, theme)
    font = theme.get("font", "Inter")
    mode = slide_def.get("mode", "light")
    accent = theme.get("gold", "#D4A853")

    _add_slide_bg(reqs, slide_id, bg)
    _add_chrome(reqs, slide_id, theme, mode, side_strip=False)

    overline = slide_def.get("overline", "")
    title = slide_def.get("title", "")
    body_fs = slide_def.get("body_font_size", 12)

    y = CONTENT_TOP
    col_gap = 24
    col_w = (CONTENT_W - col_gap) / 2

    # Overline
    if overline:
        _add_overline(reqs, slide_id, overline, y, theme, mode)
        y += 22

    # Title - accounts for multi-line titles with \n
    if title:
        tid = f"{slide_id}_title"
        num_lines = title.count("\n") + 1
        title_h = num_lines * 30 + 6
        _create_textbox(reqs, slide_id, tid, MARGIN_X, y, CONTENT_W, title_h)
        _insert_text(reqs, tid, title)
        _style_text(reqs, tid, 0, len(title),
                     font_size=24, bold=True, color=title_c, font_family=font)
        _style_para(reqs, tid, 0, len(title), line_spacing=120)
        y += title_h + 6

    # Gold bar
    bar_id = f"{slide_id}_bar"
    _create_rect(reqs, slide_id, bar_id, MARGIN_X, y, 40, 3, accent)
    y += 16

    # Left column
    left_title = slide_def.get("left_title", "")
    left_items = slide_def.get("left_body", [])
    col_h = SLIDE_H - y - MARGIN_Y

    if left_title or left_items:
        lid = f"{slide_id}_left"
        text = ""
        if left_title:
            text = left_title + "\n"
        text += "\n".join(left_items)
        _create_textbox(reqs, slide_id, lid, MARGIN_X, y, col_w, col_h)
        _insert_text(reqs, lid, text)

        offset = 0
        if left_title:
            _style_text(reqs, lid, 0, len(left_title),
                         font_size=14, bold=True, color=title_c, font_family=font)
            _style_para(reqs, lid, 0, len(left_title), space_below=8)
            offset = len(left_title) + 1
        if left_items:
            end = offset + len("\n".join(left_items))
            _style_text(reqs, lid, offset, end,
                         font_size=body_fs, color=body_c, font_family=font)
            _style_para(reqs, lid, offset, end,
                         line_spacing=115, space_below=10,
                         bullet_preset="BULLET_DISC_CIRCLE_SQUARE")

    # Right column
    right_title = slide_def.get("right_title", "")
    right_items = slide_def.get("right_body", [])
    right_x = MARGIN_X + col_w + col_gap

    if right_title or right_items:
        rid = f"{slide_id}_right"
        text = ""
        if right_title:
            text = right_title + "\n"
        text += "\n".join(right_items)
        _create_textbox(reqs, slide_id, rid, right_x, y, col_w, col_h)
        _insert_text(reqs, rid, text)

        offset = 0
        if right_title:
            _style_text(reqs, rid, 0, len(right_title),
                         font_size=14, bold=True, color=title_c, font_family=font)
            _style_para(reqs, rid, 0, len(right_title), space_below=8)
            offset = len(right_title) + 1
        if right_items:
            end = offset + len("\n".join(right_items))
            _style_text(reqs, rid, offset, end,
                         font_size=body_fs, color=body_c, font_family=font)
            _style_para(reqs, rid, offset, end,
                         line_spacing=115, space_below=10,
                         bullet_preset="BULLET_DISC_CIRCLE_SQUARE")


# =============================================================================
# Layout: TITLE_AND_TABLE
# =============================================================================

def _build_table_slide(reqs, slide_id, slide_def, theme):
    bg, title_c, body_c, gold_c, muted_c = _resolve_colors(slide_def, theme)
    font = theme.get("font", "Inter")
    mode = slide_def.get("mode", "light")
    primary = theme.get("primary", "#1A332F")

    _add_slide_bg(reqs, slide_id, bg)
    _add_chrome(reqs, slide_id, theme, mode, side_strip=False)

    overline = slide_def.get("overline", "")
    title = slide_def.get("title", "")
    table_data = slide_def.get("table", [])

    y = CONTENT_TOP
    if overline:
        _add_overline(reqs, slide_id, overline, y, theme, mode)
        y += 22
    if title:
        tid = f"{slide_id}_title"
        _create_textbox(reqs, slide_id, tid, MARGIN_X, y, CONTENT_W, 36)
        _insert_text(reqs, tid, title)
        _style_text(reqs, tid, 0, len(title),
                     font_size=24, bold=True, color=title_c, font_family=font)
        y += 46

    if not table_data:
        return

    rows = len(table_data)
    cols = len(table_data[0])
    table_id = f"{slide_id}_table"
    table_h = SLIDE_H - y - MARGIN_Y

    reqs.append({
        "createTable": {
            "objectId": table_id,
            "elementProperties": {
                "pageObjectId": slide_id,
                "size": {
                    "height": {"magnitude": _emu(table_h), "unit": "EMU"},
                    "width": {"magnitude": _emu(CONTENT_W), "unit": "EMU"},
                },
                "transform": {
                    "scaleX": 1, "scaleY": 1,
                    "translateX": _emu(MARGIN_X), "translateY": _emu(y),
                    "unit": "EMU",
                },
            },
            "rows": rows,
            "columns": cols,
        }
    })

    # Insert cell text
    for r, row in enumerate(table_data):
        for c, cell in enumerate(row):
            if cell:
                reqs.append({
                    "insertText": {
                        "objectId": table_id,
                        "cellLocation": {"rowIndex": r, "columnIndex": c},
                        "text": str(cell),
                        "insertionIndex": 0,
                    }
                })

    # Style header row - Forest Green background
    reqs.append({
        "updateTableCellProperties": {
            "objectId": table_id,
            "tableRange": {
                "location": {"rowIndex": 0, "columnIndex": 0},
                "rowSpan": 1, "columnSpan": cols,
            },
            "tableCellProperties": {
                "tableCellBackgroundFill": {
                    "solidFill": {"color": {"rgbColor": _rgb(primary)}}
                }
            },
            "fields": "tableCellBackgroundFill.solidFill.color",
        }
    })

    # Style last row (EstateMate) with gold tint
    if rows > 2:
        gold_tint = theme.get("gold_tint", "#FDF6E8")
        reqs.append({
            "updateTableCellProperties": {
                "objectId": table_id,
                "tableRange": {
                    "location": {"rowIndex": rows - 1, "columnIndex": 0},
                    "rowSpan": 1, "columnSpan": cols,
                },
                "tableCellProperties": {
                    "tableCellBackgroundFill": {
                        "solidFill": {"color": {"rgbColor": _rgb(gold_tint)}}
                    }
                },
                "fields": "tableCellBackgroundFill.solidFill.color",
            }
        })


# =============================================================================
# Layout: STATS (dark slide with stat cards)
# =============================================================================

def _build_stats_slide(reqs, slide_id, slide_def, theme):
    bg, title_c, body_c, gold_c, muted_c = _resolve_colors(slide_def, theme)
    font = theme.get("font", "Inter")
    gold = theme.get("gold", "#D4A853")
    primary_light = theme.get("primary_light", "#234540")
    mode = slide_def.get("mode", "dark")

    _add_slide_bg(reqs, slide_id, bg)
    _add_chrome(reqs, slide_id, theme, mode, side_strip=False)

    overline = slide_def.get("overline", "")
    title = slide_def.get("title", "")
    stats = slide_def.get("stats", [])

    y = CONTENT_TOP
    if overline:
        oid = f"{slide_id}_ol"
        _create_textbox(reqs, slide_id, oid, MARGIN_X, y, CONTENT_W, 18)
        _insert_text(reqs, oid, overline)
        _style_text(reqs, oid, 0, len(overline),
                     font_size=10, bold=True, color=gold_c, font_family=font)
        y += 22

    if title:
        tid = f"{slide_id}_title"
        _create_textbox(reqs, slide_id, tid, MARGIN_X, y, CONTENT_W, 36)
        _insert_text(reqs, tid, title)
        _style_text(reqs, tid, 0, len(title),
                     font_size=26, bold=True, color=title_c, font_family=font)
        y += 50

    # Stat cards
    if stats:
        card_count = len(stats)
        card_gap = 24
        card_w = (CONTENT_W - (card_gap * (card_count - 1))) / card_count
        card_h = 160
        card_y = y + 16

        for i, stat in enumerate(stats):
            card_x = MARGIN_X + i * (card_w + card_gap)

            # Card background (slightly lighter green on dark)
            cid = f"{slide_id}_card{i}"
            _create_rect(reqs, slide_id, cid, card_x, card_y, card_w, card_h,
                          primary_light, corner_radius=4)

            # Stat value (gold, large)
            vid = f"{slide_id}_val{i}"
            _create_textbox(reqs, slide_id, vid, card_x + 20, card_y + 20, card_w - 40, 50)
            value = stat.get("value", "")
            _insert_text(reqs, vid, value)
            _style_text(reqs, vid, 0, len(value),
                         font_size=32, bold=True, color=gold, font_family=font)

            # Label (white, overline style)
            label = stat.get("label", "")
            if label:
                labid = f"{slide_id}_lab{i}"
                _create_textbox(reqs, slide_id, labid, card_x + 20, card_y + 80, card_w - 40, 24)
                _insert_text(reqs, labid, label)
                _style_text(reqs, labid, 0, len(label),
                             font_size=9, bold=True, color=muted_c, font_family=font)

            # Sublabel
            sublabel = stat.get("sublabel", "")
            if sublabel:
                slid = f"{slide_id}_sl{i}"
                _create_textbox(reqs, slide_id, slid, card_x + 20, card_y + 108, card_w - 40, 28)
                _insert_text(reqs, slid, sublabel)
                _style_text(reqs, slid, 0, len(sublabel),
                             font_size=11, color=muted_c, font_family=font)


# =============================================================================
# Layout: STATEMENT (dark, centered single quote/sentence)
# =============================================================================

def _build_statement_slide(reqs, slide_id, slide_def, theme):
    bg, title_c, body_c, gold_c, muted_c = _resolve_colors(slide_def, theme)
    font = theme.get("font", "Inter")
    mode = slide_def.get("mode", "dark")

    _add_slide_bg(reqs, slide_id, bg)
    # No chrome on statement slides - pure breathing room

    text = slide_def.get("text", "")
    if text:
        tid = f"{slide_id}_text"
        _create_textbox(reqs, slide_id, tid, MARGIN_X + 60, 100, CONTENT_W - 120, 200)
        _insert_text(reqs, tid, text)
        _style_text(reqs, tid, 0, len(text),
                     font_size=22, color=title_c, font_family=font, italic=True)
        _style_para(reqs, tid, 0, len(text), alignment="CENTER", line_spacing=150)


# =============================================================================
# Layout: STAT_SPLIT (text left, stat cards right)
# =============================================================================

def _build_stat_split_slide(reqs, slide_id, slide_def, theme):
    bg, title_c, body_c, gold_c, muted_c = _resolve_colors(slide_def, theme)
    font = theme.get("font", "Inter")
    mode = slide_def.get("mode", "light")
    gold = theme.get("gold", "#D4A853")
    teal_tint = theme.get("teal_tint", "#E6F4F1")

    _add_slide_bg(reqs, slide_id, bg)
    _add_chrome(reqs, slide_id, theme, mode)

    overline = slide_def.get("overline", "")
    title = slide_def.get("title", "")
    body_text = slide_def.get("body_text", "")
    stats = slide_def.get("stats", [])

    y = CONTENT_TOP
    left_w = CONTENT_W * 0.55
    right_x = MARGIN_X + CONTENT_W * 0.60
    right_w = CONTENT_W * 0.40

    # Overline
    if overline:
        _add_overline(reqs, slide_id, overline, y, theme, mode)
        y += 24

    # Title - size accounts for multi-line wrapping
    if title:
        tid = f"{slide_id}_title"
        title_lines = max(2, len(title) // 25 + 1)  # estimate lines
        title_h = title_lines * 30
        _create_textbox(reqs, slide_id, tid, MARGIN_X, y, left_w, title_h)
        _insert_text(reqs, tid, title)
        _style_text(reqs, tid, 0, len(title),
                     font_size=22, bold=True, color=title_c, font_family=font)
        _style_para(reqs, tid, 0, len(title), line_spacing=120)
        y += title_h + 8

    # Gold bar
    bar_id = f"{slide_id}_bar"
    _create_rect(reqs, slide_id, bar_id, MARGIN_X, y, 40, 3, gold)
    y += 16

    # Body text (left)
    if body_text:
        bid = f"{slide_id}_body"
        _create_textbox(reqs, slide_id, bid, MARGIN_X, y, left_w, 160)
        _insert_text(reqs, bid, body_text)
        _style_text(reqs, bid, 0, len(body_text),
                     font_size=13, color=body_c, font_family=font)
        _style_para(reqs, bid, 0, len(body_text), line_spacing=115)

    # Stat cards (right side, 2x2 grid) - single shape per card, centered text
    if stats:
        card_w = (right_w - 12) / 2
        card_h = 80
        stat_y_start = CONTENT_TOP + 30

        for i, stat in enumerate(stats):
            col = i % 2
            row = i // 2
            cx = right_x + col * (card_w + 12)
            cy = stat_y_start + row * (card_h + 12)

            value = stat.get("value", "")
            label = stat.get("label", "")
            card_text = f"{value}\n{label}" if label else value

            # Single shape with fill, rounded corners, centered text
            cid = f"{slide_id}_sc{i}"
            _create_rect(reqs, slide_id, cid, cx, cy, card_w, card_h,
                          teal_tint, corner_radius=4)
            _insert_text(reqs, cid, card_text)

            # Style value portion (bold, large)
            _style_text(reqs, cid, 0, len(value),
                         font_size=24, bold=True,
                         color=theme.get("primary", "#1A332F"), font_family=font)
            _style_para(reqs, cid, 0, len(value), alignment="CENTER", space_above=10)

            # Style label portion (smaller, muted)
            if label:
                label_start = len(value) + 1
                _style_text(reqs, cid, label_start, label_start + len(label),
                             font_size=11, color=muted_c, font_family=font)
                _style_para(reqs, cid, label_start, label_start + len(label),
                             alignment="CENTER")


# =============================================================================
# Layout: MATRIX (feature comparison with colored symbols)
# =============================================================================

def _build_matrix_slide(reqs, slide_id, slide_def, theme):
    bg, title_c, body_c, gold_c, muted_c = _resolve_colors(slide_def, theme)
    font = theme.get("font", "Inter")
    mode = slide_def.get("mode", "light")
    primary = theme.get("primary", "#1A332F")
    teal = theme.get("teal", "#2D8B75")
    coral = theme.get("coral", "#C75B4A")
    gold = theme.get("gold", "#D4A853")
    teal_tint = theme.get("teal_tint", "#E6F4F1")

    _add_slide_bg(reqs, slide_id, bg)
    _add_chrome(reqs, slide_id, theme, mode)

    overline = slide_def.get("overline", "")
    title = slide_def.get("title", "")
    columns = slide_def.get("columns", [])
    data_rows = slide_def.get("rows", [])

    # Use tighter top for data-dense matrix
    y = CONTENT_TOP - 4
    if overline:
        _add_overline(reqs, slide_id, overline, y, theme, mode)
        y += 16
    if title:
        tid = f"{slide_id}_title"
        _create_textbox(reqs, slide_id, tid, MARGIN_X, y, CONTENT_W, 28)
        _insert_text(reqs, tid, title)
        _style_text(reqs, tid, 0, len(title),
                     font_size=20, bold=True, color=title_c, font_family=font)
        y += 34  # 28pt box + 6pt gap before table

    if not columns or not data_rows:
        return

    num_rows = len(data_rows) + 1  # +1 for header
    num_cols = len(columns)
    table_id = f"{slide_id}_matrix"
    table_h = SLIDE_H - y - 8  # extend to just above gold bar
    table_w = CONTENT_W

    # Create table
    reqs.append({
        "createTable": {
            "objectId": table_id,
            "elementProperties": {
                "pageObjectId": slide_id,
                "size": {
                    "height": {"magnitude": _emu(table_h), "unit": "EMU"},
                    "width": {"magnitude": _emu(table_w), "unit": "EMU"},
                },
                "transform": {
                    "scaleX": 1, "scaleY": 1,
                    "translateX": _emu(MARGIN_X), "translateY": _emu(y),
                    "unit": "EMU",
                },
            },
            "rows": num_rows,
            "columns": num_cols,
        }
    })

    # Vertically center all cells
    reqs.append({
        "updateTableCellProperties": {
            "objectId": table_id,
            "tableRange": {
                "location": {"rowIndex": 0, "columnIndex": 0},
                "rowSpan": num_rows, "columnSpan": num_cols,
            },
            "tableCellProperties": {
                "contentAlignment": "MIDDLE",
            },
            "fields": "contentAlignment",
        }
    })

    # Insert header text
    for c, col_name in enumerate(columns):
        reqs.append({
            "insertText": {
                "objectId": table_id,
                "cellLocation": {"rowIndex": 0, "columnIndex": c},
                "text": col_name,
                "insertionIndex": 0,
            }
        })

    # Insert data rows
    for r, row in enumerate(data_rows):
        for c, cell in enumerate(row):
            if cell:
                reqs.append({
                    "insertText": {
                        "objectId": table_id,
                        "cellLocation": {"rowIndex": r + 1, "columnIndex": c},
                        "text": str(cell),
                        "insertionIndex": 0,
                    }
                })

    # Compact line spacing for all cells (must be after text insertion)
    for r in range(num_rows):
        for c in range(num_cols):
            reqs.append({
                "updateParagraphStyle": {
                    "objectId": table_id,
                    "cellLocation": {"rowIndex": r, "columnIndex": c},
                    "textRange": {"type": "ALL"},
                    "style": {
                        "lineSpacing": 90,
                        "spaceAbove": {"magnitude": 0, "unit": "PT"},
                        "spaceBelow": {"magnitude": 0, "unit": "PT"},
                    },
                    "fields": "lineSpacing,spaceAbove,spaceBelow",
                }
            })

    # Style header row - dark bg, white text
    reqs.append({
        "updateTableCellProperties": {
            "objectId": table_id,
            "tableRange": {
                "location": {"rowIndex": 0, "columnIndex": 0},
                "rowSpan": 1, "columnSpan": num_cols,
            },
            "tableCellProperties": {
                "tableCellBackgroundFill": {
                    "solidFill": {"color": {"rgbColor": _rgb(primary)}}
                }
            },
            "fields": "tableCellBackgroundFill.solidFill.color",
        }
    })

    # Style EstateMate column (last column) with teal tint for data rows
    reqs.append({
        "updateTableCellProperties": {
            "objectId": table_id,
            "tableRange": {
                "location": {"rowIndex": 1, "columnIndex": num_cols - 1},
                "rowSpan": num_rows - 1, "columnSpan": 1,
            },
            "tableCellProperties": {
                "tableCellBackgroundFill": {
                    "solidFill": {"color": {"rgbColor": _rgb(teal_tint)}}
                }
            },
            "fields": "tableCellBackgroundFill.solidFill.color",
        }
    })

    # We need to style individual cells after creation - this requires a second
    # batchUpdate pass since we need the table to exist first. Store deferred
    # requests that the caller should execute in a second batch.
    # For now, we'll use text styling to color the symbols.
    # The symbols are: \u2713 (✓), \u2717 (✗), \u25d1 (◑)

    # Symbol colors are applied per-cell after table creation
    for r, row in enumerate(data_rows):
        for c, cell in enumerate(row):
            cell_str = str(cell)
            if cell_str in ("\u2713", "\u2717", "\u25d1"):
                if cell_str == "\u2713":
                    sym_color = teal
                elif cell_str == "\u2717":
                    sym_color = coral
                else:
                    sym_color = gold

                reqs.append({
                    "updateTextStyle": {
                        "objectId": table_id,
                        "cellLocation": {"rowIndex": r + 1, "columnIndex": c},
                        "textRange": {"type": "ALL"},
                        "style": {
                            "foregroundColor": {"opaqueColor": {"rgbColor": _rgb(sym_color)}},
                            "fontSize": {"magnitude": 10, "unit": "PT"},
                            "bold": True,
                            "fontFamily": font,
                        },
                        "fields": "foregroundColor,fontSize,bold,fontFamily",
                    }
                })

                # Center align symbols
                reqs.append({
                    "updateParagraphStyle": {
                        "objectId": table_id,
                        "cellLocation": {"rowIndex": r + 1, "columnIndex": c},
                        "textRange": {"type": "ALL"},
                        "style": {"alignment": "CENTER"},
                        "fields": "alignment",
                    }
                })
            elif c == 0:
                # First column (capability name) - left aligned, compact
                reqs.append({
                    "updateTextStyle": {
                        "objectId": table_id,
                        "cellLocation": {"rowIndex": r + 1, "columnIndex": c},
                        "textRange": {"type": "ALL"},
                        "style": {
                            "fontSize": {"magnitude": 8, "unit": "PT"},
                            "fontFamily": font,
                            "foregroundColor": {"opaqueColor": {"rgbColor": _rgb(body_c)}},
                        },
                        "fields": "fontSize,fontFamily,foregroundColor",
                    }
                })

    # Style header text - white, bold, compact
    for c in range(num_cols):
        reqs.append({
            "updateTextStyle": {
                "objectId": table_id,
                "cellLocation": {"rowIndex": 0, "columnIndex": c},
                "textRange": {"type": "ALL"},
                "style": {
                    "foregroundColor": {"opaqueColor": {"rgbColor": _rgb("#FFFFFF")}},
                    "fontSize": {"magnitude": 8, "unit": "PT"},
                    "bold": True,
                    "fontFamily": font,
                },
                "fields": "foregroundColor,fontSize,bold,fontFamily",
            }
        })
        reqs.append({
            "updateParagraphStyle": {
                "objectId": table_id,
                "cellLocation": {"rowIndex": 0, "columnIndex": c},
                "textRange": {"type": "ALL"},
                "style": {"alignment": "CENTER"},
                "fields": "alignment",
            }
        })


# =============================================================================
# Layout: TEAM (three columns with headshot placeholders)
# =============================================================================

def _build_team_slide(reqs, slide_id, slide_def, theme):
    bg, title_c, body_c, gold_c, muted_c = _resolve_colors(slide_def, theme)
    font = theme.get("font", "Inter")
    mode = slide_def.get("mode", "light")
    gold = theme.get("gold", "#D4A853")
    border = theme.get("border", "#E5E7EB")

    _add_slide_bg(reqs, slide_id, bg)
    _add_chrome(reqs, slide_id, theme, mode)

    overline = slide_def.get("overline", "")
    title = slide_def.get("title", "")
    members = slide_def.get("members", [])

    y = CONTENT_TOP
    if overline:
        _add_overline(reqs, slide_id, overline, y, theme, mode)
        y += 22
    if title:
        tid = f"{slide_id}_title"
        _create_textbox(reqs, slide_id, tid, MARGIN_X, y, CONTENT_W, 36)
        _insert_text(reqs, tid, title)
        _style_text(reqs, tid, 0, len(title),
                     font_size=24, bold=True, color=title_c, font_family=font)
        y += 46

    # Three columns for team members
    if not members:
        return

    col_count = len(members)
    col_gap = 24
    col_w = (CONTENT_W - col_gap * (col_count - 1)) / col_count

    for i, member in enumerate(members):
        cx = MARGIN_X + i * (col_w + col_gap)

        # Headshot placeholder (circle-ish rounded rect)
        photo_size = 72
        photo_x = cx + (col_w - photo_size) / 2
        pid = f"{slide_id}_photo{i}"
        _create_rect(reqs, slide_id, pid, photo_x, y, photo_size, photo_size,
                      border, corner_radius=8)

        # Name
        name = member.get("name", "")
        nid = f"{slide_id}_name{i}"
        name_y = y + photo_size + 10
        _create_textbox(reqs, slide_id, nid, cx, name_y, col_w, 22)
        _insert_text(reqs, nid, name)
        _style_text(reqs, nid, 0, len(name),
                     font_size=14, bold=True, color=title_c, font_family=font)
        _style_para(reqs, nid, 0, len(name), alignment="CENTER")

        # Role
        role = member.get("role", "")
        rid = f"{slide_id}_role{i}"
        role_y = name_y + 22
        _create_textbox(reqs, slide_id, rid, cx, role_y, col_w, 18)
        _insert_text(reqs, rid, role)
        _style_text(reqs, rid, 0, len(role),
                     font_size=10, color=gold_c, font_family=font)
        _style_para(reqs, rid, 0, len(role), alignment="CENTER")

        # Bio
        bio = member.get("bio", "")
        biid = f"{slide_id}_bio{i}"
        bio_y = role_y + 24
        _create_textbox(reqs, slide_id, biid, cx + 8, bio_y, col_w - 16, 80)
        _insert_text(reqs, biid, bio)
        _style_text(reqs, biid, 0, len(bio),
                     font_size=10, color=body_c, font_family=font)
        _style_para(reqs, biid, 0, len(bio), alignment="CENTER", line_spacing=150)


# =============================================================================
# Main creation logic
# =============================================================================

LAYOUT_BUILDERS = {
    "TITLE": _build_title_slide,
    "SECTION": _build_section_slide,
    "DIFFERENTIATOR": _build_differentiator_slide,
    "TITLE_AND_BODY": _build_title_body_slide,
    "TITLE_AND_TWO_COLUMNS": _build_two_column_slide,
    "TITLE_AND_TABLE": _build_table_slide,
    "STATS": _build_stats_slide,
    "STATEMENT": _build_statement_slide,
    "STAT_SPLIT": _build_stat_split_slide,
    "MATRIX": _build_matrix_slide,
    "TEAM": _build_team_slide,
}


def create_from_json(project, json_path):
    """Create a presentation from JSON definition with brand support."""
    with open(json_path) as f:
        deck = json.load(f)

    title = deck.get("title", "Untitled")
    slides = deck.get("slides", [])
    theme = deck.get("theme", {})
    folder_id = deck.get("folder_id")

    service = get_slides_service(project=project)

    # Create presentation
    pres = service.presentations().create(body={"title": title}).execute()
    pres_id = pres["presentationId"]
    default_slide_id = pres["slides"][0]["objectId"]

    reqs = []
    reqs.append({"deleteObject": {"objectId": default_slide_id}})

    for i, slide_def in enumerate(slides):
        slide_id = _sid()
        layout = slide_def.get("layout", "TITLE_AND_BODY")

        # Create blank slide
        reqs.append({
            "createSlide": {
                "objectId": slide_id,
                "insertionIndex": i,
                "slideLayoutReference": {"predefinedLayout": "BLANK"},
            }
        })

        # Build slide content
        builder = LAYOUT_BUILDERS.get(layout, _build_title_body_slide)
        builder(reqs, slide_id, slide_def, theme)

    # Execute main batch
    if reqs:
        service.presentations().batchUpdate(
            presentationId=pres_id, body={"requests": reqs}
        ).execute()

    # Speaker notes (separate pass - requires reading note page IDs)
    pres_data = service.presentations().get(presentationId=pres_id).execute()
    notes_reqs = []

    for i, slide_def in enumerate(slides):
        notes_text = slide_def.get("speaker_notes", "")
        if notes_text and i < len(pres_data["slides"]):
            slide_data = pres_data["slides"][i]
            notes_page = slide_data.get("slideProperties", {}).get("notesPage", {})
            for element in notes_page.get("pageElements", []):
                if "shape" in element:
                    placeholder = element["shape"].get("placeholder", {})
                    if placeholder.get("type") == "BODY":
                        notes_reqs.append({
                            "insertText": {
                                "objectId": element["objectId"],
                                "text": notes_text,
                                "insertionIndex": 0,
                            }
                        })
                        break

    if notes_reqs:
        service.presentations().batchUpdate(
            presentationId=pres_id, body={"requests": notes_reqs}
        ).execute()

    # Move to folder
    if folder_id:
        drive = get_drive_service(project=project)
        drive.files().update(
            fileId=pres_id,
            addParents=folder_id,
            removeParents="root",
            fields="id, parents",
        ).execute()

    url = f"https://docs.google.com/presentation/d/{pres_id}/edit"
    print(json.dumps({
        "presentation_id": pres_id,
        "title": title,
        "url": url,
        "slides_count": len(slides),
    }))
    return pres_id


def update_from_json(project, pres_id, json_path, slides_filter=None):
    """Update an existing presentation from JSON.

    slides_filter: optional list of 0-based slide indices. When provided,
    only those slides are rebuilt (tier 2 - insert new, delete old per slide).
    When None, full rebuild (existing behavior).
    """
    with open(json_path) as f:
        deck = json.load(f)

    slides = deck.get("slides", [])
    theme = deck.get("theme", {})
    new_title = deck.get("title")

    service = get_slides_service(project=project)

    # Rename if title changed
    if new_title:
        drive = get_drive_service(project=project)
        drive.files().update(fileId=pres_id, body={"name": new_title}).execute()

    pres = service.presentations().get(presentationId=pres_id).execute()
    existing_slides = pres.get("slides", [])

    if slides_filter is not None:
        # Selective rebuild (tier 2): replace only specified slides
        # Process in reverse to avoid index shift issues within one batch
        reqs = []
        for idx in sorted(slides_filter, reverse=True):
            if idx >= len(existing_slides) or idx >= len(slides):
                print(f"WARNING: Skipping slide {idx} (out of range)", file=sys.stderr)
                continue
            old_id = existing_slides[idx]["objectId"]
            new_id = _sid()
            slide_def = slides[idx]

            # Insert new slide at same position (pushes old to idx+1)
            reqs.append({
                "createSlide": {
                    "objectId": new_id,
                    "insertionIndex": idx,
                    "slideLayoutReference": {"predefinedLayout": "BLANK"},
                }
            })

            layout = slide_def.get("layout", "TITLE_AND_BODY")
            builder = LAYOUT_BUILDERS.get(layout, _build_title_body_slide)
            builder(reqs, new_id, slide_def, theme)

            # Delete old slide (now shifted to idx+1)
            reqs.append({"deleteObject": {"objectId": old_id}})

        if reqs:
            service.presentations().batchUpdate(
                presentationId=pres_id, body={"requests": reqs}
            ).execute()
    else:
        # Full rebuild (existing behavior)
        reqs = []
        new_slide_ids = []
        for i, slide_def in enumerate(slides):
            slide_id = _sid()
            new_slide_ids.append(slide_id)
            reqs.append({
                "createSlide": {
                    "objectId": slide_id,
                    "insertionIndex": len(existing_slides) + i,
                    "slideLayoutReference": {"predefinedLayout": "BLANK"},
                }
            })

        for old_slide in existing_slides:
            reqs.append({"deleteObject": {"objectId": old_slide["objectId"]}})

        for i, slide_def in enumerate(slides):
            layout = slide_def.get("layout", "TITLE_AND_BODY")
            builder = LAYOUT_BUILDERS.get(layout, _build_title_body_slide)
            builder(reqs, new_slide_ids[i], slide_def, theme)

        if reqs:
            service.presentations().batchUpdate(
                presentationId=pres_id, body={"requests": reqs}
            ).execute()

    # Speaker notes (separate pass)
    pres_data = service.presentations().get(presentationId=pres_id).execute()
    notes_reqs = []
    target_indices = slides_filter if slides_filter is not None else range(len(slides))

    for i in target_indices:
        if i >= len(slides):
            continue
        notes_text = slides[i].get("speaker_notes", "")
        if notes_text and i < len(pres_data["slides"]):
            slide_data = pres_data["slides"][i]
            notes_page = slide_data.get("slideProperties", {}).get("notesPage", {})
            for element in notes_page.get("pageElements", []):
                if "shape" in element:
                    placeholder = element["shape"].get("placeholder", {})
                    if placeholder.get("type") == "BODY":
                        notes_reqs.append({
                            "insertText": {
                                "objectId": element["objectId"],
                                "text": notes_text,
                                "insertionIndex": 0,
                            }
                        })
                        break

    if notes_reqs:
        service.presentations().batchUpdate(
            presentationId=pres_id, body={"requests": notes_reqs}
        ).execute()

    url = f"https://docs.google.com/presentation/d/{pres_id}/edit"
    action = f"updated (slides {','.join(map(str, slides_filter))})" if slides_filter else "updated"
    print(json.dumps({
        "presentation_id": pres_id,
        "title": new_title or pres.get("title", ""),
        "url": url,
        "slides_count": len(slides),
        "action": action,
    }))
    return pres_id


def create_presentation(project, title):
    """Create an empty presentation."""
    service = get_slides_service(project=project)
    pres = service.presentations().create(body={"title": title}).execute()
    pres_id = pres["presentationId"]
    print(json.dumps({
        "presentation_id": pres_id,
        "title": title,
        "url": f"https://docs.google.com/presentation/d/{pres_id}/edit",
    }))
    return pres_id


def list_slides(project, pres_id):
    """List slides in a presentation."""
    service = get_slides_service(project=project)
    pres = service.presentations().get(presentationId=pres_id).execute()
    info = []
    for i, s in enumerate(pres.get("slides", [])):
        entry = {"index": i, "objectId": s["objectId"]}
        for el in s.get("pageElements", []):
            if "shape" in el:
                for te in el["shape"].get("text", {}).get("textElements", []):
                    if "textRun" in te:
                        content = te["textRun"]["content"].strip()
                        if content:
                            entry.setdefault("first_text", content[:80])
                            break
        info.append(entry)
    print(json.dumps({"presentation_id": pres_id, "title": pres.get("title", ""), "slides": info}, indent=2))


def get_presentation(project, pres_id):
    """Get presentation metadata."""
    service = get_slides_service(project=project)
    pres = service.presentations().get(presentationId=pres_id).execute()
    print(json.dumps({
        "presentation_id": pres["presentationId"],
        "title": pres.get("title", ""),
        "slides_count": len(pres.get("slides", [])),
        "url": f"https://docs.google.com/presentation/d/{pres['presentationId']}/edit",
    }, indent=2))


def move_presentation(project, pres_id, folder_id):
    """Move presentation to a Drive folder."""
    drive = get_drive_service(project=project)
    f = drive.files().get(fileId=pres_id, fields="parents").execute()
    prev = ",".join(f.get("parents", []))
    drive.files().update(fileId=pres_id, addParents=folder_id, removeParents=prev, fields="id,parents").execute()
    print(json.dumps({"moved": True, "presentation_id": pres_id, "folder_id": folder_id}))


def export_pdf(project, pres_id, output_path=None):
    """Export presentation as PDF."""
    drive = get_drive_service(project=project)
    content = drive.files().export_media(fileId=pres_id, mimeType="application/pdf").execute()
    if output_path:
        path = Path(output_path)
    else:
        path = Path(f"presentation_{pres_id}.pdf")
    path.write_bytes(content)
    print(json.dumps({"exported": True, "path": str(path), "size_bytes": len(content)}))


# =============================================================================
# Slide resolution
# =============================================================================

def _resolve_slide(pres, slide_ref):
    """Find a slide by 0-based index or title substring. Returns (slide_data, index)."""
    slides = pres.get("slides", [])
    # Try numeric index
    try:
        idx = int(slide_ref)
        if 0 <= idx < len(slides):
            return slides[idx], idx
        raise ValueError(f"Slide index {idx} out of range (0-{len(slides) - 1})")
    except (ValueError, TypeError) as e:
        if "out of range" in str(e):
            raise
    # Title substring match
    ref_lower = str(slide_ref).lower()
    for i, s in enumerate(slides):
        for el in s.get("pageElements", []):
            if "shape" in el:
                for te in el["shape"].get("text", {}).get("textElements", []):
                    if "textRun" in te:
                        if ref_lower in te["textRun"]["content"].strip().lower():
                            return s, i
    raise ValueError(f"Slide not found: {slide_ref}")


# =============================================================================
# Slide inspection (tier 1 foundation)
# =============================================================================

def _extract_element_info(element):
    """Extract structured info from a page element."""
    info = {"objectId": element["objectId"]}

    if "shape" in element:
        info["type"] = element["shape"].get("shapeType", "SHAPE")
    elif "table" in element:
        info["type"] = "TABLE"
        info["rows"] = element["table"]["rows"]
        info["columns"] = element["table"]["columns"]
    elif "image" in element:
        info["type"] = "IMAGE"
    elif "line" in element:
        info["type"] = "LINE"
    else:
        info["type"] = "UNKNOWN"

    transform = element.get("transform", {})
    size = element.get("size", {})
    sx = transform.get("scaleX", 1)
    sy = transform.get("scaleY", 1)
    w_emu = size.get("width", {}).get("magnitude", 0)
    h_emu = size.get("height", {}).get("magnitude", 0)

    info["position"] = {
        "x_pt": round(transform.get("translateX", 0) / 12700, 1),
        "y_pt": round(transform.get("translateY", 0) / 12700, 1),
    }
    info["size"] = {
        "w_pt": round(w_emu * sx / 12700, 1),
        "h_pt": round(h_emu * sy / 12700, 1),
    }

    # Text content and style summary
    text_parts = []
    text_styles = []
    if "shape" in element:
        for te in element["shape"].get("text", {}).get("textElements", []):
            if "textRun" in te:
                text_parts.append(te["textRun"]["content"])
                style = te["textRun"].get("style", {})
                style_info = {}
                fs = style.get("fontSize", {})
                if fs:
                    style_info["font_size"] = fs.get("magnitude")
                if style.get("bold"):
                    style_info["bold"] = True
                if style.get("italic"):
                    style_info["italic"] = True
                fg = style.get("foregroundColor", {}).get("opaqueColor", {}).get("rgbColor", {})
                if fg:
                    r = int(fg.get("red", 0) * 255)
                    g = int(fg.get("green", 0) * 255)
                    b = int(fg.get("blue", 0) * 255)
                    style_info["color"] = f"#{r:02X}{g:02X}{b:02X}"
                if style_info:
                    text_styles.append(style_info)

    full_text = "".join(text_parts).rstrip("\n")
    if full_text:
        info["text_preview"] = full_text[:80] + ("..." if len(full_text) > 80 else "")
        info["text_length"] = len(full_text)
    if text_styles:
        info["text_styles"] = text_styles[:3]

    return info


def inspect_slide(project, pres_id, slide_ref):
    """Print every element on a slide with objectId, type, position, size, text preview."""
    service = get_slides_service(project=project)
    pres = service.presentations().get(presentationId=pres_id).execute()
    slide, idx = _resolve_slide(pres, slide_ref)

    elements = [_extract_element_info(el) for el in slide.get("pageElements", [])]
    result = {
        "slide_index": idx,
        "slide_objectId": slide["objectId"],
        "element_count": len(elements),
        "elements": elements,
    }
    print(json.dumps(result, indent=2))
    return result


# =============================================================================
# Surgical editing helpers (tier 1)
# =============================================================================

def _replace_text(reqs, object_id, new_text):
    """Clear existing text and insert new text in an element."""
    reqs.append({"deleteText": {"objectId": object_id, "textRange": {"type": "ALL"}}})
    if new_text:
        reqs.append({"insertText": {"objectId": object_id, "text": new_text, "insertionIndex": 0}})


def _update_shape_fill(reqs, object_id, fill_color):
    """Change the background fill of an existing shape."""
    reqs.append({
        "updateShapeProperties": {
            "objectId": object_id,
            "shapeProperties": {
                "shapeBackgroundFill": {
                    "solidFill": {"color": {"rgbColor": _rgb(fill_color)}}
                }
            },
            "fields": "shapeBackgroundFill.solidFill.color",
        }
    })


def patch_slide(project, pres_id, operations):
    """Apply surgical operations to specific elements.

    operations: list of dicts, each with:
      - "object_id": target element ID (from inspect)
      - "update_text": new text content (optional)
      - "style_text": {start, end, font_size, bold, color, ...} (optional)
      - "style_para": {start, end, alignment, line_spacing, ...} (optional)
      - "move": {x, y, w, h} in pt (optional, only changes provided values)
      - "fill": "#RRGGBB" (optional)
      - "delete": true (optional - remove element entirely)
    """
    service = get_slides_service(project=project)
    pres = service.presentations().get(presentationId=pres_id).execute()

    # Build element lookup for move operations (need original size/transform)
    el_lookup = {}
    for slide in pres.get("slides", []):
        for el in slide.get("pageElements", []):
            el_lookup[el["objectId"]] = el

    reqs = []
    for op in operations:
        oid = op.get("object_id")
        if not oid:
            continue

        if op.get("delete"):
            reqs.append({"deleteObject": {"objectId": oid}})
            continue

        if "update_text" in op:
            _replace_text(reqs, oid, op["update_text"])

        if "style_text" in op:
            st = op["style_text"]
            _style_text(reqs, oid, st.get("start", 0), st["end"],
                        font_size=st.get("font_size"), bold=st.get("bold"),
                        color=st.get("color"), font_family=st.get("font_family"),
                        italic=st.get("italic"), underline=st.get("underline"))

        if "style_para" in op:
            sp = op["style_para"]
            _style_para(reqs, oid, sp.get("start", 0), sp["end"],
                        alignment=sp.get("alignment"),
                        space_above=sp.get("space_above"),
                        space_below=sp.get("space_below"),
                        line_spacing=sp.get("line_spacing"))

        if "move" in op:
            el_data = el_lookup.get(oid)
            if el_data:
                cur_t = el_data.get("transform", {})
                cur_s = el_data.get("size", {})
                move = op["move"]
                orig_w = cur_s.get("width", {}).get("magnitude", 1)
                orig_h = cur_s.get("height", {}).get("magnitude", 1)

                reqs.append({
                    "updatePageElementTransform": {
                        "objectId": oid,
                        "transform": {
                            "scaleX": (_emu(move["w"]) / orig_w) if "w" in move else cur_t.get("scaleX", 1),
                            "scaleY": (_emu(move["h"]) / orig_h) if "h" in move else cur_t.get("scaleY", 1),
                            "translateX": _emu(move["x"]) if "x" in move else cur_t.get("translateX", 0),
                            "translateY": _emu(move["y"]) if "y" in move else cur_t.get("translateY", 0),
                            "shearX": cur_t.get("shearX", 0),
                            "shearY": cur_t.get("shearY", 0),
                            "unit": "EMU",
                        },
                        "applyMode": "ABSOLUTE",
                    }
                })

        if "fill" in op:
            _update_shape_fill(reqs, oid, op["fill"])

    if reqs:
        service.presentations().batchUpdate(
            presentationId=pres_id, body={"requests": reqs}
        ).execute()
        print(json.dumps({"patched": True, "operations": len(operations), "requests": len(reqs)}))
    else:
        print(json.dumps({"patched": False, "reason": "no valid operations"}))


# =============================================================================
# Single-slide rebuild (tier 2)
# =============================================================================

def rebuild_slide(project, pres_id, slide_ref, slide_def, theme):
    """Clear one slide and rebuild from layout definition. Other slides untouched.

    Strategy: insert new blank slide at same position (pushes old slide to idx+1),
    build content on new slide, delete old slide. All in one batchUpdate.
    """
    service = get_slides_service(project=project)
    pres = service.presentations().get(presentationId=pres_id).execute()
    slide, idx = _resolve_slide(pres, slide_ref)
    old_id = slide["objectId"]

    reqs = []
    new_id = _sid()

    # Insert replacement slide at same position
    reqs.append({
        "createSlide": {
            "objectId": new_id,
            "insertionIndex": idx,
            "slideLayoutReference": {"predefinedLayout": "BLANK"},
        }
    })

    # Build content
    layout = slide_def.get("layout", "TITLE_AND_BODY")
    builder = LAYOUT_BUILDERS.get(layout, _build_title_body_slide)
    builder(reqs, new_id, slide_def, theme)

    # Delete old slide (now at idx+1)
    reqs.append({"deleteObject": {"objectId": old_id}})

    service.presentations().batchUpdate(
        presentationId=pres_id, body={"requests": reqs}
    ).execute()

    # Speaker notes (separate pass - need to read new slide's notes page ID)
    notes_text = slide_def.get("speaker_notes", "")
    if notes_text:
        pres_data = service.presentations().get(presentationId=pres_id).execute()
        if idx < len(pres_data["slides"]):
            notes_page = pres_data["slides"][idx].get("slideProperties", {}).get("notesPage", {})
            for element in notes_page.get("pageElements", []):
                if "shape" in element:
                    placeholder = element["shape"].get("placeholder", {})
                    if placeholder.get("type") == "BODY":
                        service.presentations().batchUpdate(
                            presentationId=pres_id,
                            body={"requests": [{"insertText": {
                                "objectId": element["objectId"],
                                "text": notes_text,
                                "insertionIndex": 0,
                            }}]}
                        ).execute()
                        break

    print(json.dumps({
        "rebuilt": True,
        "slide_index": idx,
        "old_objectId": old_id,
        "new_objectId": new_id,
        "layout": layout,
    }))


def main():
    parser = argparse.ArgumentParser(description="Google Slides Operations")
    parser.add_argument("--project", "-p", help="Project (anaro-labs, estate-mate)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--create", action="store_true")
    group.add_argument("--create-from-json", metavar="JSON_PATH")
    group.add_argument("--update-from-json", metavar="JSON_PATH")
    group.add_argument("--inspect", metavar="PRES_ID", help="Inspect elements on a slide")
    group.add_argument("--patch", metavar="PRES_ID", help="Apply surgical element-level patches")
    group.add_argument("--rebuild-slide", metavar="PRES_ID", help="Clear and rebuild one slide")
    group.add_argument("--list", metavar="PRES_ID")
    group.add_argument("--get", metavar="PRES_ID")
    group.add_argument("--move", metavar="PRES_ID")
    group.add_argument("--export-pdf", metavar="PRES_ID", help="Export presentation as PDF")
    parser.add_argument("--title")
    parser.add_argument("--folder")
    parser.add_argument("--output", help="Output file path for export")
    parser.add_argument("--pres-id", help="Presentation ID for update operations")
    parser.add_argument("--slide", help="Slide index (0-based) or title substring")
    parser.add_argument("--slides", help="Comma-separated slide indices for selective update")
    parser.add_argument("--ops", help="Path to operations JSON file (for --patch, --rebuild-slide)")
    args = parser.parse_args()

    if args.create:
        create_presentation(args.project, args.title or "Untitled")
    elif args.create_from_json:
        create_from_json(args.project, args.create_from_json)
    elif args.update_from_json:
        if not args.pres_id:
            print("ERROR: --pres-id required for update", file=sys.stderr)
            sys.exit(1)
        slides_filter = None
        if args.slides:
            slides_filter = [int(x.strip()) for x in args.slides.split(",")]
        update_from_json(args.project, args.pres_id, args.update_from_json, slides_filter)
    elif args.inspect:
        if not args.slide:
            print("ERROR: --slide required for inspect", file=sys.stderr)
            sys.exit(1)
        inspect_slide(args.project, args.inspect, args.slide)
    elif args.patch:
        if not args.ops:
            print("ERROR: --ops required for patch (JSON array of operations)", file=sys.stderr)
            sys.exit(1)
        with open(args.ops) as f:
            operations = json.load(f)
        patch_slide(args.project, args.patch, operations)
    elif args.rebuild_slide:
        if not args.slide:
            print("ERROR: --slide required for rebuild-slide", file=sys.stderr)
            sys.exit(1)
        if not args.ops:
            print("ERROR: --ops required for rebuild-slide (JSON with slide_def and theme)", file=sys.stderr)
            sys.exit(1)
        with open(args.ops) as f:
            data = json.load(f)
        slide_def = data.get("slide_def", data)
        theme = data.get("theme", {})
        rebuild_slide(args.project, args.rebuild_slide, args.slide, slide_def, theme)
    elif args.list:
        list_slides(args.project, args.list)
    elif args.get:
        get_presentation(args.project, args.get)
    elif args.move:
        move_presentation(args.project, args.move, args.folder)
    elif args.export_pdf:
        export_pdf(args.project, args.export_pdf, args.output)

if __name__ == "__main__":
    main()
