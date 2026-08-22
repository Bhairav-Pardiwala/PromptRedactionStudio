"""Central redaction policy, for deployments where IT decides what gets redacted.

A single self-hosted instance may serve a whole company. In that setting the redaction
settings are an organisational decision, not a per-employee preference, so clients
(the tray app, a future extension) fetch the policy from the server rather than shipping
their own defaults.

Set REDACTION_POLICY_FILE to a YAML file to override any field. Anything absent falls back
to the same defaults the web UI uses, so an instance with no policy file behaves exactly
as it does today.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict

import yaml

logger = logging.getLogger(__name__)

POLICY_FILE_ENV = "REDACTION_POLICY_FILE"

# Recognised keys. Anything else in the YAML is ignored rather than silently passed
# through to the analyzer, so a typo cannot quietly change redaction behaviour.
POLICY_FIELDS = (
    "engine",
    "language",
    "entities",
    "score_threshold",
    "detect_organization",
    "default_operator",
    "per_entity_operators",
    "allow_list",
    "allow_list_match",
    "custom_recognizers",
    "locked",
)


class PolicyError(ValueError):
    """A policy file that exists but cannot be used."""


def _defaults(default_entities: list, default_engine: str, default_operator: str) -> Dict[str, Any]:
    return {
        "engine": default_engine,
        "language": "en",
        "entities": list(default_entities),
        "score_threshold": 0.35,
        "detect_organization": False,
        "default_operator": {"type": default_operator, "params": {}},
        "per_entity_operators": {},
        "allow_list": [],
        "allow_list_match": "exact",
        "custom_recognizers": [],
        # When true, clients present the policy read-only instead of letting the user
        # change it. The server does not enforce this -- it is a deployment convention,
        # and a determined user could always call the API directly.
        "locked": False,
    }


def load_policy(
    default_entities: list, default_engine: str, default_operator: str
) -> Dict[str, Any]:
    """Return the effective policy: defaults, overlaid with the policy file if present."""
    policy = _defaults(default_entities, default_engine, default_operator)
    path_value = os.environ.get(POLICY_FILE_ENV)
    if not path_value:
        policy["source"] = "defaults"
        return policy

    path = Path(path_value)
    if not path.is_file():
        raise PolicyError(
            POLICY_FILE_ENV + " points at " + str(path) + ", which does not exist."
        )

    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PolicyError("Could not parse " + str(path) + ": " + str(exc))

    if not isinstance(loaded, dict):
        raise PolicyError(str(path) + " must contain a YAML mapping at the top level.")

    unknown = [key for key in loaded if key not in POLICY_FIELDS]
    if unknown:
        logger.warning("Ignoring unknown policy keys in %s: %s", path, ", ".join(sorted(unknown)))

    for key in POLICY_FIELDS:
        if key in loaded and loaded[key] is not None:
            policy[key] = loaded[key]

    policy["source"] = str(path)
    return policy
