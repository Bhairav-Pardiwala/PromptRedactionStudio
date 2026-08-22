"""Build ad-hoc recognizers from user-supplied regex patterns and deny-lists.

These are passed to AnalyzerEngine.analyze(ad_hoc_recognizers=...), which applies them for
a single request without mutating the shared engine's registry -- important because the
analyzer instances in engines.py are cached and shared across requests.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from presidio_analyzer import Pattern, PatternRecognizer


class RecognizerConfigError(ValueError):
    """Raised for a custom recognizer definition the user can fix."""


def _slugify_entity(name: str) -> str:
    """Turn a human label into a Presidio-style entity type: 'Project code' -> PROJECT_CODE."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper()
    return slug or "CUSTOM"


def build_ad_hoc_recognizers(
    definitions: List[Dict[str, Any]], language: str = "en"
) -> List[PatternRecognizer]:
    """Convert UI recognizer definitions into PatternRecognizer instances.

    Each definition: {name, entity?, kind: "regex"|"deny_list", pattern?, deny_list?,
    score?, context?}
    """
    recognizers: List[PatternRecognizer] = []

    for index, definition in enumerate(definitions or []):
        if not definition:
            continue
        name = str(definition.get("name") or "").strip()
        if not name:
            raise RecognizerConfigError(
                "Custom recognizer #" + str(index + 1) + " needs a name."
            )

        entity = str(definition.get("entity") or "").strip() or _slugify_entity(name)
        entity = _slugify_entity(entity)
        kind = str(definition.get("kind") or "regex").lower()

        try:
            score = float(definition.get("score", 0.7))
        except (TypeError, ValueError):
            raise RecognizerConfigError(name + ": score must be a number between 0 and 1.")
        if not 0.0 < score <= 1.0:
            raise RecognizerConfigError(name + ": score must be greater than 0 and at most 1.")

        context = [word for word in (definition.get("context") or []) if word]

        if kind == "deny_list":
            deny_list = [term for term in (definition.get("deny_list") or []) if term]
            if not deny_list:
                raise RecognizerConfigError(name + ": add at least one term to the deny-list.")
            recognizers.append(
                PatternRecognizer(
                    supported_entity=entity,
                    name=name,
                    supported_language=language,
                    deny_list=deny_list,
                    deny_list_score=score,
                    context=context or None,
                )
            )
            continue

        if kind != "regex":
            raise RecognizerConfigError(
                name + ": kind must be 'regex' or 'deny_list' (got '" + kind + "')."
            )

        raw_pattern = definition.get("pattern") or ""
        if not raw_pattern:
            raise RecognizerConfigError(name + ": a regex pattern is required.")
        try:
            re.compile(raw_pattern)
        except re.error as exc:
            raise RecognizerConfigError(name + ": invalid regex -- " + str(exc))

        recognizers.append(
            PatternRecognizer(
                supported_entity=entity,
                name=name,
                supported_language=language,
                patterns=[Pattern(name=name, regex=raw_pattern, score=score)],
                context=context or None,
            )
        )

    return recognizers


def custom_entity_types(definitions: List[Dict[str, Any]]) -> List[str]:
    """The entity types the given custom recognizers will produce."""
    types: List[str] = []
    for definition in definitions or []:
        if not definition or not definition.get("name"):
            continue
        entity = str(definition.get("entity") or definition.get("name"))
        slug = _slugify_entity(entity)
        if slug not in types:
            types.append(slug)
    return types
