using System;
using System.Linq;
using Avalonia.Controls;
using Avalonia.Markup.Xaml;
using Avalonia.Media;
using PromptRedactionTray.Services;

namespace PromptRedactionTray.Views;

public partial class SettingsWindow : Window
{
    private readonly AppSettings _settings;
    private readonly RedactionClient _client;

    /// <summary>Raised after the user saves, so the app can re-apply everything.</summary>
    public event Action? Saved;

    // Parameterless constructor for the XAML designer only.
    public SettingsWindow() : this(new AppSettings(), new RedactionClient())
    {
    }

    public SettingsWindow(AppSettings settings, RedactionClient client)
    {
        _settings = settings;
        _client = client;
        InitializeComponent();

        UrlBox.Text = settings.InstanceUrl;
        KeyBox.Text = settings.ApiKey ?? string.Empty;
        RedactHotkeyBox.Text = settings.RedactHotkey;
        RestoreHotkeyBox.Text = settings.RestoreHotkey;
        StartupBox.IsChecked = settings.StartWithWindows;

        TestButton.Click += async (_, _) => await TestConnectionAsync();
        SaveButton.Click += (_, _) => Save();
        CancelButton.Click += (_, _) => Close();
    }

    // No hand-written InitializeComponent here on purpose. Avalonia's name generator emits
    // one that loads the XAML *and* assigns the x:Name'd fields (UrlBox, KeyBox, ...).
    // Declaring another that only calls AvaloniaXamlLoader.Load shadows it, leaving every
    // control null -- which is what made opening Settings throw and take the app down.

    /// <summary>
    /// Validate against the real instance. A wrong URL must fail here, loudly, rather than
    /// silently at the moment someone presses the hotkey expecting to be protected.
    /// </summary>
    private async System.Threading.Tasks.Task TestConnectionAsync()
    {
        var url = (UrlBox.Text ?? string.Empty).Trim();
        if (string.IsNullOrEmpty(url))
        {
            SetResult("Enter a URL first.", ok: false);
            return;
        }

        TestButton.IsEnabled = false;
        SetResult("Checking…", ok: null);

        var previousUrl = _client.BaseUrl;
        var previousKey = _client.ApiKey;
        _client.BaseUrl = url;
        _client.ApiKey = string.IsNullOrWhiteSpace(KeyBox.Text) ? null : KeyBox.Text;

        try
        {
            if (!await _client.IsReachableAsync())
            {
                SetResult("No response from " + url, ok: false);
                return;
            }

            var policy = await _client.GetPolicyAsync();
            SetResult("Connected.", ok: true);
            PolicyText.Text =
                "engine: " + policy.Engine + "\n" +
                "threshold: " + policy.ScoreThreshold + "\n" +
                "entities: " + policy.Entities.Count + " (" +
                string.Join(", ", policy.Entities.Take(6)) +
                (policy.Entities.Count > 6 ? ", …" : "") + ")\n" +
                "default operator: " +
                (policy.DefaultOperator.TryGetValue("type", out var type) ? type : "?") + "\n" +
                "locked by policy: " + (policy.Locked ? "yes" : "no") + "\n" +
                "source: " + policy.Source;
        }
        catch (RedactionClientException exc)
        {
            SetResult(exc.Message, ok: false);
        }
        finally
        {
            // Do not commit the tested values; Save does that.
            _client.BaseUrl = previousUrl;
            _client.ApiKey = previousKey;
            TestButton.IsEnabled = true;
        }
    }

    private void SetResult(string message, bool? ok)
    {
        TestResult.Text = message;
        TestResult.Foreground = ok switch
        {
            true => new SolidColorBrush(Color.Parse("#2F6DF6")),
            false => new SolidColorBrush(Color.Parse("#C8352B")),
            _ => new SolidColorBrush(Color.Parse("#8B95A4")),
        };
    }

    private void Save()
    {
        var url = (UrlBox.Text ?? string.Empty).Trim();
        if (string.IsNullOrEmpty(url))
        {
            SetResult("A URL is required.", ok: false);
            return;
        }

        if (!Uri.TryCreate(url, UriKind.Absolute, out var parsed) ||
            (parsed.Scheme != Uri.UriSchemeHttp && parsed.Scheme != Uri.UriSchemeHttps))
        {
            SetResult("That is not a valid http(s) URL.", ok: false);
            return;
        }

        var redact = (RedactHotkeyBox.Text ?? string.Empty).Trim();
        var restore = (RestoreHotkeyBox.Text ?? string.Empty).Trim();

        if (!HotkeyCombo.TryParse(redact, out _))
        {
            SetResult("Redact hotkey is not valid (try Ctrl+Alt+R).", ok: false);
            return;
        }

        if (!HotkeyCombo.TryParse(restore, out _))
        {
            SetResult("Restore hotkey is not valid (try Ctrl+Alt+U).", ok: false);
            return;
        }

        if (string.Equals(redact, restore, StringComparison.OrdinalIgnoreCase))
        {
            SetResult("The two hotkeys must differ.", ok: false);
            return;
        }

        _settings.InstanceUrl = url;
        _settings.ApiKey = string.IsNullOrWhiteSpace(KeyBox.Text) ? null : KeyBox.Text;
        _settings.RedactHotkey = redact;
        _settings.RestoreHotkey = restore;
        _settings.StartWithWindows = StartupBox.IsChecked ?? false;

        try
        {
            Startup.Apply(_settings.StartWithWindows);
        }
        catch (Exception exc)
        {
            SetResult("Saved, but autostart failed: " + exc.Message, ok: false);
        }

        Saved?.Invoke();
        Close();
    }
}
