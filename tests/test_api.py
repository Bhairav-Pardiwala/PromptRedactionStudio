"""End-to-end tests over the API.

All tests use the small spaCy model so the suite stays fast; the point here is the
plumbing around Presidio (operators, custom recognizers, allow-lists, the restore
round trip), not the NER model's accuracy.
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app

ENGINE = "spacy_sm"

# Unmistakable placeholders throughout: Jane Doe, the IANA-reserved example.com domain,
# the standard 4111... Visa test card (Luhn-valid, so the checksum recognizer actually
# fires) and a private-range IP. None of it can be mistaken for real personal data.
PROMPT = (
    "Please email Jane Doe at jane.doe@example.com or call "
    "+1 (415) 555-0182. Their card is 4111 1111 1111 1111 and the server is at 10.42.18.7. "
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
    email = "jane.doe@example.com"

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
    assert "jane.doe@example.com" not in result["redacted_text"]
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
    assert "example.com" in result["redacted_text"]


def test_mask_from_end_masks_the_tail(client):
    result = redact(
        client,
        entities=["EMAIL_ADDRESS"],
        default_operator={
            "type": "mask",
            "params": {"masking_char": "#", "chars_to_mask": 5, "from_end": True},
        },
    )
    assert "jane.doe" in result["redacted_text"]
    assert "example.com" not in result["redacted_text"]


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
    assert "jane.doe@example.com" not in result["redacted_text"]


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
    assert "jane.doe@example.com" not in result["redacted_text"]

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
    assert "jane.doe@example.com" not in result["redacted_text"]

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


# --- stateless mode, policy and the optional API key -----------------------------


def test_store_session_false_retains_nothing_but_still_returns_the_mapping(client):
    """What the tray app and extension use: the client keeps the key, not the server."""
    from app import redaction as redaction_module

    before = len(redaction_module.store)
    result = redact(client, entities=["EMAIL_ADDRESS"], store_session=False)

    assert result["session_id"] is None
    assert len(redaction_module.store) == before, "no session should have been created"
    assert result["mapping"], "the caller still needs the mapping to restore locally"
    assert "jane.doe@example.com" not in result["redacted_text"]
    assert "jane.doe@example.com" in result["mapping"].values()


def test_store_session_false_with_no_findings_still_returns_null_session(client):
    result = redact(client, text="Nothing sensitive here.", store_session=False)
    assert result["session_id"] is None


def test_store_session_true_still_works_for_the_web_ui(client):
    """The stateless path must not regress the browser flow."""
    result = redact(client, entities=["EMAIL_ADDRESS"])
    assert result["session_id"]

    response = client.post(
        "/api/restore",
        json={"text": result["redacted_text"], "session_id": result["session_id"]},
    )
    assert response.status_code == 200
    assert response.json()["restored_text"] == PROMPT


def test_policy_returns_defaults_without_a_policy_file(client, monkeypatch):
    monkeypatch.delenv("REDACTION_POLICY_FILE", raising=False)
    body = client.get("/api/policy").json()

    assert body["source"] == "defaults"
    assert body["locked"] is False
    assert body["default_operator"]["type"] == "placeholder"
    assert "EMAIL_ADDRESS" in body["entities"]


def test_policy_file_overrides_defaults(client, monkeypatch, tmp_path):
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(
        "locked: true\n"
        "score_threshold: 0.8\n"
        "entities:\n"
        "  - EMAIL_ADDRESS\n"
        "  - CREDIT_CARD\n"
        "default_operator:\n"
        "  type: hash\n"
        "  params:\n"
        "    hash_type: sha512\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("REDACTION_POLICY_FILE", str(policy_file))

    body = client.get("/api/policy").json()
    assert body["locked"] is True
    assert body["score_threshold"] == 0.8
    assert body["entities"] == ["EMAIL_ADDRESS", "CREDIT_CARD"]
    assert body["default_operator"]["type"] == "hash"
    # Unspecified fields keep their defaults rather than disappearing.
    assert body["allow_list"] == []


def test_missing_policy_file_is_a_server_error_not_a_crash(client, monkeypatch, tmp_path):
    monkeypatch.setenv("REDACTION_POLICY_FILE", str(tmp_path / "nope.yaml"))
    response = client.get("/api/policy")
    assert response.status_code == 500
    assert "does not exist" in response.json()["detail"]


def test_api_key_is_not_required_when_unset(client, monkeypatch):
    monkeypatch.delenv("REDACTION_API_KEY", raising=False)
    response = client.post("/api/analyze", json={"text": PROMPT, "engine": ENGINE})
    assert response.status_code == 200


def test_api_key_is_enforced_when_set(client, monkeypatch):
    monkeypatch.setenv("REDACTION_API_KEY", "s3cret-key")

    missing = client.post("/api/analyze", json={"text": PROMPT, "engine": ENGINE})
    assert missing.status_code == 401

    wrong = client.post(
        "/api/analyze",
        json={"text": PROMPT, "engine": ENGINE},
        headers={"X-Redaction-Key": "wrong"},
    )
    assert wrong.status_code == 401

    correct = client.post(
        "/api/analyze",
        json={"text": PROMPT, "engine": ENGINE},
        headers={"X-Redaction-Key": "s3cret-key"},
    )
    assert correct.status_code == 200


# --- route-level auth coverage ---------------------------------------------------
#
# The point of these is not that the key works -- the two tests above cover that -- but
# that it covers the right routes. A destructive route left open is the failure mode
# worth a regression test, so each row below is pinned rather than assumed.

KEY = "an-instance-key-long-enough-to-pass-32"
ADMIN = "a-separate-admin-key-also-long-enough-ok"


def _call(client, method, path, **kwargs):
    return client.request(method, path, **kwargs)


# (method, path, needs the ordinary key?)
ROUTES = [
    ("POST", "/api/analyze", True),
    ("POST", "/api/redact", True),
    ("POST", "/api/restore", True),
    ("GET", "/api/policy", True),
    ("DELETE", "/api/sessions/whatever", True),
    ("GET", "/api/health", False),
    ("GET", "/api/config", False),
    ("GET", "/", False),
]


@pytest.mark.parametrize("method,path,guarded", ROUTES)
def test_every_route_is_open_when_no_key_is_set(client, monkeypatch, method, path, guarded):
    """The single-user default: nothing is set, so nothing is gated."""
    monkeypatch.delenv("REDACTION_API_KEY", raising=False)
    monkeypatch.delenv("REDACTION_ADMIN_KEY", raising=False)
    body = {"text": PROMPT, "engine": ENGINE} if method == "POST" else None
    if path == "/api/restore":
        body = {"text": "nothing", "session_id": "missing"}

    assert _call(client, method, path, json=body).status_code != 401


@pytest.mark.parametrize("method,path,guarded", ROUTES)
def test_route_guarding_matches_the_documented_table(client, monkeypatch, method, path, guarded):
    monkeypatch.setenv("REDACTION_API_KEY", KEY)
    monkeypatch.delenv("REDACTION_ADMIN_KEY", raising=False)
    body = {"text": PROMPT, "engine": ENGINE} if method == "POST" else None
    if path == "/api/restore":
        body = {"text": "nothing", "session_id": "missing"}

    unauthenticated = _call(client, method, path, json=body)
    if guarded:
        assert unauthenticated.status_code == 401, path
        authenticated = _call(client, method, path, json=body, headers={"X-Redaction-Key": KEY})
        assert authenticated.status_code != 401, path
    else:
        assert unauthenticated.status_code != 401, path


def test_clearing_every_session_is_not_open_to_anonymous_callers(client, monkeypatch):
    """The regression test. This route used to need no key at all, and it discards
    every client's mapping -- so an anonymous POST could break everyone's restore."""
    monkeypatch.setenv("REDACTION_API_KEY", KEY)
    monkeypatch.delenv("REDACTION_ADMIN_KEY", raising=False)

    assert client.post("/api/sessions/clear").status_code == 401
    # With no admin key configured it falls back to the ordinary key rather than
    # leaving the route unreachable.
    assert client.post(
        "/api/sessions/clear", headers={"X-Redaction-Key": KEY}
    ).status_code == 200


def test_admin_key_separates_the_instance_wide_clear(client, monkeypatch):
    monkeypatch.setenv("REDACTION_API_KEY", KEY)
    monkeypatch.setenv("REDACTION_ADMIN_KEY", ADMIN)

    assert client.post("/api/sessions/clear").status_code == 401
    # Once an admin key exists the ordinary key is no longer enough for it.
    assert client.post(
        "/api/sessions/clear", headers={"X-Redaction-Key": KEY}
    ).status_code == 401
    assert client.post(
        "/api/sessions/clear", headers={"X-Redaction-Admin-Key": ADMIN}
    ).status_code == 200


def test_deleting_one_session_leaves_the_others_alone(client, monkeypatch):
    monkeypatch.delenv("REDACTION_API_KEY", raising=False)
    first = redact(client)["session_id"]
    second = redact(client)["session_id"]
    assert first and second and first != second

    dropped = client.delete("/api/sessions/" + first)
    assert dropped.status_code == 200
    assert dropped.json() == {"cleared": 1}

    # The other session still restores, which is the whole point of scoping the delete.
    still_there = client.post(
        "/api/restore", json={"text": "<PERSON_1>", "session_id": second}
    )
    assert still_there.status_code == 200

    # Deleting something already gone is not an error: the caller wanted it gone.
    assert client.delete("/api/sessions/" + first).json() == {"cleared": 0}


def test_config_reports_whether_a_key_is_needed_without_leaking_it(client, monkeypatch):
    monkeypatch.delenv("REDACTION_API_KEY", raising=False)
    monkeypatch.delenv("REDACTION_ADMIN_KEY", raising=False)
    body = client.get("/api/config").json()
    assert body["auth_required"] is False
    assert body["admin_auth_required"] is False

    monkeypatch.setenv("REDACTION_API_KEY", KEY)
    body = client.get("/api/config").json()
    assert body["auth_required"] is True
    # No admin key set, so the fallback means the admin route is still guarded.
    assert body["admin_auth_required"] is True
    assert KEY not in json.dumps(body)


def test_health_does_not_report_usage(client):
    """It is open so load balancers can probe it, so it carries no live session count."""
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert "active_sessions" not in body


@pytest.mark.parametrize(
    "override,api_key,expected",
    [
        ("", None, True),        # local install: docs on, as the README says
        ("", "a-key", False),    # keyed instance: docs off, they cannot send a key
        ("1", "a-key", True),    # explicit opt-in wins
        ("0", None, False),      # explicit opt-out wins
    ],
)
def test_docs_follow_the_key_unless_overridden(monkeypatch, override, api_key, expected):
    from app import main

    monkeypatch.setenv("REDACTION_ENABLE_DOCS", override)
    if api_key:
        monkeypatch.setenv("REDACTION_API_KEY", api_key)
    else:
        monkeypatch.delenv("REDACTION_API_KEY", raising=False)

    assert main._docs_enabled() is expected
