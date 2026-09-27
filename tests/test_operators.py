"""PlaceholderAllocator seeded with an existing session's tokens -- the batch case."""

from app.operators import PlaceholderAllocator


def allocate(allocator, text, spans):
    """Stand in for Presidio: replace spans back-to-front, as its engine does."""
    for start, end, entity_type in sorted(spans, reverse=True):
        text = text[:start] + allocator.token_for(entity_type, text[start:end]) + text[end:]
    return allocator.finalize(text)


def test_an_unseeded_allocator_numbers_by_reading_order():
    text, mapping = allocate(PlaceholderAllocator(), "Ann met Bob", [(0, 3, "PERSON"), (8, 11, "PERSON")])
    assert text == "<PERSON_1> met <PERSON_2>"
    assert mapping == {"<PERSON_1>": "Ann", "<PERSON_2>": "Bob"}


def test_a_seeded_allocator_reuses_tokens_and_continues_numbering():
    existing = {"<PERSON_1>": "Ann", "<PERSON_2>": "Bob", "<EMAIL_ADDRESS_1>": "a@example.com"}
    text, mapping = allocate(
        PlaceholderAllocator(existing),
        "Cat, Bob and Dan",
        [(0, 3, "PERSON"), (5, 8, "PERSON"), (13, 16, "PERSON")],
    )
    # Cat comes first in reading order but Bob keeps his token; new people start at 3.
    assert text == "<PERSON_3>, <PERSON_2> and <PERSON_4>"
    assert mapping == {"<PERSON_2>": "Bob", "<PERSON_3>": "Cat", "<PERSON_4>": "Dan"}


def test_numbering_continues_per_entity_type():
    existing = {"<PERSON_7>": "Ann", "<LOCATION_2>": "Paris"}
    text, mapping = allocate(
        PlaceholderAllocator(existing), "Zed in Rome", [(0, 3, "PERSON"), (7, 11, "LOCATION")]
    )
    assert text == "<PERSON_8> in <LOCATION_3>"


def test_seeded_tokens_absent_from_the_text_are_not_reported():
    text, mapping = allocate(PlaceholderAllocator({"<PERSON_1>": "Ann"}), "no one here", [])
    assert text == "no one here" and mapping == {}
