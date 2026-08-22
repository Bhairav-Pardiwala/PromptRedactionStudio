using System;
using System.Threading.Tasks;
using Avalonia.Controls;
using Avalonia.Input.Platform;

namespace PromptRedactionTray.Services;

/// <summary>
/// Clipboard access for an app that has no visible window.
///
/// Avalonia exposes IClipboard through TopLevel, so a tray-only app has nothing to obtain
/// it from -- Application.Current.Clipboard was removed in Avalonia 11. The workaround is
/// a permanently hidden window that exists purely as the clipboard host. It is shown
/// (a window that was never shown has no platform handle, and therefore no clipboard) but
/// made invisible and positioned off-screen so it never appears to the user.
/// </summary>
public sealed class ClipboardService : IDisposable
{
    private Window? _host;

    public void Initialise()
    {
        if (_host is not null)
        {
            return;
        }

        _host = new Window
        {
            Width = 1,
            Height = 1,
            WindowDecorations = WindowDecorations.None,
            ShowInTaskbar = false,
            Opacity = 0,
            Focusable = false,
            IsHitTestVisible = false,
            ShowActivated = false,
            Position = new Avalonia.PixelPoint(-32000, -32000),
        };

        _host.Show();
    }

    private IClipboard Clipboard
    {
        get
        {
            if (_host is null)
            {
                throw new InvalidOperationException("ClipboardService.Initialise() was not called.");
            }

            return TopLevel.GetTopLevel(_host)?.Clipboard
                   ?? throw new InvalidOperationException("This platform exposed no clipboard.");
        }
    }

    /// <summary>Read clipboard text, or null when it holds something that is not text.</summary>
    public async Task<string?> GetTextAsync()
    {
        try
        {
            return await Clipboard.TryGetTextAsync().ConfigureAwait(true);
        }
        catch (Exception)
        {
            // Another process can hold the clipboard open; treat that as "nothing to read"
            // rather than crashing the hotkey handler.
            return null;
        }
    }

    public async Task<bool> SetTextAsync(string text)
    {
        try
        {
            await Clipboard.SetTextAsync(text).ConfigureAwait(true);
            return true;
        }
        catch (Exception)
        {
            return false;
        }
    }

    public void Dispose()
    {
        _host?.Close();
        _host = null;
    }
}
