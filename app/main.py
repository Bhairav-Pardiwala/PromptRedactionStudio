"""Prompt Redaction Studio -- a FastAPI app exposing Microsoft Presidio's options.

Serves both the JSON API and the static single-page frontend from one process.
"""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from presidio_anonymizer.entities import InvalidParamError

from . import engines, operators, policy, recognizers, redaction, schemas

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("prompt_redaction")

STATIC_DIR = Path(__file__).parent / "static"

# Two independent, optional keys. Neither set -- the default, and the only thing a
# single-user install ever sees -- means every route is open and nothing below fires.
API_KEY_ENV = "REDACTION_API_KEY"
API_KEY_HEADER = "X-Redaction-Key"
# A separate key for the one destructive, instance-wide operation. Falls back to the
# ordinary key when unset, so a single-key deployment still has it covered.
ADMIN_KEY_ENV = "REDACTION_ADMIN_KEY"
ADMIN_KEY_HEADER = "X-Redaction-Admin-Key"
DOCS_ENV = "REDACTION_ENABLE_DOCS"


def _docs_enabled() -> bool:
    """Whether the interactive API docs are served.

    An explicit REDACTION_ENABLE_DOCS wins either way. Otherwise the docs follow the
    key: on for a local instance with no authentication, because the README links to
    them, and off once a key is set, because /docs has no way to send one and a shared
    instance should not advertise its whole surface to anonymous callers.
    """
    override = os.environ.get(DOCS_ENV, "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False
    return not os.environ.get(API_KEY_ENV)


_DOCS = _docs_enabled()

app = FastAPI(
    title="Prompt Redaction Studio",
    description="Redact PII from LLM prompts with Microsoft Presidio, then restore it.",
    version="1.0.0",
    docs_url="/docs" if _DOCS else None,
    redoc_url="/redoc" if _DOCS else None,
    openapi_url="/openapi.json" if _DOCS else None,
)

# Entity groupings, so the options panel can present ~100 entity types usefully rather
# than as one flat wall of checkboxes.
ENTITY_GROUPS: List[Dict[str, Any]] = [
    {
        "name": "Common",
        "description": "The everyday identifiers most prompts leak.",
        "entities": [
            "PERSON",
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
            "LOCATION",
            "ORGANIZATION",
            "DATE_TIME",
            "CREDIT_CARD",
            "IBAN_CODE",
            "IP_ADDRESS",
            "URL",
            "NRP",
            "AGE",
            "ID",
            "MEDICAL_LICENSE",
            "CRYPTO",
            "MAC_ADDRESS",
        ],
    },
    {
        "name": "United States",
        "description": "US-specific national and financial identifiers.",
        "entities": [],
        "prefixes": ["US_", "ABA_"],
    },
    {
        "name": "United Kingdom",
        "description": "UK national, driving and postal identifiers.",
        "entities": [],
        "prefixes": ["UK_"],
    },
    {
        "name": "India",
        "description": "Aadhaar, PAN, GSTIN and related identifiers.",
        "entities": [],
        "prefixes": ["IN_"],
    },
    {
        "name": "Europe",
        "description": "Country identifiers across the EU and EEA.",
        "entities": [],
        "prefixes": ["DE_", "ES_", "IT_", "PL_", "FI_", "SE_"],
    },
    {
        "name": "Rest of world",
        "description": "Australia, Canada, Singapore, Korea and more.",
        "entities": [],
        "prefixes": ["AU_", "CA_", "SG_", "KR_", "NG_", "PH_", "TH_", "TR_", "ZA_"],
    },
]

# Ticked on first load. This is the set Presidio's own default registry loads -- the
# locale-agnostic recognizers plus US/UK. Everything else (78 types in total, including
# every country pack) is one click away in the entity panel. Enabling all of them by
# default would bury real findings under national-ID false positives.
DEFAULT_ENTITIES = [
    "CREDIT_CARD",
    "CRYPTO",
    "DATE_TIME",
    "EMAIL_ADDRESS",
    "IBAN_CODE",
    "IP_ADDRESS",
    "LOCATION",
    "MAC_ADDRESS",
    "MEDICAL_LICENSE",
    "NRP",
    "ORGANIZATION",
    "PERSON",
    "PHONE_NUMBER",
    "UK_NHS",
    "URL",
    "US_BANK_NUMBER",
    "US_DRIVER_LICENSE",
    "US_ITIN",
    "US_PASSPORT",
    "US_SSN",
]

# Deliberately unmistakable placeholders: Jane Doe / John Roe, the IANA-reserved
# example.com domain, the standard 4111... Visa test card, and a private-range IP.
# Nothing here can be confused for a real person's data.
SAMPLE_PROMPT = (
    "Hi, I need help drafting a reply to a customer complaint.\n\n"
    "The customer is Jane Doe, reachable at jane.doe@example.com "
    "or on +1 (415) 555-0182. They live in Portland, Oregon and have been a member "
    "since March 3rd, 2019. Their account reference is ACME-99120 and the card they "
    "used was 4111 1111 1111 1111.\n\n"
    "They say a payment of $340 was taken twice on 12/04/2024. Our support agent John "
    "Roe already looked into it and confirmed the duplicate charge came from our "
    "billing service at 10.42.18.7. Please draft a polite apology explaining the refund "
    "will take 5 working days."
)


def _check(expected: Optional[str], supplied: Optional[str], header: str) -> None:
    """Compare one key, but only when one is configured.

    No key configured means this is a no-op, which is what keeps a single-user install
    on a laptop behaving exactly as it always has.
    """
    if not expected:
        return
    # Constant-time compare so the endpoint cannot be used as a timing oracle.
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid " + header + " header.",
        )


def require_api_key(x_redaction_key: Optional[str] = Header(default=None)) -> None:
    """The everyday key: detection, redaction, restore, policy, dropping own session."""
    _check(os.environ.get(API_KEY_ENV), x_redaction_key, API_KEY_HEADER)


def require_admin_key(
    x_redaction_admin_key: Optional[str] = Header(default=None),
    x_redaction_key: Optional[str] = Header(default=None),
) -> None:
    """The admin key, for operations that affect the whole instance.

    Admin key if one is set, otherwise the ordinary key, otherwise open. That ordering
    means separating the two is opt-in, while an instance that sets only the ordinary
    key still does not leave a destructive route unauthenticated.
    """
    admin_expected = os.environ.get(ADMIN_KEY_ENV)
    if admin_expected:
        _check(admin_expected, x_redaction_admin_key, ADMIN_KEY_HEADER)
        return
    _check(os.environ.get(API_KEY_ENV), x_redaction_key, API_KEY_HEADER)


def _http_error(exc: Exception, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail=str(exc))


@app.get("/api/health")
def health() -> Dict[str, Any]:
    """Liveness. Deliberately open, so a load balancer can probe it without a key.

    It reports no usage: `active_sessions` used to live here, and a live count of how
    many redactions an instance is holding is not something an anonymous caller needs.
    """
    return {
        "status": "ok",
        "loaded_engines": engines.loaded_engines(),
    }


@app.get("/api/config")
def get_config() -> Dict[str, Any]:
    """Everything the frontend needs to render its options panel."""
    availability = engines.availability()

    supported: List[str] = []
    error: str | None = None
    # Entity lists come from a real engine's registry. Prefer an available one so the UI
    # is populated even when the default engine's model was never downloaded.
    candidates = [engines.DEFAULT_ENGINE] + [
        key for key, info in availability.items() if info["available"]
    ]
    for key in candidates:
        if not availability.get(key, {}).get("available"):
            continue
        try:
            supported = sorted(set(engines.get_analyzer(key).get_supported_entities("en")))
            break
        except Exception as exc:  # pragma: no cover - defensive
            # This field goes to the browser, and the exception text carries model paths
            # and internals. The detail belongs in the log, not the response body.
            error = "Could not load engine " + key + " for the entity list. See server logs."
            logger.warning("Could not load engine %s for entity list: %s", key, exc)

    grouped: List[Dict[str, Any]] = []
    claimed = set()
    for group in ENTITY_GROUPS:
        members = [entity for entity in group["entities"] if entity in supported]
        for prefix in group.get("prefixes", []):
            members.extend(
                entity
                for entity in supported
                if entity.startswith(prefix) and entity not in members
            )
        members = [entity for entity in members if entity not in claimed]
        claimed.update(members)
        if members:
            grouped.append(
                {
                    "name": group["name"],
                    "description": group["description"],
                    "entities": sorted(members),
                }
            )

    leftovers = sorted(entity for entity in supported if entity not in claimed)
    if leftovers:
        grouped.append(
            {
                "name": "Other",
                "description": "Everything else this engine can recognise.",
                "entities": leftovers,
            }
        )

    return {
        "engines": availability,
        "default_engine": engines.DEFAULT_ENGINE,
        "languages": ["en"],
        "entity_groups": grouped,
        "all_entities": supported,
        "default_entities": [e for e in DEFAULT_ENTITIES if e in supported],
        "operators": operators.OPERATOR_SPECS,
        "default_operator": operators.PLACEHOLDER,
        "sample_prompt": SAMPLE_PROMPT,
        "error": error,
        # Whether a key is needed, never the key itself. The frontend renders its unlock
        # field from this, which is why this one route stays open: the UI has to be able
        # to boot before it can discover that it needs a key. Nothing here is sensitive --
        # the entity and operator inventory is identical in every install.
        "auth_required": bool(os.environ.get(API_KEY_ENV)),
        "admin_auth_required": bool(
            os.environ.get(ADMIN_KEY_ENV) or os.environ.get(API_KEY_ENV)
        ),
    }


@app.post("/api/analyze", response_model=schemas.AnalyzeResponse, dependencies=[Depends(require_api_key)])
def post_analyze(request: schemas.AnalyzeRequest) -> Dict[str, Any]:
    try:
        findings, _ = redaction.analyze(
            text=request.text,
            engine=request.engine,
            language=request.language,
            entities=request.entities,
            score_threshold=request.score_threshold,
            allow_list=request.allow_list,
            allow_list_match=request.allow_list_match,
            custom_recognizers=[r.model_dump() for r in request.custom_recognizers],
            detect_organization=request.detect_organization,
            return_explanations=request.return_explanations,
        )
    except engines.EngineUnavailable as exc:
        raise _http_error(exc, 503)
    except (
        redaction.RedactionError,
        recognizers.RecognizerConfigError,
        InvalidParamError,
    ) as exc:
        raise _http_error(exc)

    return {"findings": findings, "count": len(findings), "engine": request.engine}


@app.post("/api/redact", response_model=schemas.RedactResponse, dependencies=[Depends(require_api_key)])
def post_redact(request: schemas.RedactRequest) -> Dict[str, Any]:
    try:
        result = redaction.redact(
            text=request.text,
            default_operator=request.default_operator.model_dump(),
            per_entity_operators={
                entity: spec.model_dump() for entity, spec in request.per_entity_operators.items()
            },
            engine=request.engine,
            language=request.language,
            entities=request.entities,
            score_threshold=request.score_threshold,
            allow_list=request.allow_list,
            allow_list_match=request.allow_list_match,
            custom_recognizers=[r.model_dump() for r in request.custom_recognizers],
            detect_organization=request.detect_organization,
            return_explanations=request.return_explanations,
            store_session=request.store_session,
        )
    except engines.EngineUnavailable as exc:
        raise _http_error(exc, 503)
    except (
        redaction.RedactionError,
        recognizers.RecognizerConfigError,
        operators.OperatorConfigError,
        InvalidParamError,
    ) as exc:
        raise _http_error(exc)

    result["engine"] = request.engine
    return result


@app.post("/api/restore", response_model=schemas.RestoreResponse, dependencies=[Depends(require_api_key)])
def post_restore(request: schemas.RestoreRequest) -> Dict[str, Any]:
    try:
        return redaction.restore(text=request.text, session_id=request.session_id)
    except redaction.RedactionError as exc:
        raise _http_error(exc)


@app.post("/api/sessions/clear", dependencies=[Depends(require_admin_key)])
def clear_sessions() -> Dict[str, int]:
    """Drop every stored token mapping, for every client. An operator's tool.

    On a shared instance this discards other people's in-flight restores, which is why
    it takes the admin key. A client wanting to drop its own redaction wants the route
    below instead.
    """
    return {"cleared": redaction.store.clear()}


@app.delete("/api/sessions/{session_id}", dependencies=[Depends(require_api_key)])
def delete_session(session_id: str) -> Dict[str, int]:
    """Drop one mapping. Unknown or already-expired ids report zero rather than 404 --
    the caller wanted the mapping gone, and it is."""
    return {"cleared": 1 if redaction.store.delete(session_id) else 0}


@app.get("/api/policy", dependencies=[Depends(require_api_key)])
def get_policy() -> Dict[str, Any]:
    """The redaction settings this instance wants clients to use.

    Desktop and extension clients call this at startup instead of shipping their own
    defaults, so a company can set redaction behaviour centrally by pointing
    REDACTION_POLICY_FILE at a YAML file.
    """
    try:
        return policy.load_policy(
            default_entities=DEFAULT_ENTITIES,
            default_engine=engines.DEFAULT_ENGINE,
            default_operator=operators.PLACEHOLDER,
        )
    except policy.PolicyError as exc:
        # A misconfigured policy file is an operator error, not a client error.
        raise _http_error(exc, 500)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
