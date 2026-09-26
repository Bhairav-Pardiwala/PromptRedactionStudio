using System;
using System.Collections.Generic;
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
    private static readonly SettingsLoadResult _settingsLoad = AppSettings.Load();
    private readonly AppSettings _settings = _settingsLoad.Settings;
    private readonly MappingStore _mappings = new();
    private readonly ClipboardService _clipboard = new();
    private readonly RedactionClient _client = new();
    private readonly HotkeyService _hotkeys = new();
    private readonly TokenStore _tokens = new();

    private OidcClient? _oidc;
    private NativeMenuItem? _signInItem;
    private NativeMenuItem? _openItem;
    private NativeMenu? _menu;

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
            foreach (var source in _settingsLoad.Sources)
            {
                Log.Write("Settings from " + source);
            }

            if (_settingsLoad.ManagedFields.Count > 0)
            {
                Log.Write("Managed by policy: " + string.Join(", ", _settingsLoad.ManagedFields));
            }

            foreach (var problem in _settingsLoad.Problems)
            {
                // Loud, because falling back to defaults looks identical to working.
                Log.Write("Settings problem: " + problem);
            }
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

            if (_settingsLoad.Problems.Count > 0)
            {
                // A configuration layer that could not be read is worth interrupting for:
                // the app is running on defaults, which looks the same as running correctly.
                Dispatcher.UIThread.Post(() => ShowToast(
                    "Settings could not be read",
                    string.Join(Environment.NewLine, _settingsLoad.Problems)
                        + Environment.NewLine + "Running on defaults.",
                    isError: true));
            }

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

        if (_settings.UsesOidc)
        {
            _oidc = new OidcClient(_settings.OidcIssuer!, _settings.OidcClientId!, _settings.OidcScope);
            _client.AccessTokenProvider = (force, ct) => GetAccessTokenAsync(force, ct);
            if (!_tokens.IsProtectedAtRest)
            {
                // Said out loud rather than assumed: on this platform the refresh token is
                // owner-readable on disk, not encrypted. See docs for the Keychain gap.
                Log.Write("Sign-in tokens are stored unencrypted (owner-only file) on this platform");
            }
        }
        else
        {
            _oidc = null;
            _client.AccessTokenProvider = null;
        }

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

        // The instance may have changed to or from one that signs in, so the menu has to
        // follow. Safe before the tray icon exists: it no-ops until the item is built.
        UpdateSignInItem();
    }

    /// <summary>
    /// A bearer token for the instance, renewing silently where possible. Never opens a
    /// browser on its own: an unexpected sign-in window in the middle of a hotkey press
    /// is the kind of surprise that makes people stop using the tool.
    /// </summary>
    private async Task<string?> GetAccessTokenAsync(bool forceRefresh, CancellationToken ct)
    {
        if (_oidc is null)
        {
            return null;
        }

        if (forceRefresh)
        {
            // The server rejected what we had, so the cached copy is worthless.
            _tokens.Forget();
            return null;
        }

        return await _tokens.GetAccessTokenAsync(_oidc, ct).ConfigureAwait(false);
    }

    private async Task SignInAsync()
    {
        if (_oidc is null)
        {
            ShowToast("Sign-in not configured", "This instance does not use an identity provider.");
            return;
        }

        // Deliberately no ConfigureAwait(false) anywhere in this method. It starts on the UI
        // thread from a menu click and must finish there: everything after the await
        // touches the menu and the tray icon, which Avalonia only allows from the UI
        // thread. Resuming on the thread pool is what made a successful sign-in report
        // "the calling thread cannot access this object".
        try
        {
            ShowToast("Signing in", "Complete the sign-in in your browser.");
            var tokens = await _oidc.SignInAsync(BrowserLauncher.Open);
            _tokens.Accept(tokens);
            Log.Write("Signed in; token expires " + tokens.ExpiresAt.ToString("u"));
            UpdateSignInItem();
            ShowToast("Signed in", "The hotkeys are ready to use.");
            await RefreshStatusAsync();
        }
        catch (OidcException exc)
        {
            Log.Write("Sign-in failed: " + exc.Message);
            ShowToast("Sign-in failed", exc.Message, isError: true);
        }
        catch (Exception exc)
        {
            Log.Write("Sign-in error: " + exc);
            ShowToast("Sign-in failed", exc.Message, isError: true);
        }
    }

    private void SignOut()
    {
        _tokens.Forget();
        UpdateSignInItem();
        ShowToast("Signed out", "You will be asked to sign in on the next redaction.");
        Log.Write("Signed out");
    }

    private void UpdateSignInItem()
    {
        if (_signInItem is null || _menu is null || _openItem is null)
        {
            return;
        }

        // Added or removed rather than hidden: IsVisible is only as reliable as each
        // platform's native menu, and this one is a Win32 menu here and an NSMenu on macOS.
        MenuLayout.SyncOptionalItem<NativeMenuItemBase>(
            _menu.Items, _signInItem, _openItem, _settings.UsesOidc);
        _signInItem.Header = _tokens.HasSession ? "Sign out" : "Sign in…";
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
        _openItem = openItem;

        _signInItem = new NativeMenuItem("Sign in…");
        _signInItem.Click += (_, _) =>
        {
            if (_tokens.HasSession)
            {
                SignOut();
            }
            else
            {
                _ = SignInAsync();
            }
        };

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

        _menu = menu;
        UpdateSignInItem();   // inserts "Sign in…" after "Open web UI" only when sign-in is configured

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
            var window = new SettingsWindow(
                _settings, _client, _tokens.HasSession, _tokens.IsProtectedAtRest);
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
            BrowserLauncher.Open(_settings.EffectiveWebUiUrl);
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
