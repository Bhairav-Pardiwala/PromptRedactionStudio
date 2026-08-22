using System;
using System.Diagnostics;
using System.Runtime.InteropServices;

namespace PromptRedactionTray.Services;

/// <summary>
/// Start-with-sign-in, via the per-user Run key on Windows.
///
/// Deliberately HKEY_CURRENT_USER rather than HKEY_LOCAL_MACHINE: this needs no elevation,
/// and an IT-managed rollout would set autostart through its own deployment tooling anyway.
/// Other platforms are a no-op for now rather than a failure.
/// </summary>
public static class Startup
{
    private const string RunKey = @"Software\Microsoft\Windows\CurrentVersion\Run";
    private const string ValueName = "PromptRedactionTray";

    public static void Apply(bool enabled)
    {
        if (!RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
        {
            return;
        }

        var executable = Environment.ProcessPath;
        if (string.IsNullOrEmpty(executable))
        {
            executable = Process.GetCurrentProcess().MainModule?.FileName;
        }

        if (string.IsNullOrEmpty(executable))
        {
            throw new InvalidOperationException("Could not determine this program's path.");
        }

#pragma warning disable CA1416 // guarded by the platform check above
        using var key = Microsoft.Win32.Registry.CurrentUser.CreateSubKey(RunKey, writable: true)
                        ?? throw new InvalidOperationException("Could not open the Run registry key.");

        if (enabled)
        {
            key.SetValue(ValueName, "\"" + executable + "\"");
        }
        else
        {
            key.DeleteValue(ValueName, throwOnMissingValue: false);
        }
#pragma warning restore CA1416
    }
}
