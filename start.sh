#!/usr/bin/env bash
#
# One-step launcher for Prompt Redaction Studio (macOS and Linux).
#
# Sets up the environment if it has not been set up yet, starts the server, and opens
# the browser. Safe to run repeatedly -- after the first run it goes straight to launch.
#
#   ./start.sh                 # set up if needed, then run on port 8000
#   ./start.sh --small         # skip the 590 MB model
#   ./start.sh --port 9000
#
set -euo pipefail

cd "$(dirname "$0")"

REPO="Bhairav-Pardiwala/PromptRedactionStudio"
STAMP=".venv/.setup-stamp"
MIN_MINOR=10   # Presidio supports >=3.10,<3.15
MAX_MINOR=14

PORT=8000
SMALL=0
NO_BROWSER=0
SETUP_ONLY=0
TRAY_FLAG=""      # "yes" | "no" | "" (ask)

while [ $# -gt 0 ]; do
    case "$1" in
        --port) PORT="${2:?--port needs a value}"; shift 2 ;;
        --small) SMALL=1; shift ;;
        --no-browser) NO_BROWSER=1; shift ;;
        --setup-only) SETUP_ONLY=1; shift ;;
        --tray) TRAY_FLAG="yes"; shift ;;
        --no-tray) TRAY_FLAG="no"; shift ;;
        -h|--help)
            sed -n '3,10p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
done

if [ -t 1 ]; then
    BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; DIM=$'\033[2m'; OFF=$'\033[0m'
else
    BOLD=""; GREEN=""; YELLOW=""; RED=""; DIM=""; OFF=""
fi

step() { printf '\n%s%s%s\n' "$YELLOW" "$1" "$OFF"; }
info() { printf '      %s%s%s\n' "$DIM" "$1" "$OFF"; }
warn() { printf '      %s%s%s\n' "$YELLOW" "$1" "$OFF"; }
fail() { printf '\n%s%s%s\n' "$RED" "$1" "$OFF" >&2; }

OS="$(uname -s)"
ARCH="$(uname -m)"

# ---------------------------------------------------------------- interpreter

python_minor() {
    # Echo the minor version of a python3.x binary, or nothing if unusable.
    "$1" -c 'import sys; print(sys.version_info[1] if sys.version_info[0] == 3 else "")' 2>/dev/null || true
}

find_python() {
    # Prefer an explicitly versioned binary, highest first. A bare `python3` can be the
    # macOS Command Line Tools stub, or a distro build outside Presidio's range.
    local candidate minor
    for minor in $(seq "$MAX_MINOR" -1 "$MIN_MINOR"); do
        candidate="python3.${minor}"
        if command -v "$candidate" >/dev/null 2>&1; then
            echo "$(command -v "$candidate")"
            return 0
        fi
    done
    if command -v python3 >/dev/null 2>&1; then
        minor="$(python_minor python3)"
        if [ -n "$minor" ] && [ "$minor" -ge "$MIN_MINOR" ] && [ "$minor" -le "$MAX_MINOR" ]; then
            echo "$(command -v python3)"
            return 0
        fi
    fi
    return 1
}

explain_missing_python() {
    fail "Python 3.${MIN_MINOR}-3.${MAX_MINOR} is required and was not found."
    echo ""
    if [ "$OS" = "Darwin" ]; then
        if command -v brew >/dev/null 2>&1; then
            echo "  Install it with:"
            echo "      ${BOLD}brew install python@3.12${OFF}"
        else
            echo "  Install it from ${BOLD}https://www.python.org/downloads/${OFF}"
            echo "  (or install Homebrew first, then: brew install python@3.12)"
        fi
        echo ""
        info "A bare 'python3' on macOS may be the Xcode stub, which prompts to install"
        info "developer tools and is often too old for Presidio."
    else
        echo "  On Debian or Ubuntu:"
        echo "      ${BOLD}sudo apt install python3 python3-venv python3-pip${OFF}"
        echo "  On Fedora or RHEL:"
        echo "      ${BOLD}sudo dnf install python3 python3-pip${OFF}"
    fi
    echo ""
    echo "  Then run ./start.sh again."
}

create_venv() {
    # Separated so the python3-venv failure -- by far the most common on Debian and
    # Ubuntu -- gets a message that names the fix. Python's own error does not.
    local python="$1"
    if ! "$python" -m venv .venv 2>/tmp/prs-venv-err.$$; then
        fail "Could not create the virtual environment."
        echo ""
        sed 's/^/      /' /tmp/prs-venv-err.$$ >&2 || true
        rm -f /tmp/prs-venv-err.$$
        if [ "$OS" = "Linux" ]; then
            echo ""
            echo "  Debian and Ubuntu ship venv separately. Install it with:"
            echo "      ${BOLD}sudo apt install python3-venv${OFF}"
        fi
        exit 1
    fi
    rm -f /tmp/prs-venv-err.$$
}

# ---------------------------------------------------------------------- stamp

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'   # macOS has no sha256sum by default
    fi
}

setup_signature() {
    local model="lg"
    [ "$SMALL" -eq 1 ] && model="sm"
    echo "req=$(sha256_of requirements.txt);model=${model}"
}

stamp_get() {
    [ -f "$STAMP" ] || return 1
    local value
    value="$(grep -m1 "^$1=" "$STAMP" 2>/dev/null | cut -d= -f2-)" || return 1
    [ -n "$value" ] || return 1
    echo "$value"
}

stamp_write() {
    printf 'signature=%s\n' "$1" > "$STAMP"
    [ -n "$2" ] && printf 'tray=%s\n' "$2" >> "$STAMP"
    return 0
}

# ------------------------------------------------------------------ tray app

tray_asset() {
    case "$OS:$ARCH" in
        Darwin:arm64)   echo "PromptRedactionTray-osx-arm64.tar.gz" ;;
        Linux:x86_64)   echo "PromptRedactionTray-linux-x64" ;;
        *)              return 1 ;;
    esac
}

install_tray() {
    # Never fatal: the tray app is optional and must not stop the web app starting.
    local asset base url dist tmp expected actual
    if ! asset="$(tray_asset)"; then
        warn "No desktop app is published for ${OS} ${ARCH} -- skipping."
        info "Only macOS arm64 and Linux x86_64 builds exist. Build from source in tray/."
        return 0
    fi

    base="https://github.com/${REPO}/releases/latest/download"
    dist="dist"
    mkdir -p "$dist"
    tmp="${dist}/${asset}.partial"

    step "Downloading the desktop app..."
    info "${asset} (~97 MB)"
    if ! curl -fsSL --retry 2 -o "$tmp" "${base}/${asset}"; then
        warn "Could not download it (no published release yet, or no network) -- skipping."
        rm -f "$tmp"
        return 0
    fi

    # These binaries are not code-signed, so the checksum is the only integrity check
    # available. The release notes tell users to do exactly this by hand.
    if ! expected="$(curl -fsSL --retry 2 "${base}/SHA256SUMS" 2>/dev/null | awk -v a="$asset" '$2 == a {print $1}')" \
       || [ -z "$expected" ]; then
        warn "Could not fetch SHA256SUMS -- skipping the desktop app."
        rm -f "$tmp"
        return 0
    fi

    actual="$(sha256_of "$tmp")"
    if [ "$actual" != "$expected" ]; then
        fail "Checksum mismatch -- the download was discarded."
        info "expected $expected"
        info "got      $actual"
        rm -f "$tmp"
        return 0
    fi
    info "Checksum verified."

    if [ "$OS" = "Darwin" ]; then
        # PublishSingleFile still leaves libSkiaSharp and friends beside the binary on
        # macOS, so the release ships the whole directory as a tarball.
        local target="${dist}/PromptRedactionTray-osx-arm64"
        rm -rf "$target"
        mkdir -p "$target"
        tar -xzf "$tmp" -C "$target"
        rm -f "$tmp"
        chmod +x "${target}/PromptRedactionTray" 2>/dev/null || true
        # Downloaded binaries are quarantined; without this Gatekeeper refuses to run it.
        xattr -dr com.apple.quarantine "$target" 2>/dev/null || true
        printf '\n  %sDesktop app installed:%s\n' "$GREEN" "$OFF"
        echo "    ${target}/PromptRedactionTray"
        warn "Grant it Accessibility permission when asked:"
        warn "System Settings > Privacy & Security > Accessibility."
        info "Without that the global hotkey silently does nothing."
    else
        local target="${dist}/PromptRedactionTray-linux-x64"
        mv -f "$tmp" "$target"
        chmod +x "$target"
        printf '\n  %sDesktop app installed:%s\n' "$GREEN" "$OFF"
        echo "    ${target}"
        info "The global hotkey needs X11 input access; under Wayland it may not capture."
    fi

    info "Ctrl+Alt+R redacts the clipboard, Ctrl+Alt+U restores it."
    if [ "$PORT" != "8000" ]; then
        warn "It defaults to port 8000 -- set http://127.0.0.1:${PORT} in its Settings."
    fi
}

resolve_tray_choice() {
    # Echoes "yes", "no", or "skip" (not asked -- ask again next time).
    #
    # The answer is the only thing on stdout, because callers capture it. Everything
    # the user is meant to read goes to stderr, or the prompt would be swallowed and
    # this would look like a hang.
    if [ -n "$TRAY_FLAG" ]; then echo "$TRAY_FLAG"; return 0; fi
    if [ ! -t 0 ]; then echo "skip"; return 0; fi

    {
        printf '\n  %sOptional: the desktop app%s\n' "$BOLD" "$OFF"
        echo "  It adds a global hotkey (Ctrl+Alt+R) that redacts whatever you have"
        echo "  copied, from any application -- not just this browser tab. ~97 MB."
        printf '  Install it too? [y/N] '
    } >&2
    local answer=""
    read -r answer || true
    case "$answer" in
        y|Y|yes|YES) echo "yes" ;;
        *) echo "no" ;;
    esac
}

# ----------------------------------------------------------------------- run

printf '\n%sPrompt Redaction Studio%s\n' "$BOLD" "$OFF"
echo "======================="

SIGNATURE="$(setup_signature)"
VENV_PYTHON=".venv/bin/python"
STORED_SIGNATURE="$(stamp_get signature || true)"

if [ ! -x "$VENV_PYTHON" ] || [ "$STORED_SIGNATURE" != "$SIGNATURE" ]; then
    if [ ! -x "$VENV_PYTHON" ]; then
        if ! PYTHON="$(find_python)"; then
            explain_missing_python
            exit 1
        fi
        step "[1/3] Creating the virtual environment..."
        info "Using $PYTHON"
        create_venv "$PYTHON"
    fi

    echo ""
    printf '%sFirst run: downloading Python packages and language models.%s\n' "$YELLOW" "$OFF"
    if [ "$SMALL" -eq 1 ]; then
        info "About 700 MB, typically 3-5 minutes."
    else
        info "About 1.5 GB, typically 5-15 minutes."
        info "Most of that is en_core_web_lg. Re-run with --small to skip it."
    fi
    info "This happens once. Later runs start in seconds."

    step "[2/3] Installing dependencies..."
    "$VENV_PYTHON" -m pip install --upgrade pip --quiet
    "$VENV_PYTHON" -m pip install -r requirements.txt

    step "[3/3] Downloading language models..."
    info "en_core_web_sm (~12 MB)"
    "$VENV_PYTHON" -m spacy download en_core_web_sm
    if [ "$SMALL" -eq 1 ]; then
        info "Skipping en_core_web_lg (--small)."
    else
        info "en_core_web_lg (~590 MB -- this is the slow one)"
        "$VENV_PYTHON" -m spacy download en_core_web_lg
    fi
fi

# The tray question is asked once and remembered, so repeat launches stay quiet.
TRAY_ANSWER="$(stamp_get tray || true)"
if [ -n "$TRAY_FLAG" ] || [ -z "$TRAY_ANSWER" ]; then
    CHOICE="$(resolve_tray_choice)"
    case "$CHOICE" in
        yes) install_tray; TRAY_ANSWER="yes" ;;
        no)  TRAY_ANSWER="no" ;;
    esac
fi

stamp_write "$SIGNATURE" "$TRAY_ANSWER"

if [ "$SETUP_ONLY" -eq 1 ]; then
    printf '\n%sSetup complete. Run ./start.sh again to launch.%s\n\n' "$GREEN" "$OFF"
    exit 0
fi

# 127.0.0.1 rather than localhost, deliberately: uvicorn is bound to IPv4 only, and
# "localhost" can resolve to ::1 first, where nothing is listening.
URL="http://127.0.0.1:${PORT}"

printf '\n%sStarting on %s%s\n' "$GREEN" "$URL" "$OFF"
info "Press Ctrl+C to stop."
info "The first redaction builds the language model and takes a few seconds."

open_browser_when_ready() {
    # Wait for the port to accept connections rather than sleeping a fixed amount.
    local opener=""
    if [ "$OS" = "Darwin" ]; then opener="open"; else opener="xdg-open"; fi
    command -v "$opener" >/dev/null 2>&1 || return 0

    local i
    for i in $(seq 1 120); do
        if "$VENV_PYTHON" -c "import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('127.0.0.1', $PORT)) == 0 else 1)" 2>/dev/null; then
            "$opener" "$URL" >/dev/null 2>&1 || true
            return 0
        fi
        sleep 0.5
    done
}

if [ "$NO_BROWSER" -eq 0 ]; then
    open_browser_when_ready &
    BROWSER_PID=$!
    # Do not leave the poller running if the server dies early.
    trap 'kill "$BROWSER_PID" 2>/dev/null || true' EXIT INT TERM
fi

exec "$VENV_PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port "$PORT"
