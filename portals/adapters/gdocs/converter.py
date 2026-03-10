"""Convert between Markdown and Google Docs format."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token


def _utf16_len(s: str) -> int:
    """Count UTF-16 code units in a Python string.

    Google Docs API uses UTF-16 indices. Supplementary plane characters
    (emojis like 📊, U+1F4CA) are 1 Python char but 2 UTF-16 code units.
    """
    return sum(2 if ord(c) > 0xFFFF else 1 for c in s)


def _build_utf16_offsets(text: str) -> list[int]:
    """Build prefix-sum of supplementary character counts.

    Returns array where offsets[i] = count of supplementary chars in text[0:i].
    API index = python_index - tabs_removed + offsets[python_index].
    Array is sized len(text)+2 to safely handle end_index == len(text)+1.
    """
    n = len(text)
    offsets = [0] * (n + 2)
    for i in range(n):
        offsets[i + 1] = offsets[i] + (1 if ord(text[i]) > 0xFFFF else 0)
    offsets[n + 1] = offsets[n]
    return offsets


def _utf16_pos(text: str, char_pos: int) -> int:
    """Convert a Python char position within text to UTF-16 offset."""
    return char_pos + sum(1 for c in text[:char_pos] if ord(c) > 0xFFFF)


@dataclass
class FormatRange:
    """A range of text with formatting information."""

    start_index: int
    end_index: int
    format_type: str  # "heading", "bold", "italic", "link", etc.
    level: int | None = None  # For headings (1-6)
    url: str | None = None  # For links
    text: str | None = None  # Original text
    token_index: int | None = None  # Token list position for model lookups
    followed_by_box: bool = False  # Heading immediately followed by info box


class SectionType(Enum):
    """Classification of document sections by structural role."""

    TITLE = "title"
    SUBTITLE = "subtitle"
    GUIDANCE = "guidance"
    LEGEND = "legend"
    PHASE = "phase"
    REGULAR = "regular"


@dataclass
class SectionInfo:
    """Structural metadata for a document section (heading + its content)."""

    section_type: SectionType
    heading_token_index: int
    heading_level: int
    heading_text: str
    paragraph_count: int = 0
    list_item_count: int = 0
    has_table: bool = False
    parenthetical: str | None = None  # Extracted trailing "(Name)" attribution
    is_dense: bool = False  # True if paragraph_count + list_item_count > 8


@dataclass
class DocumentModel:
    """Pre-scan structural model of an entire document.

    Built in a single forward pass over the token list. Provides O(1) lookups
    for section classification and footnote detection during the rendering pass.
    """

    sections: list[SectionInfo] = field(default_factory=list)
    footnote_indices: set[int] = field(default_factory=set)
    _section_map: list[SectionInfo | None] = field(default_factory=list, repr=False)

    def build_lookup(self, num_tokens: int) -> None:
        """Build O(1) token-to-section map."""
        self._section_map = [None] * num_tokens
        section_starts = {s.heading_token_index: s for s in self.sections}
        current_section = None
        for i in range(num_tokens):
            if i in section_starts:
                current_section = section_starts[i]
            self._section_map[i] = current_section

    def section_for_token(self, index: int) -> SectionInfo | None:
        """Look up which section owns a given token index."""
        if 0 <= index < len(self._section_map):
            return self._section_map[index]
        return None

    def is_footnote(self, index: int) -> bool:
        """Check if a paragraph_open token index is a footnote."""
        return index in self.footnote_indices


@dataclass
class StyleMap:
    """Visual treatment parameters for semantic document formatting.

    Centralizes all visual constants so they're easy to adjust and extend.
    Pass a custom StyleMap to GoogleDocsConverter to override defaults.
    """

    # Info box (phase metadata wrapper)
    info_box_bg: tuple = (0.965, 0.988, 0.957)         # #f6fcf4
    info_box_border: tuple = (0.851, 0.918, 0.827)      # #d9ead3
    info_box_border_width: float = 0.75

    # Table styling
    table_border_color: tuple = (0.165, 0.165, 0.227)   # dark charcoal #2a2a3a
    table_header_bg: tuple = (0.961, 0.957, 0.941)      # warm gray #f5f4f0

    # Code blocks
    code_block_bg: tuple = (0.95, 0.95, 0.95)           # #f2f2f2

    # Blockquote
    blockquote_border_color: tuple = (0.6, 0.6, 0.6)
    blockquote_bg: tuple = (0.98, 0.98, 0.98)

    # Heading spacing (markdown level -> pt above)
    heading_space_above: dict = field(default_factory=lambda: {2: 24, 3: 16, 4: 12})

    # Info box heading spacing (breathing room between metadata sections)
    info_heading_space_above: float = 14.0

    # Phase emoji mapping (keyword substring -> emoji)
    phase_emojis: dict = field(default_factory=lambda: {
        "data": "📊", "preparation": "📊", "extract": "📊",
        "input": "📬", "gathering": "📬", "collect": "📬",
        "meeting": "🤝", "alignment": "🤝", "discuss": "🤝",
        "computation": "⚙️", "calculate": "⚙️", "model": "⚙️",
        "judgment": "🎯", "adjustment": "🎯", "tune": "🎯",
        "lock": "🔒", "sign-off": "🔒", "approve": "🔒",
        "communicat": "📤", "distribut": "📤", "share": "📤",
        "monitor": "👁️", "track": "👁️", "ongoing": "👁️",
        "continuous": "🔄", "automated": "🔄", "always": "🔄",
    })
    phase_emoji_fallback: str = "📋"

    # Structural element emojis (inside info boxes)
    location_emoji: str = "📍"     # "Where the work happens"
    effort_emoji: str = "⏱️"      # "Estimated effort for this phase"

    # Explicit phase overrides (heading text -> emoji, checked before keyword match)
    phase_overrides: dict = field(default_factory=dict)

    # Footnote de-emphasis
    footnote_color: tuple = (0.6, 0.6, 0.6)               # #999999
    footnote_font_size: float = 9.0

    # Heading parenthetical de-emphasis (e.g. "(Federico)" in phase headings)
    heading_parenthetical_color: tuple = (0.55, 0.55, 0.55)
    heading_parenthetical_font_size: float = 10.0

    # Legend section boxing (warm gray)
    legend_box_bg: tuple = (0.973, 0.969, 0.957)            # #f8f7f4
    legend_box_border: tuple = (0.898, 0.886, 0.863)        # #e5e2dc
    legend_box_border_width: float = 0.75

    # Guidance section boxing (blue tint, placeholder for future use)
    guidance_box_bg: tuple = (0.941, 0.961, 0.988)          # #f0f5fc
    guidance_box_border: tuple = (0.827, 0.878, 0.941)      # #d3e0f0
    guidance_box_border_width: float = 0.75

    # Step metadata de-emphasis (paragraphs starting with `code` spans)
    step_metadata_color: tuple = (0.6, 0.6, 0.6)           # #999999
    step_metadata_font_size: float = 9.0
    step_metadata_space_below: float = 14.0

    # Dense section extra spacing
    dense_section_extra_space: float = 32.0


# Known phase metadata h4 titles (case-insensitive substring match)
_METADATA_TITLES = (
    "where the work happens",
    "estimated effort",
    "participating teams",
    "note on sequencing",
)


@dataclass
class TableCell:
    """A cell in a table."""

    content: str
    is_header: bool = False
    format_ranges: list[FormatRange] = field(default_factory=list)


@dataclass
class TableData:
    """Data for a table to be inserted."""

    insert_index: int  # Where to insert the table in the document
    rows: list[list[TableCell]]  # 2D array of cells
    num_rows: int = 0
    num_cols: int = 0
    is_info_box: bool = False  # Single-cell phase metadata info box
    box_type: str | None = None  # Color routing: "phase_metadata", "legend", "guidance"

    def __post_init__(self):
        if self.rows:
            self.num_rows = len(self.rows)
            self.num_cols = max(len(row) for row in self.rows) if self.rows else 0


@dataclass
class ConversionResult:
    """Result of markdown to Google Docs conversion."""

    plain_text: str  # Text without markdown symbols
    format_ranges: list[FormatRange] = field(default_factory=list)
    list_ranges: list[dict[str, Any]] = field(default_factory=list)
    tables: list[TableData] = field(default_factory=list)


class GoogleDocsConverter:
    """Convert between Markdown and Google Docs format.

    Uses markdown-it-py for parsing and generates Google Docs API batch requests.
    """

    def __init__(self, style_map: StyleMap | None = None):
        """Initialize converter.

        Args:
            style_map: Optional visual treatment parameters. Uses defaults if None.
        """
        self.md = MarkdownIt()
        self.md.enable('table')  # Enable GFM-style table parsing
        self.current_index = 1  # Google Docs starts at index 1
        self._last_heading_level = 0  # Track heading levels for subtitle detection
        self.style_map = style_map or StyleMap()
        self._document_model: DocumentModel | None = None

    def markdown_to_gdocs(self, markdown: str) -> ConversionResult:
        """Convert markdown to Google Docs format.

        Args:
            markdown: Markdown text

        Returns:
            ConversionResult with plain text and formatting information
        """
        # Strip YAML frontmatter (--- delimited block at start of file)
        markdown = re.sub(r'^---\s*\n.*?\n---\s*\n', '', markdown, count=1, flags=re.DOTALL)

        # Parse markdown
        tokens = self.md.parse(markdown)

        # Pre-scan: build structural model for judgment-based formatting
        self._document_model = self._pre_scan(tokens)

        # Convert to plain text and track formatting
        result = ConversionResult(plain_text="")
        self.current_index = 1  # Reset index
        self._last_heading_level = 0  # Reset subtitle detection

        self._process_tokens(tokens, result)

        return result

    def _process_tokens(
        self,
        tokens: list[Token],
        result: ConversionResult,
        parent_type: str | None = None,
    ) -> None:
        """Process markdown tokens and build plain text + formatting.

        Args:
            tokens: List of markdown-it tokens
            result: ConversionResult to populate
            parent_type: Parent token type for context
        """
        i = 0
        while i < len(tokens):
            token = tokens[i]

            if token.type == "heading_open":
                heading_token_index = i
                heading_level = int(token.tag[1])
                i = self._process_heading(tokens, i, result)
                # After an h3, check for phase metadata info box
                if heading_level == 3 and self._is_metadata_heading(tokens, i):
                    # Mark the heading as followed by a box (zero spaceBelow)
                    for fmt in reversed(result.format_ranges):
                        if fmt.format_type == "heading" and fmt.level == 3:
                            fmt.followed_by_box = True
                            break
                    i = self._process_phase_metadata_block(tokens, i, result)
                # After an h2 legend or guidance section, box all sub-content
                if heading_level == 2 and self._document_model:
                    section = self._document_model.section_for_token(heading_token_index)
                    if section and section.section_type == SectionType.LEGEND:
                        # Mark heading as followed by box for tight spacing
                        for fmt in reversed(result.format_ranges):
                            if fmt.format_type == "heading" and fmt.level == 2:
                                fmt.followed_by_box = True
                                break
                        i = self._process_section_box(tokens, i, result, section, "legend")
                    elif section and section.section_type == SectionType.GUIDANCE:
                        for fmt in reversed(result.format_ranges):
                            if fmt.format_type == "heading" and fmt.level == 2:
                                fmt.followed_by_box = True
                                break
                        i = self._process_section_box(tokens, i, result, section, "guidance")
                continue
            elif token.type == "paragraph_open":
                self._last_heading_level = 0  # Reset subtitle detection
                i = self._process_paragraph(tokens, i, result)
            elif token.type == "bullet_list_open":
                i = self._process_list(tokens, i, result, ordered=False)
            elif token.type == "ordered_list_open":
                i = self._process_list(tokens, i, result, ordered=True)
            elif token.type == "blockquote_open":
                i = self._process_blockquote(tokens, i, result)
            elif token.type == "code_block":
                i = self._process_code_block(token, i, result)
            elif token.type == "fence":
                i = self._process_code_block(token, i, result)
            elif token.type == "hr":
                i = self._process_hr(token, i, result)
            elif token.type == "table_open":
                i = self._process_table(tokens, i, result)
            else:
                i += 1

        return

    def _process_heading(
        self,
        tokens: list[Token],
        index: int,
        result: ConversionResult,
    ) -> int:
        """Process heading tokens.

        Args:
            tokens: Token list
            index: Current index
            result: Result to update

        Returns:
            New index after processing
        """
        open_token = tokens[index]
        level = int(open_token.tag[1])  # h1 -> 1, h2 -> 2, etc.

        # Resolve phase emoji for h3 phase headings BEFORE processing inline
        # so that all subsequent indices account for the emoji prefix length
        emoji_prefix = ""
        if level == 3:
            for j in range(index + 1, min(index + 3, len(tokens))):
                if tokens[j].type == "inline":
                    emoji = self._resolve_phase_emoji(tokens[j].content)
                    if emoji:
                        emoji_prefix = f"{emoji} "
                    break

        start_index = self.current_index
        text = emoji_prefix
        if emoji_prefix:
            self.current_index += len(emoji_prefix)

        # Process inline content
        i = index + 1
        while i < len(tokens) and tokens[i].type != "heading_close":
            if tokens[i].type == "inline":
                text_content = self._process_inline(tokens[i], result)
                text += text_content
                self.current_index += len(text_content)
            i += 1

        end_index = self.current_index

        # Add newline after heading
        text += "\n"
        self.current_index += 1

        result.plain_text += text

        # Detect subtitle: pre-scan model (handles blank lines) or strict check
        is_subtitle = False
        if self._document_model:
            section = self._document_model.section_for_token(index)
            if section and section.section_type == SectionType.SUBTITLE:
                is_subtitle = True
        if not is_subtitle:
            is_subtitle = (level == 2 and self._last_heading_level == 1)

        # Track heading range
        result.format_ranges.append(
            FormatRange(
                start_index=start_index,
                end_index=end_index,
                format_type="subtitle" if is_subtitle else "heading",
                level=level,
                text=text.strip(),
                token_index=index,
            )
        )

        # Parenthetical de-emphasis: "(Name)" in heading rendered lighter/smaller
        if self._document_model:
            section = self._document_model.section_for_token(index)
            if section and section.parenthetical:
                paren_text = f"({section.parenthetical})"
                paren_pos = text.rfind(paren_text)
                if paren_pos >= 0:
                    result.format_ranges.append(
                        FormatRange(
                            start_index=start_index + paren_pos,
                            end_index=start_index + paren_pos + len(paren_text),
                            format_type="heading_parenthetical",
                            text=paren_text,
                        )
                    )

        self._last_heading_level = level

        return i + 1  # Skip closing token

    def _process_paragraph(
        self,
        tokens: list[Token],
        index: int,
        result: ConversionResult,
    ) -> int:
        """Process paragraph tokens.

        Args:
            tokens: Token list
            index: Current index
            result: Result to update

        Returns:
            New index after processing
        """
        start_index = self.current_index
        text = ""
        is_footnote = bool(
            self._document_model and self._document_model.is_footnote(index)
        )

        # Process inline content
        i = index + 1
        while i < len(tokens) and tokens[i].type != "paragraph_close":
            if tokens[i].type == "inline":
                text_content = self._process_inline(tokens[i], result)
                text += text_content
                self.current_index += len(text_content)
            i += 1

        # Add newline after paragraph
        text += "\n"
        self.current_index += 1
        end_index = self.current_index

        result.plain_text += text

        # Determine paragraph format type
        if is_footnote:
            fmt_type = "footnote"
        elif self._is_step_metadata(tokens, index):
            fmt_type = "step_metadata"
        else:
            fmt_type = "paragraph"

        # Track all paragraphs for spacing
        result.format_ranges.append(
            FormatRange(
                start_index=start_index,
                end_index=end_index,
                format_type=fmt_type,
            )
        )

        return i + 1  # Skip closing token

    def _process_inline(self, token: Token, result: ConversionResult) -> str:
        """Process inline tokens (bold, italic, links, etc.).

        Args:
            token: Inline token
            result: Result to update

        Returns:
            Plain text content
        """
        if not token.children:
            return ""

        text = ""
        i = 0
        while i < len(token.children):
            child = token.children[i]

            if child.type == "text":
                text += child.content
                i += 1
            elif child.type == "strong_open":
                # Track start of bold
                start_index = self.current_index + len(text)
                bold_text, skip_count = self._get_text_and_skip_count(token.children, i, "strong_close")
                result.format_ranges.append(
                    FormatRange(
                        start_index=start_index,
                        end_index=start_index + len(bold_text),
                        format_type="bold",
                        text=bold_text,
                    )
                )
                text += bold_text
                i += skip_count
            elif child.type == "em_open":
                # Track start of italic
                start_index = self.current_index + len(text)
                italic_text, skip_count = self._get_text_and_skip_count(token.children, i, "em_close")
                result.format_ranges.append(
                    FormatRange(
                        start_index=start_index,
                        end_index=start_index + len(italic_text),
                        format_type="italic",
                        text=italic_text,
                    )
                )
                text += italic_text
                i += skip_count
            elif child.type == "code_inline":
                # Inline code
                start_index = self.current_index + len(text)
                code_text = child.content
                result.format_ranges.append(
                    FormatRange(
                        start_index=start_index,
                        end_index=start_index + len(code_text),
                        format_type="code_inline",
                        text=code_text,
                    )
                )
                text += code_text
                i += 1
            elif child.type == "softbreak":
                # Soft line break (single newline in markdown) - preserve as newline
                text += "\n"
                i += 1
            elif child.type == "hardbreak":
                # Hard line break (two spaces + newline or <br>) - preserve as newline
                text += "\n"
                i += 1
            elif child.type == "link_open":
                # Track link
                start_index = self.current_index + len(text)
                link_url = child.attrs.get("href", "") if child.attrs else ""
                link_text, skip_count = self._get_text_and_skip_count(token.children, i, "link_close")
                result.format_ranges.append(
                    FormatRange(
                        start_index=start_index,
                        end_index=start_index + len(link_text),
                        format_type="link",
                        url=link_url,
                        text=link_text,
                    )
                )
                text += link_text
                i += skip_count
            elif child.type in ["strong_close", "em_close", "link_close"]:
                # Skip closing tokens (already handled)
                i += 1
            else:
                i += 1

        return text

    def _get_text_and_skip_count(
        self,
        siblings: list[Token],
        start_index: int,
        close_type: str,
    ) -> tuple[str, int]:
        """Get text content until matching close token and count tokens to skip.

        Args:
            siblings: Sibling tokens
            start_index: Index of open token
            close_type: Close token type to find

        Returns:
            Tuple of (text content, number of tokens to skip including close token)
        """
        text = ""
        i = start_index + 1

        while i < len(siblings) and siblings[i].type != close_type:
            if siblings[i].type == "text":
                text += siblings[i].content
            i += 1

        # Return text and count of tokens to skip (including close token)
        skip_count = i - start_index + 1
        return text, skip_count

    def _resolve_phase_emoji(self, heading_text: str) -> str | None:
        """Resolve the emoji for a phase heading based on its description.

        Only phase-like h3 headings (e.g. "Phase A:", "Continuous") get emojis.
        Checks explicit overrides first, then keyword matching, then fallback.

        Args:
            heading_text: The raw heading text

        Returns:
            Emoji string or None if not a phase heading
        """
        pattern = r'^(Phase\s+([A-Z]|[0-9]+|One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten)\s*:|Continuous|Pre-cycle|Post-cycle)'
        if not re.match(pattern, heading_text, re.IGNORECASE):
            return None

        # Check explicit overrides first
        if heading_text in self.style_map.phase_overrides:
            return self.style_map.phase_overrides[heading_text]

        # Extract description (text after colon, or full text)
        colon_idx = heading_text.find(':')
        if colon_idx >= 0:
            description = heading_text[colon_idx + 1:].strip().lower()
        else:
            description = heading_text.strip().lower()

        # Match keywords against description
        for keyword, emoji in self.style_map.phase_emojis.items():
            if keyword in description:
                return emoji

        return self.style_map.phase_emoji_fallback

    def _is_metadata_heading(self, tokens: list[Token], index: int) -> bool:
        """Check if the token at index starts a known phase metadata h4.

        Args:
            tokens: Token list
            index: Index to check

        Returns:
            True if token is an h4 heading_open with a known metadata title
        """
        if index >= len(tokens):
            return False
        if tokens[index].type != "heading_open":
            return False
        if int(tokens[index].tag[1]) != 4:
            return False
        # Check the inline content for known metadata titles
        if index + 1 < len(tokens) and tokens[index + 1].type == "inline":
            text = tokens[index + 1].content.lower()
            return any(title in text for title in _METADATA_TITLES)
        return False

    def _process_phase_metadata_block(
        self,
        tokens: list[Token],
        index: int,
        result: ConversionResult,
    ) -> int:
        """Collect consecutive phase metadata h4+paragraph pairs into an info box.

        Scans forward from index, consuming h4 headings that match known metadata
        titles and their following paragraphs. Builds a single-cell info box table.

        Args:
            tokens: Token list
            index: Current index (should be at a metadata heading_open)
            result: Result to update

        Returns:
            New index past all consumed tokens
        """
        insert_index = self.current_index

        cell_content = ""
        cell_formats: list[FormatRange] = []

        i = index
        first_pair = True

        while i < len(tokens) and self._is_metadata_heading(tokens, i):
            if not first_pair:
                cell_content += "\n\n"  # empty paragraph as visual separator between pairs
            first_pair = False

            # Extract h4 heading text
            heading_text = ""
            i += 1  # skip heading_open
            while i < len(tokens) and tokens[i].type != "heading_close":
                if tokens[i].type == "inline":
                    heading_text = tokens[i].content
                i += 1
            i += 1  # skip heading_close

            # Prepend structural emoji based on heading content
            heading_lower = heading_text.lower()
            if "where" in heading_lower and "work" in heading_lower:
                heading_text = f"{self.style_map.location_emoji} {heading_text}"
            elif "effort" in heading_lower:
                heading_text = f"{self.style_map.effort_emoji} {heading_text}"

            # Record heading with info_heading style (larger, green)
            heading_start = len(cell_content)
            cell_content += heading_text
            heading_end = len(cell_content)
            cell_formats.append(FormatRange(
                start_index=heading_start,
                end_index=heading_end,
                format_type="info_heading",
                text=heading_text,
            ))

            # Collect content for this metadata heading: at most one paragraph,
            # optionally followed by one bullet list. Then stop and check for
            # the next metadata h4 in the outer loop.
            if i < len(tokens) and tokens[i].type == "paragraph_open":
                i += 1  # skip paragraph_open
                para_text = ""
                para_formats: list[FormatRange] = []

                while i < len(tokens) and tokens[i].type != "paragraph_close":
                    if tokens[i].type == "inline":
                        para_text, para_formats = self._extract_cell_content(tokens[i])
                    i += 1
                i += 1  # skip paragraph_close

                if para_text:
                    cell_content += "\n"
                    offset = len(cell_content)
                    for fmt in para_formats:
                        cell_formats.append(FormatRange(
                            start_index=fmt.start_index + offset,
                            end_index=fmt.end_index + offset,
                            format_type=fmt.format_type,
                            url=fmt.url,
                            text=fmt.text,
                        ))
                    cell_content += para_text

            # If a bullet list follows the paragraph, consume it too
            # (e.g., "Where the work happens" paragraph + tab list)
            if i < len(tokens) and tokens[i].type == "bullet_list_open":
                if cell_content and not cell_content.endswith("\n"):
                    cell_content += "\n"
                list_text, list_fmts, i = self._flatten_list_into_cell(tokens, i)
                offset = len(cell_content)
                cell_content += list_text
                for fmt in list_fmts:
                    cell_formats.append(FormatRange(
                        start_index=fmt.start_index + offset,
                        end_index=fmt.end_index + offset,
                        format_type=fmt.format_type,
                        url=fmt.url,
                        text=fmt.text,
                    ))

        # Build info box table if we collected content
        if cell_content:
            cell = TableCell(
                content=cell_content,
                is_header=False,
                format_ranges=cell_formats,
            )
            table_data = TableData(
                insert_index=insert_index,
                rows=[[cell]],
                is_info_box=True,
            )
            result.tables.append(table_data)

            # Add newline placeholder for the table location
            # Mark it as a spacer to collapse its height
            spacer_start = self.current_index
            result.plain_text += "\n"
            self.current_index += 1
            result.format_ranges.append(
                FormatRange(
                    start_index=spacer_start,
                    end_index=self.current_index,
                    format_type="table_spacer",
                )
            )

        return i

    # ------------------------------------------------------------------
    # Pre-scan: structural analysis pass
    # ------------------------------------------------------------------

    def _pre_scan(self, tokens: list[Token]) -> DocumentModel:
        """Build a structural model of the document in a single forward pass.

        Classifies sections, extracts parentheticals, detects footnotes,
        and measures section density. The resulting DocumentModel provides
        O(1) lookups used during the rendering pass.
        """
        model = DocumentModel()
        last_heading_level = 0
        last_heading_index = -1

        i = 0
        while i < len(tokens):
            token = tokens[i]

            if token.type == "heading_open":
                level = int(token.tag[1])
                # Find inline content
                heading_text = ""
                for j in range(i + 1, min(i + 3, len(tokens))):
                    if tokens[j].type == "inline":
                        heading_text = tokens[j].content
                        break

                section_type = self._classify_section(
                    level, heading_text, last_heading_level, tokens, i,
                )

                # Extract trailing parenthetical: "Phase A: Prep (Federico)" -> "Federico"
                parenthetical = None
                paren_match = re.search(r'\s*\(([^)]+)\)\s*$', heading_text)
                if paren_match:
                    parenthetical = paren_match.group(1)

                section = SectionInfo(
                    section_type=section_type,
                    heading_token_index=i,
                    heading_level=level,
                    heading_text=heading_text,
                    parenthetical=parenthetical,
                )

                # Measure content density of the PREVIOUS section
                if model.sections:
                    self._count_section_content(tokens, model.sections[-1], last_heading_index, i)

                model.sections.append(section)
                last_heading_index = i
                last_heading_level = level

            i += 1

        # Measure content density of the last section
        if model.sections:
            self._count_section_content(tokens, model.sections[-1], last_heading_index, len(tokens))

        # Detect footnotes: italic paragraphs after the last heading
        if model.sections:
            last_section_start = model.sections[-1].heading_token_index
            for idx in range(last_section_start, len(tokens)):
                if tokens[idx].type == "paragraph_open":
                    if self._is_footnote_paragraph(tokens, idx):
                        model.footnote_indices.add(idx)

        # Also detect document metadata: italic paragraphs between
        # title/subtitle and the first content heading (author line, etc.)
        for s in model.sections:
            if s.section_type not in (SectionType.TITLE, SectionType.SUBTITLE):
                for idx in range(s.heading_token_index):
                    if tokens[idx].type == "paragraph_open":
                        if self._is_footnote_paragraph(tokens, idx):
                            model.footnote_indices.add(idx)
                break

        model.build_lookup(len(tokens))
        return model

    @staticmethod
    def _is_h2_immediately_after_h1(tokens: list[Token], h2_index: int) -> bool:
        """Check if h2 directly follows h1 with no content blocks between."""
        i = h2_index - 1
        while i >= 0:
            tok = tokens[i]
            if tok.type in (
                "paragraph_open", "bullet_list_open", "ordered_list_open",
                "blockquote_open", "table_open", "code_block", "fence", "hr",
            ):
                return False
            if tok.type == "heading_open" and tok.tag == "h1":
                return True
            i -= 1
        return False

    def _classify_section(
        self,
        level: int,
        text: str,
        last_heading_level: int,
        tokens: list[Token],
        index: int,
    ) -> SectionType:
        """Classify a heading into a structural section type."""
        if level == 1:
            return SectionType.TITLE

        if level == 2 and last_heading_level == 1:
            # Only subtitle if h2 immediately follows h1 with no content blocks between
            if self._is_h2_immediately_after_h1(tokens, index):
                return SectionType.SUBTITLE

        text_lower = text.lower()

        if any(kw in text_lower for kw in ("how to use", "reading guide", "about this document")):
            return SectionType.GUIDANCE

        if level == 2 and self._is_legend_section(tokens, index):
            return SectionType.LEGEND

        phase_pattern = r'^(Phase\s+([A-Z]|[0-9]+|One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten)\s*:|Continuous|Pre-cycle|Post-cycle)'
        if level == 3 and re.match(phase_pattern, text, re.IGNORECASE):
            return SectionType.PHASE

        return SectionType.REGULAR

    def _is_legend_section(self, tokens: list[Token], heading_index: int) -> bool:
        """Check if an h2 section is a legend (contains h4 sub-headings, no h3s).

        A legend section is detected by heading text containing "legend" AND
        having h4 sub-headings as direct children (no h3 sub-structure).
        """
        # Quick text check first
        for j in range(heading_index + 1, min(heading_index + 3, len(tokens))):
            if tokens[j].type == "inline":
                if "legend" not in tokens[j].content.lower():
                    return False
                break

        # Lookahead: scan until next h2 or end, check for h4s and no h3s
        i = heading_index
        while i < len(tokens) and tokens[i].type != "heading_close":
            i += 1
        i += 1  # past heading_close

        has_h4 = False
        while i < len(tokens):
            token = tokens[i]
            if token.type == "heading_open":
                next_level = int(token.tag[1])
                if next_level <= 2:
                    break  # End of section
                if next_level == 4:
                    has_h4 = True
                elif next_level == 3:
                    return False  # h3 inside means it's not a flat legend
            i += 1

        return has_h4

    def _count_section_content(
        self,
        tokens: list[Token],
        section: SectionInfo,
        start: int,
        end: int,
    ) -> None:
        """Count paragraphs, list items, and tables in a section range."""
        for idx in range(start, end):
            if tokens[idx].type == "paragraph_open":
                section.paragraph_count += 1
            elif tokens[idx].type == "list_item_open":
                section.list_item_count += 1
            elif tokens[idx].type == "table_open":
                section.has_table = True
        if section.paragraph_count + section.list_item_count > 8:
            section.is_dense = True

    def _is_footnote_paragraph(self, tokens: list[Token], para_index: int) -> bool:
        """Check if a paragraph is a footnote.

        Footnotes are detected as paragraphs that are either:
        - Entirely wrapped in italic (em_open ... em_close)
        - Start with footnote marker characters (*, dagger, double-dagger)
        """
        for j in range(para_index + 1, min(para_index + 3, len(tokens))):
            if tokens[j].type == "inline":
                children = tokens[j].children
                if not children:
                    return False

                # Check for footnote markers (†, ‡) in first text child.
                # Only check if first child is a plain text token to avoid
                # matching bold (**) or italic (*) markdown syntax.
                if children[0].type == "text":
                    first_text = children[0].content
                    if first_text and first_text[0] in ('\u2020', '\u2021'):
                        return True

                # Check if entirely italic
                if (
                    len(children) >= 3
                    and children[0].type == "em_open"
                    and children[-1].type == "em_close"
                ):
                    return True

                return False
        return False

    def _is_step_metadata(self, tokens: list[Token], para_index: int) -> bool:
        """Check if a paragraph is step-level metadata.

        Step metadata lines start with inline code: `Actor: ...` | `Type: ...`
        These are de-emphasized (9pt gray) to keep the reader focused on the
        event descriptions rather than the structural tagging.
        """
        for j in range(para_index + 1, min(para_index + 3, len(tokens))):
            if tokens[j].type == "inline":
                children = tokens[j].children
                if children and children[0].type == "code_inline":
                    return True
                return False
        return False

    # ------------------------------------------------------------------
    # Legend section boxing
    # ------------------------------------------------------------------

    def _process_section_box(
        self,
        tokens: list[Token],
        index: int,
        result: ConversionResult,
        section: SectionInfo,
        box_type: str,
    ) -> int:
        """Collect section content (h4s + bullets + paragraphs) into an info box.

        Consumes all tokens from index until the next heading of same-or-higher
        level than the section heading. Builds a single-cell TableData.

        Args:
            tokens: Token list
            index: Current index (just after the section's heading_close)
            result: Result to update
            section: SectionInfo for the section being boxed
            box_type: Color routing key ("legend", "guidance")

        Returns:
            New index past all consumed tokens
        """
        insert_index = self.current_index
        cell_content = ""
        cell_formats: list[FormatRange] = []

        i = index
        first_item = True

        while i < len(tokens):
            token = tokens[i]

            # Stop at next heading of same or higher level
            if token.type == "heading_open":
                next_level = int(token.tag[1])
                if next_level <= section.heading_level:
                    break

                # Process sub-heading (h4) as bold text in the cell
                if not first_item:
                    cell_content += "\n"
                first_item = False

                heading_text = ""
                i += 1
                while i < len(tokens) and tokens[i].type != "heading_close":
                    if tokens[i].type == "inline":
                        heading_text = tokens[i].content
                    i += 1
                i += 1  # skip heading_close

                heading_start = len(cell_content)
                cell_content += heading_text
                cell_formats.append(FormatRange(
                    start_index=heading_start,
                    end_index=len(cell_content),
                    format_type="bold",
                    text=heading_text,
                ))
                continue

            elif token.type == "bullet_list_open":
                if cell_content and not cell_content.endswith("\n"):
                    cell_content += "\n"
                list_text, list_fmts, i = self._flatten_list_into_cell(tokens, i)
                caller_offset = len(cell_content)
                cell_content += list_text
                for fmt in list_fmts:
                    cell_formats.append(FormatRange(
                        start_index=fmt.start_index + caller_offset,
                        end_index=fmt.end_index + caller_offset,
                        format_type=fmt.format_type,
                        url=fmt.url,
                        text=fmt.text,
                    ))
                first_item = False
                continue

            elif token.type == "paragraph_open":
                i += 1
                while i < len(tokens) and tokens[i].type != "paragraph_close":
                    if tokens[i].type == "inline":
                        para_text, para_fmts = self._extract_cell_content(tokens[i])
                        if para_text:
                            if cell_content and not cell_content.endswith("\n"):
                                cell_content += "\n"
                            caller_offset = len(cell_content)
                            for fmt in para_fmts:
                                cell_formats.append(FormatRange(
                                    start_index=fmt.start_index + caller_offset,
                                    end_index=fmt.end_index + caller_offset,
                                    format_type=fmt.format_type,
                                    url=fmt.url,
                                    text=fmt.text,
                                ))
                            cell_content += para_text
                    i += 1
                i += 1  # skip paragraph_close
                first_item = False
                continue

            i += 1

        # Build info box table
        if cell_content:
            cell = TableCell(content=cell_content, format_ranges=cell_formats)
            table_data = TableData(
                insert_index=insert_index,
                rows=[[cell]],
                is_info_box=True,
                box_type=box_type,
            )
            result.tables.append(table_data)

            result.plain_text += "\n"
            self.current_index += 1

        return i

    def _flatten_list_into_cell(
        self,
        tokens: list[Token],
        index: int,
    ) -> tuple[str, list[FormatRange], int]:
        """Flatten a bullet list into cell text with bullet-point prefixes.

        Returns:
            Tuple of (text, format_ranges with 0-based positions, new_token_index)
        """
        text = ""
        formats: list[FormatRange] = []

        i = index + 1  # skip bullet_list_open
        while i < len(tokens) and tokens[i].type != "bullet_list_close":
            if tokens[i].type == "list_item_open":
                i += 1
                while i < len(tokens) and tokens[i].type != "list_item_close":
                    if tokens[i].type == "paragraph_open":
                        i += 1
                        while i < len(tokens) and tokens[i].type != "paragraph_close":
                            if tokens[i].type == "inline":
                                item_text, item_fmts = self._extract_cell_content(tokens[i])
                                if text:
                                    text += "\n"
                                prefix = "\u2022 "
                                offset = len(text) + len(prefix)
                                text += prefix + item_text
                                for fmt in item_fmts:
                                    formats.append(FormatRange(
                                        start_index=fmt.start_index + offset,
                                        end_index=fmt.end_index + offset,
                                        format_type=fmt.format_type,
                                        url=fmt.url,
                                        text=fmt.text,
                                    ))
                            i += 1
                        i += 1  # skip paragraph_close
                        continue
                    i += 1
            else:
                i += 1

        return text, formats, i + 1  # skip bullet_list_close

    def _process_list(
        self,
        tokens: list[Token],
        index: int,
        result: ConversionResult,
        ordered: bool = False,
        nesting_level: int = 0,
    ) -> int:
        """Process list tokens including nested lists.

        Args:
            tokens: Token list
            index: Current index
            result: Result to update
            ordered: True for numbered lists
            nesting_level: Current nesting depth

        Returns:
            New index after processing
        """
        i = index + 1
        while i < len(tokens) and tokens[i].type not in ["bullet_list_close", "ordered_list_close"]:
            if tokens[i].type == "list_item_open":
                # Set start index BEFORE adding tabs so tabs are included in the paragraph range
                item_start = self.current_index

                # Add leading tabs for nesting (Google Docs uses tabs to determine nesting level)
                tabs = "\t" * nesting_level
                result.plain_text += tabs
                self.current_index += len(tabs)

                # Process list item content
                i += 1
                has_content = False
                while i < len(tokens) and tokens[i].type != "list_item_close":
                    if tokens[i].type == "paragraph_open":
                        # Get paragraph content
                        i += 1
                        while i < len(tokens) and tokens[i].type != "paragraph_close":
                            if tokens[i].type == "inline":
                                text_content = self._process_inline(tokens[i], result)
                                # Strip checkbox markers like [ ] or [x]
                                text_content = re.sub(r'^\s*\[\s*[x ]?\s*\]\s*', '', text_content)
                                if text_content.strip():  # Only add if there's actual content
                                    result.plain_text += text_content
                                    self.current_index += len(text_content)
                                    has_content = True
                            i += 1
                        i += 1  # Skip paragraph_close
                        continue
                    elif tokens[i].type in ["bullet_list_open", "ordered_list_open"]:
                        # Add newline before nested list if we had content
                        if has_content:
                            result.plain_text += "\n"
                            self.current_index += 1

                            # Save parent item
                            result.list_ranges.append({
                                "start_index": item_start,
                                "end_index": self.current_index,
                                "ordered": ordered,
                                "nesting_level": nesting_level,
                            })
                            has_content = False

                        # Process nested list
                        is_ordered = tokens[i].type == "ordered_list_open"
                        i = self._process_list(tokens, i, result, ordered=is_ordered, nesting_level=nesting_level + 1)
                        continue
                    i += 1

                # Add newline after list item content
                if has_content:
                    result.plain_text += "\n"
                    self.current_index += 1

                    item_end = self.current_index

                    # Track list item range with nesting level
                    result.list_ranges.append({
                        "start_index": item_start,
                        "end_index": item_end,
                        "ordered": ordered,
                        "nesting_level": nesting_level,
                    })
            else:
                i += 1

        return i + 1

    def _process_blockquote(
        self,
        tokens: list[Token],
        index: int,
        result: ConversionResult,
    ) -> int:
        """Process blockquote tokens.

        Args:
            tokens: Token list
            index: Current index
            result: Result to update

        Returns:
            New index after processing
        """
        start_index = self.current_index

        i = index + 1
        while i < len(tokens) and tokens[i].type != "blockquote_close":
            if tokens[i].type == "paragraph_open":
                i = self._process_paragraph(tokens, i, result)
            else:
                i += 1

        end_index = self.current_index

        # Track blockquote range for formatting
        result.format_ranges.append(
            FormatRange(
                start_index=start_index,
                end_index=end_index,
                format_type="blockquote",
            )
        )

        return i

    def _process_code_block(
        self,
        token: Token,
        index: int,
        result: ConversionResult,
    ) -> int:
        """Process code block tokens.

        Args:
            token: Code block token
            index: Current index
            result: Result to update

        Returns:
            New index after processing
        """
        start_index = self.current_index
        code_text = token.content

        result.plain_text += code_text + "\n"
        self.current_index += len(code_text) + 1

        # Track code block range
        result.format_ranges.append(
            FormatRange(
                start_index=start_index,
                end_index=self.current_index - 1,
                format_type="code_block",
                text=code_text,
            )
        )

        return index + 1

    def _process_hr(
        self,
        token: Token,
        index: int,
        result: ConversionResult,
    ) -> int:
        """Process horizontal rule tokens.

        Horizontal rules are skipped entirely - they render as literal "---"
        in Google Docs which looks wrong. Headings provide sufficient separation.

        Args:
            token: HR token
            index: Current index
            result: Result to update

        Returns:
            New index after processing
        """
        return index + 1

    def _process_table(
        self,
        tokens: list[Token],
        index: int,
        result: ConversionResult,
    ) -> int:
        """Process table tokens.

        Args:
            tokens: Token list
            index: Current index (at table_open)
            result: Result to update

        Returns:
            New index after processing
        """
        # Record where the table should be inserted
        table_insert_index = self.current_index

        rows: list[list[TableCell]] = []
        current_row: list[TableCell] = []
        in_header = False

        i = index + 1
        while i < len(tokens) and tokens[i].type != "table_close":
            token = tokens[i]

            if token.type == "thead_open":
                in_header = True
                i += 1
            elif token.type == "thead_close":
                in_header = False
                i += 1
            elif token.type == "tbody_open":
                i += 1
            elif token.type == "tbody_close":
                i += 1
            elif token.type == "tr_open":
                current_row = []
                i += 1
            elif token.type == "tr_close":
                if current_row:
                    rows.append(current_row)
                i += 1
            elif token.type in ["th_open", "td_open"]:
                # Start of a cell - find the inline content
                i += 1
                cell_content = ""
                cell_formats: list[FormatRange] = []

                while i < len(tokens) and tokens[i].type not in ["th_close", "td_close"]:
                    if tokens[i].type == "inline":
                        cell_content, cell_formats = self._extract_cell_content(tokens[i])
                    i += 1

                current_row.append(TableCell(
                    content=cell_content,
                    is_header=in_header,
                    format_ranges=cell_formats
                ))
                i += 1  # Skip the close token
            else:
                i += 1

        # Create TableData and add to result
        if rows:
            table_data = TableData(
                insert_index=table_insert_index,
                rows=rows
            )
            result.tables.append(table_data)

            # Add a newline placeholder for the table location
            # (the actual table will be inserted via batch requests)
            result.plain_text += "\n"
            self.current_index += 1

        return i + 1  # Skip table_close

    def _extract_cell_content(self, token: Token) -> tuple[str, list[FormatRange]]:
        """Extract text and format ranges from an inline token (for table cells).

        Format range positions are relative to the start of the cell text (0-based).
        The caller must offset them to absolute document positions after table insertion.

        Args:
            token: Inline token

        Returns:
            Tuple of (plain text, list of FormatRange with cell-relative positions)
        """
        if not token.children:
            return "", []

        text = ""
        formats: list[FormatRange] = []
        i = 0

        while i < len(token.children):
            child = token.children[i]

            if child.type == "text":
                text += child.content
                i += 1
            elif child.type == "strong_open":
                start = len(text)
                bold_text, skip_count = self._get_text_and_skip_count(token.children, i, "strong_close")
                formats.append(FormatRange(
                    start_index=start,
                    end_index=start + len(bold_text),
                    format_type="bold",
                    text=bold_text,
                ))
                text += bold_text
                i += skip_count
            elif child.type == "em_open":
                start = len(text)
                italic_text, skip_count = self._get_text_and_skip_count(token.children, i, "em_close")
                formats.append(FormatRange(
                    start_index=start,
                    end_index=start + len(italic_text),
                    format_type="italic",
                    text=italic_text,
                ))
                text += italic_text
                i += skip_count
            elif child.type == "link_open":
                start = len(text)
                link_url = child.attrs.get("href", "") if child.attrs else ""
                link_text, skip_count = self._get_text_and_skip_count(token.children, i, "link_close")
                formats.append(FormatRange(
                    start_index=start,
                    end_index=start + len(link_text),
                    format_type="link",
                    url=link_url,
                    text=link_text,
                ))
                text += link_text
                i += skip_count
            elif child.type == "code_inline":
                text += child.content
                i += 1
            elif child.type in ["strong_close", "em_close", "link_close"]:
                i += 1
            else:
                i += 1

        return text, formats

    def generate_batch_requests(self, conversion: ConversionResult) -> list[dict[str, Any]]:
        """Generate Google Docs API batch update requests.

        Args:
            conversion: Conversion result with formatting info

        Returns:
            List of batch update request dictionaries
        """
        requests = []

        # Build UTF-16 supplementary character offset table.
        # Google Docs API indices are in UTF-16 code units, not Python chars.
        supp = _build_utf16_offsets(conversion.plain_text)

        # IMPORTANT: Apply list formatting FIRST before other formatting
        # because createParagraphBullets removes tabs, which shifts indices
        # Group consecutive list items by type (ordered vs unordered) and apply
        # createParagraphBullets to entire list ranges at once so Google Docs
        # can detect nesting levels from tabs correctly
        if conversion.list_ranges:
            list_groups = []
            current_group = {
                "ordered": conversion.list_ranges[0]["ordered"],
                "start_index": conversion.list_ranges[0]["start_index"],
                "end_index": conversion.list_ranges[0]["end_index"],
            }

            for i in range(1, len(conversion.list_ranges)):
                curr = conversion.list_ranges[i]

                # Adjacent means the current item starts at or before the group's end.
                is_adjacent = curr["start_index"] <= current_group["end_index"]

                if is_adjacent:
                    # Nested child (nesting > 0): always merge into parent group,
                    # even if ordered type differs (numbered parent + bullet children).
                    is_nested_child = curr.get("nesting_level", 0) > 0
                    # Root-level item with same list type: merge (sibling items).
                    is_same_root = (curr.get("nesting_level", 0) == 0
                                    and curr["ordered"] == current_group["ordered"])

                    if is_nested_child or is_same_root:
                        current_group["end_index"] = max(
                            current_group["end_index"], curr["end_index"])
                    else:
                        # Adjacent but different type at root level = new list
                        list_groups.append(current_group)
                        current_group = {
                            "ordered": curr["ordered"],
                            "start_index": curr["start_index"],
                            "end_index": curr["end_index"],
                        }
                else:
                    # Gap between items means separate lists
                    list_groups.append(current_group)
                    current_group = {
                        "ordered": curr["ordered"],
                        "start_index": curr["start_index"],
                        "end_index": curr["end_index"],
                    }

            # Add final group
            list_groups.append(current_group)

            # Apply createParagraphBullets to each group
            # IMPORTANT: Track tab removal offset because each createParagraphBullets
            # removes leading tabs, shifting all subsequent indices
            tab_offset = 0

            for group in list_groups:
                bullet_preset = "NUMBERED_DECIMAL_ALPHA_ROMAN" if group["ordered"] else "BULLET_DISC_CIRCLE_SQUARE"

                # Count tabs in this group's text
                group_text = conversion.plain_text[group["start_index"]:group["end_index"]]
                tabs_in_group = group_text.count('\t')

                # Adjust indices based on tabs removed by previous groups
                # + UTF-16 supplementary char offset (emojis = 2 code units)
                # Use supp[i-1] to count supplementary chars BEFORE position i
                adjusted_start = group["start_index"] - tab_offset + supp[group["start_index"] - 1]
                adjusted_end = group["end_index"] - 1 - tab_offset + supp[group["end_index"] - 1]

                requests.append({
                    "createParagraphBullets": {
                        "range": {
                            "startIndex": adjusted_start,
                            "endIndex": adjusted_end,
                        },
                        "bulletPreset": bullet_preset,
                    }
                })

                # Update offset for next group
                tab_offset += tabs_in_group

        # Apply heading styles and text formatting
        # Adjust indices to account for tabs removed by list formatting
        # Only subtract tabs that appear BEFORE each formatting range
        for fmt in conversion.format_ranges:
            # Count tabs before this format range
            tabs_before = conversion.plain_text[:fmt.start_index].count('\t')
            # Compute API indices: adjust for tab removal + UTF-16 supplementary chars.
            # Use supp[i-1] because supp[i] counts supplementary chars in text[0:i]
            # which INCLUDES the char at i-1.  We need chars BEFORE the position.
            adj_s = fmt.start_index - tabs_before + supp[fmt.start_index - 1]
            adj_e = fmt.end_index - tabs_before + supp[fmt.end_index - 1]

            if fmt.format_type == "subtitle":
                # Subtitle style: h2 immediately after h1 gets SUBTITLE
                requests.append({
                    "updateParagraphStyle": {
                        "range": {
                            "startIndex": adj_s,
                            "endIndex": adj_e,
                        },
                        "paragraphStyle": {
                            "namedStyleType": "SUBTITLE"
                        },
                        "fields": "namedStyleType",
                    }
                })

            elif fmt.format_type == "heading":
                # Map markdown headings to Google Docs styles:
                # H1 -> Title, H2 -> Heading 1, H3 -> Heading 2, H4 -> Heading 3, etc.
                # This allows H1 to use the document Title style
                if fmt.level == 1:
                    style_type = "TITLE"
                else:
                    style_type = f"HEADING_{fmt.level - 1}"

                # Legend/Guidance H2 headings: use HEADING_2 (neutral, no green bg)
                # instead of the standard HEADING_1 which has a green background
                section = None
                if self._document_model and fmt.token_index is not None:
                    section = self._document_model.section_for_token(fmt.token_index)
                    if section and section.section_type in (SectionType.LEGEND, SectionType.GUIDANCE):
                        style_type = "HEADING_2"

                # Explicit spaceAbove for visual breathing room between sections
                space_above = self.style_map.heading_space_above.get(fmt.level)

                # Smart spacing: extra space after dense sections
                if section is None and self._document_model and fmt.token_index is not None:
                    section = self._document_model.section_for_token(fmt.token_index)
                if section:
                    idx = self._document_model.sections.index(section)
                    if idx > 0 and self._document_model.sections[idx - 1].is_dense:
                        dense_space = self.style_map.dense_section_extra_space
                        space_above = max(space_above or 0, dense_space)

                paragraph_style: dict[str, Any] = {"namedStyleType": style_type}
                fields = "namedStyleType"

                if space_above is not None:
                    paragraph_style["spaceAbove"] = {"magnitude": space_above, "unit": "PT"}
                    fields += ",spaceAbove"

                # Remove spaceBelow when heading is directly followed by an info box
                if fmt.followed_by_box:
                    paragraph_style["spaceBelow"] = {"magnitude": 0, "unit": "PT"}
                    fields += ",spaceBelow"

                requests.append({
                    "updateParagraphStyle": {
                        "range": {
                            "startIndex": adj_s,
                            "endIndex": adj_e,
                        },
                        "paragraphStyle": paragraph_style,
                        "fields": fields,
                    }
                })
                # Note: We intentionally do NOT clear background colors here
                # to allow custom heading styles (with highlights) to be preserved

            elif fmt.format_type == "bold":
                requests.append({
                    "updateTextStyle": {
                        "range": {
                            "startIndex": adj_s,
                            "endIndex": adj_e,
                        },
                        "textStyle": {"bold": True},
                        "fields": "bold",
                    }
                })

            elif fmt.format_type == "italic":
                requests.append({
                    "updateTextStyle": {
                        "range": {
                            "startIndex": adj_s,
                            "endIndex": adj_e,
                        },
                        "textStyle": {"italic": True},
                        "fields": "italic",
                    }
                })

            elif fmt.format_type == "link":
                requests.append({
                    "updateTextStyle": {
                        "range": {
                            "startIndex": adj_s,
                            "endIndex": adj_e,
                        },
                        "textStyle": {
                            "link": {"url": fmt.url}
                        },
                        "fields": "link",
                    }
                })

            elif fmt.format_type == "code_block":
                # Code blocks: monospace font + gray background
                cb_bg = self.style_map.code_block_bg
                requests.append({
                    "updateTextStyle": {
                        "range": {
                            "startIndex": adj_s,
                            "endIndex": adj_e,
                        },
                        "textStyle": {
                            "weightedFontFamily": {
                                "fontFamily": "Courier New"
                            },
                            "fontSize": {"magnitude": 10, "unit": "PT"},
                            "backgroundColor": {
                                "color": {
                                    "rgbColor": {
                                        "red": cb_bg[0],
                                        "green": cb_bg[1],
                                        "blue": cb_bg[2],
                                    }
                                }
                            }
                        },
                        "fields": "weightedFontFamily,fontSize,backgroundColor",
                    }
                })

                # Paragraph style: slight indentation
                requests.append({
                    "updateParagraphStyle": {
                        "range": {
                            "startIndex": adj_s,
                            "endIndex": adj_e,
                        },
                        "paragraphStyle": {
                            "indentStart": {"magnitude": 36, "unit": "PT"},
                            "indentEnd": {"magnitude": 36, "unit": "PT"},
                        },
                        "fields": "indentStart,indentEnd",
                    }
                })

            elif fmt.format_type == "paragraph":
                # Regular paragraphs: add space above for breathing room
                requests.append({
                    "updateParagraphStyle": {
                        "range": {
                            "startIndex": adj_s,
                            "endIndex": adj_e,
                        },
                        "paragraphStyle": {
                            "spaceAbove": {"magnitude": 6, "unit": "PT"},
                        },
                        "fields": "spaceAbove",
                    }
                })

            elif fmt.format_type == "blockquote":
                # Blockquotes: left indentation + left border + light gray background
                bq_bc = self.style_map.blockquote_border_color
                bq_bg = self.style_map.blockquote_bg
                requests.append({
                    "updateParagraphStyle": {
                        "range": {
                            "startIndex": adj_s,
                            "endIndex": adj_e,
                        },
                        "paragraphStyle": {
                            "indentStart": {"magnitude": 36, "unit": "PT"},
                            "borderLeft": {
                                "color": {
                                    "color": {
                                        "rgbColor": {
                                            "red": bq_bc[0],
                                            "green": bq_bc[1],
                                            "blue": bq_bc[2],
                                        }
                                    }
                                },
                                "width": {"magnitude": 3.0, "unit": "PT"},
                                "padding": {"magnitude": 10.0, "unit": "PT"},
                                "dashStyle": "SOLID",
                            },
                        },
                        "fields": "indentStart,borderLeft",
                    }
                })

                # Add subtle background color
                requests.append({
                    "updateTextStyle": {
                        "range": {
                            "startIndex": adj_s,
                            "endIndex": adj_e,
                        },
                        "textStyle": {
                            "backgroundColor": {
                                "color": {
                                    "rgbColor": {
                                        "red": bq_bg[0],
                                        "green": bq_bg[1],
                                        "blue": bq_bg[2],
                                    }
                                }
                            }
                        },
                        "fields": "backgroundColor",
                    }
                })

            elif fmt.format_type == "footnote":
                # Footnote paragraphs: italic + small font + gray
                fc = self.style_map.footnote_color
                requests.append({
                    "updateParagraphStyle": {
                        "range": {
                            "startIndex": adj_s,
                            "endIndex": adj_e,
                        },
                        "paragraphStyle": {
                            "spaceAbove": {"magnitude": 2, "unit": "PT"},
                        },
                        "fields": "spaceAbove",
                    }
                })
                requests.append({
                    "updateTextStyle": {
                        "range": {
                            "startIndex": adj_s,
                            "endIndex": adj_e,
                        },
                        "textStyle": {
                            "italic": True,
                            "fontSize": {"magnitude": self.style_map.footnote_font_size, "unit": "PT"},
                            "foregroundColor": {
                                "color": {
                                    "rgbColor": {
                                        "red": fc[0],
                                        "green": fc[1],
                                        "blue": fc[2],
                                    }
                                }
                            },
                        },
                        "fields": "italic,fontSize,foregroundColor",
                    }
                })

            elif fmt.format_type == "step_metadata":
                # Step metadata: 9pt gray with extra space below
                smc = self.style_map.step_metadata_color
                requests.append({
                    "updateParagraphStyle": {
                        "range": {
                            "startIndex": adj_s,
                            "endIndex": adj_e,
                        },
                        "paragraphStyle": {
                            "spaceBelow": {"magnitude": self.style_map.step_metadata_space_below, "unit": "PT"},
                        },
                        "fields": "spaceBelow",
                    }
                })
                requests.append({
                    "updateTextStyle": {
                        "range": {
                            "startIndex": adj_s,
                            "endIndex": adj_e,
                        },
                        "textStyle": {
                            "fontSize": {"magnitude": self.style_map.step_metadata_font_size, "unit": "PT"},
                            "foregroundColor": {
                                "color": {
                                    "rgbColor": {
                                        "red": smc[0],
                                        "green": smc[1],
                                        "blue": smc[2],
                                    }
                                }
                            },
                        },
                        "fields": "fontSize,foregroundColor",
                    }
                })

            elif fmt.format_type == "table_spacer":
                # Collapse the empty paragraph between a heading and its info box
                requests.append({
                    "updateParagraphStyle": {
                        "range": {
                            "startIndex": adj_s,
                            "endIndex": adj_e,
                        },
                        "paragraphStyle": {
                            "spaceAbove": {"magnitude": 0, "unit": "PT"},
                            "spaceBelow": {"magnitude": 0, "unit": "PT"},
                        },
                        "fields": "spaceAbove,spaceBelow",
                    }
                })

            elif fmt.format_type == "heading_parenthetical":
                # Heading attribution de-emphasis: "(Name)" in lighter gray, smaller font
                hpc = self.style_map.heading_parenthetical_color
                requests.append({
                    "updateTextStyle": {
                        "range": {
                            "startIndex": adj_s,
                            "endIndex": adj_e,
                        },
                        "textStyle": {
                            "fontSize": {"magnitude": self.style_map.heading_parenthetical_font_size, "unit": "PT"},
                            "foregroundColor": {
                                "color": {
                                    "rgbColor": {
                                        "red": hpc[0],
                                        "green": hpc[1],
                                        "blue": hpc[2],
                                    }
                                }
                            },
                        },
                        "fields": "fontSize,foregroundColor",
                    }
                })

        # Handle tables - these need special processing
        # Tables are inserted as structures and require separate handling
        if conversion.tables:
            table_requests = self._generate_table_requests(conversion, supp)
            requests.extend(table_requests)

        return requests

    def _generate_table_requests(self, conversion: ConversionResult, supp: list[int] | None = None) -> list[dict[str, Any]]:
        """Generate batch requests for inserting tables.

        Tables in Google Docs require:
        1. insertTable to create the structure
        2. insertText for each cell's content
        3. Formatting for header rows

        Args:
            conversion: Conversion result with table data
            supp: UTF-16 supplementary char offset table (built if not provided)

        Returns:
            List of batch update requests for tables
        """
        requests = []
        if supp is None:
            supp = _build_utf16_offsets(conversion.plain_text)

        # Process tables in reverse order to avoid index shifting issues
        # (inserting later tables first means earlier table indices stay valid)
        for table in reversed(conversion.tables):
            # Adjust index for tabs that were removed by list formatting
            # + UTF-16 supplementary char offset
            tabs_before_table = conversion.plain_text[:table.insert_index].count('\t')
            adjusted_index = table.insert_index - tabs_before_table + supp[table.insert_index]

            # 1. Insert the table structure
            requests.append({
                "insertTable": {
                    "rows": table.num_rows,
                    "columns": table.num_cols,
                    "location": {"index": adjusted_index}
                }
            })

            # 2. Insert cell content and apply header formatting
            # After insertTable, we need to populate cells.
            # Cell positions in a newly created table follow this pattern:
            # - Table starts at index I
            # - First cell paragraph starts at I + 4
            # - Each cell adds: 2 (for empty cell) + len(content)
            # - Each new row adds: 1 (for row boundary)
            #
            # We insert content in REVERSE order (last cell first) so indices don't shift

            cell_requests = []

            # Calculate initial cell position
            # After insertTable at index I:
            # - Table element at I
            # - Table start at I+1
            # - First row at I+2
            # - First cell at I+3
            # - First cell paragraph at I+4
            base_index = adjusted_index + 4

            # Track cumulative offset as we calculate cell positions
            # Each empty cell has 1 character (newline), each row end has 1 char
            current_index = base_index

            # Build a map of cell positions (before any content is added)
            cell_positions = []
            for row_idx, row in enumerate(table.rows):
                row_positions = []
                for col_idx, cell in enumerate(row):
                    row_positions.append(current_index)
                    # Each cell has 2 chars: cell start + newline
                    current_index += 2
                # Row boundary
                current_index += 1
                cell_positions.append(row_positions)

            # Build filled-table cell positions for formatting.
            # After all text insertions, each cell occupies:
            #   content_length + 2 (cell marker + paragraph \n)
            # and each row boundary adds 1.
            # These positions reflect where content ACTUALLY IS after
            # batch processing, unlike cell_positions which are for the
            # empty table (used only for reverse-order text insertion).
            filled_positions: list[list[int]] = []
            fp_current = adjusted_index + 4  # same base as cell_positions
            for row in table.rows:
                row_fp = []
                for cell in row:
                    row_fp.append(fp_current)
                    fp_current += _utf16_len(cell.content) + 2
                fp_current += 1  # row boundary
                filled_positions.append(row_fp)

            # Now generate requests in reverse order
            # We go from last row to first, last column to first
            # Also collect cell format requests to apply after text insertion
            cell_format_requests = []

            for row_idx in range(len(table.rows) - 1, -1, -1):
                row = table.rows[row_idx]
                for col_idx in range(len(row) - 1, -1, -1):
                    cell = row[col_idx]
                    if cell.content:
                        # Use empty-table positions for text insertion (reverse order)
                        cell_pos = cell_positions[row_idx][col_idx]
                        cell_requests.append({
                            "insertText": {
                                "location": {"index": cell_pos},
                                "text": cell.content
                            }
                        })

                        # Emit formatting requests for bold/italic/link in cell content.
                        # Use FILLED-table positions since these run after all insertions.
                        filled_pos = filled_positions[row_idx][col_idx]
                        for fmt in cell.format_ranges:
                            abs_start = filled_pos + _utf16_pos(cell.content, fmt.start_index)
                            abs_end = filled_pos + _utf16_pos(cell.content, fmt.end_index)
                            if fmt.format_type == "bold":
                                cell_format_requests.append({
                                    "updateTextStyle": {
                                        "range": {"startIndex": abs_start, "endIndex": abs_end},
                                        "textStyle": {"bold": True},
                                        "fields": "bold",
                                    }
                                })
                            elif fmt.format_type == "italic":
                                cell_format_requests.append({
                                    "updateTextStyle": {
                                        "range": {"startIndex": abs_start, "endIndex": abs_end},
                                        "textStyle": {"italic": True},
                                        "fields": "italic",
                                    }
                                })
                            elif fmt.format_type == "link" and fmt.url:
                                cell_format_requests.append({
                                    "updateTextStyle": {
                                        "range": {"startIndex": abs_start, "endIndex": abs_end},
                                        "textStyle": {"link": {"url": fmt.url}},
                                        "fields": "link",
                                    }
                                })
                            elif fmt.format_type == "info_heading":
                                # Info box section heading: Heading 3 with no spaceAbove
                                # (blank line between sections provides separation instead)
                                cell_format_requests.append({
                                    "updateParagraphStyle": {
                                        "range": {"startIndex": abs_start, "endIndex": abs_end},
                                        "paragraphStyle": {
                                            "namedStyleType": "HEADING_3",
                                            "spaceAbove": {"magnitude": 0, "unit": "PT"},
                                        },
                                        "fields": "namedStyleType,spaceAbove",
                                    }
                                })

            requests.extend(cell_requests)

            # 3. Apply border and background styling
            # Info boxes get colored SOLID borders + bg + padding;
            # regular data tables get dark charcoal DOT borders
            if table.is_info_box:
                # Route colors based on box_type
                if table.box_type == "legend":
                    ib_bg = self.style_map.legend_box_bg
                    ib_bd = self.style_map.legend_box_border
                    ib_w = self.style_map.legend_box_border_width
                elif table.box_type == "guidance":
                    ib_bg = self.style_map.guidance_box_bg
                    ib_bd = self.style_map.guidance_box_border
                    ib_w = self.style_map.guidance_box_border_width
                else:
                    # Default: phase metadata (green)
                    ib_bg = self.style_map.info_box_bg
                    ib_bd = self.style_map.info_box_border
                    ib_w = self.style_map.info_box_border_width
                border_def = {
                    "color": {"color": {"rgbColor": {"red": ib_bd[0], "green": ib_bd[1], "blue": ib_bd[2]}}},
                    "width": {"magnitude": ib_w, "unit": "PT"},
                    "dashStyle": "SOLID",
                }
                requests.append({
                    "updateTableCellStyle": {
                        "tableRange": {
                            "tableCellLocation": {
                                "tableStartLocation": {"index": adjusted_index + 1},
                                "rowIndex": 0,
                                "columnIndex": 0,
                            },
                            "rowSpan": table.num_rows,
                            "columnSpan": table.num_cols,
                        },
                        "tableCellStyle": {
                            "backgroundColor": {
                                "color": {"rgbColor": {"red": ib_bg[0], "green": ib_bg[1], "blue": ib_bg[2]}}
                            },
                            "borderTop": border_def,
                            "borderBottom": border_def,
                            "borderLeft": border_def,
                            "borderRight": border_def,
                            "paddingTop": {"magnitude": 4, "unit": "PT"},
                            "paddingBottom": {"magnitude": 4, "unit": "PT"},
                            "paddingLeft": {"magnitude": 6, "unit": "PT"},
                            "paddingRight": {"magnitude": 6, "unit": "PT"},
                        },
                        "fields": "backgroundColor,borderTop,borderBottom,borderLeft,borderRight,paddingTop,paddingBottom,paddingLeft,paddingRight",
                    }
                })
            else:
                # Regular data table: dotted dark charcoal borders
                tbc = self.style_map.table_border_color
                border_def = {
                    "color": {"color": {"rgbColor": {"red": tbc[0], "green": tbc[1], "blue": tbc[2]}}},
                    "width": {"magnitude": 0.5, "unit": "PT"},
                    "dashStyle": "DOT",
                }
                requests.append({
                    "updateTableCellStyle": {
                        "tableRange": {
                            "tableCellLocation": {
                                "tableStartLocation": {"index": adjusted_index + 1},
                                "rowIndex": 0,
                                "columnIndex": 0,
                            },
                            "rowSpan": table.num_rows,
                            "columnSpan": table.num_cols,
                        },
                        "tableCellStyle": {
                            "borderTop": border_def,
                            "borderBottom": border_def,
                            "borderLeft": border_def,
                            "borderRight": border_def,
                        },
                        "fields": "borderTop,borderBottom,borderLeft,borderRight",
                    }
                })

            # 4. Style header row (data tables only, not info boxes)
            if not table.is_info_box and table.num_rows > 0 and table.rows[0][0].is_header:
                th_bg = self.style_map.table_header_bg
                tbc = self.style_map.table_border_color
                # Apply header background
                requests.append({
                    "updateTableCellStyle": {
                        "tableRange": {
                            "tableCellLocation": {
                                "tableStartLocation": {"index": adjusted_index + 1},
                                "rowIndex": 0,
                                "columnIndex": 0,
                            },
                            "rowSpan": 1,
                            "columnSpan": table.num_cols,
                        },
                        "tableCellStyle": {
                            "backgroundColor": {
                                "color": {"rgbColor": {"red": th_bg[0], "green": th_bg[1], "blue": th_bg[2]}}
                            },
                        },
                        "fields": "backgroundColor",
                    }
                })

                # Apply bold and dark charcoal text to header cells individually.
                # Use filled_positions which account for all cell content.
                for col_idx, cell in enumerate(table.rows[0]):
                    if cell.content:
                        hdr_start = filled_positions[0][col_idx]
                        requests.append({
                            "updateTextStyle": {
                                "range": {
                                    "startIndex": hdr_start,
                                    "endIndex": hdr_start + _utf16_len(cell.content),
                                },
                                "textStyle": {
                                    "foregroundColor": {
                                        "color": {"rgbColor": {"red": tbc[0], "green": tbc[1], "blue": tbc[2]}}
                                    },
                                    "bold": True,
                                },
                                "fields": "foregroundColor,bold",
                            }
                        })

            # 5. Apply inline formatting (bold, italic, links) within cells
            # These run after text insertion so the positions are valid
            if cell_format_requests:
                requests.extend(cell_format_requests)

        return requests
