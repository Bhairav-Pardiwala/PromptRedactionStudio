# Using the Desktop App

The web page is fine for trying things out, but switching to a browser tab before every
prompt is friction nobody keeps up. The desktop app removes it:

> **Copy text anywhere → press `Ctrl+Alt+R` → paste the redacted version.**
> When the reply comes back, copy it and press `Ctrl+Alt+U` to put the real values back.

It works in ChatGPT, Claude, Copilot, Slack, Outlook, Word — anywhere at all — because it
works on your clipboard rather than on a particular website.

The app lives in the notification area (the system tray) and has no window of its own until
you open Settings.

## First-time setup

Right-click the tray icon and choose **Settings…**

![The desktop Settings window: instance URL, optional API key, a Test connection button, hotkey bindings and start-at-sign-in](https://raw.githubusercontent.com/Bhairav-Pardiwala/PromptRedactionStudio/main/docs/desktop-settings.png)

- **URL** — where the redaction service is running. It defaults to
  `http://127.0.0.1:8000`, which is exactly what the launcher starts on your own machine, so
  if you are running it yourself there is nothing to change. If your organisation hosts one,
  paste the URL they gave you.
- **API key** — leave blank unless you were given one.
- **Test connection** — press it. This is the point of the button: a wrong URL should fail
  here, visibly, rather than at the moment you press the hotkey expecting to be protected.
- **Hotkeys** — `Ctrl+Alt+R` to redact and `Ctrl+Alt+U` to restore, changeable. The format is
  `Ctrl+Alt+R`, and at least one modifier key is required.
- **Start automatically when I sign in** — Windows only; on macOS and Linux the checkbox
  does nothing.

Press **Save**.

## The tray menu

Right-click the icon:

| Item | What it does |
|---|---|
| *Connected: …* / *Unreachable: …* | Status. Shows the URL it is talking to. Not clickable. |
| **Redact clipboard** | Same as the redact hotkey |
| **Restore clipboard** | Same as the restore hotkey |
| **Open web UI** | Opens the full options page in your browser |
| **Settings…** | The window above |
| **Clear stored mappings** | Forgets every redaction it remembers, immediately |
| **Quit** | Closes the app |

Left-clicking the icon redacts the clipboard, the same as the hotkey.

## Reading the notifications

Every action shows a small toast in the corner.

- **Redacted 7 item(s)** — with a breakdown like *3x PERSON, 2x EMAIL_ADDRESS, 1x
  PHONE_NUMBER*, and a reminder of your restore hotkey. Your clipboard now holds the safe
  version; paste it.
- **Nothing detected** — nothing personal was found, and your clipboard was left exactly as
  it was.
- **Nothing to redact** — the clipboard has no text in it (an image, or empty).
- **Restored 4 token(s)** — with how many separate occurrences were replaced.
- **No tokens found** — the text you copied contains none of the tokens it remembers, or
  there are no redactions remembered yet.
- **Could not write clipboard** — another application was holding the clipboard. Copy again
  and retry; nothing was changed.
- **Redaction failed** — it could not reach the server, or the server rejected the request.
  Check the status line in the tray menu.

## Two things worth knowing

**A failed redaction never touches your clipboard.** If anything goes wrong, the original
text is still there, unchanged. It is never cleared or left half-processed, so a failure can
never cause you to paste something you did not mean to.

**What it remembers is temporary and stays on your machine.** The mapping from tokens back
to real values is held in memory by the app itself — not by the server, even when the server
belongs to your organisation. It covers your last 50 redactions, each expiring an hour after
it was made, and none of it is ever written to disk. Quit the app and it is gone, which
means you cannot restore a redaction from yesterday.

That is deliberate: a shared server never accumulates anyone's real values, and restoring
needs no network call at all.

## Where its files live

On Windows:

- Settings — `%APPDATA%\PromptRedactionTray\settings.json`
- Log — `%APPDATA%\PromptRedactionTray\log.txt`

The log records events only: whether a hotkey registered, whether a redaction succeeded, how
many items it replaced. It never contains your text and never contains a token mapping.

## Next

- Hotkey not working, or the menu says *Unreachable*? → [Troubleshooting](Troubleshooting)
- Sharing one server across a team? The setup is in the
  [technical guide](https://github.com/Bhairav-Pardiwala/PromptRedactionStudio/blob/main/docs/GUIDE.md#desktop-app-deployment).
