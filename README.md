# Prompt Redaction Studio

A local web app for stripping PII out of LLM prompts with
[Microsoft Presidio](https://github.com/microsoft/presidio) — and putting it back afterwards.

Paste the prompt you were about to send to a model. PII is highlighted in place as you type,
you get a redacted version to copy, and when the model replies you can paste the answer back
and restore the real values.

The surrounding options panel exposes Presidio's actual configuration surface — NLP engine,
78 entity types, confidence threshold, seven anonymization operators, allow-lists and custom
recognizers — so you can see what each setting does to your text.

> All names, addresses, card numbers and IDs used in the sample prompt and the tests are
> **synthetic**. `4111 1111 1111 1111` is the standard Visa test number; the people and
> companies are invented. No real personal data is in this repository.

---

## Quick start

Requires **Python 3.10–3.14** (Presidio's supported range), or just Docker.

### Docker

```bash
docker build -t prompt-redaction .
docker run --rm -p 8000:8000 prompt-redaction
```

Or `docker compose up --build`. Then open <http://localhost:8000>.

The default image is ~1.6 GB because it bakes in both spaCy models. For a much smaller
image with only `en_core_web_sm` (weaker name and place detection):

```bash
docker build --build-arg INCLUDE_LARGE_MODEL=false -t prompt-redaction:slim .
```

### Windows (PowerShell)

```powershell
.\setup.ps1        # venv + dependencies + spaCy models (the large model is ~590 MB)
.\run.ps1          # http://localhost:8000
```

```powershell
.\setup.ps1 -SkipLargeModel     # small model only, ~12 MB, faster setup
.\setup.ps1 -WithTransformers   # adds torch + the transformers NER engine, ~2 GB
.\run.ps1 -Port 9000
```

> `setup.ps1` uses the `py` launcher rather than `python`, so it picks a supported
> interpreter even when an older Python is first on `PATH`.

### macOS and Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm
python -m spacy download en_core_web_lg      # ~590 MB, optional but recommended
uvicorn app.main:app --port 8000 --reload
```

For the transformers engine, additionally:

```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install "presidio-analyzer[transformers]"
```

Any model you skip simply shows as unavailable in the UI, with the install command
alongside it.

---

## What you can control

### NLP engine

| Engine | Model | Notes |
|---|---|---|
| spaCy small | `en_core_web_sm` | ~12 MB, fastest. Misses more names and places. |
| spaCy large | `en_core_web_lg` | Presidio's documented default. Best all-round accuracy. |
| Transformers | `StanfordAIMI/stanford-deidentifier-base` | Highest recall, slowest. Opt-in. |

Engines build on first use and are cached. Any engine whose model isn't installed shows as
unavailable in the UI with the command to install it, rather than failing mid-request.

### Anonymization operators

Chosen globally, or per entity type — set PERSON to `mask` while everything else uses
placeholders.

| Operator | What it does | Reversible |
|---|---|---|
| **Numbered placeholder** | `<PERSON_1>`, `<EMAIL_ADDRESS_1>`, … | **yes** |
| Replace | Fixed text, defaulting to `<ENTITY_TYPE>` | no |
| Redact | Deletes the value | no |
| Mask | `****@example.com` — configurable char, count, direction | no |
| Hash | SHA-256 or SHA-512 digest | no |
| Encrypt | AES with a 16/24/32-character key | **yes** |
| Keep | Detects but doesn't change the text | n/a |

**Numbered placeholder** is the default and is the one built for this job. It's the only
addition to Presidio's built-in set, implemented on Presidio's own `custom` operator: each
distinct value gets a stable numbered token, so a prompt mentioning the same person five
times stays internally consistent, and the model's reply stays readable enough to reason
about. Tokens are numbered in reading order.

Encryption is reversible too, but its base64 ciphertext reads as noise to a model and tends
to come back mangled — placeholders survive the round trip far more reliably.

### Detection settings

- **78 entity types**, grouped by region (Common, US, UK, India, Europe, Rest of world).
  The default selection is the 19 types Presidio itself loads out of the box; the country
  packs are one click away.
- **Confidence threshold** — a slider over `score_threshold`.
- **Allow-list** — terms never redacted, with exact or fuzzy matching.
- **Custom recognizers** — your own entity types from a regex or a deny-list, passed as
  `ad_hoc_recognizers` so the shared engine is never mutated.
- **Detect ORGANIZATION** — Presidio suppresses `ORG` by default because it produces many
  false positives. This toggle makes that visible rather than mysterious.
- **Explanations** — `return_decision_process`, surfacing which recognizer fired, the
  pattern it matched, checksum results and context-word score boosts. Hover any row in the
  findings table.

---

## The restore round trip

1. Redact the prompt. Real values are swapped for tokens and a mapping is held server-side
   under a random session id.
2. Send the redacted prompt to any model.
3. Paste the reply into the restore box. Real values go back in.

Presidio's `DeanonymizeEngine` needs span offsets matching the text being restored — true
for the redacted prompt, but not for a model's reply, which is different text. Since the
reply is the case that matters, `restore()` locates each token in the incoming text first
and rebuilds the spans at those offsets before handing off to Presidio. Tokens the model
didn't repeat are reported as not-found rather than silently skipped.

### A note on the mapping

The token-to-original mapping is the key that undoes the redaction. It is kept **in memory
only**, keyed by a random session id, expires after an hour, and is never written to disk.
"Clear stored mappings" under Advanced drops them all immediately. Nothing in this app sends
your prompt anywhere — Presidio runs locally, and no LLM is called.

---

## API

The UI is a thin client over a JSON API; every option above is available directly.

| Route | Purpose |
|---|---|
| `GET /api/config` | engines and availability, entity groups, operator specs, sample prompt |
| `POST /api/analyze` | detections only — type, offsets, score, explanation |
| `POST /api/redact` | analyze + anonymize — redacted text, token map, session id |
| `POST /api/restore` | put real values back into text containing tokens |
| `POST /api/sessions/clear` | drop every stored mapping |
| `GET /api/health` | liveness, which engines are warm |

Interactive docs at `http://localhost:8000/docs`.

```bash
curl -X POST http://localhost:8000/api/redact \
  -H "Content-Type: application/json" \
  -d '{"text":"Email Dana at dana@example.com","engine":"spacy_lg",
       "default_operator":{"type":"placeholder","params":{}}}'
```

---

## Layout

```
Dockerfile        both spaCy models baked in; INCLUDE_LARGE_MODEL=false for a slim build
docker-compose.yml
setup.ps1 / run.ps1   Windows convenience scripts
app/
  main.py         FastAPI routes, entity grouping, sample prompt
  engines.py      lazy cached engine registry; loads all 78 predefined recognizers
  operators.py    UI operator specs -> Presidio OperatorConfig; placeholder allocator
  recognizers.py  ad-hoc regex / deny-list recognizers
  redaction.py    analyze -> anonymize -> restore, plus the TTL session store
  schemas.py      pydantic request/response models
  static/         index.html, styles.css, app.js  (no build step)
tests/test_api.py 30 tests over the API
```

`AnalyzerEngine`'s default registry loads only ~17 recognizers (19 entity types).
Presidio ships many more as classes — the country packs for India, Germany, Korea, Spain
and others — which `engines.build_registry()` registers explicitly, taking coverage to 78
entity types.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests\ -v
```

On macOS or Linux, `python -m pytest tests/ -v` inside the activated venv.

Covers every operator, the placeholder round trip (including against a reworded reply that
reorders the tokens), encrypt/decrypt, custom recognizers, allow-lists, thresholds,
explanations, and that malformed input returns a clean 4xx rather than a stack trace.

## How this was built, and why there's no live demo

I vibe coded this — built it quickly with an AI assistant rather than hand-writing and
reviewing every line myself. It works, and the test suite covers the behaviour that matters,
but I haven't audited it the way I would something I was putting in front of real users.

That's also why I haven't put it online. This is a tool for handling Personal Information: a hosted instance
would mean strangers pasting real names, card numbers and medical details into a server I
haven't hardened or reviewed properly — and I'd rather not be responsible for that.

Run it locally, or on a server you own and have hardened yourself. That's the safer answer
anyway, and rather the point of the app: Presidio runs on your own infrastructure, no LLM is
called, and nothing you paste leaves it. Clone it and run it yourself.

## License

MIT -- see [LICENSE](LICENSE). Microsoft Presidio is MIT licensed as well.
