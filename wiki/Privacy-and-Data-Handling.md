# Privacy and Data Handling

The whole point of this app is to handle personal data, so it is fair to ask what it does
with yours. Here is all of it.

## No AI model is called

Detection is done by Microsoft Presidio, a library running on your own machine. There is no
API key, no account, and no model provider involved. The app never contacts ChatGPT, Claude
or anything like them — **you** do that, with the redacted text, in whichever tool you
already use.

## Nothing is written to disk

Your text is processed in memory and dropped. The one thing that has to be kept for a while
is the token mapping — the list saying `<PERSON_1>` was Jane Doe — because that is what
makes restoring possible.

That mapping is:

- held **in memory only**, never on disk
- filed under a random session id
- **deleted automatically after one hour**
- capped, so old sessions fall off rather than piling up
- wiped instantly by **Clear stored mappings**, in the web app's Advanced section or the
  desktop app's tray menu

Restart the app and every mapping is gone. There is no cache to clean up afterwards and
nothing left behind on the machine.

## Nothing leaves the machine

The app makes no outbound network connections while it runs. That is not just a promise in
a readme — it is enforced by a test.

`tests/test_no_egress.py` intercepts every socket connection and DNS lookup the process can
make, runs a full detect → redact → restore cycle, and **fails the build** if anything tries
to reach further than the local machine. The test also deliberately attempts a connection
itself, so if the check ever silently stopped working it fails loudly instead of passing
quietly.

The only time the app needs the internet is the very first setup, to download the software
and the language model. After that you can disconnect entirely and it works exactly the
same.

## Logs never contain your text

Both the server and the desktop app write logs. They record events — which engine loaded,
whether a hotkey registered, how many items were replaced, what error occurred. They do not
record prompts, detected values or token mappings, by design. Even when the server cannot
load a language model, it returns a fixed message rather than anything that might carry
content with it.

## If your team shares one server

Some organisations run one instance for everybody. In that setup:

- The desktop app **always tells the server to keep nothing**. The server analyses your
  text, hands back the redacted version and the mapping, and retains neither.
- The mapping lives in memory on **your** computer, and restoring happens locally with no
  network call.
- So there is no central pile of everyone's real names and card numbers sitting on a shared
  machine, and nothing for anyone else to retrieve.

Your administrator can additionally set a shared API key and a central redaction policy;
both are described in the
[technical guide](https://github.com/Bhairav-Pardiwala/PromptRedactionStudio/blob/main/docs/GUIDE.md#desktop-app-deployment).

## What this does not promise

Being straight about the limits, because a privacy tool that oversells itself is worse than
none:

- **Detection is imperfect.** It will miss things — see
  [What Gets Detected](What-Gets-Detected#what-it-will-miss). Read the findings list before
  you send anything.
- **It is a redaction tool, not a compliance product.** It does not make you GDPR- or
  HIPAA-compliant, and nothing here is legal advice. If you handle regulated data, your
  own policies still decide what may be sent where.
- **It has not been security-audited.** The author built it quickly with AI assistance and
  says so plainly in the
  [README](https://github.com/Bhairav-Pardiwala/PromptRedactionStudio#how-this-was-built-and-why-theres-no-live-demo).
  The test suite covers the behaviour that matters, but nobody has reviewed it line by line
  as a security product.
- **There is no hosted version, on purpose.** A public instance would mean strangers pasting
  real names and medical details into somebody else's server — which is the exact thing this
  app exists to avoid. Run it locally, or on a machine your organisation controls.
