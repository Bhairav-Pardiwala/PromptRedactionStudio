using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using PromptRedactionTray.Services;
using Xunit;

namespace PromptRedactionTray.Tests;

/// <summary>
/// Bearer authentication and the reverse-proxy failure modes it has to survive.
/// </summary>
public class BearerAuthTests
{
    /// <summary>Records what it was sent and replies with whatever the test set up.</summary>
    private sealed class RecordingHandler : HttpMessageHandler
    {
        private readonly Queue<HttpResponseMessage> _responses = new();

        public List<string?> SeenAuthorization { get; } = new();

        public List<string?> SeenApiKey { get; } = new();

        public int Calls => SeenAuthorization.Count;

        public void Enqueue(HttpResponseMessage response) => _responses.Enqueue(response);

        public RecordingHandler Then(HttpStatusCode status, string body = "{}")
        {
            _responses.Enqueue(new HttpResponseMessage(status) { Content = new StringContent(body) });
            return this;
        }

        public RecordingHandler ThenRedirect(string location)
        {
            var response = new HttpResponseMessage(HttpStatusCode.Found)
            {
                Content = new StringContent(string.Empty),
            };
            response.Headers.Location = new Uri(location);
            _responses.Enqueue(response);
            return this;
        }

        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken cancellationToken)
        {
            SeenAuthorization.Add(request.Headers.Authorization?.ToString());
            SeenApiKey.Add(request.Headers.TryGetValues("X-Redaction-Key", out var values)
                ? string.Join(",", values)
                : null);

            return Task.FromResult(_responses.Count > 0
                ? _responses.Dequeue()
                : new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("{}") });
        }
    }

    private static RedactionClient Client(RecordingHandler handler) =>
        new(new HttpClient(handler)) { BaseUrl = "https://redaction.corp.example.com" };

    [Fact]
    public async Task A_bearer_token_is_attached_when_a_provider_is_set()
    {
        var handler = new RecordingHandler().Then(HttpStatusCode.OK, "{\"redacted_text\":\"x\"}");
        using var client = Client(handler);
        client.AccessTokenProvider = (_, _) => Task.FromResult<string?>("token-1");

        await client.RedactAsync("hello", null);

        Assert.Equal("Bearer token-1", handler.SeenAuthorization[0]);
    }

    [Fact]
    public async Task The_api_key_and_a_bearer_token_can_both_be_sent()
    {
        // The proxy may want the bearer while the instance behind it still wants its key.
        var handler = new RecordingHandler().Then(HttpStatusCode.OK, "{\"redacted_text\":\"x\"}");
        using var client = Client(handler);
        client.ApiKey = "the-key";
        client.AccessTokenProvider = (_, _) => Task.FromResult<string?>("token-1");

        await client.RedactAsync("hello", null);

        Assert.Equal("Bearer token-1", handler.SeenAuthorization[0]);
        Assert.Equal("the-key", handler.SeenApiKey[0]);
    }

    [Fact]
    public async Task A_401_triggers_one_forced_refresh_and_a_retry()
    {
        var handler = new RecordingHandler()
            .Then(HttpStatusCode.Unauthorized, "{\"detail\":\"expired\"}")
            .Then(HttpStatusCode.OK, "{\"redacted_text\":\"x\"}");

        var forced = new List<bool>();
        using var client = Client(handler);
        client.AccessTokenProvider = (force, _) =>
        {
            forced.Add(force);
            return Task.FromResult<string?>(force ? "token-2" : "token-1");
        };

        var result = await client.RedactAsync("hello", null);

        Assert.Equal("x", result.RedactedText);
        Assert.Equal(new[] { false, true }, forced);
        Assert.Equal("Bearer token-1", handler.SeenAuthorization[0]);
        Assert.Equal("Bearer token-2", handler.SeenAuthorization[1]);
    }

    [Fact]
    public async Task A_second_401_gives_up_rather_than_looping()
    {
        var handler = new RecordingHandler()
            .Then(HttpStatusCode.Unauthorized, "{\"detail\":\"nope\"}")
            .Then(HttpStatusCode.Unauthorized, "{\"detail\":\"nope\"}");

        using var client = Client(handler);
        client.AccessTokenProvider = (_, _) => Task.FromResult<string?>("token");

        var exc = await Assert.ThrowsAsync<RedactionClientException>(() => client.RedactAsync("hello", null));

        Assert.Equal(2, handler.Calls);
        Assert.Contains("rejected the sign-in", exc.Message);
    }

    [Fact]
    public async Task Without_a_token_provider_a_401_is_not_retried()
    {
        var handler = new RecordingHandler().Then(HttpStatusCode.Unauthorized, "{\"detail\":\"no key\"}");
        using var client = Client(handler);
        client.ApiKey = "the-key";

        var exc = await Assert.ThrowsAsync<RedactionClientException>(() => client.RedactAsync("hello", null));

        Assert.Equal(1, handler.Calls);
        Assert.Contains("rejected the API key", exc.Message);
    }

    // --- the reverse-proxy failure modes ------------------------------------------

    [Fact]
    public async Task A_redirect_says_it_is_a_sign_in_proxy_instead_of_following_it()
    {
        // Previously the handler followed this to the IdP, got a 200 of HTML, and the
        // failure surfaced as "'<' is an invalid start of a value".
        var handler = new RecordingHandler()
            .ThenRedirect("https://login.microsoftonline.com/common/oauth2/v2.0/authorize");
        using var client = Client(handler);

        var exc = await Assert.ThrowsAsync<RedactionClientException>(() => client.RedactAsync("hello", null));

        Assert.Contains("behind a sign-in proxy", exc.Message);
        Assert.Contains("login.microsoftonline.com", exc.Message);
        Assert.Equal(1, handler.Calls);
    }

    [Fact]
    public async Task A_403_page_of_html_is_reported_as_a_sign_in_proxy_too()
    {
        // oauth2-proxy in bearer-token mode does not redirect: it answers 403 with its own
        // sign-in page. Matching only on 3xx left the user reading "Forbidden", which was
        // found by running the real client against a real oauth2-proxy.
        var handler = new RecordingHandler();
        var response = new HttpResponseMessage(HttpStatusCode.Forbidden)
        {
            Content = new StringContent(
                "<!DOCTYPE html><html><body>Sign in</body></html>",
                System.Text.Encoding.UTF8,
                "text/html"),
        };
        handler.Enqueue(response);

        using var client = Client(handler);

        var exc = await Assert.ThrowsAsync<RedactionClientException>(() => client.RedactAsync("hi", null));

        Assert.Contains("sign-in proxy", exc.Message);
        Assert.Contains("403", exc.Message);
        Assert.DoesNotContain("invalid start of a value", exc.Message);
    }

    [Fact]
    public async Task An_html_login_page_does_not_count_as_a_reachable_instance()
    {
        // A proxy can answer with a perfectly good 200 that is a login page. Reporting
        // "connected" over that is worse than reporting nothing.
        var handler = new RecordingHandler()
            .Then(HttpStatusCode.OK, "<html><body>Sign in to your account</body></html>");
        using var client = Client(handler);

        Assert.False(await client.IsReachableAsync());
    }

    [Fact]
    public async Task A_real_health_response_counts_as_reachable()
    {
        var handler = new RecordingHandler()
            .Then(HttpStatusCode.OK, "{\"status\":\"ok\",\"loaded_engines\":[]}");
        using var client = Client(handler);

        Assert.True(await client.IsReachableAsync());
    }

    [Fact]
    public async Task Json_that_is_not_a_health_response_does_not_count_either()
    {
        var handler = new RecordingHandler().Then(HttpStatusCode.OK, "{\"something\":\"else\"}");
        using var client = Client(handler);

        Assert.False(await client.IsReachableAsync());
    }
}
