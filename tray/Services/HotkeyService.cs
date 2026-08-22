using System;
using System.Collections.Generic;
using System.Linq;
using SharpHook;
using SharpHook.Native;

namespace PromptRedactionTray.Services;

/// <summary>
/// Global hotkeys, via a SharpHook system-wide keyboard hook.
///
/// Avalonia's KeyGesture/KeyBinding only fire while the app has focus, which is useless
/// for a tray app whose whole point is working while another application is in front.
/// SharpHook (MIT, wrapping libuiohook) gives a cross-platform low-level hook instead.
/// </summary>
public sealed class HotkeyService : IDisposable
{
    private readonly TaskPoolGlobalHook _hook = new();
    private readonly List<Registration> _registrations = new();
    private readonly object _lock = new();
    private bool _running;

    private sealed record Registration(HotkeyCombo Combo, Action Callback);

    /// <summary>Raised when the hook itself fails, so the UI can report it instead of going quiet.</summary>
    public event Action<string>? HookFailed;

    public HotkeyService()
    {
        _hook.KeyPressed += OnKeyPressed;
    }

    public void Start()
    {
        if (_running)
        {
            return;
        }

        _running = true;
        Log.Write("Hotkey hook starting");
        _hook.RunAsync().ContinueWith(
            task =>
            {
                _running = false;
                Log.Write("Hotkey hook stopped (faulted=" + task.IsFaulted + ")");
                if (task.Exception is not null)
                {
                    HookFailed?.Invoke(
                        "The keyboard hook stopped: " + task.Exception.GetBaseException().Message);
                }
            },
            System.Threading.Tasks.TaskScheduler.Default);
    }

    /// <summary>Replace all registrations. Returns combos that could not be parsed.</summary>
    public IReadOnlyList<string> SetHotkeys(params (string Combo, Action Callback)[] hotkeys)
    {
        var invalid = new List<string>();
        lock (_lock)
        {
            _registrations.Clear();
            foreach (var (combo, callback) in hotkeys)
            {
                if (HotkeyCombo.TryParse(combo, out var parsed))
                {
                    Log.Write("Hotkey registered: " + parsed);
                    _registrations.Add(new Registration(parsed, callback));
                }
                else
                {
                    Log.Write("Could not parse hotkey: " + combo);
                    invalid.Add(combo);
                }
            }
        }

        return invalid;
    }

    private void OnKeyPressed(object? sender, KeyboardHookEventArgs e)
    {
        Registration[] snapshot;
        lock (_lock)
        {
            snapshot = _registrations.ToArray();
        }

        var mask = e.RawEvent.Mask;
        foreach (var registration in snapshot)
        {
            if (registration.Combo.Matches(e.Data.KeyCode, mask))
            {
                Log.Write("Hotkey matched: " + registration.Combo);
                // The hook thread must not be blocked; the callback marshals to the UI itself.
                registration.Callback();
                return;
            }
        }
    }

    public void Dispose()
    {
        _hook.KeyPressed -= OnKeyPressed;
        _hook.Dispose();
    }
}

/// <summary>A parsed hotkey such as "Ctrl+Alt+R".</summary>
public readonly record struct HotkeyCombo(bool Ctrl, bool Alt, bool Shift, KeyCode Key)
{
    public bool Matches(KeyCode key, ModifierMask mask)
    {
        if (key != Key)
        {
            return false;
        }

        var ctrl = mask.HasFlag(ModifierMask.LeftCtrl) || mask.HasFlag(ModifierMask.RightCtrl);
        var alt = mask.HasFlag(ModifierMask.LeftAlt) || mask.HasFlag(ModifierMask.RightAlt);
        var shift = mask.HasFlag(ModifierMask.LeftShift) || mask.HasFlag(ModifierMask.RightShift);

        return ctrl == Ctrl && alt == Alt && shift == Shift;
    }

    public static bool TryParse(string? text, out HotkeyCombo combo)
    {
        combo = default;
        if (string.IsNullOrWhiteSpace(text))
        {
            return false;
        }

        var parts = text.Split('+', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
        if (parts.Length == 0)
        {
            return false;
        }

        var ctrl = false;
        var alt = false;
        var shift = false;
        string? keyName = null;

        foreach (var part in parts)
        {
            switch (part.ToLowerInvariant())
            {
                case "ctrl":
                case "control":
                    ctrl = true;
                    break;
                case "alt":
                    alt = true;
                    break;
                case "shift":
                    shift = true;
                    break;
                default:
                    // Anything that is not a modifier is the key itself; only one is allowed.
                    if (keyName is not null)
                    {
                        return false;
                    }

                    keyName = part;
                    break;
            }
        }

        if (keyName is null || !TryParseKey(keyName, out var key))
        {
            return false;
        }

        // Require at least one modifier, or the hotkey would swallow ordinary typing.
        if (!ctrl && !alt && !shift)
        {
            return false;
        }

        combo = new HotkeyCombo(ctrl, alt, shift, key);
        return true;
    }

    private static bool TryParseKey(string name, out KeyCode key)
    {
        // SharpHook names keys VcA, VcF1, Vc1 and so on.
        var candidates = new[] { "Vc" + name.ToUpperInvariant(), "Vc" + name };
        foreach (var candidate in candidates)
        {
            if (Enum.TryParse(candidate, ignoreCase: true, out key))
            {
                return true;
            }
        }

        key = default;
        return false;
    }

    public override string ToString()
    {
        var parts = new List<string>();
        if (Ctrl) parts.Add("Ctrl");
        if (Alt) parts.Add("Alt");
        if (Shift) parts.Add("Shift");
        parts.Add(Key.ToString().Replace("Vc", string.Empty));
        return string.Join("+", parts);
    }
}
