# FAQ

### Does this send my text to an AI?

No. Detection runs on your own machine using Microsoft Presidio. No model is called, there
is no API key, and no account. You are the one who sends the redacted text to a chatbot,
in whatever tool you already use.

### Does anything leave my computer?

No. There is a test in the project that intercepts every network connection the app could
make, runs a full redact-and-restore cycle, and fails the build if anything reaches past the
local machine. Details in [Privacy and Data Handling](Privacy-and-Data-Handling).

### Can I use it with ChatGPT / Claude / Copilot / anything else?

Yes, all of them. The app never talks to any of them — it just hands you safe text to paste.
The desktop app works on the clipboard, so it applies everywhere: chatbots, email, Slack,
Word, a support ticket.

### Will it catch everything?

No, and you should not treat it as if it will. It is very good at emails, phone numbers,
card numbers, national IDs and most names, and it will miss unusual names, informal
references like "my sister", and bare numbers with no context.

Read the findings list before you send. That is what it is for. See
[What Gets Detected](What-Gets-Detected#what-it-will-miss).

### Which engine should I choose?

**spaCy large**, unless you have a reason not to. Small is faster and a much smaller
download, but misses noticeably more names and places. Transformers is the most thorough and
the slowest, and needs an extra install.

### Does it work offline?

Yes, after the first setup. The only step needing internet is the initial download of the
software and the language model. After that you can disconnect completely.

### What happens if I close the app mid-conversation?

Anything it remembered is gone, and you cannot restore those tokens. Mappings live in memory
only, expire after an hour, and are never written to disk — deliberately. If you still have
the original text, redact it again and carry on.

### Where is my data stored?

Nowhere. Nothing you paste is written to disk. The only thing kept is the token-to-value
mapping, in memory, for up to an hour, and *Clear stored mappings* deletes it on the spot.

### Can my whole team share one instance?

Yes — that is what it was built for. One server, everyone pointing the desktop app at its
URL. Notably, the desktop app tells the server to keep nothing, so a shared server never
accumulates anyone's real values. Your administrator will find the setup, the optional API
key and the central policy file in the
[technical guide](https://github.com/Bhairav-Pardiwala/PromptRedactionStudio/blob/main/docs/GUIDE.md#desktop-app-deployment).

### Why can't I just use a website version?

There isn't one on purpose. A hosted instance would mean strangers pasting real names, card
numbers and medical details into somebody else's server — the exact thing this app exists to
prevent. Run it on your own machine or one your organisation controls.

### Is it free?

Yes. MIT licensed, as is Microsoft Presidio underneath it. No account, no telemetry, no paid
tier.

### How reliable is it, honestly?

The author's own answer, from the README: it was built quickly with AI assistance rather
than hand-written and reviewed line by line. It works, and the test suite covers the
behaviour that matters — the privacy guarantees included — but it has not been security
audited. Treat it as a useful safety net that reduces what you leak, not as a guarantee that
you leak nothing.

### Something is broken.

[Troubleshooting](Troubleshooting) covers the common cases. If your problem is not there,
[open an issue](https://github.com/Bhairav-Pardiwala/PromptRedactionStudio/issues) — without
pasting any real personal data into it.
