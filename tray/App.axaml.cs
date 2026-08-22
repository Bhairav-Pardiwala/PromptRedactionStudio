using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Markup.Xaml;
using Avalonia.Threading;
using PromptRedactionTray.Services;
using PromptRedactionTray.Views;

namespace PromptRedactionTray;

public partial class App : Application
{
    private readonly AppSettings _settings = AppSettings.Load();
    private readonly MappingStore _mappings = new();
    private readonly ClipboardService _clipboard = new();
    private readonly RedactionClient _client = new();
    private readonly HotkeyService _hotkeys = new();

    private TrayIcon? _trayIcon;
    private NativeMenuItem? _statusItem;
    private PolicyResponse? _policy;
    private bool _busy;

    public override void Initialize() => AvaloniaXamlLoader.Load(this);

    public override void OnFrameworkInitializationCompleted()
    {
        if (ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
        {
            // The app lives in the tray; closing a window must not quit it.
            desktop.ShutdownMode = ShutdownMode.OnExplicitShutdown;

            Log.Write("--- starting, instance=" + _settings.InstanceUrl + " ---");
            try
            {
                _clipboard.Initialise();
                Log.Write("Clipboard host window created");
            }
            catch (Exception exc)
            {
                Log.Write("Clipboard host FAILED: " + exc);
            }

            ApplySettings();
            BuildTrayIcon();
            Log.Write("Tray icon created");

            _hotkeys.HookFailed += message => Dispatcher.UIThread.Post(
                () => ShowToast("Hotkeys unavailable", message, isError: true));
            _hotkeys.Start();

            _ = RefreshStatusAsync();
            StartHealthPolling();

            // Lets the Settings window be opened without a mouse, for diagnosis on a
            // machine where the tray menu cannot be clicked.
            if (Environment.GetEnvironmentVariable("PRT_OPEN_SETTINGS") == "1")
            {
                Dispatcher.UIThread.Post(ShowSettings);
            }
        }

        base.OnFrameworkInitializationCompleted();
    }

    private void ApplySettings()
    {
        _client.BaseUrl = _settings.InstanceUrl;
        _client.ApiKey = _settings.ApiKey;

        var invalid = _hotkeys.SetHotkeys(
            (_settings.RedactHotkey, () => Dispatcher.UIThread.Post(() => _ = RedactClipboardAsync())),
            (_settings.RestoreHotkey, () => Dispatcher.UIThread.Post(() => _ = RestoreClipboardAsync())));

        if (invalid.Count > 0)
        {
            ShowToast(
                "Hotkey not understood",
                "Could not parse: " + string.Join(", ", invalid) + ". Check Settings.",
                isError: true);
        }
    }

    private void BuildTrayIcon()
    {
        _statusItem = new NativeMenuItem("Checking instance…") { IsEnabled = false };

        var redactItem = new NativeMenuItem("Redact clipboard");
        redactItem.Click += (_, _) => _ = RedactClipboardAsync();

        var restoreItem = new NativeMenuItem("Restore clipboard");
        restoreItem.Click += (_, _) => _ = RestoreClipboardAsync();

        var openItem = new NativeMenuItem("Open web UI");
        openItem.Click += (_, _) => OpenWebUi();

        var settingsItem = new NativeMenuItem("Settings…");
        settingsItem.Click += (_, _) => ShowSettings();

        var clearItem = new NativeMenuItem("Clear stored mappings");
        clearItem.Click += (_, _) =>
        {
            var count = _mappings.Count;
            _mappings.Clear();
            ShowToast("Mappings cleared", count + " token(s) forgotten.");
        };

        var quitItem = new NativeMenuItem("Quit");
        quitItem.Click += (_, _) => Shutdown();

        var menu = new NativeMenu
        {
            Items =
            {
                redactItem,
                restoreItem,
                new NativeMenuItemSeparator(),
                openItem,
                settingsItem,
                clearItem,
                new NativeMenuItemSeparator(),
                _statusItem,
                quitItem,
            },
        };

        _trayIcon = new TrayIcon
        {
            ToolTipText = "Prompt Redaction",
            Menu = menu,
            IsVisible = true,
            Icon = TrayIconFactory.Build(),
        };

        _trayIcon.Clicked += (_, _) => _ = RedactClipboardAsync();
    }

    // --- the two flows that matter -------------------------------------------------

    private async Task RedactClipboardAsync()
    {
        if (_busy)
        {
            return;
        }

        _busy = true;
        try
        {
            Log.Write("Redact requested");
            var text = await _clipboard.GetTextAsync();
            Log.Write("Clipboard read: " + (text is null ? "null" : text.Length + " chars"));
            if (string.IsNullOrWhiteSpace(text))
            {
                ShowToast("Nothing to redact", "The clipboard holds no text.");
                return;
            }

            _policy ??= await TryGetPolicyAsync();

            var result = await _client.RedactAsync(text, _policy);
            _mappings.Add(result.Mapping);

            // Write only after a successful call: a failed redaction must never leave the
            // clipboard empty or half-processed.
            Log.Write("Redacted: " + result.AppliedCount + " replacement(s) from " + result.Findings.Count + " finding(s), " + result.Mapping.Count + " token(s)");
            if (!await _clipboard.SetTextAsync(result.RedactedText))
            {
                Log.Write("Clipboard write FAILED");
                ShowToast("Could not write clipboard", "Another app may be holding it.", isError: true);
                return;
            }

            Log.Write("Clipboard updated");
            if (result.AppliedCount == 0)
            {
                ShowToast("Nothing detected", "The text was left unchanged.");
                return;
            }

            // Summarise what was actually replaced, not what was merely detected. The
            // analyzer reports overlapping spans (an email also matches URL), and the
            // anonymizer merges those away before rewriting.
            var groups = result.Items
                .GroupBy(item => item.EntityType)
                .OrderByDescending(g => g.Count())
                .ToList();

            var summary = string.Join(", ", groups.Take(4).Select(g => g.Count() + "x " + g.Key));
            if (groups.Count > 4)
            {
                summary += " +" + (groups.Count - 4) + " more";
            }

            ShowToast(
                "Redacted " + result.AppliedCount + " item(s)",
                summary + "\nPaste anywhere; press " + _settings.RestoreHotkey + " to restore.");
        }
        catch (RedactionClientException exc)
        {
            Log.Write("Redaction failed: " + exc.Message);
            ShowToast("Redaction failed", exc.Message, isError: true);
        }
        catch (Exception exc)
        {
            Log.Write("Unexpected error: " + exc);
            ShowToast("Unexpected error", exc.Message, isError: true);
        }
        finally
        {
            _busy = false;
        }
    }

    private async Task RestoreClipboardAsync()
    {
        if (_busy)
        {
            return;
        }

        _busy = true;
        try
        {
            var text = await _clipboard.GetTextAsync();
            if (string.IsNullOrWhiteSpace(text))
            {
                ShowToast("Nothing to restore", "The clipboard holds no text.");
                return;
            }

            var result = _mappings.Restore(text);
            if (result.Replacements == 0)
            {
                ShowToast(
                    "No tokens found",
                    _mappings.Count == 0
                        ? "No redactions are remembered yet."
                        : "This text contains none of the remembered tokens.");
                return;
            }

            if (!await _clipboard.SetTextAsync(result.Text))
            {
                ShowToast("Could not write clipboard", "Another app may be holding it.", isError: true);
                return;
            }

            ShowToast(
                "Restored " + result.DistinctTokens + " token(s)",
                result.Replacements + " occurrence(s) replaced.");
        }
        finally
        {
            _busy = false;
        }
    }

    // --- status, settings, plumbing ------------------------------------------------

    private async Task<PolicyResponse?> TryGetPolicyAsync()
    {
        try
        {
            return await _client.GetPolicyAsync();
        }
        catch (RedactionClientException)
        {
            // An older instance may not have /api/policy; server defaults still apply.
            return null;
        }
    }

    private void StartHealthPolling()
    {
        var timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(30) };
        timer.Tick += (_, _) => _ = RefreshStatusAsync();
        timer.Start();
    }

    private async Task RefreshStatusAsync()
    {
        var reachable = await _client.IsReachableAsync();
        if (_statusItem is not null)
        {
            _statusItem.Header = (reachable ? "Connected: " : "Unreachable: ") + _settings.InstanceUrl;
        }

        if (_trayIcon is not null)
        {
            _trayIcon.ToolTipText = reachable
                ? "Prompt Redaction — connected"
                : "Prompt Redaction — instance unreachable";
            _trayIcon.Icon = TrayIconFactory.Build(reachable);
        }
    }

    private void ShowSettings()
    {
        // A throw here would otherwise reach the UI thread unhandled and kill the whole
        // app -- taking the tray icon and both hotkeys with it, from one menu click.
        try
        {
            var window = new SettingsWindow(_settings, _client);
            window.Saved += () =>
            {
                _settings.Save();
                _policy = null;   // re-fetch against whatever instance is configured now
                ApplySettings();
                _ = RefreshStatusAsync();
            };
            window.Show();
            window.Activate();
            Log.Write("Settings window opened");
        }
        catch (Exception exc)
        {
            Log.Write("Settings window FAILED: " + exc);
            ShowToast("Could not open Settings", exc.Message, isError: true);
        }
    }

    private void OpenWebUi()
    {
        try
        {
            Process.Start(new ProcessStartInfo(_settings.InstanceUrl) { UseShellExecute = true });
        }
        catch (Exception exc)
        {
            ShowToast("Could not open browser", exc.Message, isError: true);
        }
    }

    private void ShowToast(string title, string body, bool isError = false)
        => ToastWindow.Show(title, body, isError);

    private void Shutdown()
    {
        _hotkeys.Dispose();
        _clipboard.Dispose();
        _client.Dispose();
        if (_trayIcon is not null)
        {
            _trayIcon.IsVisible = false;
            _trayIcon.Dispose();
        }

        if (ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
        {
            desktop.Shutdown();
        }
    }
}
