"""Lazy, cached registry of Presidio AnalyzerEngine instances.

Each NLP engine (spaCy small/large, transformers) is expensive to build, so engines are
constructed on first use and cached. Availability is reported without building anything, so
the UI can grey out an engine whose model was never installed instead of failing mid-request.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# spaCy's shipped Presidio config drops ORG because it produces many false positives.
# We keep that default but let the UI turn it back on -- see build_config().
DEFAULT_SPACY_LABELS_TO_IGNORE = [
    "ORG",
    "ORGANIZATION",
    "CARDINAL",
    "EVENT",
    "LANGUAGE",
    "LAW",
    "MONEY",
    "ORDINAL",
    "PERCENT",
    "PRODUCT",
    "QUANTITY",
    "WORK_OF_ART",
]

SPACY_ENTITY_MAPPING = {
    "PER": "PERSON",
    "PERSON": "PERSON",
    "NORP": "NRP",
    "FAC": "LOCATION",
    "LOC": "LOCATION",
    "LOCATION": "LOCATION",
    "GPE": "LOCATION",
    "ORG": "ORGANIZATION",
    "ORGANIZATION": "ORGANIZATION",
    "DATE": "DATE_TIME",
    "TIME": "DATE_TIME",
}

# Mirrors presidio_analyzer/conf/transformers.yaml as shipped in 2.2.364.
TRANSFORMERS_ENTITY_MAPPING = {
    "PER": "PERSON",
    "PERSON": "PERSON",
    "LOC": "LOCATION",
    "LOCATION": "LOCATION",
    "GPE": "LOCATION",
    "ORG": "ORGANIZATION",
    "ORGANIZATION": "ORGANIZATION",
    "NORP": "NRP",
    "AGE": "AGE",
    "ID": "ID",
    "EMAIL": "EMAIL",
    "PATIENT": "PERSON",
    "STAFF": "PERSON",
    "HOSP": "ORGANIZATION",
    "PATORG": "ORGANIZATION",
    "DATE": "DATE_TIME",
    "TIME": "DATE_TIME",
    "PHONE": "PHONE_NUMBER",
    "HCW": "PERSON",
    "HOSPITAL": "LOCATION",
    "FACILITY": "LOCATION",
    "VENDOR": "ORGANIZATION",
}


@dataclass(frozen=True)
class EngineSpec:
    """Static description of an NLP engine: how to build it and how to advertise it."""

    key: str
    label: str
    description: str
    kind: str  # "spacy" | "transformers"
    spacy_model: str
    transformers_model: Optional[str] = None
    languages: List[str] = field(default_factory=lambda: ["en"])
    install_hint: str = ""


ENGINE_SPECS: Dict[str, EngineSpec] = {
    "spacy_sm": EngineSpec(
        key="spacy_sm",
        label="spaCy small",
        description="en_core_web_sm. Fast and light, but misses more names and places.",
        kind="spacy",
        spacy_model="en_core_web_sm",
        install_hint="python -m spacy download en_core_web_sm",
    ),
    "spacy_lg": EngineSpec(
        key="spacy_lg",
        label="spaCy large",
        description="en_core_web_lg. Presidio's documented default and best all-round accuracy.",
        kind="spacy",
        spacy_model="en_core_web_lg",
        install_hint="python -m spacy download en_core_web_lg",
    ),
    "transformers": EngineSpec(
        key="transformers",
        label="Transformers",
        description="stanford-deidentifier-base. Highest recall on personal data, slowest.",
        kind="transformers",
        spacy_model="en_core_web_sm",
        transformers_model="StanfordAIMI/stanford-deidentifier-base",
        install_hint=".\\setup.ps1 -WithTransformers",
    ),
}

DEFAULT_ENGINE = "spacy_lg"


class EngineUnavailable(RuntimeError):
    """Raised when an engine's model or dependency is not installed."""


def _spacy_model_installed(model: str) -> bool:
    try:
        import spacy.util

        return spacy.util.is_package(model)
    except Exception:  # pragma: no cover - spacy is always installed in practice
        return False


def availability() -> Dict[str, Dict[str, Any]]:
    """Report which engines can actually be built, without building any of them."""
    report: Dict[str, Dict[str, Any]] = {}
    for key, spec in ENGINE_SPECS.items():
        missing: List[str] = []
        if not _spacy_model_installed(spec.spacy_model):
            missing.append("spaCy model " + spec.spacy_model)
        if spec.kind == "transformers":
            if importlib.util.find_spec("torch") is None:
                missing.append("torch")
            if importlib.util.find_spec("transformers") is None:
                missing.append("transformers")
        report[key] = {
            "key": key,
            "label": spec.label,
            "description": spec.description,
            "languages": spec.languages,
            "available": not missing,
            "missing": missing,
            "install_hint": spec.install_hint,
            "loaded": _cache_key_loaded(key),
        }
    return report


def build_config(spec: EngineSpec, detect_organization: bool) -> Dict[str, Any]:
    """Build the nlp_configuration dict that NlpEngineProvider consumes."""
    labels_to_ignore = [
        label
        for label in DEFAULT_SPACY_LABELS_TO_IGNORE
        if not (detect_organization and label in ("ORG", "ORGANIZATION"))
    ]

    if spec.kind == "transformers":
        return {
            "nlp_engine_name": "transformers",
            "models": [
                {
                    "lang_code": "en",
                    "model_name": {
                        "spacy": spec.spacy_model,
                        "transformers": spec.transformers_model,
                    },
                }
            ],
            "ner_model_configuration": {
                "labels_to_ignore": ["O"],
                "aggregation_strategy": "max",
                "stride": 16,
                "alignment_mode": "expand",
                "model_to_presidio_entity_mapping": TRANSFORMERS_ENTITY_MAPPING,
                "low_confidence_score_multiplier": 0.4,
                "low_score_entity_names": ["ID"],
            },
        }

    return {
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "en", "model_name": spec.spacy_model}],
        "ner_model_configuration": {
            "model_to_presidio_entity_mapping": SPACY_ENTITY_MAPPING,
            "low_confidence_score_multiplier": 0.4,
            "low_score_entity_names": [],
            "labels_to_ignore": labels_to_ignore,
        },
    }


# Engines are keyed by (engine, detect_organization): the ORG toggle changes the NER
# config, so it needs its own engine instance rather than a per-request tweak.
_engines: Dict[str, Any] = {}
_lock = threading.Lock()


def build_registry(nlp_engine: Any, languages: List[str]):
    """Build a registry holding every predefined recognizer Presidio ships.

    AnalyzerEngine's default registry loads only ~17 locale-agnostic and US/UK
    recognizers. The country-specific ones (IN_PAN, DE_TAX_ID, KR_RRN, ...) ship as
    classes but are not in that default list, so they are registered here explicitly --
    taking the entity coverage from 19 types to 80+.

    Only PatternRecognizer subclasses are added. That deliberately excludes the
    third-party recognizers (Azure AI Language, GLiNER, LangExtract, HuggingFace NER),
    which need credentials or extra downloads and would fail to construct.
    """
    from presidio_analyzer import PatternRecognizer, RecognizerRegistry
    from presidio_analyzer import predefined_recognizers

    registry = RecognizerRegistry(supported_languages=languages)
    registry.load_predefined_recognizers(languages=languages, nlp_engine=nlp_engine)

    already_loaded = {type(recognizer).__name__ for recognizer in registry.recognizers}

    for attribute in dir(predefined_recognizers):
        if not attribute.endswith("Recognizer") or attribute in already_loaded:
            continue
        candidate = getattr(predefined_recognizers, attribute)
        if not isinstance(candidate, type) or not issubclass(candidate, PatternRecognizer):
            continue
        for language in languages:
            try:
                registry.add_recognizer(candidate(supported_language=language))
            except Exception as exc:  # a recognizer that needs arguments we cannot supply
                logger.debug("Skipping recognizer %s: %s", attribute, exc)

    return registry


def _cache_key(engine: str, detect_organization: bool) -> str:
    return engine + ":org=" + str(int(detect_organization))


def _cache_key_loaded(engine: str) -> bool:
    return any(key.startswith(engine + ":") for key in _engines)


def get_analyzer(engine: str = DEFAULT_ENGINE, detect_organization: bool = False):
    """Return a cached AnalyzerEngine, building it on first use."""
    spec = ENGINE_SPECS.get(engine)
    if spec is None:
        raise EngineUnavailable("Unknown engine: " + str(engine))

    status = availability()[engine]
    if not status["available"]:
        raise EngineUnavailable(
            spec.label
            + " is not installed (missing: "
            + ", ".join(status["missing"])
            + "). Install it with: "
            + spec.install_hint
        )

    key = _cache_key(engine, detect_organization)
    cached = _engines.get(key)
    if cached is not None:
        return cached

    with _lock:
        cached = _engines.get(key)
        if cached is not None:  # another thread won the race
            return cached
        from presidio_analyzer import AnalyzerEngine
        from presidio_analyzer.nlp_engine import NlpEngineProvider

        logger.info("Building analyzer engine %s", key)
        provider = NlpEngineProvider(nlp_configuration=build_config(spec, detect_organization))
        nlp_engine = provider.create_engine()
        analyzer = AnalyzerEngine(
            nlp_engine=nlp_engine,
            registry=build_registry(nlp_engine, spec.languages),
            supported_languages=spec.languages,
        )
        _engines[key] = analyzer
        return analyzer


def loaded_engines() -> List[str]:
    return sorted(_engines.keys())
