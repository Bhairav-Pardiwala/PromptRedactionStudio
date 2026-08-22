using System;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using Xunit;

namespace PromptRedactionTray.Tests;

/// <summary>
/// A source-level guard against a mistake that cost real debugging time.
///
/// Avalonia's name generator emits an InitializeComponent that loads the XAML *and*
/// assigns every x:Name'd control to a generated field. Writing another one in the
/// code-behind — the obvious `private void InitializeComponent() => AvaloniaXamlLoader
/// .Load(this);` seen in older samples — compiles fine and shadows it, so the XAML loads
/// but every control field stays null. The failure appears far away, as a
/// NullReferenceException the first time the window is constructed.
///
/// In this app that took the whole process down from a single tray-menu click, because an
/// unhandled exception on the UI thread kills it. Cheap to assert, expensive to rediscover.
/// </summary>
public class XamlCodeBehindTests
{
    private static string TrayProjectDirectory()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !Directory.Exists(Path.Combine(dir.FullName, "tray")))
        {
            dir = dir.Parent;
        }

        Assert.NotNull(dir);
        return Path.Combine(dir!.FullName, "tray");
    }

    [Fact]
    public void No_code_behind_declares_its_own_InitializeComponent()
    {
        var files = Directory.GetFiles(TrayProjectDirectory(), "*.axaml.cs", SearchOption.AllDirectories);
        Assert.NotEmpty(files);

        var declaration = new Regex(
            @"(private|protected|public|internal)\s+void\s+InitializeComponent\s*\(",
            RegexOptions.Compiled);

        var offenders = files
            .Where(file => declaration.IsMatch(File.ReadAllText(file)))
            .Select(Path.GetFileName)
            .ToList();

        Assert.True(
            offenders.Count == 0,
            "These files declare their own InitializeComponent, which shadows Avalonia's "
            + "generated one and leaves x:Name'd controls null: " + string.Join(", ", offenders));
    }
}
