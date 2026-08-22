using PromptRedactionTray.Services;
using SharpHook.Native;
using Xunit;

namespace PromptRedactionTray.Tests;

public class HotkeyComboTests
{
    [Theory]
    [InlineData("Ctrl+Alt+R")]
    [InlineData("ctrl+alt+r")]
    [InlineData("Ctrl + Alt + R")]
    [InlineData("Control+Alt+R")]
    public void Accepts_the_expected_spellings(string text)
    {
        Assert.True(HotkeyCombo.TryParse(text, out var combo));
        Assert.True(combo.Ctrl);
        Assert.True(combo.Alt);
        Assert.False(combo.Shift);
        Assert.Equal(KeyCode.VcR, combo.Key);
    }

    [Theory]
    [InlineData("R")]                 // no modifier would swallow ordinary typing
    [InlineData("Ctrl")]              // modifier with no key
    [InlineData("Ctrl+Alt+R+T")]      // two non-modifier keys
    [InlineData("Ctrl+Nonsense")]
    [InlineData("")]
    [InlineData(null)]
    public void Rejects_invalid_combinations(string? text)
    {
        Assert.False(HotkeyCombo.TryParse(text, out _));
    }

    [Fact]
    public void Matches_requires_exactly_the_declared_modifiers()
    {
        Assert.True(HotkeyCombo.TryParse("Ctrl+Alt+R", out var combo));

        Assert.True(combo.Matches(KeyCode.VcR, ModifierMask.LeftCtrl | ModifierMask.LeftAlt));
        Assert.True(combo.Matches(KeyCode.VcR, ModifierMask.RightCtrl | ModifierMask.RightAlt));

        // Wrong key, missing modifier, and an extra modifier must all fail.
        Assert.False(combo.Matches(KeyCode.VcU, ModifierMask.LeftCtrl | ModifierMask.LeftAlt));
        Assert.False(combo.Matches(KeyCode.VcR, ModifierMask.LeftCtrl));
        Assert.False(combo.Matches(
            KeyCode.VcR,
            ModifierMask.LeftCtrl | ModifierMask.LeftAlt | ModifierMask.LeftShift));
    }

    [Fact]
    public void Round_trips_through_ToString()
    {
        Assert.True(HotkeyCombo.TryParse("Ctrl+Shift+U", out var combo));
        Assert.Equal("Ctrl+Shift+U", combo.ToString());
    }
}
