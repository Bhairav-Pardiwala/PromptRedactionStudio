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

from presidio_analyzer import BatchAnalyzerEngine, RecognizerResult
from presidio_anonymizer import AnonymizerEngine, DeanonymizeEngine
from presidio_anonymizer.entities import OperatorConfig, OperatorResult

from . import engines, operators, recognizers

SESSION_TTL_SECONDS = 60 * 60  # one hour
MAX_SESSIONS = 200
MAX_TEXT_CHARS = 100_000
# A document is redacted as one joined text so tokens stay consistent across it, which
# makes it longer than any prompt. Still bounded, because detection time grows with it.
MAX_DOCUMENT_CHARS = 500_000

# Joins a document's segments into one text for a single analyze pass. The unit
# separator cannot come from a real document (it is stripped from every segment first),
# so splitting on it afterwards gives back exactly the segments that went in.
SEGMENT_SEP = "\n\x1f\n"

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


def _validate_text(text: str, max_chars: int = MAX_TEXT_CHARS) -> str:
    if text is None:
        raise RedactionError("No text supplied.")
    if len(text) > max_chars:
        raise RedactionError(
            "Text is too long ("
            + str(len(text))
            + " characters). The limit is "
            + str(max_chars)
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
    max_chars: int = MAX_TEXT_CHARS,
) -> Tuple[List[Dict[str, Any]], List[Any]]:
    """Run Presidio detection and return both JSON-ready findings and the raw results."""
    text = _validate_text(text, max_chars)
    return _analyze_texts(
        [text],
        engine=engine,
        language=language,
        entities=entities,
        score_threshold=score_threshold,
        allow_list=allow_list,
        allow_list_match=allow_list_match,
        custom_recognizers=custom_recognizers,
        detect_organization=detect_organization,
        return_explanations=return_explanations,
    )[0]


def _analyze_texts(
    texts: List[str],
    engine: str = engines.DEFAULT_ENGINE,
    language: str = "en",
    entities: Optional[List[str]] = None,
    score_threshold: Optional[float] = None,
    allow_list: Optional[List[str]] = None,
    allow_list_match: str = "exact",
    custom_recognizers: Optional[List[Dict[str, Any]]] = None,
    detect_organization: bool = False,
    return_explanations: bool = False,
) -> List[Tuple[List[Dict[str, Any]], List[Any]]]:
    """Detect in each text independently. Several texts go through spaCy as one batch."""
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
            return [([], []) for _ in texts]

    options = dict(
        language=language,
        entities=requested,
        score_threshold=score_threshold,
        allow_list=[term for term in (allow_list or []) if term] or None,
        allow_list_match=allow_list_match,
        ad_hoc_recognizers=ad_hoc or None,
        return_decision_process=return_explanations,
    )
    if len(texts) == 1:
        batches = [analyzer.analyze(text=texts[0], **options)]
    else:
        language_option = options.pop("language")
        batches = BatchAnalyzerEngine(analyzer_engine=analyzer).analyze_iterator(
            texts, language_option, batch_size=32, **options
        )

    return [
        (_to_findings(text, results, return_explanations), results)
        for text, results in zip(texts, batches)
    ]


def _to_findings(text: str, results: List[Any], return_explanations: bool) -> List[Dict[str, Any]]:
    return [
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
    return _anonymize(
        text, findings, results, default_operator, per_entity_operators, store_session
    )


def _anonymize(
    text: str,
    findings: List[Dict[str, Any]],
    results: List[Any],
    default_operator: Optional[Dict[str, Any]],
    per_entity_operators: Optional[Dict[str, Dict[str, Any]]],
    store_session: bool,
) -> Dict[str, Any]:
    """Everything after detection: allocate tokens, rewrite, and optionally store."""
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


def redact_segments(
    segments: List[str],
    default_operator: Optional[Dict[str, Any]] = None,
    per_entity_operators: Optional[Dict[str, Dict[str, Any]]] = None,
    store_session: bool = True,
    **analyze_kwargs: Any,
) -> Dict[str, Any]:
    """Redact a document's text segments in one pass, so tokens are consistent across it.

    Redacting each paragraph or cell on its own would restart <PERSON_1> in every one of
    them. So detection runs per segment -- a model must not read one cell's name into the
    next cell's text, and it will across any separator -- while token allocation and
    rewriting run once, over all segments joined, so one person gets one token throughout.

    Findings come back with offsets into their own segment and a `segment` index.
    """
    clean = [segment.replace("\x1f", "") for segment in segments]
    joined = _validate_text(SEGMENT_SEP.join(clean), MAX_DOCUMENT_CHARS)

    # Blank segments (empty lines, spacer cells) cannot hold PII; skip the model for them.
    to_scan = [i for i, segment in enumerate(clean) if segment.strip()]
    per_segment = _analyze_texts([clean[i] for i in to_scan], **analyze_kwargs) if to_scan else []
    _propagate_detections(clean, to_scan, per_segment)

    findings: List[Dict[str, Any]] = []
    results: List[Any] = []
    offsets = []
    cursor = 0
    for segment in clean:
        offsets.append(cursor)
        cursor += len(segment) + len(SEGMENT_SEP)
    for index, (segment_findings, segment_results) in zip(to_scan, per_segment):
        for finding in segment_findings:
            findings.append(dict(finding, segment=index))
        for result in segment_results:
            # Move each span into the joined text, where anonymization happens.
            result.start += offsets[index]
            result.end += offsets[index]
            results.append(result)

    result = _anonymize(
        joined, findings, results, default_operator, per_entity_operators, store_session
    )
    redacted_segments = result.pop("redacted_text").split(SEGMENT_SEP)
    if len(redacted_segments) != len(clean):  # pragma: no cover - spans never cross a separator
        raise RedactionError("The document could not be split back into its parts.")

    del result["original_text"]
    # Offsets into the joined text mean nothing to a caller holding separate segments.
    for item in result["items"]:
        item.pop("start", None)
        item.pop("end", None)
    result["segments"] = redacted_segments
    return result


# Dates are not carried across a document: finding "May" or "today" once must not
# redact every other "May" and "today" in the file.
_NOT_PROPAGATED = {"DATE_TIME"}
PROPAGATED_RECOGNIZER = "Same value found elsewhere in the document"


def _propagate_detections(
    segments: List[str],
    scanned: List[int],
    per_segment: List[Tuple[List[Dict[str, Any]], List[Any]]],
) -> None:
    """Redact every other copy of a value the model found anywhere in the document.

    Detection is per segment, and a model that finds "Marcus Testwell" in a ticket's
    message can miss the same name alone in the customer_name column. Leaving that copy
    would leak the exact person the rest of the file hides. So each detected value is
    looked for, whole-word and case-sensitive, in every segment, and any copy not already
    covered is added with the same entity type. A copy that only partly overlaps an
    existing detection -- "Priya" found inside "Priya Placeholder" -- is still added, and
    the anonymizer keeps the wider span.

    Mutates `per_segment` in place: new findings and results are appended.
    """
    seen: Dict[Tuple[str, str], float] = {}
    for segment_findings, _ in per_segment:
        for finding in segment_findings:
            value = finding["text"]
            if finding["entity_type"] in _NOT_PROPAGATED or len(value.strip()) < 3:
                continue
            key = (value, finding["entity_type"])
            seen[key] = max(seen.get(key, 0.0), finding["score"])
    if not seen:
        return

    # Longest first, so "Jane Doe" claims its span before "Jane" is considered.
    for (value, entity_type), score in sorted(seen.items(), key=lambda kv: -len(kv[0][0])):
        pattern = re.compile(r"(?<!\w)" + re.escape(value) + r"(?!\w)")
        for index, (segment_findings, segment_results) in zip(scanned, per_segment):
            for match in pattern.finditer(segments[index]):
                start, end = match.span()
                covered = any(r.start <= start and end <= r.end for r in segment_results)
                if covered:
                    continue
                segment_results.append(RecognizerResult(entity_type, start, end, score))
                segment_findings.append(
                    {
                        "entity_type": entity_type,
                        "start": start,
                        "end": end,
                        "score": score,
                        "text": value,
                        "recognizer": PROPAGATED_RECOGNIZER,
                        "explanation": None,
                    }
                )


def restore_segments(segments: List[str], session_id: str) -> Dict[str, Any]:
    """Restore every segment of a document against one session, counting once."""
    clean = [segment.replace("\x1f", "") for segment in segments]
    result = restore(SEGMENT_SEP.join(clean), session_id, max_chars=MAX_DOCUMENT_CHARS)
    restored = result.pop("restored_text").split(SEGMENT_SEP)
    if len(restored) != len(clean):  # pragma: no cover - originals never contain \x1f
        raise RedactionError("The document could not be split back into its parts.")
    result["segments"] = restored
    return result


def restore(text: str, session_id: str, max_chars: int = MAX_TEXT_CHARS) -> Dict[str, Any]:
    """Put the real values back into text that contains redaction tokens.

    Presidio's DeanonymizeEngine needs OperatorResult offsets that match the text being
    restored. Those offsets hold for the redacted prompt itself but not for an LLM's
    reply, which is different text -- and the reply is the case that actually matters.
    So each token is located in the incoming text first and the spans are rebuilt at
    those offsets before handing off to Presidio.
    """
    text = _validate_text(text, max_chars)
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
