"""Words left in a redacted document, offered to the user as candidates to redact.

The model misses things -- a surname that is also an ordinary word, an internal ID it has
no recognizer for. Listing what remains, minus the grammatical glue that can never be
personal data, turns "read the whole file again" into "scan a list of words".

The closed classes below are fixed lists by nature: English does not coin new articles,
auxiliaries, prepositions, conjunctions or pronouns, so a word list is complete where a
model would only be approximate -- and it needs no engine loaded.
"""

from __future__ import annotations

import re
from typing import Dict, List, Tuple

ARTICLES = {"a", "an", "the"}

AUXILIARIES = {
    "be", "am", "is", "are", "was", "were", "been", "being",
    "have", "has", "had", "having",
    "do", "does", "did", "done", "doing",
    "will", "would", "shall", "should", "can", "could", "may", "might", "must", "ought",
}

PREPOSITIONS = {
    "aboard", "about", "above", "across", "after", "against", "along", "alongside", "amid",
    "amidst", "among", "amongst", "around", "at", "atop", "before", "behind", "below",
    "beneath", "beside", "besides", "between", "beyond", "by", "concerning", "considering",
    "despite", "down", "during", "except", "excluding", "following", "for", "from", "in",
    "including", "inside", "into", "like", "minus", "near", "of", "off", "on", "onto",
    "opposite", "out", "outside", "over", "past", "per", "plus", "regarding", "round",
    "since", "than", "through", "throughout", "till", "to", "toward", "towards", "under",
    "underneath", "unlike", "until", "unto", "up", "upon", "versus", "via", "with", "within",
    "without",
}

CONJUNCTIONS = {
    "and", "or", "but", "nor", "so", "yet", "because", "although", "though", "while",
    "whilst", "whereas", "if", "unless", "whether", "as", "once", "lest", "provided",
    "whenever", "wherever", "whereby", "either", "neither", "both", "also", "then",
    "therefore", "however", "otherwise",
}

PRONOUNS = {
    # personal, possessive, reflexive
    "i", "me", "my", "mine", "myself",
    "we", "us", "our", "ours", "ourselves",
    "you", "your", "yours", "yourself", "yourselves",
    "he", "him", "his", "himself",
    "she", "her", "hers", "herself",
    "it", "its", "itself",
    "they", "them", "their", "theirs", "themselves",
    # demonstrative, relative, interrogative
    "this", "that", "these", "those",
    "who", "whom", "whose", "which", "what", "whoever", "whomever", "whatever", "whichever",
    # indefinite
    "all", "another", "any", "anybody", "anyone", "anything", "each", "everybody",
    "everyone", "everything", "few", "many", "most", "much", "none", "nobody", "nothing",
    "one", "other", "others", "several", "some", "somebody", "someone", "something",
    "such", "own",
}

# Contractions of the words above, in both apostrophe styles once normalised below.
CONTRACTIONS = {
    "i'm", "i've", "i'd", "i'll", "you're", "you've", "you'd", "you'll",
    "he's", "he'd", "he'll", "she's", "she'd", "she'll", "it's", "it'd", "it'll",
    "we're", "we've", "we'd", "we'll", "they're", "they've", "they'd", "they'll",
    "that's", "that'll", "there's", "here's", "who's", "what's", "let's",
    "isn't", "aren't", "wasn't", "weren't", "haven't", "hasn't", "hadn't",
    "don't", "doesn't", "didn't", "won't", "wouldn't", "shan't", "shouldn't",
    "can't", "cannot", "couldn't", "mightn't", "mustn't",
}

CLOSED_CLASS_WORDS = frozenset(
    ARTICLES | AUXILIARIES | PREPOSITIONS | CONJUNCTIONS | PRONOUNS | CONTRACTIONS
)

# The placeholder tokens this app writes, e.g. <PERSON_1>. Already redacted: skip.
_TOKEN = re.compile(r"<[A-Z][A-Z0-9_]*_\d+>")
# A word may carry inner apostrophes and hyphens, so ACME-99120 and O'Neil stay whole.
_WORD = re.compile(r"[^\W_][\w'’\-]*")
_POSSESSIVE = re.compile(r"['’]s$", re.IGNORECASE)

DEFAULT_LIMIT = 300
# Where a word appears, so the user can judge it without opening the file: a few
# occurrences, each with some text either side.
CONTEXTS_PER_TERM = 3
CONTEXT_CHARS = 40
_SPACE = re.compile(r"\s+")
_CUT_START = re.compile(r"^\w+")
_CUT_END = re.compile(r"\w+$")


def _context(segment: str, start: int, end: int) -> Dict[str, str]:
    """The text either side of segment[start:end], cut at word boundaries, one line."""
    lo = max(0, start - CONTEXT_CHARS)
    hi = min(len(segment), end + CONTEXT_CHARS)
    # Never end the window inside a placeholder token: widen it to take the whole token.
    for token in _TOKEN.finditer(segment):
        if token.start() < lo < token.end():
            lo = token.start()
        if token.start() < hi < token.end():
            hi = token.end()
    before = segment[lo:start]
    after = segment[end:hi]
    # Drop only a word actually cut in half at either edge. Trimming back to the nearest
    # space instead would empty a CSV row, where fields are separated by commas.
    if lo > 0 and segment[lo - 1 : lo + 1].isalnum():
        before = _CUT_START.sub("", before)
    if hi < len(segment) and segment[hi - 1 : hi + 1].isalnum():
        after = _CUT_END.sub("", after)
    return {
        "before": ("…" if lo > 0 else "") + _SPACE.sub(" ", before).lstrip(),
        "match": segment[start:end],
        "after": _SPACE.sub(" ", after).rstrip() + ("…" if hi < len(segment) else ""),
    }


def suggest_terms(segments: List[str], limit: int = DEFAULT_LIMIT) -> Tuple[List[Dict], int]:
    """Distinct words left in redacted text, most likely to be personal data first.

    Returns up to `limit` of `{"term", "count", "contexts"}` and the total number of
    distinct words. Capitalised words and words with digits come first -- that is where
    names and IDs are -- then the most frequent, then alphabetical, so the order is stable.
    Each context is `{"before", "match", "after"}` taken from the redacted text, so
    anything already redacted shows as its token, never as the value.
    """
    counts: Dict[str, int] = {}
    spelling: Dict[str, str] = {}
    contexts: Dict[str, List[Dict[str, str]]] = {}

    for segment in segments:
        # Blank tokens out with spaces rather than removing them, so offsets still line
        # up with the segment the contexts are cut from.
        masked = _TOKEN.sub(lambda m: " " * len(m.group(0)), segment)
        for match in _WORD.finditer(masked):
            word = _POSSESSIVE.sub("", match.group(0)).strip("'’-")
            if len(word) < 2:
                continue
            key = word.lower().replace("’", "'")
            if key in CLOSED_CLASS_WORDS:
                continue
            counts[key] = counts.get(key, 0) + 1
            spelling.setdefault(key, word)
            found = contexts.setdefault(key, [])
            if len(found) < CONTEXTS_PER_TERM:
                start = match.start() + match.group(0).index(word)
                found.append(_context(segment, start, start + len(word)))

    def rank(key: str) -> Tuple[int, int, str]:
        word = spelling[key]
        likely = word[0].isupper() or any(ch.isdigit() for ch in word)
        return (0 if likely else 1, -counts[key], key)

    ordered = sorted(counts, key=rank)
    return [
        {"term": spelling[k], "count": counts[k], "contexts": contexts[k]} for k in ordered[:limit]
    ], len(ordered)
