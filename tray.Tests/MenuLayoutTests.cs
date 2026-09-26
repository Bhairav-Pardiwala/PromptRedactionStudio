using System.Collections.Generic;
using PromptRedactionTray.Services;
using Xunit;

namespace PromptRedactionTray.Tests;

/// <summary>
/// The sign-in item's presence in the tray menu. Once built and never added, once present
/// on instances that do not sign in: both were only visible by opening the real menu.
/// </summary>
public class MenuLayoutTests
{
    private static List<string> Menu() => new() { "Redact", "Restore", "-", "Open web UI", "Settings", "Quit" };

    [Fact]
    public void A_shown_item_is_inserted_right_after_its_anchor()
    {
        var items = Menu();
        MenuLayout.SyncOptionalItem(items, "Sign in", "Open web UI", show: true);
        Assert.Equal(new[] { "Redact", "Restore", "-", "Open web UI", "Sign in", "Settings", "Quit" }, items);
    }

    [Fact]
    public void An_item_that_should_not_show_is_absent_rather_than_hidden()
    {
        var items = Menu();
        MenuLayout.SyncOptionalItem(items, "Sign in", "Open web UI", show: false);
        Assert.DoesNotContain("Sign in", items);
    }

    [Fact]
    public void Switching_off_removes_it_and_switching_on_again_adds_it_once()
    {
        // Settings can move an instance to or from sign-in while the app is running.
        var items = Menu();
        MenuLayout.SyncOptionalItem(items, "Sign in", "Open web UI", show: true);
        MenuLayout.SyncOptionalItem(items, "Sign in", "Open web UI", show: true);
        Assert.Single(items, i => i == "Sign in");

        MenuLayout.SyncOptionalItem(items, "Sign in", "Open web UI", show: false);
        Assert.DoesNotContain("Sign in", items);

        MenuLayout.SyncOptionalItem(items, "Sign in", "Open web UI", show: true);
        Assert.Equal(4, items.IndexOf("Sign in"));
    }

    [Fact]
    public void A_missing_anchor_appends_instead_of_throwing()
    {
        var items = new List<string> { "Quit" };
        MenuLayout.SyncOptionalItem(items, "Sign in", "Open web UI", show: true);
        Assert.Equal("Sign in", items[^1]);
    }
}
