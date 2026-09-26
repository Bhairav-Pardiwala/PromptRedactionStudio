using System;
using System.Net;
using System.Net.Http;
using System.Threading;
using System.Threading.Tasks;
using PromptRedactionTray.Services;
using Xunit;

namespace PromptRedactionTray.Tests;

/// <summary>
/// The error paths, which are the ones nobody exercises by hand until an auth rollout
/// is already underway and the message on screen is the only diagnostic anyone has.
/// </summary>
public class RedactionClientTests
{
    /// <summary>Answers every request with one canned response, so no server is needed.</summary>
    private sealed class StubHandler : HttpMessageHandler
    {
        private readonly HttpStatusCode _status;
        private readonly string _body;

        public StubHandler(HttpStatusCode status, string body)
        {
            _status = status;
            _body = body;
        }

        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken cancellationToken)
        {
            return Task.FromResult(new HttpResponseMessage(_status)
            {
                Content = new StringContent(_body),
            });
        }
    }

    private static RedactionClient ClientReturning(HttpStatusCode status, string body)
    {
        return new RedactionClient(new HttpClient(new StubHandler(status, body)))
        {
            BaseUrl = "http://localhost:8000",
            ApiKey = "a-key",
        };
    }

    [Fact]
    public async Task A_rejected_key_says_so_rather_than_throwing_something_else()
    {
        using var client = ClientReturning(
            HttpStatusCode.Unauthorized,
            "{\"detail\":\"Missing or invalid X-Redaction-Key header.\"}");

        var exception = await Assert.ThrowsAsync<RedactionClientException>(
            () => client.RedactAsync("Email jane.doe@example.com", null));

        Assert.Contains("rejected the API key", exception.Message);
        Assert.Contains("Missing or invalid X-Redaction-Key header.", exception.Message);
    }

    [Fact]
    public async Task Another_failure_reports_the_server_detail_without_the_key_wording()
    {
        using var client = ClientReturning(
            HttpStatusCode.BadRequest, "{\"detail\":\"Text is too long.\"}");

        var exception = await Assert.ThrowsAsync<RedactionClientException>(
            () => client.RedactAsync("whatever", null));

        Assert.Equal("Text is too long.", exception.Message);
        Assert.DoesNotContain("API key", exception.Message);
    }

    [Fact]
    public async Task A_body_that_is_not_json_still_produces_a_readable_message()
    {
        using var client = ClientReturning(HttpStatusCode.ServiceUnavailable, "<html>nope</html>");

        var exception = await Assert.ThrowsAsync<RedactionClientException>(
            () => client.RedactAsync("whatever", null));

        Assert.False(string.IsNullOrWhiteSpace(exception.Message));
    }
}
