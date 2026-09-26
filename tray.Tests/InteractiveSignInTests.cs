using System;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;
using PromptRedactionTray.Services;
using Xunit;
using Xunit.Abstractions;

namespace PromptRedactionTray.Tests;

/// <summary>
/// The desktop client's real sign-in against a real identity provider, through a real
/// reverse proxy. A browser opens and a person has to complete the sign-in, so this runs
/// only when PRT_E2E_INTERACTIVE=1 -- never in CI.
///
///   PRT_E2E_URL        the proxy in front of the instance
///   PRT_E2E_ISSUER     e.g. https://login.microsoftonline.com/{tenant}/v2.0
///   PRT_E2E_CLIENT_ID  the desktop client's registration
///   PRT_E2E_SCOPE      e.g. api://{api-client-id}/redact
///
/// The tokens stay inside this process. Only the claims that decide whether a proxy
/// accepts them are printed -- never the token, and never who the user is.
/// </summary>
public class InteractiveSignInTests
{
    private readonly ITestOutputHelper _output;

    public InteractiveSignInTests(ITestOutputHelper output) => _output = output;

    private static string? Env(string name) => Environment.GetEnvironmentVariable(name);

    [SkippableFact]
    public async Task Sign_in_then_redact_through_the_proxy_then_refresh()
    {
        Skip.IfNot(Env("PRT_E2E_INTERACTIVE") == "1", "PRT_E2E_INTERACTIVE not set");

        var oidc = new OidcClient(Env("PRT_E2E_ISSUER")!, Env("PRT_E2E_CLIENT_ID")!, Env("PRT_E2E_SCOPE"))
        {
            SignInTimeout = TimeSpan.FromMinutes(4),
        };

        _output.WriteLine("Opening the browser -- complete the sign-in there.");
        var tokens = await oidc.SignInAsync(BrowserLauncher.Open);
        _output.WriteLine("Signed in. Refresh token issued: " + (tokens.RefreshToken is not null));
        Describe("access token", tokens.AccessToken);

        using var client = new RedactionClient { BaseUrl = Env("PRT_E2E_URL")! };
        client.AccessTokenProvider = (_, _) => Task.FromResult<string?>(tokens.AccessToken);

        Assert.True(await client.IsReachableAsync(), "the proxy did not accept the token for /api/health");

        var result = await client.RedactAsync(
            "Email Jane Doe at jane.doe@example.com about card 4111 1111 1111 1111.", null);
        _output.WriteLine("Redacted through the proxy: " + result.RedactedText);
        Assert.DoesNotContain("jane.doe@example.com", result.RedactedText);
        Assert.NotEmpty(result.Mapping);

        // The scope fix: an API scope must still earn a refresh token, and it must work.
        Assert.NotNull(tokens.RefreshToken);
        var renewed = await oidc.RefreshAsync(tokens.RefreshToken!);
        Describe("refreshed token", renewed.AccessToken);
        client.AccessTokenProvider = (_, _) => Task.FromResult<string?>(renewed.AccessToken);
        Assert.True(await client.IsReachableAsync(), "the proxy did not accept the refreshed token");
        _output.WriteLine("Refreshed token accepted by the proxy.");
    }

    private void Describe(string label, string jwt)
    {
        var parts = jwt.Split('.');
        if (parts.Length < 2)
        {
            _output.WriteLine(label + ": not a JWT");
            return;
        }

        var payload = parts[1].Replace('-', '+').Replace('_', '/');
        payload += new string('=', (4 - payload.Length % 4) % 4);
        using var doc = JsonDocument.Parse(Encoding.UTF8.GetString(Convert.FromBase64String(payload)));
        var root = doc.RootElement;

        string Claim(string name) => root.TryGetProperty(name, out var v) ? v.ToString() : "(absent)";

        _output.WriteLine($"{label}: iss={Claim("iss")}");
        _output.WriteLine($"{label}: aud={Claim("aud")} ver={Claim("ver")} scp={Claim("scp")} azp={Claim("azp")}");
    }
}
