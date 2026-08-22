using System;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Media.Imaging;
using Avalonia.Platform;

namespace PromptRedactionTray.Services;

/// <summary>Tray icon bitmaps, cached because the health poll rebuilds the icon often.</summary>
public static class TrayIconFactory
{
    private static WindowIcon? _connected;
    private static WindowIcon? _offline;

    public static WindowIcon Build(bool connected = true)
    {
        if (connected)
        {
            return _connected ??= Load("tray-connected.png");
        }

        return _offline ??= Load("tray-offline.png");
    }

    private static WindowIcon Load(string fileName)
    {
        var uri = new Uri("avares://PromptRedactionTray/Assets/" + fileName);
        using var stream = AssetLoader.Open(uri);
        return new WindowIcon(new Bitmap(stream));
    }
}
