using System;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using PromptRedactionTray.Services;
using Xunit;

namespace PromptRedactionTray.Tests;

/// <summary>
/// Drives the real client against a real reverse proxy, rather than a stub handler.
///
/// Skipped unless PRT_E2E_URL is set, because it needs oauth2-proxy, an OIDC issuer and a
/// running instance. See docs/ADMIN.md; the harness that stands those up lives outside the
/// repository. PRT_E2E_TOKEN is a bearer token the proxy should accept.
/// </summary>
public class ProxyEndToEndTests
{
    private static string? Url => Environment.GetEnvironmentVariable("PRT_E2E_URL");

    private static string? Token => Environment.GetEnvironmentVariable("PRT_E2E_TOKEN");

    private static bool Enabled => !string.IsNullOrWhiteSpace(Url);

    private static RedactionClient Client(string? token)
    {
        var client = new RedactionClient { BaseUrl = Url! };
        if (token is not null)
        {
            client.AccessTokenProvider = (_, _) => Task.FromResult<string?>(token);
        }

        return client;
    }

    [SkippableFact]
    public async Task A_bearer_token_gets_the_client_through_the_proxy()
    {
        Skip.IfNot(Enabled, "PRT_E2E_URL not set");

        using var client = Client(Token);

        Assert.True(await client.IsReachableAsync(), "health check failed through the proxy");

        var policy = await client.GetPolicyAsync();
        Assert.NotNull(policy);

        var result = await client.RedactAsync(
            "Email Jane Doe at jane.doe@example.com about card 4111 1111 1111 1111.", null);

        Assert.DoesNotContain("jane.doe@example.com", result.RedactedText);
        Assert.DoesNotContain("Jane Doe", result.RedactedText);
        Assert.True(result.AppliedCount > 0);
        // store_session is always false, so the mapping comes back to the client.
        Assert.NotEmpty(result.Mapping);
        Assert.Null(result.SessionId);
    }

    [SkippableFact]
    public async Task Without_a_token_the_proxy_refuses_and_the_client_says_something_useful()
    {
        Skip.IfNot(Enabled, "PRT_E2E_URL not set");

        using var client = Client(null);

        // The tray must not report a healthy instance it cannot actually use.
        Assert.False(await client.IsReachableAsync());

        var exc = await Assert.ThrowsAsync<RedactionClientException>(
            () => client.RedactAsync("Email jane.doe@example.com", null));

        // Whatever the proxy's rejection looks like, the message has to point at sign-in
        // rather than at a parser.
        Assert.DoesNotContain("invalid start of a value", exc.Message);
        Assert.Contains("sign-in", exc.Message, StringComparison.OrdinalIgnoreCase);
    }
}
