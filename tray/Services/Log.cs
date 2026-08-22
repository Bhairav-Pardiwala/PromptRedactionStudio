using System;
using System.IO;

namespace PromptRedactionTray.Services;

/// <summary>
/// A small rolling file log.
///
/// This app has no console and usually no visible window, so without a log there is no
/// way to find out why a hotkey did nothing. Only events and error messages are written
/// -- never clipboard contents, and never a token mapping.
/// </summary>
public static class Log
{
    private const long MaxBytes = 512 * 1024;
    private static readonly object Lock = new();

    public static string Path { get; } = System.IO.Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "PromptRedactionTray",
        "log.txt");

    public static void Write(string message)
    {
        try
        {
            lock (Lock)
            {
                var directory = System.IO.Path.GetDirectoryName(Path);
                if (!string.IsNullOrEmpty(directory))
                {
                    Directory.CreateDirectory(directory);
                }

                if (File.Exists(Path) && new FileInfo(Path).Length > MaxBytes)
                {
                    File.Delete(Path);
                }

                File.AppendAllText(
                    Path,
                    DateTimeOffset.Now.ToString("yyyy-MM-dd HH:mm:ss.fff") + "  " + message + Environment.NewLine);
            }
        }
        catch (Exception)
        {
            // Logging must never be the thing that breaks the app.
        }
    }
}
