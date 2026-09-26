using System;
using System.Collections.Generic;
using System.IO;
using PromptRedactionTray.Services;
using Xunit;

namespace PromptRedactionTray.Tests;

/// <summary>
/// Configuration layering, which is what lets an administrator deploy one file to a
/// fleet instead of talking every employee through the Settings window.
/// </summary>
public class AppSettingsTests : IDisposable
{
    private readonly string _root;
    private readonly SettingsPaths _paths;

    public AppSettingsTests()
    {
        _root = Path.Combine(Path.GetTempPath(), "prt-settings-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_root);
        _paths = new SettingsPaths
        {
            UserFile = Path.Combine(_root, "settings.json"),
            ManagedFile = Path.Combine(_root, "managed.json"),
        };
    }

    public void Dispose()
    {
        try { Directory.Delete(_root, recursive: true); } catch (IOException) { }
    }

    private void WriteUser(string json) => File.WriteAllText(_paths.UserFile, json);

    private void WriteManaged(string json) => File.WriteAllText(_paths.ManagedFile!, json);

    private static Dictionary<string, string?> Env(params (string Name, string Value)[] pairs)
    {
        var dict = new Dictionary<string, string?>();
        foreach (var (name, value) in pairs)
        {
            dict[name] = value;
        }

        return dict;
    }

    [Fact]
    public void Nothing_configured_gives_the_built_in_defaults()
    {
        var result = AppSettings.Load(_paths, Env());

        Assert.Equal("http://127.0.0.1:8000", result.Settings.InstanceUrl);
        Assert.Null(result.Settings.ApiKey);
        Assert.Empty(result.Problems);
        Assert.Empty(result.ManagedFields);
    }

    [Fact]
    public void The_managed_file_wins_over_the_user_file()
    {
        WriteUser("{\"InstanceUrl\":\"http://localhost:9999\"}");
        WriteManaged("{\"InstanceUrl\":\"https://redaction.corp.example.com\"}");

        var result = AppSettings.Load(_paths, Env());

        Assert.Equal("https://redaction.corp.example.com", result.Settings.InstanceUrl);
        Assert.Contains("InstanceUrl", result.ManagedFields);
    }

    [Fact]
    public void Merging_is_per_field_so_a_partial_managed_file_leaves_the_rest_alone()
    {
        WriteUser("{\"InstanceUrl\":\"http://localhost:9999\",\"RedactHotkey\":\"Ctrl+Alt+P\"}");
        WriteManaged("{\"InstanceUrl\":\"https://redaction.corp.example.com\"}");

        var result = AppSettings.Load(_paths, Env());

        // The administrator pinned the URL and said nothing about hotkeys.
        Assert.Equal("https://redaction.corp.example.com", result.Settings.InstanceUrl);
        Assert.Equal("Ctrl+Alt+P", result.Settings.RedactHotkey);
        Assert.DoesNotContain("RedactHotkey", result.ManagedFields);
    }

    [Fact]
    public void Environment_variables_sit_above_the_user_file_and_below_the_managed_one()
    {
        WriteUser("{\"InstanceUrl\":\"http://localhost:9999\"}");

        var overridden = AppSettings.Load(_paths, Env(("PRT_INSTANCE_URL", "http://from-env:8000")));
        Assert.Equal("http://from-env:8000", overridden.Settings.InstanceUrl);

        WriteManaged("{\"InstanceUrl\":\"https://managed.example.com\"}");
        var managed = AppSettings.Load(_paths, Env(("PRT_INSTANCE_URL", "http://from-env:8000")));
        Assert.Equal("https://managed.example.com", managed.Settings.InstanceUrl);
    }

    [Fact]
    public void Identity_provider_settings_come_through_every_layer()
    {
        WriteManaged(
            "{\"OidcIssuer\":\"https://login.microsoftonline.com/tenant/v2.0\"," +
            "\"OidcClientId\":\"client-123\",\"OidcScope\":\"api://redaction/.default\"}");

        var result = AppSettings.Load(_paths, Env());

        Assert.Equal("https://login.microsoftonline.com/tenant/v2.0", result.Settings.OidcIssuer);
        Assert.Equal("client-123", result.Settings.OidcClientId);
        Assert.True(result.Settings.UsesOidc);
    }

    [Fact]
    public void An_instance_without_an_issuer_is_not_treated_as_using_oidc()
    {
        WriteManaged("{\"OidcClientId\":\"client-123\"}");

        var result = AppSettings.Load(_paths, Env());

        Assert.False(result.Settings.UsesOidc);
    }

    [Fact]
    public void Open_web_ui_falls_back_to_the_instance_url()
    {
        // A single server serves both the API and the website, so nothing changes there.
        WriteManaged("{\"InstanceUrl\":\"https://redaction.corp.example.com\"}");

        var settings = AppSettings.Load(_paths, Env()).Settings;

        Assert.Null(settings.WebUiUrl);
        Assert.Equal("https://redaction.corp.example.com", settings.EffectiveWebUiUrl);
    }

    [Fact]
    public void Open_web_ui_uses_its_own_address_when_one_is_set()
    {
        // Behind an SSO proxy the browser and the desktop client reach the server at
        // different addresses. Sending a browser to the bearer-only one is a dead end.
        WriteManaged(
            "{\"InstanceUrl\":\"https://redaction-api.corp.example.com\"," +
            "\"WebUiUrl\":\"https://redaction.corp.example.com\"}");

        var settings = AppSettings.Load(_paths, Env()).Settings;

        Assert.Equal("https://redaction.corp.example.com", settings.EffectiveWebUiUrl);
        Assert.Contains("WebUiUrl", settings.ManagedFields);
    }

    [Fact]
    public void The_web_ui_address_can_come_from_the_environment()
    {
        var settings = AppSettings.Load(_paths, Env(("PRT_WEB_UI_URL", "https://web.example.com"))).Settings;
        Assert.Equal("https://web.example.com", settings.EffectiveWebUiUrl);
    }

    [Fact]
    public void A_corrupt_file_is_reported_rather_than_silently_ignored()
    {
        WriteUser("{ this is not json");

        var result = AppSettings.Load(_paths, Env());

        // The old behaviour was to swallow this and quietly run on defaults, which looks
        // exactly like working until someone presses the hotkey expecting protection.
        var problem = Assert.Single(result.Problems);
        Assert.Contains("settings.json", problem);
        Assert.Equal("http://127.0.0.1:8000", result.Settings.InstanceUrl);
    }

    [Fact]
    public void A_corrupt_managed_file_does_not_stop_the_user_file_being_used()
    {
        WriteUser("{\"InstanceUrl\":\"http://localhost:9999\"}");
        WriteManaged("{{{");

        var result = AppSettings.Load(_paths, Env());

        Assert.Single(result.Problems);
        Assert.Equal("http://localhost:9999", result.Settings.InstanceUrl);
    }

    [Fact]
    public void Saving_never_writes_a_managed_value_into_the_user_file()
    {
        WriteManaged("{\"InstanceUrl\":\"https://redaction.corp.example.com\"}");
        var result = AppSettings.Load(_paths, Env());

        var settings = result.Settings;
        settings.RedactHotkey = "Ctrl+Alt+J";
        settings.Save(_paths);

        var written = File.ReadAllText(_paths.UserFile);
        Assert.DoesNotContain("redaction.corp.example.com", written);
        Assert.Contains("Ctrl+Alt+J", written);

        // And removing the managed file hands control back rather than stranding the user
        // on a value they can no longer see or edit.
        File.Delete(_paths.ManagedFile!);
        var reloaded = AppSettings.Load(_paths, Env());
        Assert.Equal("http://127.0.0.1:8000", reloaded.Settings.InstanceUrl);
        Assert.Equal("Ctrl+Alt+J", reloaded.Settings.RedactHotkey);
    }

    [Fact]
    public void A_saved_file_round_trips()
    {
        var settings = new AppSettings
        {
            InstanceUrl = "https://example.com",
            ApiKey = "a-key",
            RestoreHotkey = "Ctrl+Alt+K",
            StartWithWindows = true,
            OidcIssuer = "https://issuer.example.com",
        };
        settings.Save(_paths);

        var reloaded = AppSettings.Load(_paths, Env()).Settings;

        Assert.Equal("https://example.com", reloaded.InstanceUrl);
        Assert.Equal("a-key", reloaded.ApiKey);
        Assert.Equal("Ctrl+Alt+K", reloaded.RestoreHotkey);
        Assert.True(reloaded.StartWithWindows);
        Assert.Equal("https://issuer.example.com", reloaded.OidcIssuer);
    }

    [Fact]
    public void Which_layers_were_used_is_reported_for_the_log()
    {
        WriteUser("{\"InstanceUrl\":\"http://localhost:9999\"}");
        WriteManaged("{\"OidcClientId\":\"client-123\"}");

        var result = AppSettings.Load(_paths, Env(("PRT_API_KEY", "from-env")));

        Assert.Equal(3, result.Sources.Count);
        Assert.Contains(result.Sources, s => s.Contains("managed"));
        Assert.Contains(result.Sources, s => s.Contains("environment"));
    }
}
