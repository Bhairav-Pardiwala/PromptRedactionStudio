# Prompt Redaction Studio

Strip PII out of LLM prompts with [Microsoft Presidio](https://github.com/microsoft/presidio),
then put it back afterwards. Runs entirely on your machine — no LLM is called and nothing
you paste leaves the box.

[![CI](https://github.com/Bhairav-Pardiwala/PromptRedactionStudio/actions/workflows/ci.yml/badge.svg)](https://github.com/Bhairav-Pardiwala/PromptRedactionStudio/actions/workflows/ci.yml)
[![Docker image](https://img.shields.io/docker/image-size/bhairavpardiwala/prompt-redaction-studio/latest?logo=docker&label=docker%20image)](https://hub.docker.com/r/bhairavpardiwala/prompt-redaction-studio)
[![Docker pulls](https://img.shields.io/docker/pulls/bhairavpardiwala/prompt-redaction-studio?label=pulls)](https://hub.docker.com/r/bhairavpardiwala/prompt-redaction-studio)

![The web UI detecting personal data in a prompt, with the options panel on the left and scored findings on the right](docs/web-ui-detection.png)

Paste a prompt, get a redacted version to copy, send it to any model, then paste the reply
back to restore the real values. A desktop tray app does the same on the clipboard from any
application, via a hotkey.

📖 **[Full guide](docs/GUIDE.md)** — every option explained, API reference, org deployment,
how the restore round trip works.

> All names, cards and IDs in the sample prompt, tests and screenshots are **synthetic**.
> No real personal data is in this repository.

---

## Just run it

No Python, no terminal, no Docker knowledge needed.

**Windows** — download or clone this repository, then double-click **`Start.cmd`**.

**macOS and Linux** — download or clone, then in Terminal:

```bash
./start.sh
```

That is the whole thing. The launcher finds a supported Python (offering to install one if
there is none), sets everything up, starts the app, and opens your browser at
<http://127.0.0.1:8000>. It also offers to install the desktop app with its clipboard
hotkey. Run it again whenever you like — it remembers, and starts in seconds.

The first run downloads about 1.5 GB and takes 5–15 minutes, most of it the
`en_core_web_lg` language model. Add `-Small` / `--small` to skip it and use the 12 MB
model instead, which detects noticeably fewer names and places.

| | Windows | macOS / Linux |
|---|---|---|
| Different port | `Start.cmd -Port 9000` | `./start.sh --port 9000` |
| Small model only | `Start.cmd -Small` | `./start.sh --small` |
| Leave the browser alone | `Start.cmd -NoBrowser` | `./start.sh --no-browser` |
| Skip the desktop app | `Start.cmd -NoTray` | `./start.sh --no-tray` |

The first run needs to reach PyPI. On a locked-down machine where that is blocked, use
Docker instead.

---

## Other ways to run it

Requires **Python 3.10–3.14** (Presidio's supported range), or just Docker.

### Docker

```bash
docker run --rm -p 8000:8000 bhairavpardiwala/prompt-redaction-studio
```

Then open <http://localhost:8000>. That pulls about 550 MB and runs natively on both Intel
and Apple Silicon. Both spaCy models are already baked in, so no model is downloaded at
startup.

Or build it yourself:

```bash
docker build -t prompt-redaction .
docker run --rm -p 8000:8000 prompt-redaction
```

Or `docker compose up --build`. A local build unpacks to ~1.6 GB on disk because it bakes in
both spaCy models. Add `--build-arg INCLUDE_LARGE_MODEL=false` for a slim image with only
`en_core_web_sm`.

### Windows (PowerShell)

```powershell
.\setup.ps1        # venv + dependencies + spaCy models (the large model is ~590 MB)
.\run.ps1          # http://localhost:8000
```

Useful flags: `.\setup.ps1 -SkipLargeModel` (small model only, ~12 MB),
`.\setup.ps1 -WithTransformers` (adds the transformers NER engine, ~2 GB),
`.\run.ps1 -Port 9000`.

### macOS and Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m spacy download en_core_web_sm
python -m spacy download en_core_web_lg      # ~590 MB, optional but recommended
uvicorn app.main:app --port 8000 --reload
```

Any model you skip simply shows as unavailable in the UI, with the install command
alongside it.

---

## Sample run

### In the browser

1. Open <http://localhost:8000>. The box is pre-filled with a sample prompt — findings
   appear as you type, highlighted in place and listed with their confidence scores.
2. Click **Redact**. Each distinct value becomes a numbered token: `<PERSON_1>`,
   `<EMAIL_ADDRESS_1>`. The same person mentioned five times keeps the same token.
3. Copy the redacted prompt, send it to a model, and paste its reply into the **restore**
   box. Real values go back in, and you're told how many tokens came back.

![The restore panel putting real values back into a model's JSON reply, reporting 18 of 18 tokens restored](docs/web-ui-restore.png)

### Against the API

```bash
curl -X POST http://localhost:8000/api/redact \
  -H "Content-Type: application/json" \
  -d '{"text":"Email Dana Whitfield at dana.whitfield@example.com or call +1 415 555 0132.",
       "engine":"spacy_lg","default_operator":{"type":"placeholder","params":{}}}'
```

```json
{
  "session_id": "wItW2Byi5zAf9FpVbVLAsw",
  "redacted_text": "Email <PERSON_1> at <EMAIL_ADDRESS_1> or call <PHONE_NUMBER_1>.",
  "mapping": {
    "<PERSON_1>": "Dana Whitfield",
    "<EMAIL_ADDRESS_1>": "dana.whitfield@example.com",
    "<PHONE_NUMBER_1>": "+1 415 555 0132"
  },
  "reversible": true,
  "engine": "spacy_lg"
}
```

Send `redacted_text` to a model, then restore whatever it says back — the reply is different
text from the prompt, and tokens it repeated are found wherever they landed:

```bash
curl -X POST http://localhost:8000/api/restore \
  -H "Content-Type: application/json" \
  -d '{"text":"I have drafted a note to <PERSON_1>; confirm <EMAIL_ADDRESS_1> is correct.",
       "session_id":"wItW2Byi5zAf9FpVbVLAsw"}'
```

```json
{
  "restored_text": "I have drafted a note to Dana Whitfield; confirm dana.whitfield@example.com is correct.",
  "tokens_restored": 2,
  "tokens_total": 3,
  "not_found": ["<PHONE_NUMBER_1>"]
}
```

The model never repeated the phone number, so it's reported in `not_found` rather than
silently dropped. Interactive docs for every route are at <http://localhost:8000/docs>; the
full route table is in the [guide](docs/GUIDE.md#api).

---

## Desktop app

**Copy text in any app → press Ctrl+Alt+R → paste redacted.** Press **Ctrl+Alt+U** on the
model's reply to put the real values back. It works everywhere — ChatGPT, Claude, Slack,
Outlook — because it operates on the clipboard, not on a website's DOM.

```powershell
cd tray
dotnet run          # development
dotnet publish -c Release -r win-x64 --self-contained -p:PublishSingleFile=true -o ../dist
```

That produces a single ~97 MB executable with no runtime to install; swap `win-x64` for
`linux-x64` or `osx-arm64`. Point it at your server's URL in Settings and press **Test
connection**.

Org deployment, central policy and the API key are covered in the
[guide](docs/GUIDE.md#desktop-app-deployment).

---

## Verifying what you downloaded

Both the Docker image and the tray binaries are built in GitHub Actions and carry a
[build attestation](https://docs.github.com/actions/security-guides/using-artifact-attestations)
— cryptographic proof of which repository, workflow and commit produced them. Check it with
the [GitHub CLI](https://cli.github.com) before trusting either one:

```bash
gh attestation verify --owner Bhairav-Pardiwala oci://bhairavpardiwala/prompt-redaction-studio:1.0.0

gh attestation verify --owner Bhairav-Pardiwala PromptRedactionTray-win-x64.exe
```

A pass means the artifact came from this repository's CI and has not been altered since.
It is not a statement that the code is free of bugs — see the note at the bottom.

---

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest tests\ -v     # 40 backend tests
dotnet test tray.Tests                             # 25 desktop client tests
```

Among them, `tests/test_no_egress.py` intercepts every socket and DNS lookup, runs a full
detect → redact → restore round trip, and fails if anything reaches past loopback. That is
the claim in the first paragraph of this README written down as something which breaks the
build, rather than something you have to take on faith.

On macOS or Linux, `python -m pytest tests/ -v` inside the activated venv.

---

## How this was built, and why there's no live demo

I vibe coded this — built it quickly with an AI assistant rather than hand-writing and
reviewing every line myself. It works, and the test suite covers the behaviour that matters,
but I haven't audited it the way I would something I was putting in front of real users.

That's also why I haven't put it online. This is a tool for handling personal information: a
hosted instance would mean strangers pasting real names, card numbers and medical details
into a server hosted by me which i would not want and would defeat the purpose of this app!.

Run it locally, or on a server you own and have hardened yourself. That's the safer answer
anyway, and rather the point of the app: Presidio runs on your own infrastructure, no LLM is
called, and nothing you paste leaves it. Clone it and run it yourself.

## License

MIT -- see [LICENSE](LICENSE). Microsoft Presidio is MIT licensed as well.
