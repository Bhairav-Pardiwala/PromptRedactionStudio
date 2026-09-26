using System;
using PromptRedactionTray.Services;
using Xunit;

namespace PromptRedactionTray.Tests;

public class BrowserLauncherTests
{
    [Theory]
    [InlineData("file:///etc/passwd")]
    [InlineData("ms-settings:privacy")]
    [InlineData("javascript:alert(1)")]
    [InlineData("/Applications/Calculator.app")]
    [InlineData("not a url")]
    public void Only_web_urls_are_ever_handed_to_the_system(string url)
    {
        // Launching an arbitrary scheme runs whatever handler the OS has registered for
        // it, on the user's behalf. The instance URL comes from a config file, so this
        // is the boundary that keeps a bad one from turning into a launched application.
        Assert.Throws<ArgumentException>(() => BrowserLauncher.Open(url));
    }

    [Theory]
    [InlineData("")]
    [InlineData("   ")]
    [InlineData(null)]
    public void An_empty_url_is_rejected_rather_than_silently_ignored(string? url)
    {
        Assert.Throws<ArgumentException>(() => BrowserLauncher.Open(url!));
    }
}
