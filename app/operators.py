"""Translate the UI's operator choices into Presidio OperatorConfig objects.

Parameter names here match presidio_anonymizer 2.2.364 exactly:
  replace -> new_value | mask -> masking_char, chars_to_mask, from_end
  hash    -> hash_type | encrypt -> key | redact, keep -> no params

On top of Presidio's built-ins we add "placeholder", built on Presidio's own `custom`
operator. It emits stable numbered tokens (<PERSON_1>, <EMAIL_ADDRESS_1>) and records the
token -> original mapping so the redaction can be reversed later. Encryption is reversible
too, but its base64 ciphertext reads as noise to an LLM and tends to come back mangled;
numbered placeholders keep the prompt readable and survive a round trip.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from presidio_anonymizer.entities import OperatorConfig

PLACEHOLDER = "placeholder"
VALID_AES_KEY_LENGTHS = (16, 24, 32)
HASH_TYPES = ("sha256", "sha512")  # md5 was removed from Presidio in 2.2.x

# Advertised to the UI so the options panel can render itself from the API.
OPERATOR_SPECS: List[Dict[str, Any]] = [
    {
        "name": PLACEHOLDER,
        "label": "Numbered placeholder",
        "description": "Replace with <PERSON_1>, <PERSON_2>, ... Readable and reversible.",
        "reversible": True,
        "params": [],
    },
    {
        "name": "replace",
        "label": "Replace",
        "description": "Swap in fixed text. Defaults to the entity type in angle brackets.",
        "reversible": False,
        "params": [
            {
                "name": "new_value",
                "type": "string",
                "label": "Replacement text",
                "default": "",
                "placeholder": "<ENTITY_TYPE>",
            }
        ],
    },
    {
        "name": "redact",
        "label": "Redact",
        "description": "Delete the value entirely, leaving nothing behind.",
        "reversible": False,
        "params": [],
    },
    {
        "name": "mask",
        "label": "Mask",
        "description": "Overwrite some characters with a masking character.",
        "reversible": False,
        "params": [
            {"name": "masking_char", "type": "string", "label": "Masking char", "default": "*"},
            {"name": "chars_to_mask", "type": "int", "label": "Chars to mask", "default": 12},
            {"name": "from_end", "type": "bool", "label": "Mask from end", "default": False},
        ],
    },
    {
        "name": "hash",
        "label": "Hash",
        "description": "Replace with a cryptographic digest of the value.",
        "reversible": False,
        "params": [
            {
                "name": "hash_type",
                "type": "choice",
                "label": "Algorithm",
                "default": "sha256",
                "choices": list(HASH_TYPES),
            }
        ],
    },
    {
        "name": "encrypt",
        "label": "Encrypt (AES)",
        "description": "Reversible with the same key, but produces unreadable ciphertext.",
        "reversible": True,
        "params": [
            {
                "name": "key",
                "type": "string",
                "label": "AES key (16, 24 or 32 chars)",
                "default": "",
                "placeholder": "0123456789abcdef",
            }
        ],
    },
    {
        "name": "keep",
        "label": "Keep",
        "description": "Detect the entity but leave the text untouched.",
        "reversible": True,
        "params": [],
    },
]

OPERATOR_NAMES = [spec["name"] for spec in OPERATOR_SPECS]


class OperatorConfigError(ValueError):
    """Raised for operator settings the user can fix, e.g. a wrong-length AES key."""


class PlaceholderAllocator:
    """Hands out stable numbered tokens and remembers what each one stood for.

    The same original value always maps to the same token within one redaction, so a
    prompt that mentions "Dana" five times stays internally consistent.

    Presidio's engine rewrites spans from the end of the text backwards
    (`sorted(pii_entities, reverse=True)` in EngineBase._operate), so tokens are handed
    out in reverse reading order. finalize() renumbers them by first appearance in the
    anonymized text, so the user sees <PERSON_1> before <PERSON_2>.
    """

    def __init__(self) -> None:
        self._by_value: Dict[Tuple[str, str], str] = {}
        self._counters: Dict[str, int] = {}
        self._provisional: Dict[str, str] = {}

    def token_for(self, entity_type: str, original: str) -> str:
        key = (entity_type, original)
        existing = self._by_value.get(key)
        if existing is not None:
            return existing
        index = self._counters.get(entity_type, 0) + 1
        self._counters[entity_type] = index
        # Marked with a NUL so a provisional token can never collide with a final one
        # (or with literal angle-bracket text the user typed) during renumbering.
        token = "<\x00" + entity_type + "_" + str(index) + ">"
        self._by_value[key] = token
        self._provisional[token] = original
        return token

    def make_lambda(self, entity_type: str):
        """Return the callable Presidio's `custom` operator invokes per matched span."""

        def _replace(original_text: str) -> str:
            return self.token_for(entity_type, original_text)

        return _replace

    def finalize(self, text: str) -> Tuple[str, Dict[str, str]]:
        """Renumber provisional tokens by reading order and substitute them into the text.

        Returns the cleaned text and the token -> original mapping, containing only
        tokens that actually survived into the output (Presidio may merge or drop
        overlapping spans after a token was already allocated).
        """
        if not self._provisional:
            return text, {}

        # Order provisional tokens by where they first appear in the anonymized text.
        appearances = []
        for token in self._provisional:
            position = text.find(token)
            if position >= 0:
                appearances.append((position, token))
        appearances.sort()

        counters: Dict[str, int] = {}
        final_map: Dict[str, str] = {}
        mapping: Dict[str, str] = {}
        for _, token in appearances:
            entity_type = token[2 : token.rindex("_")]
            index = counters.get(entity_type, 0) + 1
            counters[entity_type] = index
            final_token = "<" + entity_type + "_" + str(index) + ">"
            final_map[token] = final_token
            mapping[final_token] = self._provisional[token]

        for provisional, final_token in final_map.items():
            text = text.replace(provisional, final_token)
        return text, mapping


def _coerce_int(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise OperatorConfigError(label + " must be a whole number.")


def _validate_key(key: Any) -> str:
    if not isinstance(key, str) or not key:
        raise OperatorConfigError(
            "An AES key is required for the encrypt operator (16, 24 or 32 characters)."
        )
    if len(key) not in VALID_AES_KEY_LENGTHS:
        raise OperatorConfigError(
            "AES key must be 16, 24 or 32 characters long (got "
            + str(len(key))
            + "). These correspond to AES-128, AES-192 and AES-256."
        )
    return key


def build_operator_config(
    spec: Dict[str, Any],
    entity_type: str,
    allocator: PlaceholderAllocator,
) -> OperatorConfig:
    """Convert one UI operator spec into a Presidio OperatorConfig."""
    name = (spec or {}).get("type") or PLACEHOLDER
    params = dict((spec or {}).get("params") or {})

    if name == PLACEHOLDER:
        return OperatorConfig("custom", {"lambda": allocator.make_lambda(entity_type)})

    if name == "replace":
        new_value = params.get("new_value") or ""
        if not new_value:
            # Presidio's own default: the entity type in angle brackets.
            return OperatorConfig("replace", {})
        return OperatorConfig("replace", {"new_value": str(new_value)})

    if name == "redact":
        return OperatorConfig("redact", {})

    if name == "mask":
        masking_char = str(params.get("masking_char") or "*")
        if len(masking_char) != 1:
            raise OperatorConfigError("Masking character must be exactly one character.")
        return OperatorConfig(
            "mask",
            {
                "masking_char": masking_char,
                "chars_to_mask": _coerce_int(params.get("chars_to_mask", 12), "Chars to mask"),
                "from_end": bool(params.get("from_end", False)),
            },
        )

    if name == "hash":
        hash_type = str(params.get("hash_type") or "sha256").lower()
        if hash_type not in HASH_TYPES:
            raise OperatorConfigError("Hash type must be one of: " + ", ".join(HASH_TYPES))
        return OperatorConfig("hash", {"hash_type": hash_type})

    if name == "encrypt":
        return OperatorConfig("encrypt", {"key": _validate_key(params.get("key"))})

    if name == "keep":
        return OperatorConfig("keep", {})

    raise OperatorConfigError(
        "Unknown operator '" + str(name) + "'. Valid operators: " + ", ".join(OPERATOR_NAMES)
    )


def build_operators(
    default_operator: Optional[Dict[str, Any]],
    per_entity: Optional[Dict[str, Dict[str, Any]]],
    detected_entity_types: Optional[List[str]] = None,
) -> Tuple[Dict[str, OperatorConfig], PlaceholderAllocator, Dict[str, str]]:
    """Build the operators dict AnonymizerEngine.anonymize() expects.

    `detected_entity_types` matters for the placeholder operator: a lambda registered
    under DEFAULT is not told which entity type it was invoked for, so a placeholder
    default is expanded into one explicit entry per detected type. That is what keeps
    tokens labelled <PERSON_1> / <EMAIL_ADDRESS_1> rather than a generic <PII_1>.

    Returns the config map, the allocator holding the placeholder mapping, and a record
    of which operator name applies per entity type (used to label the findings table).
    """
    allocator = PlaceholderAllocator()
    chosen: Dict[str, str] = {}
    operators: Dict[str, OperatorConfig] = {}

    default_spec = default_operator or {"type": PLACEHOLDER}
    default_name = default_spec.get("type") or PLACEHOLDER
    chosen["DEFAULT"] = default_name

    overrides = {
        entity_type: spec
        for entity_type, spec in (per_entity or {}).items()
        if spec and spec.get("type")
    }

    if default_name == PLACEHOLDER:
        for entity_type in detected_entity_types or []:
            if entity_type not in overrides:
                operators[entity_type] = build_operator_config(
                    default_spec, entity_type, allocator
                )
                chosen.setdefault(entity_type, default_name)

    for entity_type, spec in overrides.items():
        operators[entity_type] = build_operator_config(spec, entity_type, allocator)
        chosen[entity_type] = spec["type"]

    # Still register DEFAULT as a backstop for any entity type not covered above.
    operators["DEFAULT"] = build_operator_config(
        default_spec if default_name != PLACEHOLDER else {"type": "replace"},
        "DEFAULT",
        allocator,
    )
    return operators, allocator, chosen


def encrypt_keys_in_use(
    default_operator: Optional[Dict[str, Any]],
    per_entity: Optional[Dict[str, Dict[str, Any]]],
) -> Dict[str, str]:
    """Collect the AES key used per entity type, so restore knows how to decrypt each span."""
    keys: Dict[str, str] = {}
    default_spec = default_operator or {}
    if default_spec.get("type") == "encrypt":
        keys["DEFAULT"] = str((default_spec.get("params") or {}).get("key") or "")
    for entity_type, spec in (per_entity or {}).items():
        if spec and spec.get("type") == "encrypt":
            keys[entity_type] = str((spec.get("params") or {}).get("key") or "")
    return keys
