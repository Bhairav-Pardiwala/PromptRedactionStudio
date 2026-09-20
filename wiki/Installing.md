# Installing

There is one step. You do not need Python, a terminal, or any knowledge of Docker.

> **Were you given a URL by your IT department?** Then someone else is running the server
> for you and you do not need any of this. Go to
> [Using the Desktop App](Using-the-Desktop-App) instead.

## Windows

1. Download this project — on the
   [GitHub page](https://github.com/Bhairav-Pardiwala/PromptRedactionStudio), click
   **Code → Download ZIP**, then unzip it somewhere like your Documents folder.
2. Double-click **`Start.cmd`**.

## macOS and Linux

Download and unzip the same way, then in Terminal, from inside the folder:

```bash
./start.sh
```

That is the whole thing. The launcher checks for a supported Python (offering to install one
if there is none), sets everything up, starts the app, and opens your browser at
<http://127.0.0.1:8000>. It also offers to install the desktop app with its clipboard
hotkey — say yes if you want to redact text without switching to a browser tab.

## What to expect the first time

**The first run downloads about 1.5 GB and takes 5 to 15 minutes.** Most of that is
`en_core_web_lg`, the language model that recognises names and places. It looks like nothing
is happening for long stretches; that is normal.

**Every run after that starts in seconds.** The launcher remembers that setup is done and
goes straight to launching.

**The first run needs internet access** to fetch the packages and the language model. After
that, the app works with no network at all — see
[Privacy and Data Handling](Privacy-and-Data-Handling).

If you are in a hurry, or on a slow connection, add `-Small` (Windows) or `--small`
(macOS/Linux) to use a 12 MB model instead. It is fast, but it misses noticeably more names
and places. You can always re-run without the flag later to fetch the bigger one.

## Options

| What you want | Windows | macOS / Linux |
|---|---|---|
| A different port | `Start.cmd -Port 9000` | `./start.sh --port 9000` |
| Small model only, quick setup | `Start.cmd -Small` | `./start.sh --small` |
| Don't open a browser | `Start.cmd -NoBrowser` | `./start.sh --no-browser` |
| Don't install the desktop app | `Start.cmd -NoTray` | `./start.sh --no-tray` |

On Windows you can type these in a Command Prompt opened in the project folder, or make a
shortcut to `Start.cmd` and add the option to the shortcut's target.

## Stopping it

Close the launcher window, or press `Ctrl+C` in it. The desktop app, if you installed it,
keeps running in the notification area and will report the server as unreachable until you
start it again.

## Running it with Docker instead

If your machine blocks package downloads, or you would rather not install anything,
everything is packaged as a container image with both language models already inside:

```bash
docker run --rm -p 8000:8000 bhairavpardiwala/prompt-redaction-studio
```

Then open <http://localhost:8000>. That pulls about 550 MB and runs natively on both Intel
and Apple Silicon. Build arguments and a compose file are covered in the
[README](https://github.com/Bhairav-Pardiwala/PromptRedactionStudio#docker).

## Next

- [Using the Web App](Using-the-Web-App) — the three-step round trip
- [Using the Desktop App](Using-the-Desktop-App) — the same thing, by hotkey, from anywhere
- Something went wrong? [Troubleshooting](Troubleshooting)
