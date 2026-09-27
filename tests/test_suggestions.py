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
    assert [(s["term"], s["count"]) for s in result] == [("Sample", 4)]
    assert total == 1


def test_likely_names_and_ids_come_before_ordinary_words():
    found = terms(["refund refund refund declined Elena 4417 Zed"])
    assert found == ["4417", "Elena", "Zed", "refund", "declined"]


def test_limit_caps_the_list_but_total_counts_everything():
    result, total = suggest_terms(["alpha bravo charlie delta echo"], limit=2)
    assert len(result) == 2 and total == 5


def contexts_of(segments, term):
    result, _ = suggest_terms(segments)
    return next(s["contexts"] for s in result if s["term"] == term)


def test_each_word_carries_the_text_either_side_of_it():
    [ctx] = contexts_of(["TKT-1002,Elena Sample,<EMAIL_ADDRESS_2>,refund"], "Sample")
    # Tokens stay as tokens: the preview never shows a redacted value.
    assert ctx == {"before": "TKT-1002,Elena ", "match": "Sample", "after": ",<EMAIL_ADDRESS_2>,refund"}


def test_long_context_is_cut_at_word_boundaries_with_ellipses():
    text = "alpha bravo charlie delta echo foxtrot golf Zephyr hotel india juliet kilo lima mike november"
    [ctx] = contexts_of([text], "Zephyr")
    assert ctx["before"].startswith("…") and not ctx["before"].startswith("… ")
    assert ctx["after"].endswith("…")
    assert ctx["before"].endswith("golf ") and ctx["after"].startswith(" hotel")
    # No word is left cut in half at either edge.
    assert all(w in text.split() for w in ctx["before"].strip("… ").split())
    assert all(w in text.split() for w in ctx["after"].strip("… ").split())


def test_csv_context_keeps_the_fields_after_the_word():
    row = "TKT-1002,Elena Sample,<EMAIL_ADDRESS_2>,<PHONE_NUMBER_2>,Customer Elena Sample reports"
    ctx = contexts_of([row], "Elena")[0]
    assert ctx["before"] == "TKT-1002,"
    assert ctx["after"].startswith(" Sample,<EMAIL_ADDRESS_2>,")
    assert ctx["after"].endswith("…")
    # Tokens are never cut in half at the edge of the window, for any word in the row.
    for suggestion in suggest_terms([row])[0]:
        for context in suggestion["contexts"]:
            for piece in (context["before"], context["after"]):
                assert piece.count("<") == piece.count(">"), piece


def test_contexts_are_capped_and_one_line():
    segments = ["Marcus\nsaid hi"] * 5
    ctxs = contexts_of(segments, "Marcus")
    assert len(ctxs) == 3
    assert ctxs[0]["after"] == " said hi"


def test_possessive_context_points_at_the_word_itself():
    [ctx] = contexts_of(["Ask for Elena's refund"], "Elena")
    assert ctx["match"] == "Elena" and ctx["after"] == "'s refund"


def test_single_characters_and_punctuation_are_skipped():
    assert terms(["x - _ , 7 ... ok"]) == ["ok"]
