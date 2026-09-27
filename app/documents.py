"""Pull the text out of a document, and put redacted text back into it.

Every supported format becomes a list of segments -- a paragraph, a cell, a line, a page --
each with a label saying where it sits. The caller redacts all of them in one pass
(`redaction.redact_segments`, which keeps tokens consistent across the whole file) and hands
the results to `Document.rebuild()`, which writes them into a copy of the original.

Everything happens on bytes in memory. Nothing is written to disk, and nothing from the
document is logged: parser errors are reported by exception class only, because their
messages can quote the content they choked on.

DOCX and XLSX are edited at the XML level rather than through python-docx or openpyxl.
openpyxl drops charts and images when it saves, and python-docx does not see text inside
hyperlinks, tracked insertions, comments or footnotes -- exactly the places PII hides. Only
the parts that changed are re-serialized; every other file in the package is copied through
byte for byte.
"""

from __future__ import annotations

import codecs
import io
import logging
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, unquote

from lxml import etree

logger = logging.getLogger("prompt_redaction.documents")

MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
# A 10 MB zip can inflate to gigabytes. This caps what is actually parsed.
MAX_UNPACKED_BYTES = 100 * 1024 * 1024

MEDIA_TYPES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
}
DOCUMENT_TYPES = list(MEDIA_TYPES)

# No entity expansion, no DTDs, no network: a document is untrusted input.
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
CORE_NS = {
    "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
    "dc": "http://purl.org/dc/elements/1.1/",
}
APP_NS = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"

# Attributes that name people rather than describe content: comment and revision authors,
# and the sign-in identities Word records in people.xml.
_IDENTITY_ATTRS = {"author", "initials", "userId", "displayName"}


def _w(tag: str) -> str:
    return "{" + W + "}" + tag


def _s(tag: str) -> str:
    return "{" + S + "}" + tag


class DocumentError(ValueError):
    """A problem with an uploaded document that the user can do something about."""


# --- slots: one piece of text, and how to write it back ---------------------------------


class _Slot:
    """One segment's worth of text in a document, and how to write a new value back."""

    part: Optional[str] = None
    label: str = ""

    def text(self) -> str:
        raise NotImplementedError

    def write(self, new: str) -> bool:
        """Write `new` back. Returns True when formatting had to be merged to do it."""
        raise NotImplementedError


class _RunGroup(_Slot):
    """Text spread across several elements, such as a Word paragraph's runs.

    `pieces` holds the text-bearing elements in reading order, with "\\t" and "\\n" strings
    standing for the tab and break elements between them. On write, the new text is split
    at those same separators, and each stretch goes into the first element of its chunk
    with the rest emptied. That keeps the first run's formatting and every tab and break.
    """

    def __init__(self, part: str, label: str, pieces: List[object]):
        self.part = part
        self.label = label
        self.pieces = pieces

    def text(self) -> str:
        return "".join(p if isinstance(p, str) else (p.text or "") for p in self.pieces)

    def write(self, new: str) -> bool:
        chunks: List[List[etree._Element]] = [[]]
        separators: List[str] = []
        for piece in self.pieces:
            if isinstance(piece, str):
                separators.append(piece)
                chunks.append([])
            else:
                chunks[-1].append(piece)

        parts = re.split(r"(\t|\n)", new)
        texts, found = parts[0::2], parts[1::2]
        aligned = found == separators and all(
            chunk or not text for chunk, text in zip(chunks, texts)
        )
        if not aligned:
            # A detection swallowed a tab or a break, so the text no longer lines up with
            # the layout. Put it all in the first element rather than risk leaving any
            # original text behind in the others.
            texts = [re.sub(r"[\t\n]", " ", new)] + [""] * (len(chunks) - 1)
            chunks = [[e for chunk in chunks for e in chunk]] + [[]] * (len(chunks) - 1)

        merged = False
        for chunk, text in zip(chunks, texts):
            if not chunk:
                continue
            if sum(1 for e in chunk if e.text) > 1:
                merged = True
            chunk[0].text = text
            chunk[0].set(XML_SPACE, "preserve")
            for element in chunk[1:]:
                element.text = ""
        return merged or not aligned


class _ElementText(_Slot):
    def __init__(self, part: str, label: str, element: etree._Element):
        self.part = part
        self.label = label
        self.element = element

    def text(self) -> str:
        return self.element.text or ""

    def write(self, new: str) -> bool:
        self.element.text = new
        return False


class _LinkTarget(_Slot):
    """A hyperlink's address in a .rels file. Tokens are not valid in a URI, so a changed
    address is percent-encoded on the way back in, and decoded again on the way out."""

    def __init__(self, part: str, element: etree._Element):
        self.part = part
        self.label = "Link address"
        self.element = element

    def text(self) -> str:
        return unquote(self.element.get("Target") or "")

    def write(self, new: str) -> bool:
        self.element.set("Target", quote(new, safe=":/?#[]@!$&'()*+,;=%~"))
        return False


class _NumericCell(_Slot):
    """A long number in a spreadsheet cell -- a phone or card number typed as a number.

    If redaction changes it, the cell becomes an inline string holding the token."""

    def __init__(self, part: str, label: str, cell: etree._Element, value: etree._Element):
        self.part = part
        self.label = label
        self.cell = cell
        self.value = value

    def text(self) -> str:
        return self.value.text or ""

    def write(self, new: str) -> bool:
        self.cell.remove(self.value)
        self.cell.set("t", "inlineStr")
        inline = etree.SubElement(self.cell, _s("is"))
        text = etree.SubElement(inline, _s("t"))
        text.text = new
        text.set(XML_SPACE, "preserve")
        return False


# --- the document handed back to the caller ---------------------------------------------


@dataclass
class Document:
    """A parsed upload: its text segments, where each one lives, and how to rebuild it."""

    kind: str
    segments: List[str]
    labels: List[str]
    output_suffix: str
    media_type: str
    warnings: List[str] = field(default_factory=list)
    _rebuild: Optional[Callable[[List[str]], bytes]] = None

    def rebuild(self, new_segments: List[str]) -> bytes:
        if len(new_segments) != len(self.segments):
            raise DocumentError("The document changed shape while it was being redacted.")
        assert self._rebuild is not None
        return self._rebuild(new_segments)


def output_name(filename: str, tag: str, suffix: str) -> str:
    """complaint.docx -> complaint.redacted.docx; complaint.redacted.docx -> complaint.restored.docx"""
    stem = PurePath(filename.replace("\\", "/")).stem or "document"
    for old in (".redacted", ".restored"):
        if stem.endswith(old):
            stem = stem[: -len(old)]
    return stem + "." + tag + suffix


def open_document(filename: str, data: bytes) -> Document:
    """Parse an upload into a Document. Raises DocumentError for anything unusable."""
    extension = PurePath(filename.replace("\\", "/")).suffix.lower()
    if extension not in MEDIA_TYPES:
        raise DocumentError(
            "Unsupported file type"
            + (" " + extension if extension else "")
            + ". Supported types: "
            + ", ".join(DOCUMENT_TYPES)
            + "."
        )
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentError(
            "That file is too large. The limit is "
            + str(MAX_DOCUMENT_BYTES // (1024 * 1024))
            + " MB."
        )

    openers = {
        ".docx": _open_docx,
        ".xlsx": _open_xlsx,
        ".pdf": _open_pdf,
        ".txt": lambda d: _open_text(d, ".txt", "Line"),
        ".md": lambda d: _open_text(d, ".md", "Line"),
        ".csv": lambda d: _open_text(d, ".csv", "Row"),
    }
    try:
        return openers[extension](data)
    except DocumentError:
        raise
    except Exception as exc:
        # The class name only: parser messages can quote the document's own content.
        logger.warning("Could not parse a %s upload: %s", extension, type(exc).__name__)
        raise DocumentError(
            "Could not read that file as " + extension.lstrip(".").upper() + "."
        ) from None


# --- plain text -------------------------------------------------------------------------


def _open_text(data: bytes, extension: str, unit: str) -> Document:
    bom = data.startswith(codecs.BOM_UTF8)
    encoding = "utf-8"
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Windows-1252 covers most legacy files; Latin-1 decodes anything and round-trips
        # the bytes exactly, so the file comes back as it went in.
        for encoding in ("cp1252", "latin-1"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue

    lines = text.split("\n")

    def rebuild(new: List[str]) -> bytes:
        body = "\n".join(new).encode(encoding)
        return codecs.BOM_UTF8 + body if bom else body

    return Document(
        kind=extension.lstrip("."),
        segments=lines,
        labels=[unit + " " + str(i + 1) for i in range(len(lines))],
        output_suffix=extension,
        media_type=MEDIA_TYPES[extension],
        _rebuild=rebuild,
    )


# --- PDF --------------------------------------------------------------------------------


def _open_pdf(data: bytes) -> Document:
    import pypdf  # imported here so the rest of the app never pays for it

    reader = pypdf.PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        try:
            unlocked = reader.decrypt("")
        except Exception:
            unlocked = 0
        if not unlocked:
            raise DocumentError("That PDF is password-protected. Remove the password and try again.")

    pages = [page.extract_text() or "" for page in reader.pages]
    warnings = [
        "A PDF comes back as redacted text: its layout, images and formatting are not kept."
    ]
    if not any(page.strip() for page in pages):
        warnings.append(
            "No text was found in this PDF. A scanned PDF is an image of text and would "
            "need OCR, which is not supported."
        )

    return Document(
        kind="pdf",
        segments=pages,
        labels=["Page " + str(i + 1) for i in range(len(pages))],
        output_suffix=".txt",
        media_type=MEDIA_TYPES[".txt"],
        warnings=warnings,
        _rebuild=lambda new: "\n\n".join(new).encode("utf-8"),
    )


# --- Office packages (shared by DOCX and XLSX) -----------------------------------------


class _Package:
    """An Office zip held in memory, parsing parts on demand and writing back changed ones."""

    def __init__(self, data: bytes, kind: str):
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise DocumentError("Could not read that file as " + kind.upper() + ".") from None
        self.infos = self.zip.infolist()
        self.names = [info.filename for info in self.infos]
        self.trees: Dict[str, etree._ElementTree] = {}
        self.dirty: set = set()
        self._unpacked = 0

    def has(self, name: str) -> bool:
        return name in self.names

    def tree(self, name: str) -> etree._ElementTree:
        if name not in self.trees:
            info = self.zip.getinfo(name)
            self._unpacked += info.file_size
            if self._unpacked > MAX_UNPACKED_BYTES:
                raise DocumentError("That file unpacks to more text than can be processed.")
            self.trees[name] = etree.parse(io.BytesIO(self.zip.read(name)), _PARSER)
        return self.trees[name]

    def root(self, name: str) -> etree._Element:
        return self.tree(name).getroot()

    def matching(self, pattern: str) -> List[str]:
        regex = re.compile(pattern)
        return [name for name in self.names if regex.fullmatch(name)]

    def save(self) -> bytes:
        out = io.BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
            for info in self.infos:
                if info.filename in self.dirty:
                    tree = self.trees[info.filename]
                    payload = etree.tostring(
                        tree,
                        xml_declaration=True,
                        encoding="UTF-8",
                        standalone=tree.docinfo.standalone,
                    )
                else:
                    payload = self.zip.read(info.filename)
                copy = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                copy.compress_type = info.compress_type
                copy.external_attr = info.external_attr
                target.writestr(copy, payload)
        return out.getvalue()


def _scrub_properties(pkg: _Package, slots: List[_Slot]) -> None:
    """Blank who wrote the file, and offer its title and subject up for redaction."""
    if pkg.has("docProps/core.xml"):
        root = pkg.root("docProps/core.xml")
        for tag in ("dc:creator", "cp:lastModifiedBy"):
            for element in root.findall(tag, CORE_NS):
                if element.text:
                    element.text = ""
                    pkg.dirty.add("docProps/core.xml")
        for tag in ("dc:title", "dc:subject", "dc:description", "cp:keywords", "cp:category"):
            for element in root.findall(tag, CORE_NS):
                if element.text and element.text.strip():
                    slots.append(_ElementText("docProps/core.xml", "Document properties", element))
    if pkg.has("docProps/app.xml"):
        root = pkg.root("docProps/app.xml")
        for tag in ("Company", "Manager"):
            element = root.find("{" + APP_NS + "}" + tag)
            if element is not None and element.text:
                element.text = ""
                pkg.dirty.add("docProps/app.xml")


def _scrub_identities(pkg: _Package, name: str) -> None:
    """Blank comment and revision authors, and the sign-in names Office records."""
    for element in pkg.root(name).iter():
        for attr in list(element.attrib):
            if etree.QName(attr).localname in _IDENTITY_ATTRS and element.get(attr):
                element.set(attr, "")
                pkg.dirty.add(name)


def _link_slots(pkg: _Package, rels_name: str) -> List[_Slot]:
    slots: List[_Slot] = []
    for rel in pkg.root(rels_name).iter("{" + PKG_REL + "}Relationship"):
        if rel.get("TargetMode") == "External" and (rel.get("Type") or "").endswith("/hyperlink"):
            slots.append(_LinkTarget(rels_name, rel))
    return slots


def _office_document(pkg: _Package, kind: str, slots: List[_Slot], warnings: List[str]) -> Document:
    originals = [slot.text() for slot in slots]

    def rebuild(new: List[str]) -> bytes:
        merged = 0
        for slot, old, value in zip(slots, originals, new):
            if value == old:
                continue
            if slot.write(value):
                merged += 1
            pkg.dirty.add(slot.part)
        if merged:
            document.warnings.append(
                "Mixed formatting inside "
                + str(merged)
                + " changed "
                + ("paragraph was" if merged == 1 else "paragraphs were")
                + " merged into the formatting of its first run."
            )
        return pkg.save()

    suffix = "." + kind
    document = Document(
        kind=kind,
        segments=originals,
        labels=[slot.label for slot in slots],
        output_suffix=suffix,
        media_type=MEDIA_TYPES[suffix],
        warnings=warnings,
        _rebuild=rebuild,
    )
    return document


# --- DOCX -------------------------------------------------------------------------------


def _owner(element: etree._Element, tag: str) -> Optional[etree._Element]:
    """The nearest ancestor with `tag` -- nested text-box paragraphs have their own."""
    for ancestor in element.iterancestors(tag):
        return ancestor
    return None


def _paragraph_pieces(p: etree._Element) -> List[object]:
    pieces: List[object] = []
    for element in p.iter(_w("t"), _w("tab"), _w("br"), _w("cr")):
        if _owner(element, _w("p")) is not p:
            continue
        if element.tag == _w("t"):
            pieces.append(element)
        elif element.getparent() is not None and element.getparent().tag == _w("r"):
            # A tab inside w:tabs is a tab-stop definition, not a character.
            pieces.append("\t" if element.tag == _w("tab") else "\n")
    return pieces


def _table_cell_labels(root: etree._Element) -> Dict[etree._Element, str]:
    labels: Dict[etree._Element, str] = {}
    for number, table in enumerate(root.iter(_w("tbl")), start=1):
        rows = [tr for tr in table.iter(_w("tr")) if _owner(tr, _w("tbl")) is table]
        for r, row in enumerate(rows, start=1):
            cells = [tc for tc in row.iter(_w("tc")) if _owner(tc, _w("tr")) is row]
            for c, cell in enumerate(cells, start=1):
                labels[cell] = "Table " + str(number) + " · R" + str(r) + "C" + str(c)
    return labels


def _docx_part_slots(pkg: _Package, name: str, part_label: Optional[str]) -> List[_Slot]:
    root = pkg.root(name)
    cells = _table_cell_labels(root)
    slots: List[_Slot] = []
    paragraph_number = 0

    for p in root.iter(_w("p")):
        pieces = _paragraph_pieces(p)
        text = "".join(x if isinstance(x, str) else (x.text or "") for x in pieces)
        in_table = _owner(p, _w("tc"))
        in_textbox = _owner(p, _w("txbxContent")) is not None

        if in_textbox:
            where = "Text box"
        elif in_table is not None:
            where = cells.get(in_table, "Table")
        else:
            paragraph_number += 1
            where = "Paragraph " + str(paragraph_number)
        if not part_label:
            label = where
        elif in_table is not None or in_textbox:
            label = part_label + " · " + where
        else:
            label = part_label

        if text.strip():
            slots.append(_RunGroup(name, label, pieces))

        # Field instructions are hidden, but HYPERLINK "mailto:..." is still in the file.
        for instr in p.iter(_w("instrText")):
            if _owner(instr, _w("p")) is p and (instr.text or "").strip():
                slots.append(_ElementText(name, "Field code · " + label, instr))
    return slots


def _remove_tracked_deletions(pkg: _Package, name: str) -> bool:
    removed = False
    root = pkg.root(name)
    for tag in ("del", "moveFrom"):
        for element in list(root.iter(_w(tag))):
            if element.find(".//" + _w("delText")) is not None or element.find(
                ".//" + _w("delInstrText")
            ) is not None:
                element.getparent().remove(element)
                removed = True
    if removed:
        pkg.dirty.add(name)
    return removed


def _open_docx(data: bytes) -> Document:
    pkg = _Package(data, "docx")
    if not pkg.has("word/document.xml"):
        raise DocumentError("Could not read that file as DOCX.")

    parts: List[Tuple[str, Optional[str]]] = [("word/document.xml", None)]
    for kind in ("header", "footer"):
        for name in sorted(pkg.matching(r"word/" + kind + r"\d*\.xml")):
            number = re.sub(r"\D", "", name)
            parts.append((name, (kind.title() + " " + number).strip()))
    for name, label in (
        ("word/footnotes.xml", "Footnote"),
        ("word/endnotes.xml", "Endnote"),
        ("word/comments.xml", "Comment"),
    ):
        if pkg.has(name):
            parts.append((name, label))

    warnings: List[str] = []
    slots: List[_Slot] = []
    removed_deletions = False
    for name, label in parts:
        removed_deletions = _remove_tracked_deletions(pkg, name) or removed_deletions
        _scrub_identities(pkg, name)
        slots.extend(_docx_part_slots(pkg, name, label))

    for name in pkg.matching(r"word/_rels/[^/]+\.rels"):
        slots.extend(_link_slots(pkg, name))
    for name in pkg.matching(r"word/(people|commentsExtended|commentsIds|commentsExtensible)\.xml"):
        _scrub_identities(pkg, name)
    _scrub_properties(pkg, slots)

    if removed_deletions:
        warnings.append(
            "Tracked deletions were removed, because the deleted text was still in the file."
        )
    if pkg.matching(r"word/(media|embeddings|charts)/.+"):
        warnings.append("Images, charts and embedded objects were not checked.")
    if any(True for _ in pkg.root("word/document.xml").iter(_w("dataBinding"))):
        warnings.append(
            "Content controls bound to custom XML keep a second copy of their values, "
            "which was not checked."
        )

    return _office_document(pkg, "docx", slots, warnings)


# --- XLSX -------------------------------------------------------------------------------


def _rels_targets(pkg: _Package, rels_name: str, base: str) -> Dict[str, Tuple[str, str]]:
    """rId -> (relationship type, part name) for one .rels file."""
    targets: Dict[str, Tuple[str, str]] = {}
    if not pkg.has(rels_name):
        return targets
    for rel in pkg.root(rels_name).iter("{" + PKG_REL + "}Relationship"):
        target = rel.get("Target") or ""
        if rel.get("TargetMode") == "External":
            continue
        path = target.lstrip("/") if target.startswith("/") else _join(base, target)
        targets[rel.get("Id") or ""] = (rel.get("Type") or "", path)
    return targets


def _join(base: str, target: str) -> str:
    parts = [p for p in base.split("/") if p]
    for piece in target.split("/"):
        if piece == "..":
            if parts:
                parts.pop()
        elif piece and piece != ".":
            parts.append(piece)
    return "/".join(parts)


def _string_pieces(container: etree._Element) -> List[object]:
    """The visible text of a shared or inline string: its own <t>, or each rich run's.
    Phonetic hints (<rPh>) are skipped -- they are readings, not the value."""
    pieces: List[object] = []
    for child in container:
        if child.tag == _s("t"):
            pieces.append(child)
        elif child.tag == _s("r"):
            pieces.extend(child.findall(_s("t")))
    return pieces


_LONG_NUMBER = re.compile(r"\d{7,}")


def _open_xlsx(data: bytes) -> Document:
    pkg = _Package(data, "xlsx")
    if not pkg.has("xl/workbook.xml"):
        raise DocumentError("Could not read that file as XLSX.")

    book = _rels_targets(pkg, "xl/_rels/workbook.xml.rels", "xl")
    sheets: List[Tuple[str, str]] = []
    for sheet in pkg.root("xl/workbook.xml").iter(_s("sheet")):
        rel = book.get(sheet.get("{" + R + "}id") or "")
        if rel and rel[0].endswith("/worksheet") and pkg.has(rel[1]):
            sheets.append((sheet.get("name") or "Sheet", rel[1]))
    shared_name = next(
        (path for kind, path in book.values() if kind.endswith("/sharedStrings")), None
    )

    slots: List[_Slot] = []
    shared_refs: Dict[int, List[str]] = {}
    comment_parts: List[Tuple[str, str]] = []

    for sheet_name, part in sheets:
        root = pkg.root(part)
        for cell in root.iter(_s("c")):
            ref = sheet_name + "!" + (cell.get("r") or "?")
            kind = cell.get("t")
            value = cell.find(_s("v"))
            if kind == "s" and value is not None and (value.text or "").isdigit():
                shared_refs.setdefault(int(value.text), []).append(ref)
            elif kind == "inlineStr":
                inline = cell.find(_s("is"))
                if inline is not None:
                    pieces = _string_pieces(inline)
                    if "".join(p.text or "" for p in pieces).strip():
                        slots.append(_RunGroup(part, ref, pieces))
            elif kind == "str" and value is not None and (value.text or "").strip():
                # A formula's cached result. Excel recalculates it from the (redacted)
                # inputs, but until then this copy is what the file holds.
                slots.append(_ElementText(part, ref + " (formula result)", value))
            elif kind in (None, "n") and value is not None and cell.find(_s("f")) is None:
                if _LONG_NUMBER.fullmatch(value.text or ""):
                    slots.append(_NumericCell(part, ref, cell, value))

        base = part.rsplit("/", 1)[0]
        rels_name = base + "/_rels/" + part.rsplit("/", 1)[1] + ".rels"
        for _, (kind, path) in _rels_targets(pkg, rels_name, base).items():
            if kind.endswith("/comments") and pkg.has(path):
                comment_parts.append((sheet_name, path))
        if pkg.has(rels_name):
            slots.extend(_link_slots(pkg, rels_name))

    if shared_name and pkg.has(shared_name):
        for index, item in enumerate(pkg.root(shared_name).iter(_s("si"))):
            pieces = _string_pieces(item)
            if not "".join(p.text or "" for p in pieces).strip():
                continue
            refs = shared_refs.get(index, [])
            label = refs[0] if refs else "Shared string"
            if len(refs) > 1:
                label += " (+" + str(len(refs) - 1) + " more)"
            slots.append(_RunGroup(shared_name, label, pieces))

    for sheet_name, path in comment_parts:
        root = pkg.root(path)
        for author in root.iter(_s("author")):
            if author.text:
                author.text = ""
                pkg.dirty.add(path)
        for comment in root.iter(_s("comment")):
            text = comment.find(_s("text"))
            if text is not None:
                pieces = _string_pieces(text)
                if "".join(p.text or "" for p in pieces).strip():
                    label = "Comment on " + sheet_name + "!" + (comment.get("ref") or "?")
                    slots.append(_RunGroup(path, label, pieces))

    for path in pkg.matching(r"xl/threadedComments/[^/]+\.xml"):
        for element in pkg.root(path).iter():
            if etree.QName(element).localname == "text" and (element.text or "").strip():
                slots.append(_ElementText(path, "Threaded comment", element))
        _scrub_identities(pkg, path)
    for path in pkg.matching(r"xl/persons/[^/]+\.xml"):
        _scrub_identities(pkg, path)
    _scrub_properties(pkg, slots)

    warnings: List[str] = []
    if pkg.matching(r"xl/pivotCache/.+"):
        warnings.append(
            "Pivot table caches keep their own copy of the source data, which was not "
            "checked. Refresh or remove pivot tables before sharing."
        )
    if pkg.matching(r"xl/(media|embeddings|charts|drawings)/.+"):
        warnings.append("Images, charts and embedded objects were not checked.")
    if pkg.matching(r"xl/externalLinks/.+"):
        warnings.append("Cached values from linked workbooks were not checked.")

    return _office_document(pkg, "xlsx", slots, warnings)
