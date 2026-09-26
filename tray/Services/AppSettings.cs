using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace PromptRedactionTray.Services;

/// <summary>
/// Where each configuration layer lives. Injectable so tests never touch the real
/// machine-wide directory, which they could not write to anyway.
/// </summary>
public sealed class SettingsPaths
{
    /// <summary>Machine-wide file an administrator deploys. Null when the platform has none.</summary>
    public string? ManagedFile { get; init; }

    /// <summary>Per-user file the Settings window writes.</summary>
    public required string UserFile { get; init; }

    public static SettingsPaths Default { get; } = BuildDefault();

    private static SettingsPaths BuildDefault()
    {
        // Unchanged from before, so existing installs keep their settings.
        var userFile = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
            "PromptRedactionTray",
            "settings.json");

        // Each platform's conventional location for administrator-deployed configuration.
        // .NET maps CommonApplicationData to /usr/share off Windows, which is not where
        // anyone looks, so the Unix paths are spelled out rather than derived.
        string managedDirectory;
        if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
        {
            managedDirectory = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData),
                "PromptRedactionTray");
        }
        else if (RuntimeInformation.IsOSPlatform(OSPlatform.OSX))
        {
            managedDirectory = "/Library/Application Support/PromptRedactionTray";
        }
        else
        {
            managedDirectory = "/etc/promptredactiontray";
        }

        return new SettingsPaths
        {
            UserFile = userFile,
            ManagedFile = Path.Combine(managedDirectory, "managed.json"),
        };
    }
}

/// <summary>What a load produced, including what went wrong on the way.</summary>
public sealed class SettingsLoadResult
{
    public required AppSettings Settings { get; init; }

    /// <summary>Field names supplied by the machine-wide file. The user file never overrides these.</summary>
    public required IReadOnlySet<string> ManagedFields { get; init; }

    /// <summary>
    /// Readable descriptions of any layer that could not be used. Never empty silently:
    /// a settings file that fails to parse used to leave the app pointing at localhost
    /// with no indication, which looks exactly like working.
    /// </summary>
    public required IReadOnlyList<string> Problems { get; init; }

    /// <summary>Which layers actually contributed, most significant last. For the log.</summary>
    public required IReadOnlyList<string> Sources { get; init; }
}

/// <summary>
/// User settings, resolved from three layers so a fleet can be configured centrally:
///
///   1. machine-wide managed.json  (an administrator deploys it; wins outright)
///   2. PRT_* environment variables
///   3. the per-user settings.json the Settings window writes
///
/// Merging is per field, not per file: a managed file naming only the instance URL
/// leaves every other setting to the user.
///
/// Only configuration lives here -- never a token mapping. Those stay in memory in
/// <see cref="MappingStore"/>, because writing them to disk would leave the key to a
/// redaction lying around after the app closes.
/// </summary>
public sealed class AppSettings
{
    public string InstanceUrl { get; set; } = "http://127.0.0.1:8000";

    /// <summary>
    /// Where "Open web UI" goes, when that is not the instance URL. Behind an SSO proxy the
    /// browser and the desktop client usually reach the server at different addresses --
    /// one proxy doing interactive sign-in, one accepting bearer tokens -- and sending a
    /// browser to the bearer-only address lands it on a sign-in that cannot complete.
    /// </summary>
    public string? WebUiUrl { get; set; }

    /// <summary>The address "Open web UI" should open: <see cref="WebUiUrl"/>, else the instance.</summary>
    [JsonIgnore]
    public string EffectiveWebUiUrl =>
        string.IsNullOrWhiteSpace(WebUiUrl) ? InstanceUrl : WebUiUrl!;

    /// <summary>Optional shared key, only needed when the instance sets REDACTION_API_KEY.</summary>
    public string? ApiKey { get; set; }

    public string RedactHotkey { get; set; } = "Ctrl+Alt+R";

    public string RestoreHotkey { get; set; } = "Ctrl+Alt+U";

    public bool StartWithWindows { get; set; }

    // --- identity provider, for instances behind an OAuth reverse proxy ---------------
    // None of these are secrets: a desktop app is a public client, so the client id is
    // published in every authorization request and recoverable from the binary anyway.
    // They are here because they have to reach each machine, not because they are private.

    /// <summary>OIDC issuer, e.g. https://login.microsoftonline.com/{tenant}/v2.0</summary>
    public string? OidcIssuer { get; set; }

    /// <summary>This application's client id in the identity provider.</summary>
    public string? OidcClientId { get; set; }

    /// <summary>Scope identifying the API the token is for; must match what the proxy accepts.</summary>
    public string? OidcScope { get; set; }

    /// <summary>True when the instance is configured to sign in rather than use a shared key.</summary>
    [JsonIgnore]
    public bool UsesOidc =>
        !string.IsNullOrWhiteSpace(OidcIssuer) && !string.IsNullOrWhiteSpace(OidcClientId);

    /// <summary>Fields the machine-wide file supplied; <see cref="Save"/> will not write them back.</summary>
    [JsonIgnore]
    public IReadOnlySet<string> ManagedFields { get; internal set; } = new HashSet<string>();

    public bool IsManaged(string field) => ManagedFields.Contains(field);

    /// <summary>The per-user file. Kept for callers that only want the path.</summary>
    [JsonIgnore]
    public static string ConfigPath => SettingsPaths.Default.UserFile;

    private static readonly JsonSerializerOptions ReadOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        ReadCommentHandling = JsonCommentHandling.Skip,
        AllowTrailingCommas = true,
    };

    private static readonly JsonSerializerOptions WriteOptions = new()
    {
        WriteIndented = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
        // The default encoder escapes '+' as \u002B, so every hotkey would be written
        // as "Ctrl\u002BAlt\u002BR". These files are read and edited by administrators,
        // so they should be legible. There is no HTML context here for the strict
        // encoder to protect against.
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    /// <summary>One layer's worth of settings. Every field nullable, so "absent" and
    /// "explicitly set to the default" stay distinguishable while merging.</summary>
    private sealed class Layer
    {
        public string? InstanceUrl { get; set; }
        public string? WebUiUrl { get; set; }
        public string? ApiKey { get; set; }
        public string? RedactHotkey { get; set; }
        public string? RestoreHotkey { get; set; }
        public bool? StartWithWindows { get; set; }
        public string? OidcIssuer { get; set; }
        public string? OidcClientId { get; set; }
        public string? OidcScope { get; set; }
    }

    public static SettingsLoadResult Load(
        SettingsPaths? paths = null,
        IReadOnlyDictionary<string, string?>? environment = null)
    {
        paths ??= SettingsPaths.Default;
        var problems = new List<string>();
        var sources = new List<string>();
        var settings = new AppSettings();
        var managed = new HashSet<string>(StringComparer.Ordinal);

        // Least significant first; each later layer overwrites the fields it names.
        var user = ReadFile(paths.UserFile, problems);
        if (user is not null)
        {
            Apply(settings, user, null);
            sources.Add("user file " + paths.UserFile);
        }

        var fromEnvironment = ReadEnvironment(environment);
        if (fromEnvironment is not null)
        {
            Apply(settings, fromEnvironment, null);
            sources.Add("PRT_* environment variables");
        }

        if (paths.ManagedFile is not null)
        {
            var machine = ReadFile(paths.ManagedFile, problems);
            if (machine is not null)
            {
                Apply(settings, machine, managed);
                sources.Add("managed file " + paths.ManagedFile);
            }
        }

        settings.ManagedFields = managed;

        return new SettingsLoadResult
        {
            Settings = settings,
            ManagedFields = managed,
            Problems = problems,
            Sources = sources,
        };
    }

    /// <summary>Read one JSON layer. A missing file is normal; an unreadable one is a problem.</summary>
    private static Layer? ReadFile(string path, List<string> problems)
    {
        try
        {
            if (!File.Exists(path))
            {
                return null;
            }

            var layer = JsonSerializer.Deserialize<Layer>(File.ReadAllText(path), ReadOptions);
            if (layer is null)
            {
                problems.Add(path + " is empty, so it was ignored.");
            }

            return layer;
        }
        catch (Exception exc)
        {
            // Reported rather than swallowed. Falling back to defaults silently means the
            // user presses the hotkey believing they are protected by a configured instance.
            problems.Add("Could not read " + path + ": " + exc.Message);
            return null;
        }
    }

    private static Layer? ReadEnvironment(IReadOnlyDictionary<string, string?>? environment)
    {
        string? Read(string name)
        {
            if (environment is not null)
            {
                return environment.TryGetValue(name, out var value) ? value : null;
            }

            return Environment.GetEnvironmentVariable(name);
        }

        var layer = new Layer
        {
            InstanceUrl = Blank(Read("PRT_INSTANCE_URL")),
            WebUiUrl = Blank(Read("PRT_WEB_UI_URL")),
            ApiKey = Blank(Read("PRT_API_KEY")),
            OidcIssuer = Blank(Read("PRT_OIDC_ISSUER")),
            OidcClientId = Blank(Read("PRT_OIDC_CLIENT_ID")),
            OidcScope = Blank(Read("PRT_OIDC_SCOPE")),
        };

        var empty = layer.InstanceUrl is null && layer.WebUiUrl is null
            && layer.ApiKey is null && layer.OidcIssuer is null
            && layer.OidcClientId is null && layer.OidcScope is null;
        return empty ? null : layer;

        static string? Blank(string? value) => string.IsNullOrWhiteSpace(value) ? null : value;
    }

    /// <summary>Copy the fields this layer names onto the running settings.</summary>
    private static void Apply(AppSettings target, Layer layer, HashSet<string>? markManaged)
    {
        void Set(string name, Action assign, bool present)
        {
            if (!present)
            {
                return;
            }

            assign();
            markManaged?.Add(name);
        }

        Set(nameof(InstanceUrl), () => target.InstanceUrl = layer.InstanceUrl!, layer.InstanceUrl is not null);
        Set(nameof(WebUiUrl), () => target.WebUiUrl = layer.WebUiUrl, layer.WebUiUrl is not null);
        Set(nameof(ApiKey), () => target.ApiKey = layer.ApiKey, layer.ApiKey is not null);
        Set(nameof(RedactHotkey), () => target.RedactHotkey = layer.RedactHotkey!, layer.RedactHotkey is not null);
        Set(nameof(RestoreHotkey), () => target.RestoreHotkey = layer.RestoreHotkey!, layer.RestoreHotkey is not null);
        Set(nameof(StartWithWindows), () => target.StartWithWindows = layer.StartWithWindows!.Value, layer.StartWithWindows is not null);
        Set(nameof(OidcIssuer), () => target.OidcIssuer = layer.OidcIssuer, layer.OidcIssuer is not null);
        Set(nameof(OidcClientId), () => target.OidcClientId = layer.OidcClientId, layer.OidcClientId is not null);
        Set(nameof(OidcScope), () => target.OidcScope = layer.OidcScope, layer.OidcScope is not null);
    }

    /// <summary>
    /// Write the per-user file, omitting anything the machine-wide file supplied so a
    /// managed value is never copied into a place the user could later edit.
    /// </summary>
    public void Save(SettingsPaths? paths = null)
    {
        paths ??= SettingsPaths.Default;
        var path = paths.UserFile;

        var directory = Path.GetDirectoryName(path);
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }

        var layer = new Layer
        {
            InstanceUrl = IsManaged(nameof(InstanceUrl)) ? null : InstanceUrl,
            WebUiUrl = IsManaged(nameof(WebUiUrl)) ? null : WebUiUrl,
            ApiKey =IsManaged(nameof(ApiKey)) ? null : ApiKey,
            RedactHotkey = IsManaged(nameof(RedactHotkey)) ? null : RedactHotkey,
            RestoreHotkey = IsManaged(nameof(RestoreHotkey)) ? null : RestoreHotkey,
            StartWithWindows = IsManaged(nameof(StartWithWindows)) ? null : StartWithWindows,
            OidcIssuer = IsManaged(nameof(OidcIssuer)) ? null : OidcIssuer,
            OidcClientId = IsManaged(nameof(OidcClientId)) ? null : OidcClientId,
            OidcScope = IsManaged(nameof(OidcScope)) ? null : OidcScope,
        };

        File.WriteAllText(path, JsonSerializer.Serialize(layer, WriteOptions));
    }
}
