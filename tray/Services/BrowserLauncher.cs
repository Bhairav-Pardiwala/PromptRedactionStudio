using System;
using System.Diagnostics;
using System.Runtime.InteropServices;

namespace PromptRedactionTray.Services;

/// <summary>
/// Opens a URL in the user's default browser.
///
/// UseShellExecute alone is not enough off Windows: on macOS and Linux .NET hands the
/// URL to the shell in ways that quietly do nothing depending on the desktop. Each
/// platform gets its own launcher instead, because sign-in cannot happen at all if the
/// browser never opens, and a silent no-op there is indistinguishable from a hang.
/// </summary>
public static class BrowserLauncher
{
    public static void Open(string url)
    {
        if (string.IsNullOrWhiteSpace(url))
        {
            throw new ArgumentException("No URL to open.", nameof(url));
        }

        // Only ever hand a browser an http(s) URL. Anything else -- file://, a local path,
        // a custom scheme -- would be launching an arbitrary handler on the user's behalf.
        if (!Uri.TryCreate(url, UriKind.Absolute, out var parsed)
            || (parsed.Scheme != Uri.UriSchemeHttp && parsed.Scheme != Uri.UriSchemeHttps))
        {
            throw new ArgumentException("Refusing to open a non-web URL: " + url, nameof(url));
        }

        var absolute = parsed.AbsoluteUri;

        if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
        {
            Start(new ProcessStartInfo(absolute) { UseShellExecute = true });
            return;
        }

        if (RuntimeInformation.IsOSPlatform(OSPlatform.OSX))
        {
            Start(new ProcessStartInfo("open", absolute) { UseShellExecute = false });
            return;
        }

        Start(new ProcessStartInfo("xdg-open", absolute) { UseShellExecute = false });
    }

    private static void Start(ProcessStartInfo info)
    {
        using var process = Process.Start(info);
        // Nothing to wait for: the browser owns the tab from here.
    }
}
