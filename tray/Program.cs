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

        // With no console, an unhandled exception would otherwise vanish silently and the
        // tray icon would just disappear. Log it so there is something to diagnose from.
        AppDomain.CurrentDomain.UnhandledException += (_, e) =>
            Services.Log.Write("FATAL unhandled: " + e.ExceptionObject);
        System.Threading.Tasks.TaskScheduler.UnobservedTaskException += (_, e) =>
        {
            Services.Log.Write("Unobserved task exception: " + e.Exception);
            e.SetObserved();
        };

        try
        {
            BuildAvaloniaApp().StartWithClassicDesktopLifetime(args);
        }
        catch (Exception exc)
        {
            Services.Log.Write("FATAL during startup or run: " + exc);
            throw;
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
