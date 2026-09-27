"""The word list offered after a document redaction: what is left, minus grammar."""

import pytest

from app.suggestions import CLOSED_CLASS_WORDS, suggest_terms


def terms(segments, **kwargs):
    return [s["term"] for s in suggest_terms(segments, **kwargs)[0]]


@pytest.mark.parametrize(
    "word",
    ["the", "an", "is", "were", "has", "would", "must", "of", "between", "into", "and",
     "because", "whether", "they", "herself", "whose", "everyone", "don't", "it’s"],
)
def test_every_closed_class_is_filtered(word):
    assert word.replace("’", "'").lower() in CLOSED_CLASS_WORDS
    assert terms([word + " Marcus"]) == ["Marcus"]


def test_placeholder_tokens_and_their_contents_never_appear():
    found = terms(["Call <PERSON_1> about <CREDIT_CARD_12> today"])
    assert found == ["Call", "today"]


def test_ids_stay_whole_and_possessives_are_trimmed():
    found = terms(["Ticket ACME-99120 is Elena's, O'Neil’s too"])
    assert found[:4] == ["ACME-99120", "Elena", "O'Neil", "Ticket"]


def test_duplicates_merge_case_insensitively_with_counts():
    result, total = suggest_terms(["Sample sample SAMPLE", "sample"])
    assert result == [{"term": "Sample", "count": 4}]
    assert total == 1


def test_likely_names_and_ids_come_before_ordinary_words():
    found = terms(["refund refund refund declined Elena 4417 Zed"])
    assert found == ["4417", "Elena", "Zed", "refund", "declined"]


def test_limit_caps_the_list_but_total_counts_everything():
    result, total = suggest_terms(["alpha bravo charlie delta echo"], limit=2)
    assert len(result) == 2 and total == 5


def test_single_characters_and_punctuation_are_skipped():
    assert terms(["x - _ , 7 ... ok"]) == ["ok"]
