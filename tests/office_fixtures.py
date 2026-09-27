"""Small, synthetic Office and PDF files, built in memory for the document tests.

Hand-written XML rather than python-docx or openpyxl, so the tests need nothing the app
itself does not, and so each file holds exactly the awkward cases being tested: text split
across runs, a hyperlink address, a tracked deletion, a header, author metadata. The data
is the same unmistakable placeholders as the rest of the suite.
"""

import io
import zipfile

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_BASE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"

DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

# Kept byte-for-byte so a test can check an untouched paragraph really was untouched.
PLAIN_PARAGRAPH = '<w:p><w:r><w:t>Nothing sensitive here.</w:t></w:r></w:p>'


def _zip(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return out.getvalue()


def _core(creator, title):
    return (
        DECL
        + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>" + title + "</dc:title>"
        "<dc:creator>" + creator + "</dc:creator>"
        "<cp:lastModifiedBy>John Roe</cp:lastModifiedBy>"
        "</cp:coreProperties>"
    )


def build_docx():
    document = (
        DECL
        + '<w:document xmlns:w="' + W_NS + '" xmlns:r="' + R_NS + '"><w:body>'
        # Split across runs with different formatting, as Word usually saves a name.
        '<w:p><w:r><w:rPr><w:b/></w:rPr><w:t xml:space="preserve">Customer: </w:t></w:r>'
        '<w:r><w:t>Jane</w:t></w:r><w:r><w:t xml:space="preserve"> Doe</w:t></w:r></w:p>'
        + PLAIN_PARAGRAPH
        + '<w:tbl><w:tr>'
        '<w:tc><w:p><w:r><w:t>Card</w:t></w:r></w:p></w:tc>'
        '<w:tc><w:p><w:r><w:t>4111 1111 1111 1111</w:t></w:r></w:p></w:tc>'
        '</w:tr></w:tbl>'
        # The display text is harmless; the address behind it is not.
        '<w:p><w:hyperlink r:id="rId2"><w:r><w:t>email her</w:t></w:r></w:hyperlink></w:p>'
        '<w:p><w:r><w:t>Jane Doe called again.</w:t><w:tab/><w:t>Urgent</w:t></w:r></w:p>'
        # A tracked deletion: invisible in Word, still in the file.
        '<w:p><w:del w:id="1" w:author="John Roe"><w:r><w:delText>jane.doe@example.com</w:delText></w:r></w:del>'
        '<w:r><w:t>Address removed.</w:t></w:r></w:p>'
        '<w:sectPr><w:headerReference w:type="default" r:id="rId1"/></w:sectPr>'
        "</w:body></w:document>"
    )
    header = (
        DECL
        + '<w:hdr xmlns:w="' + W_NS + '"><w:p><w:r><w:t>Prepared for Jane Doe</w:t></w:r></w:p></w:hdr>'
    )
    return _zip(
        {
            "[Content_Types].xml": DECL
            + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            "</Types>",
            "_rels/.rels": DECL
            + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="' + REL_BASE + 'officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            "</Relationships>",
            "word/document.xml": document,
            "word/header1.xml": header,
            "word/_rels/document.xml.rels": DECL
            + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="' + REL_BASE + 'header" Target="header1.xml"/>'
            '<Relationship Id="rId2" Type="' + REL_BASE + 'hyperlink" Target="mailto:jane.doe@example.com" TargetMode="External"/>'
            "</Relationships>",
            "docProps/core.xml": _core("Jane Doe", "Complaint from Jane Doe"),
        }
    )


def build_xlsx():
    shared = ["Name", "Jane Doe", "jane.doe@example.com"]
    shared_xml = (
        DECL
        + '<sst xmlns="' + S_NS + '" count="3" uniqueCount="3">'
        + "".join("<si><t>" + s + "</t></si>" for s in shared)
        + "</sst>"
    )
    sheet1 = (
        DECL
        + '<worksheet xmlns="' + S_NS + '"><sheetData>'
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
        '<row r="2"><c r="A2" t="s"><v>2</v></c></row>'
        '<row r="3"><c r="A3"><v>42</v></c></row>'
        '<row r="4"><c r="A4"><f>SUM(1,2)</f><v>3</v></c></row>'
        # A card number typed as a number rather than as text.
        '<row r="5"><c r="A5"><v>4111111111111111</v></c></row>'
        "</sheetData></worksheet>"
    )
    sheet2 = (
        DECL
        + '<worksheet xmlns="' + S_NS + '"><sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>Follow up with Jane Doe</t></is></c></row>'
        "</sheetData></worksheet>"
    )
    return _zip(
        {
            "[Content_Types].xml": DECL
            + '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            "</Types>",
            "_rels/.rels": DECL
            + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="' + REL_BASE + 'officeDocument" Target="xl/workbook.xml"/>'
            "</Relationships>",
            "xl/workbook.xml": DECL
            + '<workbook xmlns="' + S_NS + '" xmlns:r="' + R_NS + '"><sheets>'
            '<sheet name="Customers" sheetId="1" r:id="rId1"/>'
            '<sheet name="Tasks" sheetId="2" r:id="rId2"/>'
            "</sheets></workbook>",
            "xl/_rels/workbook.xml.rels": DECL
            + '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="' + REL_BASE + 'worksheet" Target="worksheets/sheet1.xml"/>'
            '<Relationship Id="rId2" Type="' + REL_BASE + 'worksheet" Target="worksheets/sheet2.xml"/>'
            '<Relationship Id="rId3" Type="' + REL_BASE + 'sharedStrings" Target="sharedStrings.xml"/>'
            "</Relationships>",
            "xl/worksheets/sheet1.xml": sheet1,
            "xl/worksheets/sheet2.xml": sheet2,
            "xl/sharedStrings.xml": shared_xml,
            "docProps/core.xml": _core("Jane Doe", "Customer list"),
        }
    )


def build_pdf(text):
    """A one-page PDF whose only content is `text` in Helvetica."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(612, 792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 720 Td (" + text.encode("latin-1") + b") Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def all_text(archive_bytes):
    """Every file in a zip, decoded, joined -- for "is this value anywhere at all?" checks."""
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        return "\n".join(
            archive.read(name).decode("utf-8", "replace") for name in archive.namelist()
        )


def part(archive_bytes, name):
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        return archive.read(name).decode("utf-8")
