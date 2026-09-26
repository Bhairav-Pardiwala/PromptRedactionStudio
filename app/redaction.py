"""The analyze -> anonymize -> restore pipeline, plus the short-lived redaction store.

The token -> original mapping produced by a redaction is the sensitive artifact here: it
is the key that undoes the redaction. It is held in memory only, keyed by a random session
id, and expires after a TTL. Nothing is written to disk.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from presidio_anonymizer import AnonymizerEngine, DeanonymizeEngine
from presidio_anonymizer.entities import OperatorConfig, OperatorResult

from . import engines, operators, recognizers

SESSION_TTL_SECONDS = 60 * 60  # one hour
MAX_SESSIONS = 200
MAX_TEXT_CHARS = 100_000

_anonymizer = AnonymizerEngine()
_deanonymizer = DeanonymizeEngine()


class RedactionError(ValueError):
    """A user-fixable problem with a redaction or restore request."""


@dataclass
class RedactionSession:
    """Everything needed to reverse one redaction."""

    session_id: str
    created_at: float
    placeholder_map: Dict[str, str] = field(default_factory=dict)
    encrypted_spans: List[Dict[str, str]] = field(default_factory=list)
    encrypt_keys: Dict[str, str] = field(default_factory=dict)


class SessionStore:
    """In-memory, TTL-expiring store for redaction mappings."""

    def __init__(self, ttl: int = SESSION_TTL_SECONDS, max_sessions: int = MAX_SESSIONS):
        self._ttl = ttl
        self._max = max_sessions
        self._data: Dict[str, RedactionSession] = {}
        self._lock = threading.Lock()

    def _purge(self) -> None:
        cutoff = time.time() - self._ttl
        for key in [k for k, v in self._data.items() if v.created_at < cutoff]:
            del self._data[key]
        # Bound memory even if nothing has expired yet.
        while len(self._data) > self._max:
            oldest = min(self._data.items(), key=lambda kv: kv[1].created_at)[0]
            del self._data[oldest]

    def create(self) -> RedactionSession:
        with self._lock:
            self._purge()
            session = RedactionSession(session_id=secrets.token_urlsafe(16), created_at=time.time())
            self._data[session.session_id] = session
            return session

    def get(self, session_id: str) -> RedactionSession:
        with self._lock:
            self._purge()
            session = self._data.get(session_id)
        if session is None:
            raise RedactionError(
                "That redaction session has expired or was never created. "
                "Redact the prompt again to get a fresh one."
            )
        return session

    def delete(self, session_id: str) -> bool:
        """Drop one mapping. Returns whether there was one to drop."""
        with self._lock:
            return self._data.pop(session_id, None) is not None

    def clear(self) -> int:
        with self._lock:
            count = len(self._data)
            self._data.clear()
            return count

    def __len__(self) -> int:
        return len(self._data)


store = SessionStore()


def _validate_text(text: str) -> str:
    if text is None:
        raise RedactionError("No text supplied.")
    if len(text) > MAX_TEXT_CHARS:
        raise RedactionError(
            "Text is too long ("
            + str(len(text))
            + " characters). The limit is "
            + str(MAX_TEXT_CHARS)
            + "."
        )
    return text


def _explanation_to_dict(result: Any) -> Optional[Dict[str, Any]]:
    explanation = getattr(result, "analysis_explanation", None)
    if explanation is None:
        return None
    return {
        "recognizer": getattr(explanation, "recognizer", None),
        "pattern_name": getattr(explanation, "pattern_name", None),
        "pattern": getattr(explanation, "pattern", None),
        "original_score": getattr(explanation, "original_score", None),
        "score": getattr(explanation, "score", None),
        "textual_explanation": getattr(explanation, "textual_explanation", None),
        "score_context_improvement": getattr(explanation, "score_context_improvement", None),
        "supportive_context_word": getattr(explanation, "supportive_context_word", None),
        "validation_result": getattr(explanation, "validation_result", None),
    }


def analyze(
    text: str,
    engine: str = engines.DEFAULT_ENGINE,
    language: str = "en",
    entities: Optional[List[str]] = None,
    score_threshold: Optional[float] = None,
    allow_list: Optional[List[str]] = None,
    allow_list_match: str = "exact",
    custom_recognizers: Optional[List[Dict[str, Any]]] = None,
    detect_organization: bool = False,
    return_explanations: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Any]]:
    """Run Presidio detection and return both JSON-ready findings and the raw results."""
    text = _validate_text(text)
    analyzer = engines.get_analyzer(engine, detect_organization)
    ad_hoc = recognizers.build_ad_hoc_recognizers(custom_recognizers or [], language)

    requested = list(entities) if entities else None
    if requested is not None:
        # Custom recognizers introduce entity types the engine does not know about; they
        # must be in the requested list or analyze() would filter their results away.
        for entity_type in recognizers.custom_entity_types(custom_recognizers or []):
            if entity_type not in requested:
                requested.append(entity_type)
        if not requested:
            return [], []

    results = analyzer.analyze(
        text=text,
        language=language,
        entities=requested,
        score_threshold=score_threshold,
        allow_list=[term for term in (allow_list or []) if term] or None,
        allow_list_match=allow_list_match,
        ad_hoc_recognizers=ad_hoc or None,
        return_decision_process=return_explanations,
    )

    findings = [
        {
            "entity_type": result.entity_type,
            "start": result.start,
            "end": result.end,
            "score": round(float(result.score), 4),
            "text": text[result.start : result.end],
            "recognizer": (result.recognition_metadata or {}).get("recognizer_name")
            if getattr(result, "recognition_metadata", None)
            else None,
            "explanation": _explanation_to_dict(result) if return_explanations else None,
        }
        for result in sorted(results, key=lambda r: (r.start, -r.score))
    ]
    return findings, results


def redact(
    text: str,
    default_operator: Optional[Dict[str, Any]] = None,
    per_entity_operators: Optional[Dict[str, Dict[str, Any]]] = None,
    store_session: bool = True,
    **analyze_kwargs: Any,
) -> Dict[str, Any]:
    """Analyze then anonymize, returning the redacted text and the token mapping.

    When `store_session` is false nothing is retained server-side: no session is created
    and `session_id` comes back as None. The caller still receives the full `mapping`, so
    desktop and extension clients can restore locally without the server ever holding the
    key to the redaction. The web UI passes true because its restore box relies on the
    server remembering.
    """
    findings, results = analyze(text, **analyze_kwargs)

    detected_types = []
    for finding in findings:
        if finding["entity_type"] not in detected_types:
            detected_types.append(finding["entity_type"])

    operator_map, allocator, chosen = operators.build_operators(
        default_operator, per_entity_operators, detected_types
    )

    if not results:
        session = store.create() if store_session else None
        return {
            "session_id": session.session_id if session else None,
            "original_text": text,
            "redacted_text": text,
            "findings": findings,
            "items": [],
            "mapping": {},
            "operators_applied": chosen,
            "reversible": True,
        }

    engine_result = _anonymizer.anonymize(
        text=text, analyzer_results=results, operators=operator_map
    )

    redacted_text, mapping = allocator.finalize(engine_result.text)

    # engine_result.items carry offsets into the pre-finalize text. Renumbering only ever
    # shortens tokens by one NUL character each, so recompute offsets by locating each
    # item's final text rather than trusting the originals.
    items = []
    encrypted_spans: List[Dict[str, str]] = []
    for item in sorted(engine_result.items, key=lambda i: i.start):
        item_text = item.text
        if item_text.startswith("<\x00"):
            item_text = item_text.replace("\x00", "", 1)
        items.append(
            {
                "entity_type": item.entity_type,
                "start": item.start,
                "end": item.end,
                "text": item_text,
                "operator": item.operator,
            }
        )
        if item.operator == "encrypt":
            encrypted_spans.append({"entity_type": item.entity_type, "text": item_text})

    encrypt_keys = operators.encrypt_keys_in_use(default_operator, per_entity_operators)

    session = None
    if store_session:
        session = store.create()
        session.placeholder_map = mapping
        session.encrypted_spans = encrypted_spans
        session.encrypt_keys = encrypt_keys

    reversible = bool(mapping or encrypted_spans) or not findings

    return {
        "session_id": session.session_id if session else None,
        "original_text": text,
        "redacted_text": redacted_text,
        "findings": findings,
        "items": items,
        "mapping": mapping,
        "operators_applied": chosen,
        "reversible": reversible,
    }


def restore(text: str, session_id: str) -> Dict[str, Any]:
    """Put the real values back into text that contains redaction tokens.

    Presidio's DeanonymizeEngine needs OperatorResult offsets that match the text being
    restored. Those offsets hold for the redacted prompt itself but not for an LLM's
    reply, which is different text -- and the reply is the case that actually matters.
    So each token is located in the incoming text first and the spans are rebuilt at
    those offsets before handing off to Presidio.
    """
    text = _validate_text(text)
    session = store.get(session_id)

    restored_counts: Dict[str, int] = {}
    not_found: List[str] = []

    # Placeholders are a plain lookup: find every occurrence of each token.
    spans: List[Tuple[int, int, str, str]] = []  # (start, end, entity_type, original)
    for token, original in session.placeholder_map.items():
        entity_type = token[1 : token.rindex("_")]
        occurrences = [match.start() for match in re.finditer(re.escape(token), text)]
        if not occurrences:
            not_found.append(token)
            continue
        restored_counts[token] = len(occurrences)
        for start in occurrences:
            spans.append((start, start + len(token), entity_type, original))

    # Encrypted spans go through Presidio's decrypt operator so the AES handling stays
    # in the library rather than being reimplemented here.
    decrypt_entities: List[OperatorResult] = []
    decrypt_operators: Dict[str, OperatorConfig] = {}
    for span in session.encrypted_spans:
        ciphertext = span["text"]
        entity_type = span["entity_type"]
        key = session.encrypt_keys.get(entity_type) or session.encrypt_keys.get("DEFAULT")
        if not key:
            not_found.append(ciphertext)
            continue
        occurrences = [match.start() for match in re.finditer(re.escape(ciphertext), text)]
        if not occurrences:
            not_found.append(ciphertext)
            continue
        restored_counts[ciphertext] = len(occurrences)
        for start in occurrences:
            decrypt_entities.append(
                OperatorResult(start, start + len(ciphertext), entity_type)
            )
        decrypt_operators[entity_type] = OperatorConfig("decrypt", {"key": key})

    restored_text = text
    if decrypt_entities:
        try:
            deanonymized = _deanonymizer.deanonymize(
                text=restored_text,
                entities=decrypt_entities,
                operators=decrypt_operators,
            )
            restored_text = deanonymized.text
        except Exception as exc:  # wrong key, corrupted ciphertext
            raise RedactionError("Could not decrypt: " + str(exc))

        # Decryption changed the text length, so placeholder offsets computed above are
        # stale. Recompute them against the decrypted text.
        spans = []
        for token, original in session.placeholder_map.items():
            entity_type = token[1 : token.rindex("_")]
            for match in re.finditer(re.escape(token), restored_text):
                spans.append((match.start(), match.end(), entity_type, original))

    # Apply placeholder restorations back-to-front so earlier offsets stay valid.
    for start, end, _entity_type, original in sorted(spans, reverse=True):
        restored_text = restored_text[:start] + original + restored_text[end:]

    total_tokens = len(session.placeholder_map) + len(session.encrypted_spans)
    return {
        "restored_text": restored_text,
        "restored_counts": restored_counts,
        "tokens_restored": len(restored_counts),
        "tokens_total": total_tokens,
        "not_found": not_found,
    }
