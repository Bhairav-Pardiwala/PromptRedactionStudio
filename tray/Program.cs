using System;
using System.Threading;
using Avalonia;

namespace PromptRedactionTray;

internal static class Program
{
    // A second tray icon and a second set of global hotkeys would fight each other, so
    // only one instance may run. The mutex is per-user, matching where settings live.
    private static Mutex? _instanceMutex;

    [STAThread]
    public static void Main(string[] args)
    {
        _instanceMutex = new Mutex(initiallyOwned: true, "PromptRedactionTray.SingleInstance", out var isFirst);
        if (!isFirst)
        {
            Console.Error.WriteLine("Prompt Redaction Tray is already running (check the system tray).");
            return;
        }

        try
        {
            BuildAvaloniaApp().StartWithClassicDesktopLifetime(args);
        }
        finally
        {
            _instanceMutex.ReleaseMutex();
            _instanceMutex.Dispose();
        }
    }

    public static AppBuilder BuildAvaloniaApp()
        => AppBuilder.Configure<App>()
            .UsePlatformDetect()
            .WithInterFont()
            .LogToTrace();
}
