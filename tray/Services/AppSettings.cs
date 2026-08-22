using System;
using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace PromptRedactionTray.Services;

/// <summary>
/// User settings, persisted to the per-user config directory.
///
/// Only configuration lives here -- never a token mapping. Those stay in memory in
/// <see cref="MappingStore"/>, because writing them to disk would leave the key to a
/// redaction lying around after the app closes.
/// </summary>
public sealed class AppSettings
{
    public string InstanceUrl { get; set; } = "http://127.0.0.1:8000";

    /// <summary>Optional shared key, only needed when the instance sets REDACTION_API_KEY.</summary>
    public string? ApiKey { get; set; }

    public string RedactHotkey { get; set; } = "Ctrl+Alt+R";

    public string RestoreHotkey { get; set; } = "Ctrl+Alt+U";

    public bool StartWithWindows { get; set; }

    [JsonIgnore]
    public static string ConfigPath
    {
        get
        {
            var dir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "PromptRedactionTray");
            return Path.Combine(dir, "settings.json");
        }
    }

    public static AppSettings Load()
    {
        try
        {
            if (File.Exists(ConfigPath))
            {
                var json = File.ReadAllText(ConfigPath);
                var loaded = JsonSerializer.Deserialize<AppSettings>(json);
                if (loaded is not null)
                {
                    return loaded;
                }
            }
        }
        catch (Exception)
        {
            // A corrupt settings file should not stop the app starting; defaults are fine.
        }

        return new AppSettings();
    }

    public void Save()
    {
        var path = ConfigPath;
        var directory = Path.GetDirectoryName(path);
        if (!string.IsNullOrEmpty(directory))
        {
            Directory.CreateDirectory(directory);
        }

        var json = JsonSerializer.Serialize(this, new JsonSerializerOptions { WriteIndented = true });
        File.WriteAllText(path, json);
    }
}
