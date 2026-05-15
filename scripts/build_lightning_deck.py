#!/usr/bin/env python3
"""Lightning talk deck builder.

Minimalist Apple-style deck, native Google Slides (text is editable).
Default run builds the first 3 slides — Roman wants to style-check before
the remaining 10 are generated.

Run:
    bash "/Users/romansiepelmeyer/Documents/Claude Code/portals/run.sh" \
        scripts/build_lightning_deck.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from slides_operations import (  # noqa: E402
    _create_textbox,
    _insert_text,
    _style_text,
    _style_para,
    _add_slide_bg,
    _sid,
)
from google_client import get_slides_service  # noqa: E402


# ============================================================================
# Brand (per the design brief in 2026-05-14_LIGHTNING_TALK_BUILD_PLAN.md)
# ============================================================================
BG = "#FFFFFF"
PRIMARY = "#0A0A0F"
SECONDARY = "#71717A"
ACCENT = "#7C5CFC"
FONT = "Inter"

# 16:9 dimensions in PT (10" × 5.625" = 720 × 405)
SLIDE_W = 720
SLIDE_H = 405
MARGIN = 60

# Type scale (sized for 720pt-wide slide)
HERO = 56
HEADLINE = 36
BODY = 18
META = 10


# ============================================================================
# Helpers
# ============================================================================
def _new_slide(reqs, idx):
    """Create a blank white slide. Returns the slide ID."""
    sid = _sid()
    reqs.append({
        "createSlide": {
            "objectId": sid,
            "insertionIndex": idx,
            "slideLayoutReference": {"predefinedLayout": "BLANK"},
        }
    })
    _add_slide_bg(reqs, sid, BG)
    return sid


# ============================================================================
# Slide builders
# ============================================================================
def slide_1_title(reqs):
    """Template A: headline top-area, subtitle below, meta bottom-left."""
    sid = _new_slide(reqs, 0)

    # Headline
    hid = _sid()
    text = "Toward ambient intelligence."
    _create_textbox(reqs, sid, hid, MARGIN, 110, SLIDE_W - 2 * MARGIN, 80)
    _insert_text(reqs, hid, text)
    _style_text(reqs, hid, 0, len(text), font_size=HERO, bold=True,
                color=PRIMARY, font_family=FONT)
    _style_para(reqs, hid, 0, len(text), line_spacing=110)

    # Subtitle (italic, gray)
    sub_id = _sid()
    sub = "A knowledge graph journey."
    _create_textbox(reqs, sid, sub_id, MARGIN, 195, SLIDE_W - 2 * MARGIN, 30)
    _insert_text(reqs, sub_id, sub)
    _style_text(reqs, sub_id, 0, len(sub), font_size=BODY,
                color=SECONDARY, font_family=FONT, italic=True)

    # Meta line (bottom-left)
    meta_id = _sid()
    meta = "Roman Siepelmeyer · Anaro Labs"
    _create_textbox(reqs, sid, meta_id, MARGIN, SLIDE_H - MARGIN + 18,
                    SLIDE_W - 2 * MARGIN, 14)
    _insert_text(reqs, meta_id, meta)
    _style_text(reqs, meta_id, 0, len(meta), font_size=META,
                color=SECONDARY, font_family=FONT)


def slide_2_anchor(reqs):
    """Template C: headline + 4-line body + accent kicker."""
    sid = _new_slide(reqs, 1)

    # Headline
    hid = _sid()
    text = "A lot of context switching."
    _create_textbox(reqs, sid, hid, MARGIN, MARGIN, SLIDE_W - 2 * MARGIN, 60)
    _insert_text(reqs, hid, text)
    _style_text(reqs, hid, 0, len(text), font_size=HEADLINE, bold=True,
                color=PRIMARY, font_family=FONT)

    # Body (4 lines)
    body_lines = [
        "AI roadmap for a customer service team.",
        "Four growth & culture stakeholders, four different topics.",
        "Co-design call for my startup's first test customer.",
        "Two languages. Four companies.",
    ]
    body_text = "\n".join(body_lines)

    body_id = _sid()
    _create_textbox(reqs, sid, body_id, MARGIN, 150, SLIDE_W - 2 * MARGIN, 160)
    _insert_text(reqs, body_id, body_text)
    _style_text(reqs, body_id, 0, len(body_text), font_size=BODY,
                color=PRIMARY, font_family=FONT)
    _style_para(reqs, body_id, 0, len(body_text), line_spacing=150)

    # Accent kicker
    acc_id = _sid()
    acc = "Every AI call started from zero."
    _create_textbox(reqs, sid, acc_id, MARGIN, 320, SLIDE_W - 2 * MARGIN, 30)
    _insert_text(reqs, acc_id, acc)
    _style_text(reqs, acc_id, 0, len(acc), font_size=BODY, bold=True,
                color=ACCENT, font_family=FONT)


def slide_3_hero(reqs):
    """Template B: three hero lines, one word in accent."""
    sid = _new_slide(reqs, 2)

    line1 = "The models are good enough."
    line2 = "The gap is persistent structured memory."
    line3 = "I'm team scaffolding."
    full = f"{line1}\n{line2}\n{line3}"

    hid = _sid()
    _create_textbox(reqs, sid, hid, MARGIN, 90, SLIDE_W - 2 * MARGIN, 240)
    _insert_text(reqs, hid, full)
    _style_text(reqs, hid, 0, len(full), font_size=HERO, bold=True,
                color=PRIMARY, font_family=FONT)
    _style_para(reqs, hid, 0, len(full), line_spacing=130)

    # Highlight "scaffolding" in purple
    scaff_start = full.find("scaffolding")
    scaff_end = scaff_start + len("scaffolding")
    _style_text(reqs, hid, scaff_start, scaff_end, color=ACCENT)


# ============================================================================
# Speaker notes (one per slide)
# ============================================================================
SPEAKER_NOTES = [
    (
        "I run an AI consultancy called Anaro Labs - fractional and strategic work, "
        "mostly with customer experience, customer service, and growth and culture "
        "teams. I'm also co-founder and CEO of a PropTech startup in Germany. My "
        "background is behavior, not engineering. Today is the practitioner's view "
        "of building a knowledge graph: where I started, what broke, what I learned, "
        "what I haven't solved yet."
    ),
    (
        "Two weeks ago. AI roadmap for a customer service team - strategy, "
        "playbooks, ROI math. Four different growth and culture stakeholders, each "
        "on something different: onboarding redesign, a department audit, an AI "
        "proficiency survey, manager enablement. Co-design call for the startup's "
        "first test customer in the evening. German with the startup, English with "
        "the consulting clients. Four companies. Every AI call started from zero "
        "context. The thing I wanted: AI that knows what I'm working on without me "
        "pointing at files. 'Marcus, prep me for the call' should be enough."
    ),
    (
        "Hot take for an engineering room. For most of the work I do, the models "
        "are good enough. The better-model arguments keep getting answered on the "
        "model side every six months. What doesn't get solved by the next release "
        "is persistent structured memory - what the system carries between sessions "
        "about my world. I'm team scaffolding, not team model. The structure I put "
        "around the model compounds faster than the model improves on its own."
    ),
]


# ============================================================================
# Orchestrator
# ============================================================================
def build(project: str = "anaro-labs") -> str:
    service = get_slides_service(project=project)

    # Create empty presentation
    pres = service.presentations().create(
        body={"title": "Lightning talk - toward ambient intelligence (v1, slides 1-3)"}
    ).execute()
    pres_id = pres["presentationId"]
    default_slide_id = pres["slides"][0]["objectId"]

    # Build slides 1-3
    reqs = [{"deleteObject": {"objectId": default_slide_id}}]
    slide_1_title(reqs)
    slide_2_anchor(reqs)
    slide_3_hero(reqs)

    service.presentations().batchUpdate(
        presentationId=pres_id, body={"requests": reqs}
    ).execute()

    # Speaker notes in a separate pass
    pres_data = service.presentations().get(presentationId=pres_id).execute()
    notes_reqs = []
    for i, notes_text in enumerate(SPEAKER_NOTES):
        if i >= len(pres_data["slides"]):
            break
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
    print(json.dumps({
        "presentation_id": pres_id,
        "url": url,
        "slides_built": 3,
    }, indent=2))
    return pres_id


if __name__ == "__main__":
    build()
