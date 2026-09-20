# What Gets Detected

Detection is done by [Microsoft Presidio](https://microsoft.github.io/presidio/), running on
your machine. It knows **78 types of personal data**, of which **19 are switched on by
default**.

## The default 19

These are ticked when you open the app:

`PERSON` · `EMAIL_ADDRESS` · `PHONE_NUMBER` · `LOCATION` · `ORGANIZATION` · `DATE_TIME` ·
`CREDIT_CARD` · `IBAN_CODE` · `IP_ADDRESS` · `URL` · `NRP` (nationality, religion, political
group) · `CRYPTO` · `MAC_ADDRESS` · `MEDICAL_LICENSE` · `US_SSN` · `US_BANK_NUMBER` ·
`US_ITIN` · `US_PASSPORT` · `US_DRIVER_LICENSE` · `UK_NHS`

That is the set Presidio itself loads out of the box. It covers the things that actually
leak in everyday prompts.

## The other 59

Open **Entities to detect** in the Options rail and you will find everything grouped by
region:

| Group | What's in it |
|---|---|
| **Common** | The everyday identifiers most prompts leak — names, emails, phones, places, cards, dates, IPs |
| **United States** | US national and financial identifiers (SSN, ITIN, passport, driver's licence, bank and routing numbers) |
| **United Kingdom** | UK national, driving and postal identifiers |
| **India** | Aadhaar, PAN, GSTIN, voter ID, passport and vehicle registration |
| **Europe** | Country identifiers across the EU and EEA — Germany, Spain, Italy, Poland, Finland, Sweden |
| **Rest of world** | Australia, Canada, Singapore, Korea, Nigeria, the Philippines, Thailand, Türkiye, South Africa |

Three buttons make this quick: **Select all**, **Select none**, and **Presidio defaults** to
get back to the starting 19.

**Why isn't everything on by default?** Because national ID formats are mostly just numbers
of a certain length. Turn all 78 on and an order reference starts matching a Thai national
ID, an email's first few characters score as an Indian PAN, and the findings you care about
get buried. Switch on the country packs that apply to you — that is one click — and leave
the rest off.

## Choosing an engine

The **NLP engine** is what finds names, places and organisations. Patterns like card numbers
and emails are matched by rules and work the same whichever engine you pick.

| Engine | Pick it when |
|---|---|
| **spaCy small** | You want speed, or you skipped the big download. Misses noticeably more names and places. |
| **spaCy large** | Default. Best all-round accuracy, and what you should use unless you have a reason not to. |
| **Transformers** | You need the highest possible recall and can wait. Slowest, and an optional extra install. |

An engine whose model is not installed shows as unavailable and the app tells you the
command to install it, rather than failing halfway through a redaction. The status pill in
the top right tells you which engine is live.

## What it will miss

This is pattern matching and statistical language modelling, not comprehension. It is very
good and it is not a guarantee. Expect it to struggle with:

- **Unusual or non-Western names**, especially ones that look like ordinary words
- **Informal references** — "my sister", "the guy from accounts on Tuesday"
- **Numbers with no context** — a bare account number in a column of figures, with nothing
  nearby to say what it is
- **Typos and odd spacing** inside otherwise recognisable values
- **Anything specific to your organisation** — internal IDs, project codenames, ticket refs

The last one is solvable: add a [custom recognizer](Redaction-Options#custom-recognizers)
and it becomes a first-class entity type from then on.

For the rest, the answer is the habit from
[Using the Web App](Using-the-Web-App): **read the findings list before you send.** It is
there so you can see what was caught, and notice what wasn't.

## Tuning how much it catches

The **Confidence threshold** slider sets how sure Presidio must be before something counts.
It starts at 0.35.

- **Missing things?** Lower it, and try the large engine.
- **Redacting things that aren't personal data?** Raise it, or add the term to the
  [allow-list](Redaction-Options#allow-list).

Both are covered in [Redaction Options](Redaction-Options).
