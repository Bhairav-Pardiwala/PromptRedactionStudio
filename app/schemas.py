"""Pydantic request/response models for the API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .engines import DEFAULT_ENGINE


class OperatorSpec(BaseModel):
    """One anonymization operator choice, e.g. {"type": "mask", "params": {...}}."""

    type: str = "placeholder"
    params: Dict[str, Any] = Field(default_factory=dict)


class CustomRecognizerSpec(BaseModel):
    name: str
    entity: Optional[str] = None
    kind: str = "regex"  # "regex" | "deny_list"
    pattern: Optional[str] = None
    deny_list: List[str] = Field(default_factory=list)
    score: float = 0.7
    context: List[str] = Field(default_factory=list)


class AnalyzeRequest(BaseModel):
    text: str = ""
    engine: str = DEFAULT_ENGINE
    language: str = "en"
    entities: Optional[List[str]] = None  # None means "every supported entity"
    score_threshold: Optional[float] = None
    allow_list: List[str] = Field(default_factory=list)
    allow_list_match: str = "exact"  # "exact" | "fuzzy"
    custom_recognizers: List[CustomRecognizerSpec] = Field(default_factory=list)
    detect_organization: bool = False
    return_explanations: bool = False


class RedactRequest(AnalyzeRequest):
    default_operator: OperatorSpec = Field(default_factory=OperatorSpec)
    per_entity_operators: Dict[str, OperatorSpec] = Field(default_factory=dict)


class RestoreRequest(BaseModel):
    text: str
    session_id: str


class Finding(BaseModel):
    entity_type: str
    start: int
    end: int
    score: float
    text: str
    recognizer: Optional[str] = None
    explanation: Optional[Dict[str, Any]] = None


class AnalyzeResponse(BaseModel):
    findings: List[Finding]
    count: int
    engine: str


class RedactResponse(BaseModel):
    session_id: str
    original_text: str
    redacted_text: str
    findings: List[Finding]
    items: List[Dict[str, Any]]
    mapping: Dict[str, str]
    operators_applied: Dict[str, str]
    reversible: bool
    engine: str


class RestoreResponse(BaseModel):
    restored_text: str
    restored_counts: Dict[str, int]
    tokens_restored: int
    tokens_total: int
    not_found: List[str]
