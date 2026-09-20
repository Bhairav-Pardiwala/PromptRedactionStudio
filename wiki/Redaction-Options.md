# Redaction Options

Everything in the left-hand **Options** rail, in the order you meet it. The defaults are
sensible — you can use the app for a long time without touching any of this.

## NLP engine

Which language model finds names, places and organisations. Covered in
[What Gets Detected](What-Gets-Detected#choosing-an-engine).

## Confidence threshold

A slider from 0 to 1, starting at **0.35**. Only findings scoring at or above it are
redacted.

- **Raise it** if harmless text is being redacted. Fewer, more certain findings.
- **Lower it** if real personal data is slipping through. More findings, more false alarms.

Scores are shown next to every finding, so you can see where a sensible line sits for the
kind of text you paste.

## Default anonymization

What replaces each detected value. This is the setting most worth understanding, because
**only two of the seven choices can be undone.**

| Option | What you get | Can you restore it? |
|---|---|---|
| **Numbered placeholder** | `<PERSON_1>`, `<EMAIL_ADDRESS_1>` … | **Yes** |
| **Replace** | Fixed text you choose; defaults to the entity type in angle brackets | No |
| **Redact** | The value is deleted, leaving nothing | No |
| **Mask** | `****@example.com` | No |
| **Hash** | A long cryptographic digest | No |
| **Encrypt (AES)** | Unreadable ciphertext | **Yes**, with the same key |
| **Keep** | Detected and highlighted, but the text is unchanged | Nothing to restore |

**Use numbered placeholder unless you have a specific reason not to.** It is the default, it
keeps the prompt readable enough for a model to reason about, and it is the only option
built for the round trip.

Some of them take settings, which appear underneath when you pick them:

- **Replace** — *Replacement text*. Leave it blank to get `<PERSON>`, `<EMAIL_ADDRESS>` and
  so on.
- **Mask** — *Masking char* (default `*`), *Chars to mask* (default 12), and *Mask from end*
  to cover the tail rather than the start.
- **Hash** — *Algorithm*, `sha256` or `sha512`.
- **Encrypt** — an *AES key* of **exactly 16, 24 or 32 characters**. Anything else is
  rejected with a message telling you so.

A note on encryption: it is reversible, but its output is base64 noise. Models tend to
mangle it, truncate it, or refuse to repeat it, and then it cannot be decrypted. Placeholders
survive the round trip far more reliably. If your goal is to send text to a model and get an
answer back, use placeholders.

### Per-entity overrides

The default applies to everything, but any entity type can do its own thing. Expand
**Entities to detect** and set an individual type's operator — for example placeholders
everywhere, but `hash` for `CREDIT_CARD` so the number never exists in any form you could
recover.

Remember that a one-way operator on one entity type means those values simply will not come
back when you restore. That is often exactly what you want.

## Allow-list

Terms that are never redacted, even when a recognizer matches them. One per line.

Useful for your own company name, a shared support address, product names that happen to
look like people, and the names of colleagues who are on the email anyway.

**Fuzzy matching** widens it from an exact match to a substring match — with `Acme` in the
list and fuzzy on, `Acme Corp` and `Acme Ltd` are both left alone.

## Custom recognizers

Your own entity types, for the things Presidio has no way of knowing about: internal
reference numbers, project codenames, ticket IDs, customer codes.

Click **+ Add recognizer** and fill in:

- **Name** — what to call it. It becomes the entity type, so *Project code* gives you
  `<PROJECT_CODE_1>` tokens.
- **Kind** — *regex* for a pattern, or *deny list* for a fixed set of words.
- **Pattern** or **word list** — the regular expression, or one term per line.
- **Score** — how confident a match should count as, default 0.7. It is compared against the
  confidence threshold above, so keep it comfortably higher than the slider.

For example, a regex of `ACME-\d{5}` named *Account ref* turns `ACME-99120` in the sample
prompt into `<ACCOUNT_REF_1>`.

Custom recognizers apply to the request you are making and nothing else — they do not change
the app for anyone else, and they are not saved between sessions.

## Advanced

- **Detect ORGANIZATION** — Presidio ignores company names by default because it flags a lot
  of things that are not companies. Switch it on if organisation names matter to you, and
  expect to use the allow-list alongside it.
- **Explain every detection** — on by default. Adds the hover explanation on each finding
  showing which recognizer fired and why.
- **Clear stored mappings** — immediately forgets every token-to-value mapping the server is
  holding. Anything you redacted before pressing it can no longer be restored. See
  [Privacy and Data Handling](Privacy-and-Data-Handling).

---

Want the same information as a reference table, with the API parameter names? That is in the
[technical guide](https://github.com/Bhairav-Pardiwala/PromptRedactionStudio/blob/main/docs/GUIDE.md#what-you-can-control).
