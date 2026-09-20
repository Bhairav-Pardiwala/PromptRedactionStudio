# Troubleshooting

Find your symptom.

## Installing and starting

### The first run seems stuck

It is downloading about 1.5 GB, most of it one language model, and there are long quiet
stretches with no visible progress. Give it 5 to 15 minutes. If you cannot wait, close it
and start again with `Start.cmd -Small` (Windows) or `./start.sh --small` — a 12 MB model
that detects fewer names and places.

### The download fails, or never starts

The first run needs to reach the Python package index. On a locked-down or offline machine
that is often blocked. Use Docker instead:

```bash
docker run --rm -p 8000:8000 bhairavpardiwala/prompt-redaction-studio
```

Everything is already inside the image, so nothing is downloaded at startup.

### "Port 8000 is already in use"

Something else is on that port. Start on another one:

```
Start.cmd -Port 9000            ./start.sh --port 9000
```

Then open <http://127.0.0.1:9000>. **If you use the desktop app, change the URL in its
Settings to match** — it defaults to port 8000 and will report the server as unreachable
otherwise.

### Windows warns that it won't run the app

The downloaded desktop app is not code-signed, so SmartScreen shows a blue warning on first
run. Choose **More info → Run anyway**. If your IT department distributed the app to you,
you will not see this at all.

### PowerShell refuses to run start.ps1

Don't run it directly — double-click **`Start.cmd`**, which is there precisely because
PowerShell scripts open in Notepad on double-click and get blocked by the execution policy.

## Detection

### An engine is greyed out

The language model for it is not installed. The app shows the command to install it next to
the engine name. `en_core_web_lg` is skipped if you used the `-Small` / `--small` option;
the transformers engine is always an opt-in extra.

### It didn't detect an obvious name

In order of how often it helps:

1. Switch the engine to **spaCy large**.
2. Lower the **Confidence threshold** — findings below it are shown but not redacted.
3. Check the entity type is ticked under **Entities to detect**. National ID types are off
   until you turn on your country's pack.
4. If it is something only your organisation would know — an internal ID, a codename — add a
   [custom recognizer](Redaction-Options#custom-recognizers).

Some misses are simply the limits of the technology; see
[What Gets Detected](What-Gets-Detected#what-it-will-miss).

### It's redacting things that aren't personal data

Raise the confidence threshold, add the term to the [allow-list](Redaction-Options#allow-list),
or untick the entity type responsible — hover the finding to see which recognizer fired. If
company names are being flagged everywhere, turn **Detect ORGANIZATION** back off under
Advanced.

## Restoring

### Fewer tokens came back than I expected

The app tells you exactly which ones did not, rather than hiding it. Usual reasons:

- **The model never repeated that token.** If it did not mention the phone number in its
  answer, there is nothing to put back. This is normal and is not an error.
- **The model altered the token** — reworded it, split it across lines, or dropped the angle
  brackets. Nothing can be matched then. Asking the model to keep placeholders like
  `<PERSON_1>` exactly as written usually prevents it.
- **More than an hour has passed.** Mappings expire after an hour, by design.
- **The app was restarted**, or you pressed *Clear stored mappings*. Both forget everything
  immediately.

Once a mapping is gone it cannot be recovered — that is the privacy guarantee working as
intended. Re-redact the original text and run the conversation again.

### I used Hash, Mask or Redact and now I can't restore

Those are one-way on purpose. Only **numbered placeholder** and **encrypt** can be undone;
see the table in [Redaction Options](Redaction-Options#default-anonymization).

## The desktop app

### The hotkey does nothing

- Something else has claimed `Ctrl+Alt+R`. Pick a different combination in **Settings**
  (at least one modifier key is required).
- Check `%APPDATA%\PromptRedactionTray\log.txt` — it records whether the hotkeys registered
  at startup and whether a press was seen.
- **On macOS**, global hotkeys need permission. Grant the app Accessibility and Input
  Monitoring access in System Settings → Privacy & Security, then restart it.
- The tray menu's **Redact clipboard** item always works and is a good way to confirm the
  rest of the app is fine.

### The menu says "Unreachable"

The app cannot reach the server at the URL shown next to it.

1. Is the server running? On your own machine, that is the launcher window.
2. Does the URL match, including the port?
3. If your organisation requires an API key, is it filled in?

Open **Settings** and press **Test connection** — it reports what actually went wrong.

### "Could not write clipboard"

Another application was holding the clipboard at that moment; some password managers and
remote desktop tools do this. Copy your text again and retry. Your clipboard was not
modified.

### I can't find the tray icon

Windows hides tray icons in the overflow area (the `^` arrow). Drag it out to keep it
visible. If you cannot reach the menu at all, start the app with the environment variable
`PRT_OPEN_SETTINGS=1` to open Settings directly.

### Nothing happened and there was no notification

Check the log at `%APPDATA%\PromptRedactionTray\log.txt`. It records every action and every
error — and never your text or a token mapping.

---

Still stuck? [Open an issue](https://github.com/Bhairav-Pardiwala/PromptRedactionStudio/issues)
— and please don't paste real personal data into it.
