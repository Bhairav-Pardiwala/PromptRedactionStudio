"""Document redaction: DOCX, XLSX, PDF and plain text, through the API.

Like test_api.py this pins spacy_sm and tests plumbing, not model accuracy. The name is
marked with a deny-list recognizer so detection is deterministic; the email and card
recognizers are pattern-based and need no help.
"""

import base64

import pytest
from fastapi.testclient import TestClient

from app import documents, redaction
from app.main import app

from .office_fixtures import PLAIN_PARAGRAPH, all_text, build_docx, build_pdf, build_xlsx, part

ENGINE = "spacy_sm"
MARK_NAME = [{"name": "names", "entity": "PERSON", "kind": "deny_list", "deny_list": ["Jane Doe"]}]

SECRETS = ("Jane Doe", "jane.doe@example.com", "4111 1111 1111 1111", "4111111111111111")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def b64(data):
    return base64.b64encode(data).decode("ascii")


def redact_file(client, filename, data, **overrides):
    payload = {
        "filename": filename,
        "content_base64": b64(data),
        "engine": ENGINE,
        "score_threshold": 0.4,
        "custom_recognizers": MARK_NAME,
    }
    payload.update(overrides)
    response = client.post("/api/documents/redact", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    return body, base64.b64decode(body["content_base64"])


def restore_file(client, filename, data, session_id):
    response = client.post(
        "/api/documents/restore",
        json={"filename": filename, "content_base64": b64(data), "session_id": session_id},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return body, base64.b64decode(body["content_base64"])


# --- DOCX -----------------------------------------------------------------------------


def test_docx_leaves_no_trace_of_the_originals_anywhere_in_the_package(client):
    body, out = redact_file(client, "complaint.docx", build_docx())
    everything = all_text(out)

    for secret in SECRETS:
        assert secret not in everything, secret + " survived somewhere in the package"
    # The author and editor are blanked outright; the title goes through redaction.
    assert "John Roe" not in everything
    assert body["filename"] == "complaint.redacted.docx"
    assert body["media_type"] == documents.MEDIA_TYPES[".docx"]


def test_docx_uses_one_token_per_person_across_body_header_and_properties(client):
    body, out = redact_file(client, "complaint.docx", build_docx())
    token = next(t for t, v in body["mapping"].items() if v == "Jane Doe")
    escaped = token.replace("<", "&lt;").replace(">", "&gt;")

    assert escaped in part(out, "word/document.xml")
    assert escaped in part(out, "word/header1.xml")
    assert escaped in part(out, "docProps/core.xml")
    assert [t for t, v in body["mapping"].items() if v == "Jane Doe"] == [token]


def test_docx_keeps_untouched_paragraphs_and_layout(client):
    _, out = redact_file(client, "complaint.docx", build_docx())
    document = part(out, "word/document.xml")

    assert PLAIN_PARAGRAPH in document
    # The tab between "called again." and "Urgent" is still its own element.
    assert "<w:tab/>" in document and "Urgent" in document
    # The first run kept its bold formatting and got the whole redacted text.
    assert "<w:b/>" in document


def test_docx_hides_the_hyperlink_address_and_drops_tracked_deletions(client):
    body, out = redact_file(client, "complaint.docx", build_docx())
    rels = part(out, "word/_rels/document.xml.rels")

    assert "mailto:%3CEMAIL_ADDRESS_1%3E" in rels
    assert "delText" not in part(out, "word/document.xml")
    assert any("Tracked deletions" in w for w in body["warnings"])


def test_docx_reports_where_each_finding_sits(client):
    body, _ = redact_file(client, "complaint.docx", build_docx())
    locations = {f["location"] for f in body["findings"]}

    assert {"Paragraph 1", "Header 1", "Table 1 · R1C2", "Link address"} <= locations
    labels = {segment["label"] for segment in body["segments"]}
    assert "Paragraph 2" not in labels, "an unchanged paragraph was listed as changed"
    assert body["segments_changed"] == len(body["segments"])


def test_docx_round_trip_restores_every_value(client):
    body, redacted = redact_file(client, "complaint.docx", build_docx())
    restored_body, restored = restore_file(
        client, body["filename"], redacted, body["session_id"]
    )

    assert restored_body["filename"] == "complaint.restored.docx"
    assert "Jane Doe" in part(restored, "word/header1.xml")
    assert "4111 1111 1111 1111" in part(restored, "word/document.xml")
    assert 'Target="mailto:jane.doe@example.com"' in part(restored, "word/_rels/document.xml.rels")
    assert restored_body["not_found"] == []


def test_a_reply_about_the_document_restores_through_the_text_route(client):
    body, _ = redact_file(client, "complaint.docx", build_docx())
    token = next(t for t, v in body["mapping"].items() if v == "Jane Doe")

    response = client.post(
        "/api/restore",
        json={"text": "Dear " + token + ", sorry.", "session_id": body["session_id"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["restored_text"] == "Dear Jane Doe, sorry."


# --- XLSX -----------------------------------------------------------------------------


def test_xlsx_redacts_strings_and_long_numbers_and_leaves_the_rest(client):
    body, out = redact_file(client, "customers.xlsx", build_xlsx())
    everything = all_text(out)

    for secret in SECRETS:
        assert secret not in everything, secret + " survived somewhere in the workbook"

    sheet1 = part(out, "xl/worksheets/sheet1.xml")
    assert '<c r="A3"><v>42</v></c>' in sheet1
    assert "<f>SUM(1,2)</f><v>3</v>" in sheet1
    assert 't="inlineStr"' in sheet1 and "CREDIT_CARD_1" in sheet1
    assert "<t>Name</t>" in part(out, "xl/sharedStrings.xml")


def test_xlsx_tokens_are_consistent_across_sheets(client):
    body, out = redact_file(client, "customers.xlsx", build_xlsx())
    token = next(t for t, v in body["mapping"].items() if v == "Jane Doe")
    escaped = token.replace("<", "&lt;").replace(">", "&gt;")

    assert escaped in part(out, "xl/sharedStrings.xml")
    assert escaped in part(out, "xl/worksheets/sheet2.xml")
    locations = {f["location"] for f in body["findings"]}
    assert {"Customers!B1", "Tasks!A1", "Customers!A5"} <= locations


def test_xlsx_round_trip(client):
    body, redacted = redact_file(client, "customers.xlsx", build_xlsx())
    _, restored = restore_file(client, body["filename"], redacted, body["session_id"])

    assert "Jane Doe" in part(restored, "xl/worksheets/sheet2.xml")
    assert "jane.doe@example.com" in part(restored, "xl/sharedStrings.xml")


# --- text and PDF ---------------------------------------------------------------------


def test_csv_keeps_its_shape_and_labels_rows(client):
    source = "name,email\nJane Doe,jane.doe@example.com\nnobody,-\n"
    body, out = redact_file(client, "people.csv", source.encode("utf-8"))
    text = out.decode("utf-8")

    assert text.splitlines()[0] == "name,email"
    assert text.splitlines()[2] == "nobody,-"
    assert "Jane Doe" not in text and "jane.doe@example.com" not in text
    assert {f["location"] for f in body["findings"]} == {"Row 2"}

    _, restored = restore_file(client, body["filename"], out, body["session_id"])
    assert restored.decode("utf-8") == source


def test_text_file_keeps_a_byte_order_mark(client):
    source = "\ufeffCall Jane Doe on Monday.\r\n".encode("utf-8")
    _, out = redact_file(client, "notes.txt", source)
    assert out.startswith(b"\xef\xbb\xbf")
    assert out.endswith(b"\r\n") and b"Jane Doe" not in out


def test_pdf_comes_back_as_redacted_text_with_a_warning(client):
    body, out = redact_file(client, "letter.pdf", build_pdf("Contact jane.doe@example.com please"))

    assert body["filename"] == "letter.redacted.txt"
    assert body["media_type"] == "text/plain"
    assert out.decode("utf-8") == "Contact <EMAIL_ADDRESS_1> please"
    assert any("layout" in w for w in body["warnings"])
    assert body["findings"][0]["location"] == "Page 1"


# --- errors ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filename, data, message",
    [
        ("setup.exe", b"MZ", "Unsupported file type .exe"),
        ("broken.docx", b"this is not a zip", "Could not read that file as DOCX."),
        ("broken.xlsx", build_docx(), "Could not read that file as XLSX."),
        ("broken.pdf", b"%PDF-nonsense", "Could not read that file as PDF."),
    ],
)
def test_unusable_files_get_a_clear_fixed_message(client, filename, data, message):
    response = client.post(
        "/api/documents/redact",
        json={"filename": filename, "content_base64": b64(data), "engine": ENGINE},
    )
    assert response.status_code == 400
    assert response.json()["detail"].startswith(message)


def test_oversized_and_malformed_uploads_are_refused(client):
    too_big = b64(b"a" * (documents.MAX_DOCUMENT_BYTES + 1))
    response = client.post(
        "/api/documents/redact",
        json={"filename": "big.txt", "content_base64": too_big, "engine": ENGINE},
    )
    assert response.status_code == 400 and "too large" in response.json()["detail"]

    response = client.post(
        "/api/documents/redact",
        json={"filename": "x.txt", "content_base64": "not base64!!", "engine": ENGINE},
    )
    assert response.status_code == 400 and "base64" in response.json()["detail"]


def test_restore_with_an_unknown_session_is_a_400(client):
    response = client.post(
        "/api/documents/restore",
        json={"filename": "x.txt", "content_base64": b64(b"hi"), "session_id": "nope"},
    )
    assert response.status_code == 400


def test_config_advertises_document_types(client):
    config = client.get("/api/config").json()
    assert set(config["document_types"]) == {".docx", ".xlsx", ".pdf", ".txt", ".md", ".csv"}
    assert config["max_document_bytes"] == documents.MAX_DOCUMENT_BYTES


# --- segment handling -----------------------------------------------------------------


def test_detection_never_reads_across_two_segments():
    spanning = [{"name": "span", "entity": "SPANNED", "kind": "regex", "pattern": r"Jane[\s\S]{1,5}Doe"}]
    result = redaction.redact_segments(
        ["Jane", "Doe"], engine=ENGINE, entities=["SPANNED"], custom_recognizers=spanning,
        store_session=False,
    )
    assert result["segments"] == ["Jane", "Doe"]
    assert result["findings"] == []


def test_segment_findings_carry_offsets_into_their_own_segment():
    result = redaction.redact_segments(
        ["nothing here", "mail jane.doe@example.com"], engine=ENGINE, store_session=False,
        entities=["EMAIL_ADDRESS"],
    )
    finding = result["findings"][0]
    assert finding["segment"] == 1
    assert "mail jane.doe@example.com"[finding["start"]:finding["end"]] == "jane.doe@example.com"
    assert result["segments"] == ["nothing here", "mail <EMAIL_ADDRESS_1>"]


# --- one detection covers every copy ---------------------------------------------------


def test_csv_name_found_in_a_message_is_also_redacted_in_the_name_column(client):
    """The case from the sample support-ticket export: the model found the name in the
    free-text message but not alone in the customer_name column, which left the very
    person the row was hiding. Here a recognizer that only fires after "Customer " plays
    the model's part, so the test does not depend on what spaCy happens to catch."""
    source = (
        "ticket_id,customer_name,email,message\n"
        "TKT-1001,Marcus Testwell,marcus.testwell@example.com,Customer Marcus Testwell reports a billing error\n"
        "TKT-1002,Marcus Testwell,marcus.testwell@example.com,Duplicate of TKT-1001\n"
        "TKT-1003,Elena Sample,elena.sample@example.com,Card declined\n"
    )
    only_after_customer = [
        {"name": "ctx", "entity": "CUSTOMER", "kind": "regex", "pattern": r"(?<=Customer )Marcus Testwell"}
    ]
    body, out = redact_file(
        client,
        "tickets.csv",
        source.encode("utf-8"),
        entities=["CUSTOMER"],
        custom_recognizers=only_after_customer,
    )
    rows = out.decode("utf-8").splitlines()

    assert "Marcus Testwell" not in out.decode("utf-8")
    assert rows[1] == "TKT-1001,<CUSTOMER_1>,marcus.testwell@example.com,Customer <CUSTOMER_1> reports a billing error"
    assert rows[2].startswith("TKT-1002,<CUSTOMER_1>,")
    # Never detected anywhere, so never guessed at.
    assert rows[3].startswith("TKT-1003,Elena Sample,")

    carried = [f for f in body["findings"] if f["recognizer"] == redaction.PROPAGATED_RECOGNIZER]
    assert {f["location"] for f in carried} == {"Row 2", "Row 3"}
    assert body["mapping"] == {"<CUSTOMER_1>": "Marcus Testwell"}


def test_a_wider_copy_found_elsewhere_replaces_a_partial_detection():
    """spaCy caught only "Priya" of "Priya Placeholder" in one cell, but the whole name
    in another: the whole name wins in both, rather than leaving the surname behind."""
    recognizers = [
        {"name": "whole", "entity": "NAME", "kind": "regex", "pattern": r"Priya Placeholder(?= called)"},
        {"name": "first", "entity": "NAME", "kind": "regex", "pattern": r"Priya(?= Placeholder signed)"},
    ]
    result = redaction.redact_segments(
        ["Priya Placeholder called", "Priya Placeholder signed"],
        engine=ENGINE, entities=["NAME"], custom_recognizers=recognizers, store_session=False,
    )
    assert result["segments"] == ["<NAME_1> called", "<NAME_1> signed"]


def _finding(text, entity_type, start=0):
    return {"entity_type": entity_type, "start": start, "end": start + len(text),
            "score": 0.85, "text": text, "recognizer": "test", "explanation": None}


def test_propagation_is_whole_word_and_skips_dates():
    from presidio_analyzer import RecognizerResult

    segments = ["Ann said Monday", "Annual report by Ann on Monday"]
    per_segment = [
        (
            [_finding("Ann", "PERSON"), _finding("Monday", "DATE_TIME", 9)],
            [RecognizerResult("PERSON", 0, 3, 0.85), RecognizerResult("DATE_TIME", 9, 15, 0.85)],
        ),
        ([], []),
    ]
    redaction._propagate_detections(segments, [0, 1], per_segment)

    added = [(r.entity_type, segments[1][r.start:r.end], r.start) for r in per_segment[1][1]]
    assert added == [("PERSON", "Ann", 17)], "matched inside 'Annual', or carried a date"
    assert per_segment[0][1][0].entity_type == "PERSON" and len(per_segment[0][1]) == 2


def test_output_names():
    assert documents.output_name("a.docx", "redacted", ".docx") == "a.redacted.docx"
    assert documents.output_name("a.redacted.docx", "restored", ".docx") == "a.restored.docx"
    assert documents.output_name("C:\\tmp\\scan.pdf", "redacted", ".txt") == "scan.redacted.txt"
