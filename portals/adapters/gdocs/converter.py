"""Convert between Markdown and Google Docs format."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from markdown_it import MarkdownIt
from markdown_it.token import Token


@dataclass
class FormatRange:
    """A range of text with formatting information."""

    start_index: int
    end_index: int
    format_type: str  # "heading", "bold", "italic", "link", etc.
    level: int | None = None  # For headings (1-6)
    url: str | None = None  # For links
    text: str | None = None  # Original text


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

    def __init__(self):
        """Initialize converter."""
        self.md = MarkdownIt()
        self.md.enable('table')  # Enable GFM-style table parsing
        self.current_index = 1  # Google Docs starts at index 1

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

        # Convert to plain text and track formatting
        result = ConversionResult(plain_text="")
        self.current_index = 1  # Reset index

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
                i = self._process_heading(tokens, i, result)
            elif token.type == "paragraph_open":
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

        start_index = self.current_index
        text = ""

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

        # Track heading range
        result.format_ranges.append(
            FormatRange(
                start_index=start_index,
                end_index=end_index,
                format_type="heading",
                level=level,
                text=text.strip(),
            )
        )

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
        text = ""

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

        result.plain_text += text

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

        Args:
            token: HR token
            index: Current index
            result: Result to update

        Returns:
            New index after processing
        """
        # Add horizontal line as separator
        result.plain_text += "---\n"
        self.current_index += 4

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
                        # Extract cell text content
                        cell_content = self._extract_cell_text(tokens[i])
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

    def _extract_cell_text(self, token: Token) -> str:
        """Extract plain text from an inline token (for table cells).

        Args:
            token: Inline token

        Returns:
            Plain text content
        """
        if not token.children:
            return ""

        text = ""
        for child in token.children:
            if child.type == "text":
                text += child.content
            elif child.type in ["strong_open", "em_open", "link_open"]:
                # Skip formatting markers, just get the text
                continue
            elif child.type in ["strong_close", "em_close", "link_close"]:
                continue
            elif child.type == "code_inline":
                text += child.content

        return text

    def generate_batch_requests(self, conversion: ConversionResult) -> list[dict[str, Any]]:
        """Generate Google Docs API batch update requests.

        Args:
            conversion: Conversion result with formatting info

        Returns:
            List of batch update request dictionaries
        """
        requests = []

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
                prev = conversion.list_ranges[i-1]

                # If same type and adjacent/overlapping, extend the current group
                # Adjacent means the current item starts at or before the previous item's end
                is_adjacent = curr["start_index"] <= current_group["end_index"]
                same_type = curr["ordered"] == current_group["ordered"]

                if same_type and is_adjacent:
                    current_group["end_index"] = max(current_group["end_index"], curr["end_index"])
                else:
                    # Different type or non-adjacent, start new group
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
                adjusted_start = group["start_index"] - tab_offset
                adjusted_end = group["end_index"] - 1 - tab_offset  # -1 for trailing newline

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

            if fmt.format_type == "heading":
                # Map markdown headings to Google Docs styles:
                # H1 -> Title, H2 -> Heading 1, H3 -> Heading 2, H4 -> Heading 3, etc.
                # This allows H1 to use the document Title style
                if fmt.level == 1:
                    style_type = "TITLE"
                else:
                    style_type = f"HEADING_{fmt.level - 1}"

                requests.append({
                    "updateParagraphStyle": {
                        "range": {
                            "startIndex": fmt.start_index - tabs_before,
                            "endIndex": fmt.end_index - tabs_before,
                        },
                        "paragraphStyle": {
                            "namedStyleType": style_type
                        },
                        "fields": "namedStyleType",
                    }
                })
                # Note: We intentionally do NOT clear background colors here
                # to allow custom heading styles (with highlights) to be preserved

            elif fmt.format_type == "bold":
                requests.append({
                    "updateTextStyle": {
                        "range": {
                            "startIndex": fmt.start_index - tabs_before,
                            "endIndex": fmt.end_index - tabs_before,
                        },
                        "textStyle": {"bold": True},
                        "fields": "bold",
                    }
                })

            elif fmt.format_type == "italic":
                requests.append({
                    "updateTextStyle": {
                        "range": {
                            "startIndex": fmt.start_index - tabs_before,
                            "endIndex": fmt.end_index - tabs_before,
                        },
                        "textStyle": {"italic": True},
                        "fields": "italic",
                    }
                })

            elif fmt.format_type == "code_inline":
                # Note: Skip font family for now - API format is complex
                # Just use smaller font size to distinguish inline code
                requests.append({
                    "updateTextStyle": {
                        "range": {
                            "startIndex": fmt.start_index - tabs_before,
                            "endIndex": fmt.end_index - tabs_before,
                        },
                        "textStyle": {
                            "fontSize": {"magnitude": 10, "unit": "PT"},
                        },
                        "fields": "fontSize",
                    }
                })

            elif fmt.format_type == "link":
                requests.append({
                    "updateTextStyle": {
                        "range": {
                            "startIndex": fmt.start_index - tabs_before,
                            "endIndex": fmt.end_index - tabs_before,
                        },
                        "textStyle": {
                            "link": {"url": fmt.url}
                        },
                        "fields": "link",
                    }
                })

            elif fmt.format_type == "code_block":
                # Code blocks: monospace font + gray background
                # Text style: monospace font and background color
                requests.append({
                    "updateTextStyle": {
                        "range": {
                            "startIndex": fmt.start_index - tabs_before,
                            "endIndex": fmt.end_index - tabs_before,
                        },
                        "textStyle": {
                            "weightedFontFamily": {
                                "fontFamily": "Courier New"
                            },
                            "fontSize": {"magnitude": 10, "unit": "PT"},
                            "backgroundColor": {
                                "color": {
                                    "rgbColor": {
                                        "red": 0.95,
                                        "green": 0.95,
                                        "blue": 0.95
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
                            "startIndex": fmt.start_index - tabs_before,
                            "endIndex": fmt.end_index - tabs_before,
                        },
                        "paragraphStyle": {
                            "indentStart": {"magnitude": 36, "unit": "PT"},
                            "indentEnd": {"magnitude": 36, "unit": "PT"},
                        },
                        "fields": "indentStart,indentEnd",
                    }
                })

            elif fmt.format_type == "blockquote":
                # Blockquotes: left indentation + left border + light gray background
                requests.append({
                    "updateParagraphStyle": {
                        "range": {
                            "startIndex": fmt.start_index - tabs_before,
                            "endIndex": fmt.end_index - tabs_before,
                        },
                        "paragraphStyle": {
                            "indentStart": {"magnitude": 36, "unit": "PT"},
                            "borderLeft": {
                                "color": {
                                    "color": {
                                        "rgbColor": {
                                            "red": 0.6,
                                            "green": 0.6,
                                            "blue": 0.6
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
                            "startIndex": fmt.start_index - tabs_before,
                            "endIndex": fmt.end_index - tabs_before,
                        },
                        "textStyle": {
                            "backgroundColor": {
                                "color": {
                                    "rgbColor": {
                                        "red": 0.98,
                                        "green": 0.98,
                                        "blue": 0.98
                                    }
                                }
                            }
                        },
                        "fields": "backgroundColor",
                    }
                })

        # Handle tables - these need special processing
        # Tables are inserted as structures and require separate handling
        if conversion.tables:
            table_requests = self._generate_table_requests(conversion)
            requests.extend(table_requests)

        return requests

    def _generate_table_requests(self, conversion: ConversionResult) -> list[dict[str, Any]]:
        """Generate batch requests for inserting tables.

        Tables in Google Docs require:
        1. insertTable to create the structure
        2. insertText for each cell's content
        3. Formatting for header rows

        Args:
            conversion: Conversion result with table data

        Returns:
            List of batch update requests for tables
        """
        requests = []

        # Process tables in reverse order to avoid index shifting issues
        # (inserting later tables first means earlier table indices stay valid)
        for table in reversed(conversion.tables):
            # Adjust index for tabs that were removed by list formatting
            tabs_before_table = conversion.plain_text[:table.insert_index].count('\t')
            adjusted_index = table.insert_index - tabs_before_table

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
            header_ranges = []

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

            # Now generate requests in reverse order
            # We go from last row to first, last column to first
            for row_idx in range(len(table.rows) - 1, -1, -1):
                row = table.rows[row_idx]
                for col_idx in range(len(row) - 1, -1, -1):
                    cell = row[col_idx]
                    if cell.content:
                        cell_pos = cell_positions[row_idx][col_idx]
                        cell_requests.append({
                            "insertText": {
                                "location": {"index": cell_pos},
                                "text": cell.content
                            }
                        })

                        # Track header cells for bold formatting
                        if cell.is_header:
                            header_ranges.append({
                                "start": cell_pos,
                                "end": cell_pos + len(cell.content),
                                "text": cell.content
                            })

            requests.extend(cell_requests)

            # 3. Apply table border styling (dotted, dark charcoal #2a2a3a)
            # Dark charcoal matches the H1 heading background for visual consistency
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
                        "borderTop": {
                            "color": {"color": {"rgbColor": {"red": 0.165, "green": 0.165, "blue": 0.227}}},
                            "width": {"magnitude": 0.5, "unit": "PT"},
                            "dashStyle": "DOT",
                        },
                        "borderBottom": {
                            "color": {"color": {"rgbColor": {"red": 0.165, "green": 0.165, "blue": 0.227}}},
                            "width": {"magnitude": 0.5, "unit": "PT"},
                            "dashStyle": "DOT",
                        },
                        "borderLeft": {
                            "color": {"color": {"rgbColor": {"red": 0.165, "green": 0.165, "blue": 0.227}}},
                            "width": {"magnitude": 0.5, "unit": "PT"},
                            "dashStyle": "DOT",
                        },
                        "borderRight": {
                            "color": {"color": {"rgbColor": {"red": 0.165, "green": 0.165, "blue": 0.227}}},
                            "width": {"magnitude": 0.5, "unit": "PT"},
                            "dashStyle": "DOT",
                        },
                    },
                    "fields": "borderTop,borderBottom,borderLeft,borderRight",
                }
            })

            # 4. Style header row with light warm gray background (#f5f4f0) and dark text
            # Neutral background that doesn't compete with heading highlight colors
            if table.num_rows > 0 and table.rows[0][0].is_header:
                # Apply light warm gray background to header row cells
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
                                "color": {"rgbColor": {"red": 0.961, "green": 0.957, "blue": 0.941}}
                            },
                        },
                        "fields": "backgroundColor",
                    }
                })

                # Apply bold and dark charcoal text to header cells individually
                # Process each header cell separately to avoid index overflow
                header_cell_start = adjusted_index + 4
                for cell in table.rows[0]:
                    if cell.content:
                        requests.append({
                            "updateTextStyle": {
                                "range": {
                                    "startIndex": header_cell_start,
                                    "endIndex": header_cell_start + len(cell.content),
                                },
                                "textStyle": {
                                    "foregroundColor": {
                                        "color": {"rgbColor": {"red": 0.165, "green": 0.165, "blue": 0.227}}
                                    },
                                    "bold": True,
                                },
                                "fields": "foregroundColor,bold",
                            }
                        })
                    # Move to next cell: content + 2 (cell markers)
                    header_cell_start += len(cell.content) + 2

        return requests
