# Prompt Redaction Studio — guide for AI assistants

You are an AI assistant, coding agent or chatbot. A person has asked you to install this
project for them, or to use it on their behalf. This page tells you how to do both
without hand-holding. It is written for you, not for them. It favours exact commands,
expected output and failure modes over explanation.

Humans have their own docs: [README](../README.md), the [full guide](GUIDE.md), and the
[administrator guide](ADMIN.md). To change the code rather than run it, read
[AGENTS.md](../AGENTS.md) instead.

---

## 1. What this is

A local web app and JSON API that finds personal data (PII) in text and documents with
[Microsoft Presidio](https://github.com/microsoft/presidio). It swaps each value for a
reversible token such as `<PERSON_1>`, and puts the real values back afterwards.

```
"Email Dana Whitfield at dana.whitfield@example.com"
        │  POST /api/redact
        ▼
"Email <PERSON_1> at <EMAIL_ADDRESS_1>"   ──►  sent to any LLM  ──►  reply mentioning <PERSON_1>
                                                                              │  POST /api/restore
                                                                              ▼
                                                                  reply with "Dana Whitfield" back in
```

What it guarantees, and you must not undermine:

- **It never calls an LLM.** It is the step before and after one.
- **No network egress at runtime.** Everything runs on the user's machine. The only
  downloads are during setup (PyPI packages and spaCy models) or the Docker image pull.
- **Nothing is written to disk.** The token→value mapping lives in server memory for one
  hour at most, capped at 200 sessions. Restarting the server erases every mapping.
- **Prompt content is never logged.**

What it is **not**: a guarantee. It is statistical NER plus pattern matching. It misses
things and over-matches things. Treat its output as a strong first pass that the person reviews.

---

## 2. Rules for you

1. **You are the model the PII is being kept from.** If the person wants their text
   redacted, send the raw text straight to the local API. Do not repeat it in your
   reply, summarise it, or "pre-clean" it yourself. Show them `redacted_text`, not the
   `mapping`, unless they ask for the mapping.
2. **Use the API; do not redact by eye.** Your own guess at what is PII is not reversible
   and is not what the person installed this for.
3. **Keep it local.** Do not expose the server beyond `127.0.0.1` unless the person asks.
   If they do, point them at [ADMIN.md](ADMIN.md) for keys, HTTPS and SSO. Never add a
   cloud service to the pipeline.
4. **Use synthetic data for any test you run**: `Dana Whitfield`,
   `dana.whitfield@example.com`, `+1 415 555 0132`, card `4111 1111 1111 1111`, private IPs
   such as `10.0.0.5`. Never test with the person's real data unless they asked you to process it.
5. **Warn before the big download.** The first setup pulls about 1.5 GB and takes 5–15
   minutes. Say so and offer the small model (about 700 MB, weaker at names and places)
   before you start.
6. **Don't run installers for system software** (Python, Docker, winget or brew packages)
   without the person's go-ahead. Tell them the command, and let them run it or approve it.

---

## 3. Pick an install path

Check what the machine already has, then pick the first row that applies.

```bash
docker version --format '{{.Server.Version}}'   # Docker engine running?
python3 --version  ||  py -3 --version          # Python 3.10–3.14?
```

| Machine has | Use | Download | Time |
|---|---|---|---|
| A running Docker engine | [4a. Docker](#4a-docker) | ~550 MB image, both models included | 1–3 min |
| Python **3.10–3.14** and a clone of the repo | [4b. Launcher](#4b-launcher-windows) | ~1.5 GB (`-Small`: ~700 MB) | 5–15 min |
| Neither | Ask the person to install one (see below), then use the table again | | |

Presidio does not support Python below 3.10 or 3.15+. A bare `python` on the PATH is often
the wrong version, and on Windows it can be an old Anaconda. The launchers search for a
suitable interpreter themselves, so let them.

Installing Python, if the person agrees:

| OS | Command |
|---|---|
| Windows | `winget install -e --id Python.Python.3.12 --scope user` (no admin rights needed) |
| macOS | `brew install python@3.12`, or the installer from python.org |
| Debian / Ubuntu | `sudo apt install python3 python3-venv python3-pip` |
| Fedora | `sudo dnf install python3 python3-pip` |

Getting the code, if it isn't already on the machine:

```bash
git clone https://github.com/Bhairav-Pardiwala/PromptRedactionStudio.git
cd PromptRedactionStudio
```

---

## 4. Install and start, non-interactively

The launchers are made for double-clicking. A person gets a browser tab, a question
about the desktop app, and a server running in the foreground. Your shell probably has
no stdin, and your tool calls probably time out after a few minutes, so:

- **Always pass the no-browser and no-tray flags.** On Windows, `start.ps1` asks with
  `Read-Host` whether to install the desktop app; without `-NoTray` a non-interactive
  PowerShell throws on that prompt. `start.sh` skips the question when stdin is not a TTY,
  but pass `--no-tray` anyway.
- **Split setup from running.** Run setup once with `-SetupOnly` / `--setup-only`, with a
  long timeout or in the background. Then start the server in the background and poll it.
- **Call `start.ps1`, not `Start.cmd`.** `Start.cmd` is a double-click wrapper that ends in
  `pause` on failure.

The launchers write `.venv/.setup-stamp` when they finish. Later runs skip setup and start
in seconds. The stamp is invalidated when `requirements.txt` changes or when you switch
between `-Small` and the full model, so setup can run again even though `.venv` exists.
That is expected.

### 4a. Docker

```bash
docker run -d --name prompt-redaction --rm -p 127.0.0.1:8000:8000 bhairavpardiwala/prompt-redaction-studio
```

Multi-arch (amd64 and arm64), runs as non-root, both spaCy models baked in. Nothing is
downloaded at startup. Stop it with `docker stop prompt-redaction`. To build locally
instead: `docker build -t prompt-redaction .` (about 1.6 GB), or
`docker build --build-arg INCLUDE_LARGE_MODEL=false -t prompt-redaction:slim .`.

### 4b. Launcher (Windows)

From the repo root:

```powershell
# 1. Setup only. Long-running: 5-15 min on first run, seconds afterwards.
powershell -NoProfile -NonInteractive -ExecutionPolicy Bypass -File .\start.ps1 -SetupOnly -NoTray -NoBrowser
#    add -Small to skip en_core_web_lg (~590 MB)

# 2. Start the server in the background (does not block your shell).
Start-Process -WindowStyle Hidden -FilePath .\.venv\Scripts\python.exe `
  -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000'
```

It succeeded if the last line is `Setup complete. Run Start.cmd again to launch.` and the
exit code is 0.

`.\start.ps1 -NoTray -NoBrowser` without `-SetupOnly` also works. It sets up if needed,
then runs the server **in the foreground**, so use it only when your harness can
background a command. `.\run.ps1 -Port 8000 -NoReload` starts an existing setup in the
foreground. Leave out `-NoReload` and uvicorn reloads on code changes, which you only
want when developing.

### 4c. Launcher (macOS / Linux)

```bash
# 1. Setup only.
./start.sh --setup-only --no-tray --no-browser        # add --small for the small model

# 2. Start in the background, logging somewhere you can read.
nohup .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > /tmp/prompt-redaction.log 2>&1 &
```

Or `./start.sh --no-tray --no-browser` to set up and run in one foreground command.

### Flags

| `start.ps1` | `start.sh` | Effect |
|---|---|---|
| `-Port 9000` | `--port 9000` | listen elsewhere (default 8000) |
| `-Small` | `--small` | small spaCy model only |
| `-NoBrowser` | `--no-browser` | don't open a browser |
| `-SetupOnly` | `--setup-only` | set up, then exit without serving |
| `-NoTray` | `--no-tray` | don't install the desktop app, and don't ask |
| `-Tray` | `--tray` | install the desktop app without asking (~97 MB download) |

The server always binds to `127.0.0.1`. Use `127.0.0.1` in URLs, not `localhost`: on
Windows, `localhost` resolves to `::1` first, nothing is listening there, and requests
hang instead of failing.

---

## 5. Check that it works

```bash
# Poll until up (a few seconds on a warm machine).
curl -s http://127.0.0.1:8000/api/health
# {"status":"ok","loaded_engines":[]}
```

`loaded_engines` is **empty on a fresh process, and that is normal**. Engines are built on
first use and cached, so the first redaction takes a few extra seconds. It is not an error.

```bash
# Which engines are installed? Is an API key required?
curl -s http://127.0.0.1:8000/api/config
```

In the response, check:

- `engines.<key>.available`, where the keys are `spacy_sm`, `spacy_lg` and `transformers`.
  An unavailable engine carries `missing` and an `install_hint`. `transformers` is
  unavailable by default, which is expected.
- `auth_required`. If it's `true`, see [section 8](#8-instances-with-an-api-key).
- `default_engine` is `spacy_lg`.

Smoke test with synthetic data:

```bash
curl -s -X POST http://127.0.0.1:8000/api/redact -H "Content-Type: application/json" \
  -d '{"text":"Email Dana Whitfield at dana.whitfield@example.com or call +1 415 555 0132."}'
```

You should get `"redacted_text": "Email <PERSON_1> at <EMAIL_ADDRESS_1> or call <PHONE_NUMBER_1>."`
together with a `mapping`, a `session_id` and `"engine": "spacy_lg"`.

Then tell the person the web UI is at **http://127.0.0.1:8000**.

---

## 6. Using it on the person's behalf

Base URL `http://127.0.0.1:8000`. All bodies are JSON. Interactive OpenAPI docs are at
`/docs` when no API key is set.

### Routes

| Route | Purpose |
|---|---|
| `GET /api/health` | liveness; always open |
| `GET /api/config` | engines, entity groups, operator specs, `auth_required`; always open |
| `POST /api/analyze` | detections only: type, offsets, score, optional explanation |
| `POST /api/redact` | detect and replace; returns redacted text, `mapping` and `session_id` |
| `POST /api/restore` | put real values back into any text that contains the tokens |
| `POST /api/documents/redact` | the same for a file (base64): `.docx .xlsx .pdf .txt .md .csv`, max 10 MB |
| `POST /api/documents/restore` | put real values back into a redacted file |
| `DELETE /api/sessions/{id}` | forget one mapping now instead of waiting for the 1-hour TTL |
| `GET /api/policy` | org-wide default options, if the admin configured any |

### The round trip (curl)

```bash
# 1. Redact.
curl -s -X POST http://127.0.0.1:8000/api/redact -H "Content-Type: application/json" \
  -d '{"text":"Email Dana Whitfield at dana.whitfield@example.com or call +1 415 555 0132."}'
# → {"session_id":"wItW2Byi5zAf9FpVbVLAsw",
#    "redacted_text":"Email <PERSON_1> at <EMAIL_ADDRESS_1> or call <PHONE_NUMBER_1>.",
#    "mapping":{"<PERSON_1>":"Dana Whitfield", ...}, "reversible":true, "engine":"spacy_lg", ...}

# 2. Send redacted_text to the model, whichever one.

# 3. Restore the model's reply. It is different text, and the tokens are found wherever they land.
curl -s -X POST http://127.0.0.1:8000/api/restore -H "Content-Type: application/json" \
  -d '{"text":"I have drafted a note to <PERSON_1>; confirm <EMAIL_ADDRESS_1> is correct.",
       "session_id":"wItW2Byi5zAf9FpVbVLAsw"}'
# → {"restored_text":"I have drafted a note to Dana Whitfield; confirm dana.whitfield@example.com is correct.",
#    "tokens_restored":2, "tokens_total":3, "not_found":["<PHONE_NUMBER_1>"], "restored_counts":{...}}
```

`not_found` lists tokens the reply never mentioned. That is information, not an error.

### The round trip (PowerShell)

Use `Invoke-RestMethod` with `ConvertTo-Json`. It handles quoting, and Windows `curl.exe`
with inline JSON often doesn't.

```powershell
$B = "http://127.0.0.1:8000"
$r = Invoke-RestMethod -Method Post -Uri "$B/api/redact" -ContentType "application/json" `
       -Body (@{ text = "Email Dana Whitfield at dana.whitfield@example.com." } | ConvertTo-Json)
$r.redacted_text

$reply = "I have drafted a note to <PERSON_1>."
(Invoke-RestMethod -Method Post -Uri "$B/api/restore" -ContentType "application/json" `
   -Body (@{ text = $reply; session_id = $r.session_id } | ConvertTo-Json)).restored_text
```

### If you are the one calling another LLM

For an agent that redacts before calling a model and restores after:

1. `POST /api/redact` with the text, and keep `session_id` (or `mapping`).
2. Send only `redacted_text` to the model. Tell it to keep tokens such as `<PERSON_1>`
   verbatim. Placeholders survive rewording well, because models treat them as names.
3. `POST /api/restore` with the reply and the `session_id`.
4. Optionally, `DELETE /api/sessions/{session_id}` when done.

**Keeping the key yourself instead of on the server.** Pass `"store_session": false`.
`session_id` comes back `null` and the server retains nothing, but `mapping` is still
returned. Restore by replacing each token with its value locally. Replace the longest
tokens first, so that `<PERSON_1>` never eats the start of `<PERSON_10>`. The desktop
client works this way.

Sessions expire one hour after last use, and every mapping is lost on a server restart.
If `/api/restore` returns 400 with *"That redaction session has expired or was never
created"*, redact the original again. The mapping is gone, and nothing on disk can recover it.

### Options

All are optional fields on `/api/analyze`, `/api/redact` and `/api/documents/redact`:

| Field | Example | Notes |
|---|---|---|
| `engine` | `"spacy_sm"` | `spacy_lg` by default. An engine that isn't installed returns **503** with the install hint. |
| `entities` | `["PERSON","EMAIL_ADDRESS"]` | `null` means every supported type (78). `GET /api/config` → `all_entities` lists them. |
| `score_threshold` | `0.4` | Drops low-confidence matches. Raise it when unrelated substrings get flagged. |
| `allow_list` | `["Acme"]` | Never redact these. `allow_list_match`: `"exact"` (default) or `"fuzzy"`. |
| `custom_recognizers` | see below | Your own terms or patterns. |
| `detect_organization` | `true` | ORG is off by default because it produces many false positives. |
| `return_explanations` | `true` | Which recognizer fired and why, per finding. |
| `default_operator` | `{"type":"placeholder","params":{}}` | How values are replaced. |
| `per_entity_operators` | `{"PERSON":{"type":"mask","params":{...}}}` | Overrides per type. |
| `store_session` | `false` | See above. |

Operators (`type` and `params`):

| `type` | `params` | Reversible |
|---|---|---|
| `placeholder` *(default)* | none | yes: `<PERSON_1>` |
| `replace` | `new_value` | no |
| `redact` | none | no, the value is deleted |
| `mask` | `masking_char`, `chars_to_mask`, `from_end` | no |
| `hash` | `hash_type`: `sha256` \| `sha512` | no |
| `encrypt` | `key`: 16, 24 or 32 characters | yes, but models mangle ciphertext; prefer `placeholder` |
| `keep` | none | detects without changing the text |

Custom recognizers. Use a deny-list for names or code words the models miss, and a
regex for IDs:

```json
{
  "text": "Project Bluefin ships to Dana Whitfield. Ticket ACME-48213.",
  "custom_recognizers": [
    {"name": "codenames", "entity": "PROJECT", "kind": "deny_list", "deny_list": ["Bluefin"]},
    {"name": "tickets", "entity": "TICKET_ID", "kind": "regex", "pattern": "ACME-\\d{5}", "score": 0.9}
  ]
}
```

A deny-list matches whole words, case-insensitively, and matches **every** occurrence.
If you set `entity` to a real type such as `"PERSON"`, the term gets a `<PERSON_n>` token
alongside the names Presidio found itself.

### Documents

Base64 in JSON, not multipart. `filename`'s extension selects the format.

```powershell
$B = "http://127.0.0.1:8000"
$bytes = [IO.File]::ReadAllBytes("C:\path\to\report.docx")
$doc = Invoke-RestMethod -Method Post -Uri "$B/api/documents/redact" -ContentType "application/json" `
  -Body (@{ filename = "report.docx"; content_base64 = [Convert]::ToBase64String($bytes) } | ConvertTo-Json)
[IO.File]::WriteAllBytes("C:\path\to\$($doc.filename)", [Convert]::FromBase64String($doc.content_base64))
# $doc.filename is "report.redacted.docx"; keep $doc.session_id to restore later
```

```bash
B64=$(base64 < report.docx | tr -d '\n')
curl -s -X POST http://127.0.0.1:8000/api/documents/redact -H "Content-Type: application/json" \
  -d "{\"filename\":\"report.docx\",\"content_base64\":\"$B64\"}" > resp.json
# for large files, write the JSON body to a file and use -d @body.json
```

The response has `content_base64`, `filename` (`*.redacted.<ext>`), `findings` (each with a
`location` such as `"Paragraph 3"` or `"Sheet1!B4"`), `mapping`, `session_id`, `warnings`,
and `suggestions`, which are words left in the file that the person may want to redact too.
Show the person the suggestions: they are the main way to catch what detection missed.

- A **PDF comes back as `.txt`**. Only its text can be redacted; the layout is lost. DOCX and
  XLSX keep their formatting, charts and images.
- **Batches**: pass the previous response's `session_id` with each further file, so one
  person keeps one token across all files.
- Restore with `POST /api/documents/restore` (`filename`, `content_base64`, `session_id`).
  The result is `*.restored.<ext>`.

### Expect some false positives

It is NER, so a capitalised label such as `Email:` at the start of a line can be tagged
`PERSON`, and short numbers can match ID patterns. Fix it with `allow_list`, a higher
`score_threshold`, or a narrower `entities` list. Don't discard the result.

---

## 7. Point the person at the UI

The browser UI at **http://127.0.0.1:8000** does everything the API does, with an
options panel:

- **Prompt tab**: paste, review the highlighted findings, copy the redacted text, then paste
  the model's reply into Restore. Right-click a word to mark it for redaction, or to allow-list it.
  **Dictate** takes speech on-device only.
- **Document tab**: drop files in and download the redacted copies. *Anything else to redact?*
  lists the words that remain.
- **Install app**: the browser can install it as a standalone app window.

**The desktop tray app** (optional, ~97 MB) does the same on the clipboard in any
application: copy, **Ctrl+Alt+R**, paste redacted; **Ctrl+Alt+U** on the model's reply
restores it. It keeps the mapping on the local machine. Install it with `start.ps1 -Tray` or
`./start.sh --tray`, or have the person re-run the launcher and answer yes. Its log is at
`%APPDATA%\PromptRedactionTray\log.txt` and never contains clipboard content.

---

## 8. Instances with an API key

If `GET /api/config` returns `"auth_required": true`, someone deployed this with
`REDACTION_API_KEY`. Send the key on every call:

```bash
curl -s -H "X-Redaction-Key: $KEY" ...
```

Ask the person for the key. Don't search the machine for it. A wrong or missing key
returns 401 (`Missing or invalid X-Redaction-Key header.`). Teams behind SSO use a
reverse proxy in front of the server, so auth
happens before requests reach this app. See [ADMIN.md](ADMIN.md).

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Read-Host` error, or the Windows launcher hangs | The tray-app prompt in a non-interactive shell | Add `-NoTray` (and `-NoBrowser`). |
| `Python 3.10-3.14 is required and was not found` | No supported interpreter | See the Python install table in [section 3](#3-pick-an-install-path). On Windows, the launcher's own offer to install uses `Read-Host`. Do it yourself with winget, with the person's approval. |
| `Could not create the virtual environment.` (Debian/Ubuntu) | `python3-venv` missing | `sudo apt install python3-venv`, then delete `.venv` and re-run. |
| macOS: `python3` asks to install developer tools | Xcode stub, not a real Python | `brew install python@3.12`. |
| Pip or spaCy download fails with an SSL, proxy or 403 error | PyPI or GitHub blocked (corporate network) | Use Docker, or ask the person for their proxy settings. |
| Setup times out in your tool | 1.5 GB first run | Run `-SetupOnly` / `--setup-only` in the background and re-run it: it resumes. Or use `-Small`. |
| Setup runs again although `.venv` exists | `requirements.txt` changed, or you toggled `-Small` | Expected. Let it finish. |
| `address already in use` / port 8000 busy | Another process is on the port | Use `-Port 9000` / `--port 9000`, or `-p 127.0.0.1:9000:8000` with Docker. |
| Requests to `localhost` hang on Windows | `localhost` resolved to IPv6 `::1` | Use `127.0.0.1`. |
| `503` with "is not installed" | That engine's model is missing | Use `"engine":"spacy_sm"`, or install it with `.venv/bin/python -m spacy download en_core_web_lg` (Windows: `.venv\Scripts\python.exe`). |
| `400` "session has expired or was never created" | Over an hour idle, or the server restarted | Redact again. The mapping is not recoverable. |
| `401` | `auth_required` is true | Send `X-Redaction-Key`. See [section 8](#8-instances-with-an-api-key). |
| A name was not detected | NER miss, common with the small model | Use `spacy_lg`, or add a `deny_list` custom recognizer with `"entity":"PERSON"`. |

---

## 10. Stop, update, uninstall

- **Stop**: stop the uvicorn process you started (`Stop-Process` for the PID / `kill <pid>`),
  or `docker stop prompt-redaction`. Every in-memory mapping is gone at that point.
- **Update**: `git pull`, then run the launcher again. The stamp notices when requirements
  change. For Docker, run `docker pull bhairavpardiwala/prompt-redaction-studio`.
- **Uninstall**: delete the repo folder. `.venv` inside it holds all the packages and models.
  That includes the tray app, which the launcher downloads into `dist/`. The tray's own
  settings live in `%APPDATA%\PromptRedactionTray\` (Windows) or
  `~/.config/PromptRedactionTray/` (macOS/Linux); see
  [ADMIN.md](ADMIN.md#where-the-files-live). For Docker, run
  `docker rmi bhairavpardiwala/prompt-redaction-studio`.
