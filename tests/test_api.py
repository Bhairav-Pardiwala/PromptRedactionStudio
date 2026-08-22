"""End-to-end tests over the API.

All tests use the small spaCy model so the suite stays fast; the point here is the
plumbing around Presidio (operators, custom recognizers, allow-lists, the restore
round trip), not the NER model's accuracy.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

ENGINE = "spacy_sm"

# A Luhn-valid test card and a well-known placeholder SSN, so the checksum-validating
# recognizers actually fire.
PROMPT = (
    "Please email Marcus Delgado at marcus.delgado@northwind.com or call "
    "+1 (415) 555-0182. His card is 4111 1111 1111 1111 and the server is at 10.42.18.7. "
    "The reference is ACME-99120."
)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def redact(client, **overrides):
    payload = {"text": PROMPT, "engine": ENGINE, "score_threshold": 0.4}
    payload.update(overrides)
    response = client.post("/api/redact", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def entity_types(findings):
    return {finding["entity_type"] for finding in findings}


# --- config ---------------------------------------------------------------------


def test_config_lists_engines_entities_and_operators(client):
    config = client.get("/api/config").json()

    assert set(config["engines"]) == {"spacy_sm", "spacy_lg", "transformers"}
    assert config["engines"][ENGINE]["available"] is True
    assert config["entity_groups"], "expected at least one entity group"

    operator_names = {op["name"] for op in config["operators"]}
    assert {"placeholder", "replace", "redact", "mask", "hash", "encrypt", "keep"} == operator_names
    assert config["sample_prompt"]


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"


# --- detection ------------------------------------------------------------------


def test_analyze_detects_the_expected_entity_types(client):
    response = client.post(
        "/api/analyze", json={"text": PROMPT, "engine": ENGINE, "score_threshold": 0.4}
    )
    assert response.status_code == 200
    found = entity_types(response.json()["findings"])

    for expected in ("EMAIL_ADDRESS", "CREDIT_CARD", "PHONE_NUMBER"):
        assert expected in found, "missing " + expected + " in " + str(found)


def test_score_threshold_narrows_results(client):
    def count(threshold):
        response = client.post(
            "/api/analyze",
            json={"text": PROMPT, "engine": ENGINE, "score_threshold": threshold},
        )
        return response.json()["count"]

    assert count(0.95) < count(0.1)


def test_entity_selection_limits_detection(client):
    response = client.post(
        "/api/analyze",
        json={"text": PROMPT, "engine": ENGINE, "entities": ["EMAIL_ADDRESS"]},
    )
    assert entity_types(response.json()["findings"]) == {"EMAIL_ADDRESS"}


def test_explanations_are_populated_when_requested(client):
    response = client.post(
        "/api/analyze",
        json={
            "text": PROMPT,
            "engine": ENGINE,
            "entities": ["EMAIL_ADDRESS"],
            "return_explanations": True,
        },
    )
    finding = response.json()["findings"][0]
    assert finding["explanation"] is not None
    assert finding["explanation"]["recognizer"]


def test_allow_list_prevents_redaction(client):
    email = "marcus.delgado@northwind.com"

    without = redact(client, entities=["EMAIL_ADDRESS"])
    assert email not in without["redacted_text"]

    with_allow = redact(client, entities=["EMAIL_ADDRESS"], allow_list=[email])
    assert email in with_allow["redacted_text"]
    assert with_allow["findings"] == []


# --- operators ------------------------------------------------------------------


def test_replace_uses_the_entity_type_by_default(client):
    result = redact(
        client, entities=["EMAIL_ADDRESS"], default_operator={"type": "replace", "params": {}}
    )
    assert "<EMAIL_ADDRESS>" in result["redacted_text"]


def test_replace_honours_a_custom_value(client):
    result = redact(
        client,
        entities=["EMAIL_ADDRESS"],
        default_operator={"type": "replace", "params": {"new_value": "[hidden]"}},
    )
    assert "[hidden]" in result["redacted_text"]


def test_redact_removes_the_value_entirely(client):
    result = redact(
        client, entities=["EMAIL_ADDRESS"], default_operator={"type": "redact", "params": {}}
    )
    assert "marcus.delgado@northwind.com" not in result["redacted_text"]
    assert "<" not in result["redacted_text"]


def test_mask_applies_the_requested_character_count(client):
    result = redact(
        client,
        entities=["EMAIL_ADDRESS"],
        default_operator={
            "type": "mask",
            "params": {"masking_char": "#", "chars_to_mask": 5, "from_end": False},
        },
    )
    assert "#####" in result["redacted_text"]
    # Only the first five characters are masked, so the domain survives.
    assert "northwind.com" in result["redacted_text"]


def test_mask_from_end_masks_the_tail(client):
    result = redact(
        client,
        entities=["EMAIL_ADDRESS"],
        default_operator={
            "type": "mask",
            "params": {"masking_char": "#", "chars_to_mask": 5, "from_end": True},
        },
    )
    assert "marcus" in result["redacted_text"]
    assert "northwind.com" not in result["redacted_text"]


@pytest.mark.parametrize("hash_type,length", [("sha256", 64), ("sha512", 128)])
def test_hash_produces_a_digest_of_the_right_length(client, hash_type, length):
    result = redact(
        client,
        entities=["EMAIL_ADDRESS"],
        default_operator={"type": "hash", "params": {"hash_type": hash_type}},
    )
    digest = result["items"][0]["text"]
    assert len(digest) == length
    assert all(char in "0123456789abcdef" for char in digest)


def test_keep_leaves_the_text_untouched_but_still_reports_the_finding(client):
    result = redact(
        client, entities=["EMAIL_ADDRESS"], default_operator={"type": "keep", "params": {}}
    )
    assert result["redacted_text"] == PROMPT
    assert result["findings"], "keep should still report what it detected"


def test_per_entity_operator_overrides_the_default(client):
    result = redact(
        client,
        entities=["EMAIL_ADDRESS", "CREDIT_CARD"],
        default_operator={"type": "redact", "params": {}},
        per_entity_operators={"CREDIT_CARD": {"type": "replace", "params": {"new_value": "[CARD]"}}},
    )
    assert "[CARD]" in result["redacted_text"]
    assert "marcus.delgado@northwind.com" not in result["redacted_text"]


# --- placeholders and the restore round trip ------------------------------------


def test_placeholder_tokens_are_numbered_in_reading_order(client):
    text = "Email alpha@example.com then beta@example.com then gamma@example.com."
    result = redact(client, text=text, entities=["EMAIL_ADDRESS"])

    redacted = result["redacted_text"]
    assert redacted.index("<EMAIL_ADDRESS_1>") < redacted.index("<EMAIL_ADDRESS_2>")
    assert redacted.index("<EMAIL_ADDRESS_2>") < redacted.index("<EMAIL_ADDRESS_3>")
    assert result["mapping"]["<EMAIL_ADDRESS_1>"] == "alpha@example.com"
    assert result["mapping"]["<EMAIL_ADDRESS_3>"] == "gamma@example.com"


def test_the_same_value_reuses_one_token(client):
    text = "Write to sam@example.com, and copy sam@example.com as well."
    result = redact(client, text=text, entities=["EMAIL_ADDRESS"])

    assert len(result["mapping"]) == 1
    assert result["redacted_text"].count("<EMAIL_ADDRESS_1>") == 2


def test_placeholder_round_trip_is_exact(client):
    result = redact(client)
    assert "marcus.delgado@northwind.com" not in result["redacted_text"]

    response = client.post(
        "/api/restore",
        json={"text": result["redacted_text"], "session_id": result["session_id"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["restored_text"] == PROMPT


def test_restore_works_on_a_reworded_reply(client):
    """The real use case: the LLM's answer is different text that reuses the tokens."""
    result = redact(client, entities=["EMAIL_ADDRESS", "PHONE_NUMBER"])
    mapping = result["mapping"]
    tokens = sorted(mapping)
    assert len(tokens) >= 2

    reply = (
        "Certainly. I would reach " + tokens[1] + " first, then follow up with "
        + tokens[0] + ". If " + tokens[0] + " bounces, call again."
    )
    response = client.post(
        "/api/restore", json={"text": reply, "session_id": result["session_id"]}
    )
    restored = response.json()["restored_text"]

    for token, original in mapping.items():
        assert token not in restored
        assert original in restored
    assert response.json()["tokens_restored"] == len(mapping)


def test_restore_reports_tokens_absent_from_the_text(client):
    result = redact(client, entities=["EMAIL_ADDRESS"])
    response = client.post(
        "/api/restore", json={"text": "No tokens here at all.", "session_id": result["session_id"]}
    )
    body = response.json()
    assert body["tokens_restored"] == 0
    assert body["not_found"]


def test_unknown_session_is_a_clean_error(client):
    response = client.post("/api/restore", json={"text": "x", "session_id": "nope"})
    assert response.status_code == 400
    assert "expired" in response.json()["detail"]


# --- encryption -----------------------------------------------------------------


def test_encrypt_decrypt_round_trip(client):
    key = "0123456789abcdef0123456789abcdef"  # 32 chars -> AES-256
    result = redact(
        client,
        entities=["EMAIL_ADDRESS"],
        default_operator={"type": "encrypt", "params": {"key": key}},
    )
    assert "marcus.delgado@northwind.com" not in result["redacted_text"]

    response = client.post(
        "/api/restore",
        json={"text": result["redacted_text"], "session_id": result["session_id"]},
    )
    assert response.status_code == 200, response.text
    assert response.json()["restored_text"] == PROMPT


def test_a_bad_aes_key_length_is_a_clean_400(client):
    response = client.post(
        "/api/redact",
        json={
            "text": PROMPT,
            "engine": ENGINE,
            "entities": ["EMAIL_ADDRESS"],
            "default_operator": {"type": "encrypt", "params": {"key": "tooshort"}},
        },
    )
    assert response.status_code == 400
    assert "16, 24 or 32" in response.json()["detail"]


# --- custom recognizers ---------------------------------------------------------


def test_custom_regex_recognizer_detects_an_internal_reference(client):
    result = redact(
        client,
        custom_recognizers=[
            {"name": "Account ref", "kind": "regex", "pattern": r"ACME-\d{5}", "score": 0.9}
        ],
    )
    assert "ACME-99120" not in result["redacted_text"]
    assert "ACCOUNT_REF" in entity_types(result["findings"])


def test_custom_deny_list_recognizer(client):
    result = redact(
        client,
        text="The codename is Bluebird and the backup is Kestrel.",
        custom_recognizers=[
            {
                "name": "Codename",
                "kind": "deny_list",
                "deny_list": ["Bluebird", "Kestrel"],
                "score": 0.9,
            }
        ],
    )
    assert "Bluebird" not in result["redacted_text"]
    assert "Kestrel" not in result["redacted_text"]


def test_invalid_custom_regex_is_a_clean_400(client):
    response = client.post(
        "/api/analyze",
        json={
            "text": PROMPT,
            "engine": ENGINE,
            "custom_recognizers": [{"name": "Broken", "kind": "regex", "pattern": "ACME-(\\d{5}"}],
        },
    )
    assert response.status_code == 400
    assert "invalid regex" in response.json()["detail"].lower()


# --- misc -----------------------------------------------------------------------


def test_unknown_engine_returns_service_unavailable(client):
    response = client.post("/api/analyze", json={"text": PROMPT, "engine": "not_a_engine"})
    assert response.status_code == 503


def test_empty_text_is_handled_gracefully(client):
    result = redact(client, text="")
    assert result["redacted_text"] == ""
    assert result["findings"] == []


def test_unsupported_hash_type_is_a_clean_400(client):
    """Presidio itself rejects the value; it should surface as a 400, not a 500."""
    response = client.post(
        "/api/redact",
        json={
            "text": PROMPT,
            "engine": ENGINE,
            "entities": ["EMAIL_ADDRESS"],
            "default_operator": {"type": "hash", "params": {"hash_type": "md5"}},
        },
    )
    assert response.status_code == 400
    assert "sha256" in response.json()["detail"]
