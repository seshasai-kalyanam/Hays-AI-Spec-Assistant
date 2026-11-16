import streamlit as st
from docx import Document
from docx.shared import Inches, Pt
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml.shared import OxmlElement, qn
from docx.opc.constants import RELATIONSHIP_TYPE
from io import BytesIO
import markdown
from bs4 import BeautifulSoup, NavigableString

# Check if running in Streamlit context
def _is_streamlit_context():
    """Check if we're running in a Streamlit app context."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except:
        return False
import re

URL_PATTERN = re.compile(r"(https?://[^\s<>()]+)")

MARKDOWN_EXTENSIONS = ["extra", "nl2br", "sane_lists"]

def sanitize_content_for_word(content):
    """Sanitize markdown content for Word export by removing/replacing problematic characters."""
    if not content:
        return content
    
    # Simple approach: just remove all emoji characters using a comprehensive pattern
    # This avoids replacement issues and duplication
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map symbols
        "\U0001F1E0-\U0001F1FF"  # flags (iOS)
        "\U00002500-\U00002BEF"  # chinese char
        "\U00002702-\U000027B0"  # dingbats
        "\U000024C2-\U0001F251"  # enclosed characters
        "\U0001f926-\U0001f937"  # additional emojis
        "\U00010000-\U0010ffff"  # supplementary multilingual plane
        "\u2640-\u2642"         # gender symbols
        "\u2600-\u2B55"         # misc symbols
        "\u200d"                # zero width joiner
        "\u23cf"                # eject symbol
        "\u23e9"                # fast forward
        "\u231a"                # watch
        "\ufe0f"                # variation selector
        "\u3030"                # wavy dash
        "]+", flags=re.UNICODE
    )
    
    # Remove all emojis and replace with space, then clean up
    content = emoji_pattern.sub(' ', content)
    
    # Replace problematic characters that might cause Word issues
    content = content.replace('\u2019', "'")  # Right single quotation mark
    content = content.replace('\u2018', "'")  # Left single quotation mark
    content = content.replace('\u201C', '"')  # Left double quotation mark
    content = content.replace('\u201D', '"')  # Right double quotation mark
    content = content.replace('\u2013', '-')  # En dash
    content = content.replace('\u2014', '--') # Em dash
    content = content.replace('\u2026', '...') # Horizontal ellipsis
    
    # Clean up multiple consecutive spaces and newlines
    content = re.sub(r' {2,}', ' ', content)
    content = re.sub(r'\n{3,}', '\n\n', content)
    
    return content.strip()


def _set_table_borders(table):
    """Add borders to all cells in the table."""
    try:
        tbl = table._tbl
        for row in tbl.tr_lst:
            for cell in row.tc_lst:
                # Get or create table cell properties
                tcPr = cell.tcPr
                if tcPr is None:
                    tcPr = OxmlElement('w:tcPr')
                    cell.append(tcPr)
                
                # Create borders element
                tcBorders = OxmlElement('w:tcBorders')
                
                # Define border style (single line, black, 0.5pt)
                for border_name in ['top', 'left', 'bottom', 'right']:
                    border = OxmlElement(f'w:{border_name}')
                    border.set(qn('w:val'), 'single')
                    border.set(qn('w:sz'), '4')  # 0.5pt
                    border.set(qn('w:space'), '0')
                    border.set(qn('w:color'), '000000')
                    tcBorders.append(border)
                
                tcPr.append(tcBorders)
    except Exception:
        # If border setting fails, continue without borders
        pass


def _linkify_plain_urls(soup: BeautifulSoup) -> None:
    """Wrap bare URLs in anchor tags so they become hyperlinks."""
    for text_node in soup.find_all(string=URL_PATTERN):
        if not isinstance(text_node, NavigableString):
            continue
        parent = text_node.parent
        if parent and parent.name in {'a', 'code', 'pre'}:
            continue
        text_value = str(text_node)
        parts = URL_PATTERN.split(text_value)
        if len(parts) <= 1:
            continue
        new_nodes = []
        for index, part in enumerate(parts):
            if index % 2 == 0:
                if part:
                    new_nodes.append(NavigableString(part))
            else:
                link_text = part
                trailing = ''
                while link_text and link_text[-1] in '.,);':
                    trailing = link_text[-1] + trailing
                    link_text = link_text[:-1]
                if not link_text:
                    new_nodes.append(NavigableString(part))
                    continue
                link = soup.new_tag('a', href=link_text)
                link.string = link_text
                new_nodes.append(link)
                if trailing:
                    new_nodes.append(NavigableString(trailing))
        for node in reversed(new_nodes):
            text_node.insert_after(node)
        text_node.extract()




def _add_table_to_doc(doc, table_element):
    """Add a properly formatted table to the Word document."""
    try:
        # Extract table data
        rows = table_element.find_all('tr')
        if not rows:
            return

        # Get headers and data rows
        headers = []
        data_rows = []

        for row_idx, row in enumerate(rows):
            cells = row.find_all(['th', 'td'])
            if not cells:
                continue

            cell_texts = [cell.get_text().strip() for cell in cells]

            # First row with th elements or first row is considered header
            if row.find_all('th') or row_idx == 0:
                headers = cell_texts
            else:
                data_rows.append(cell_texts)

        # If no data rows, don't create table
        if not data_rows and not headers:
            return

        # Create Word table
        num_cols = max(len(headers) if headers else 0,
                      max(len(row) for row in data_rows) if data_rows else 0)
        num_rows = (1 if headers else 0) + len(data_rows)

        if num_rows == 0 or num_cols == 0:
            return

        word_table = doc.add_table(rows=num_rows, cols=num_cols)
        word_table.style = 'Table Grid'  # Use Table Grid for better borders
        word_table.alignment = WD_TABLE_ALIGNMENT.LEFT

        # Set column widths to distribute evenly
        for col in word_table.columns:
            col.width = Inches(6.5 / num_cols)  # Distribute across page width

        row_idx = 0

        # Add headers if they exist
        if headers:
            header_row = word_table.rows[row_idx]
            for col_idx, header_text in enumerate(headers[:num_cols]):
                cell = header_row.cells[col_idx]
                cell.text = header_text
                # Format header text
                for paragraph in cell.paragraphs:
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    for run in paragraph.runs:
                        run.bold = True
                        run.font.size = Pt(12)
                        run.font.name = 'Arial'
                
                # Add header cell shading (light gray)
                try:
                    from docx.oxml.ns import nsdecls, parse_xml
                    shading_elm = parse_xml(f'<w:shd {nsdecls("w")} w:fill="E7E6E6"/>')
                    cell._tc.get_or_add_tcPr().append(shading_elm)
                except:
                    pass  # Continue without shading if it fails
                    
            row_idx += 1

        # Add data rows
        for data_row in data_rows:
            if row_idx >= len(word_table.rows):
                break
            word_row = word_table.rows[row_idx]
            for col_idx, cell_text in enumerate(data_row[:num_cols]):
                if col_idx < len(word_row.cells):
                    cell = word_row.cells[col_idx]
                    cell.text = cell_text
                    # Format data cell text
                    for paragraph in cell.paragraphs:
                        # Left align first column (usually IDs), center others for numeric data
                        if col_idx == 0:
                            paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
                        else:
                            # Check if content looks numeric or date-like for center alignment
                            text = cell_text.strip()
                            if (text.replace('-', '').replace('+', '').replace(' ', '').replace('days', '').replace('Day', '').isdigit() or
                                any(month in text for month in ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 
                                                               'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'])):
                                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                            else:
                                paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
                        for run in paragraph.runs:
                            run.font.size = Pt(11)
                            run.font.name = 'Arial'
            row_idx += 1

        # Apply borders to the table
        _set_table_borders(word_table)

        # Add some spacing after table
        doc.add_paragraph()

    except Exception as e:
        try:
            if _is_streamlit_context():
                st.error(f"Error adding table to document: {str(e)}")
        except:
            pass

def _is_nested_element(element, processed_elements):
    """Check if an element is nested inside another element we're already processing."""
    parent = element.parent
    while parent:
        if parent in processed_elements:
            return True
        # Check if parent is a type we process at top level
        if parent.name in ['ul', 'ol', 'table', 'blockquote']:
            return True
        parent = parent.parent
    return False


def _markdown_to_soup(md_content: str) -> BeautifulSoup:
    """Convert markdown to parsed HTML with bare URLs linkified."""
    sanitized_content = sanitize_content_for_word(md_content or "")
    html_content = markdown.markdown(sanitized_content, extensions=MARKDOWN_EXTENSIONS)
    soup = BeautifulSoup(html_content, "html.parser")
    _linkify_plain_urls(soup)
    return soup


def _add_html_body_to_doc(doc, soup: BeautifulSoup) -> None:
    """Append block-level HTML elements from soup to an existing document."""
    processed_elements = set()

    for element in soup.find_all(
        ["h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "table", "hr", "blockquote", "pre", "code"]
    ):
        if _is_nested_element(element, processed_elements):
            continue

        processed_elements.add(element)

        if element.name in ["h1", "h2", "h3", "h4", "h5", "h6"]:
            level = int(element.name[1])
            heading_text = element.get_text().strip()
            if heading_text:
                doc.add_heading(heading_text, level=level)
        elif element.name == "p":
            if element.parent and element.parent.name == "li":
                continue
            text = element.get_text().strip()
            if text and text not in ["---", "***", "___"]:
                p = doc.add_paragraph()
                _add_formatted_text(p, element)
        elif element.name == "ul":
            for li in element.find_all("li", recursive=False):
                paragraph = _add_list_bullet_paragraph(doc)
                _add_formatted_text(paragraph, li)
        elif element.name == "ol":
            for li in element.find_all("li", recursive=False):
                paragraph = doc.add_paragraph(style="List Number")
                _add_formatted_text(paragraph, li)
        elif element.name == "table":
            _add_table_to_doc(doc, element)
        elif element.name == "hr":
            doc.add_paragraph("─" * 50)
        elif element.name == "blockquote":
            quote_text = element.get_text().strip()
            if quote_text:
                p = doc.add_paragraph(style="Quote")
                _add_formatted_text(p, element)
        elif element.name in ["pre", "code"]:
            if element.parent and element.parent.name in ["p", "li"]:
                continue
            code_text = element.get_text().strip()
            if code_text:
                p = doc.add_paragraph(code_text)
                for run in p.runs:
                    run.font.name = "Consolas"
                    run.font.size = Pt(10)


def append_markdown_to_doc(doc, md_content: str) -> None:
    """Append LLM markdown (bold, links, lists) to an existing Word document."""
    if not md_content or not str(md_content).strip():
        return
    soup = _markdown_to_soup(md_content)
    _add_html_body_to_doc(doc, soup)


def _apply_compact_paragraph_format(
    paragraph,
    *,
    space_before_pt: float = 0,
    space_after_pt: float = 0,
) -> None:
    """Tight single-line spacing for footnote citation bullets."""
    pf = paragraph.paragraph_format
    pf.space_before = Pt(space_before_pt)
    pf.space_after = Pt(space_after_pt)
    pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
    pf.line_spacing = 1.0


def _add_list_bullet_paragraph(doc):
    """Add a bullet paragraph without leaving orphan empty paragraphs.

    ``add_paragraph(style=...)`` can append an empty paragraph before raising
    KeyError when the style is missing (common on checklist templates).
    """
    for style_name in ("List Bullet", "List Paragraph"):
        if style_name in doc.styles:
            return doc.add_paragraph(style=style_name)
    p = doc.add_paragraph()
    p.add_run("\u2022 ")
    return p


def add_footnote_citations(doc, citations: list[str]) -> None:
    """Add a compact Citations label and bullet list to a Word document."""
    add_markdown_paragraph(doc, "**Citations:**")
    if doc.paragraphs:
        _apply_compact_paragraph_format(doc.paragraphs[-1], space_after_pt=2)
    lines: list[str] = []
    for cite in citations:
        text = str(cite).strip()
        if not text:
            continue
        if text.startswith("http"):
            lines.append(f"- [{text}]({text})")
        else:
            lines.append(f"- {text}")
    if not lines:
        return
    soup = _markdown_to_soup("\n".join(lines))
    ul = soup.find("ul")
    if not ul:
        return
    for li in ul.find_all("li", recursive=False):
        p = _add_list_bullet_paragraph(doc)
        _add_formatted_text(p, li)
        _apply_compact_paragraph_format(p)


def add_markdown_paragraph(doc, md_text: str, style: str | None = None) -> None:
    """Add one or more paragraphs rendered from a markdown fragment."""
    if not md_text or not str(md_text).strip():
        return
    soup = _markdown_to_soup(md_text)
    blocks = soup.find_all(["p", "ul", "ol"], recursive=False)
    if not blocks:
        text = soup.get_text().strip()
        if text:
            kwargs = {"style": style} if style else {}
            p = doc.add_paragraph(**kwargs)
            _add_formatted_text(p, soup)
        return
    for block in blocks:
        if block.name == "p":
            kwargs = {"style": style} if style else {}
            p = doc.add_paragraph(**kwargs)
            _add_formatted_text(p, block)
        elif block.name == "ul":
            for li in block.find_all("li", recursive=False):
                add_markdown_list_item(doc, li)
        elif block.name == "ol":
            for li in block.find_all("li", recursive=False):
                try:
                    p = doc.add_paragraph(style="List Number")
                except KeyError:
                    p = doc.add_paragraph()
                _add_formatted_text(p, li)


def add_markdown_list_item(doc, md_text: str, *, compact: bool = False) -> None:
    """Add a bullet list item with markdown formatting and hyperlinks."""
    if not md_text or not str(md_text).strip():
        return
    if hasattr(md_text, "name"):
        li_element = md_text
    else:
        soup = _markdown_to_soup(f"- {md_text}")
        li_element = soup.find("li")
        if not li_element:
            text = soup.get_text().strip()
            p = _add_list_bullet_paragraph(doc)
            if text.startswith("http"):
                _add_hyperlink(p, text, text)
            else:
                _add_styled_run(p, text)
            if compact:
                _apply_compact_paragraph_format(p)
            return
    p = _add_list_bullet_paragraph(doc)
    _add_formatted_text(p, li_element)
    if compact:
        _apply_compact_paragraph_format(p)


def convert_md_to_docx(md_content):
    """Convert markdown content to a Word document with proper table support."""
    try:
        doc = Document()
        append_markdown_to_doc(doc, md_content)
        return doc
    except Exception as e:
        try:
            if _is_streamlit_context():
                st.error(f"Error converting to Word document: {str(e)}")
        except:
            pass
        return None


def _split_url_trailing_punctuation(url: str) -> tuple[str, str]:
    """Separate trailing punctuation from a URL match."""
    link_text = url
    trailing = ""
    while link_text and link_text[-1] in ".,);":
        trailing = link_text[-1] + trailing
        link_text = link_text[:-1]
    return link_text, trailing


def _add_styled_run(paragraph, text: str, *, bold: bool = False, italic: bool = False, monospace: bool = False) -> None:
    """Add a plain run with optional bold, italic, or monospace."""
    if not text:
        return
    run = paragraph.add_run(text)
    if bold:
        run.bold = True
    if italic:
        run.italic = True
    if monospace:
        run.font.name = "Consolas"
        run.font.size = Pt(10)


def _add_text_with_urls(paragraph, text: str, *, bold: bool = False, italic: bool = False) -> None:
    """Add text, turning bare http(s) URLs into clickable hyperlinks."""
    if not text:
        return
    parts = URL_PATTERN.split(text)
    if len(parts) <= 1:
        _add_styled_run(paragraph, text, bold=bold, italic=italic)
        return
    for index, part in enumerate(parts):
        if index % 2 == 0:
            _add_styled_run(paragraph, part, bold=bold, italic=italic)
        else:
            link_text, trailing = _split_url_trailing_punctuation(part)
            if not link_text:
                _add_styled_run(paragraph, part, bold=bold, italic=italic)
                continue
            _add_hyperlink(paragraph, link_text, link_text, bold=bold, italic=italic)
            if trailing:
                _add_styled_run(paragraph, trailing, bold=bold, italic=italic)


def _add_hyperlink(
    paragraph,
    text: str,
    url: str,
    *,
    bold: bool = False,
    italic: bool = False,
) -> None:
    """Add a clickable hyperlink run to a paragraph."""
    if not url:
        _add_styled_run(paragraph, text, bold=bold, italic=italic)
        return
    try:
        part = paragraph.part
        r_id = part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
        hyperlink = OxmlElement("w:hyperlink")
        hyperlink.set(qn("r:id"), r_id)

        new_run = OxmlElement("w:r")
        r_pr = OxmlElement("w:rPr")

        r_style = OxmlElement("w:rStyle")
        r_style.set(qn("w:val"), "Hyperlink")
        r_pr.append(r_style)

        color = OxmlElement("w:color")
        color.set(qn("w:val"), "0563C1")
        r_pr.append(color)

        underline = OxmlElement("w:u")
        underline.set(qn("w:val"), "single")
        r_pr.append(underline)

        if bold:
            r_pr.append(OxmlElement("w:b"))
        if italic:
            r_pr.append(OxmlElement("w:i"))

        new_run.append(r_pr)

        text_elem = OxmlElement("w:t")
        text_elem.text = text
        new_run.append(text_elem)

        hyperlink.append(new_run)
        paragraph._p.append(hyperlink)
    except Exception:
        _add_styled_run(paragraph, text, bold=bold, italic=italic)


def _add_formatted_text(paragraph, element, bold: bool = False, italic: bool = False) -> None:
    """Add formatted text (bold, italic, hyperlinks) to a paragraph."""
    try:
        if isinstance(element, NavigableString):
            _add_text_with_urls(paragraph, str(element), bold=bold, italic=italic)
            return

        if not hasattr(element, "name"):
            _add_styled_run(paragraph, str(element), bold=bold, italic=italic)
            return

        if element.name == "br":
            paragraph.add_run("\n")
            return

        if element.name == "a" and element.get("href"):
            link_text = element.get_text().strip() or element.get("href")
            _add_hyperlink(paragraph, link_text, element.get("href"), bold=bold, italic=italic)
            return

        if element.name in {"strong", "b"}:
            for child in element.contents:
                _add_formatted_text(paragraph, child, bold=True, italic=italic)
            return

        if element.name in {"em", "i"}:
            for child in element.contents:
                _add_formatted_text(paragraph, child, bold=bold, italic=True)
            return

        if element.name == "code":
            _add_styled_run(paragraph, element.get_text(), bold=bold, italic=italic, monospace=True)
            return

        for child in element.contents:
            _add_formatted_text(paragraph, child, bold=bold, italic=italic)
    except Exception:
        fallback = element.get_text() if hasattr(element, "get_text") else str(element)
        _add_styled_run(paragraph, fallback, bold=bold, italic=italic)




def get_docx_bytes(doc):
    """Convert a docx Document object to bytes."""
    try:
        docx_bytes = BytesIO()
        doc.save(docx_bytes)
        docx_bytes.seek(0)
        return docx_bytes.getvalue()
    except Exception as e:
        try:
            if _is_streamlit_context():
                st.error(f"Error generating Word document bytes: {str(e)}")
        except:
            pass
        return None
