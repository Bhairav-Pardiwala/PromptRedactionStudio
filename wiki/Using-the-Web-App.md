# Using the Web App

Open <http://127.0.0.1:8000>. The page is split into three columns: **Options** on the left,
your text down the middle, and **Findings** on the right.

![The web UI detecting personal data in a prompt, with the options panel on the left and scored findings on the right](https://raw.githubusercontent.com/Bhairav-Pardiwala/PromptRedactionStudio/main/docs/web-ui-detection.png)

If you just want to see it work, click **Load sample prompt** in the top right. It fills the
box with an invented customer complaint containing a name, an email, a phone number, an
address, a date, a reference code and a test card number.

## The round trip

### 1. Paste your prompt

Type or paste into the **Your prompt** box. You do not have to press anything — findings
appear as you type, highlighted in the text itself and listed in the Findings panel with a
confidence score.

Read that list before you go further. It is the answer to "did it actually catch
everything?", and it is the one habit worth forming. See
[What Gets Detected](What-Gets-Detected) for what to do when something is missing.

### 2. Click Redact

The **Redacted prompt** box fills with your text, with each personal value replaced by a
numbered token:

```
Hi, I need help drafting a reply to a customer complaint.

The customer is <PERSON_1>, reachable at <EMAIL_ADDRESS_1> or on <PHONE_NUMBER_1>.
They live in <LOCATION_1> and have been a member since <DATE_TIME_1>.
```

Each distinct value gets its own token, and the same value keeps the same token everywhere
it appears — mention one person five times and they are `<PERSON_1>` all five times. That is
what keeps the text making sense to a model: it can still reason about who did what, it just
cannot know who they are.

Click **Copy**, and paste that into ChatGPT, Claude, Copilot, or whatever you use.

### 3. Paste the reply back

When the model answers, its reply will still contain the tokens — models copy them through
quite reliably. Paste the whole reply into the **Restore the reply** box and click
**Restore**.

![The restore panel putting real values back into a model's JSON reply, reporting 18 of 18 tokens restored](https://raw.githubusercontent.com/Bhairav-Pardiwala/PromptRedactionStudio/main/docs/web-ui-restore.png)

The real values go back in, and you are told how many came back — for example *18 of 18
tokens restored*. If a number is missing, the tokens the model never mentioned are named
explicitly rather than quietly dropped, so you always know what you are looking at.

Restoring works on the model's reply, not just on the prompt you redacted. The reply is
different text in a different order, and that is the case this was built for.

## The Findings panel

**Findings** lists every detection with its entity type, the matched text and a confidence
score between 0 and 1. Click a column header to sort. Hover any row to see *why* it fired:
which recognizer matched, the pattern it used, whether a checksum passed, and whether nearby
words boosted the score. That explanation comes from the **Explain every detection**
checkbox under Advanced, which is on by default.

**Token map**, below it, shows each token next to the real value it stands for. This is the
key that undoes the redaction — it is kept in memory only, expires after an hour, and is
never written to disk. [Privacy and Data Handling](Privacy-and-Data-Handling) explains
exactly what is held and for how long.

## Changing how it behaves

Everything in the left-hand Options rail — which engine, how strict to be, what replaces
each value, terms to never touch, your own custom entity types — has its own page:
[Redaction Options](Redaction-Options).

## Next

- [What Gets Detected](What-Gets-Detected) — and what gets missed
- [Using the Desktop App](Using-the-Desktop-App) — skip the browser tab entirely
- [Troubleshooting](Troubleshooting)
