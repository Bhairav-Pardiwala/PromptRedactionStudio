using System;
using System.Collections.Generic;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using PromptRedactionTray.Services;
using Xunit;

namespace PromptRedactionTray.Tests;

public class OidcClientTests
{
    private const string Issuer = "https://idp.example.com";
    private const string ClientId = "tray-client";

    /// <summary>Stands in for an identity provider: discovery plus a token endpoint.</summary>
    private sealed class FakeProvider : HttpMessageHandler
    {
        public string TokenResponse { get; set; } =
            "{\"access_token\":\"at-1\",\"refresh_token\":\"rt-1\",\"expires_in\":3600}";

        public HttpStatusCode TokenStatus { get; set; } = HttpStatusCode.OK;

        public Dictionary<string, string> LastForm { get; } = new();

        public int DiscoveryCalls { get; private set; }

        protected override async Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var url = request.RequestUri!.ToString();

            if (url.EndsWith("/.well-known/openid-configuration", StringComparison.Ordinal))
            {
                DiscoveryCalls++;
                return new HttpResponseMessage(HttpStatusCode.OK)
                {
                    Content = new StringContent(
                        "{\"authorization_endpoint\":\"" + Issuer + "/authorize\"," +
                        "\"token_endpoint\":\"" + Issuer + "/token\"}"),
                };
            }

            if (url.EndsWith("/token", StringComparison.Ordinal))
            {
                var body = await request.Content!.ReadAsStringAsync(cancellationToken);
                LastForm.Clear();
                foreach (var pair in body.Split('&', StringSplitOptions.RemoveEmptyEntries))
                {
                    var parts = pair.Split('=', 2);
                    LastForm[Uri.UnescapeDataString(parts[0])] =
                        parts.Length > 1 ? Uri.UnescapeDataString(parts[1].Replace('+', ' ')) : "";
                }

                return new HttpResponseMessage(TokenStatus)
                {
                    Content = new StringContent(TokenResponse),
                };
            }

            return new HttpResponseMessage(HttpStatusCode.NotFound);
        }
    }

    private static OidcClient Client(FakeProvider provider, string? scope = null) =>
        new(Issuer, ClientId, scope, new HttpClient(provider));

    // --- PKCE ---------------------------------------------------------------------

    [Fact]
    public void The_pkce_challenge_is_the_sha256_of_the_verifier()
    {
        var (verifier, challenge) = OidcClient.CreatePkcePair();

        using var sha = SHA256.Create();
        var expected = Convert.ToBase64String(sha.ComputeHash(Encoding.ASCII.GetBytes(verifier)))
            .TrimEnd('=').Replace('+', '-').Replace('/', '_');

        Assert.Equal(expected, challenge);
        // Base64url only -- a '+' or '/' would be mangled in a query string.
        Assert.DoesNotContain('+', challenge);
        Assert.DoesNotContain('/', challenge);
        Assert.DoesNotContain('=', challenge);
    }

    [Fact]
    public void Each_sign_in_gets_a_fresh_verifier()
    {
        var first = OidcClient.CreatePkcePair().Verifier;
        var second = OidcClient.CreatePkcePair().Verifier;
        Assert.NotEqual(first, second);
    }

    [Fact]
    public async Task The_authorization_url_carries_pkce_and_no_secret()
    {
        var provider = new FakeProvider();
        var client = Client(provider);
        var endpoint = await client.AuthorizationEndpointAsync();

        var url = client.BuildAuthorizationUrl(endpoint, "http://127.0.0.1:5000/", "the-challenge", "the-state");

        Assert.Contains("response_type=code", url);
        Assert.Contains("code_challenge=the-challenge", url);
        Assert.Contains("code_challenge_method=S256", url);
        Assert.Contains("client_id=" + ClientId, url);
        Assert.Contains("state=the-state", url);
        Assert.Contains("redirect_uri=http%3A%2F%2F127.0.0.1%3A5000%2F", url);
        // A public client has no secret to send, and sending one in a URL would be worse.
        Assert.DoesNotContain("client_secret", url);
    }

    [Fact]
    public async Task Offline_access_is_requested_by_default_so_a_refresh_token_comes_back()
    {
        var provider = new FakeProvider();
        var endpoint = await Client(provider).AuthorizationEndpointAsync();
        var url = Client(provider).BuildAuthorizationUrl(endpoint, "http://127.0.0.1:5000/", "c", "s");

        Assert.Contains("offline_access", url);
    }

    [Fact]
    public void A_configured_api_scope_keeps_offline_access()
    {
        // Setting OidcScope to the API's scope used to replace the defaults outright, so
        // no refresh token came back and people were sent to the browser every hour.
        var scope = OidcClient.BuildScope("api://11111111-2222-3333-4444-555555555555/redact");

        var parts = scope.Split(' ');
        Assert.Contains("api://11111111-2222-3333-4444-555555555555/redact", parts);
        Assert.Contains("offline_access", parts);
        Assert.Contains("openid", parts);
    }

    [Fact]
    public void Scopes_are_not_duplicated()
    {
        var scope = OidcClient.BuildScope("openid api://x/redact offline_access");
        Assert.Equal(1, scope.Split(' ').Count(s => s == "offline_access"));
        Assert.Equal(1, scope.Split(' ').Count(s => s == "openid"));
    }

    // --- callback parsing ---------------------------------------------------------

    [Fact]
    public void A_matching_state_yields_the_code()
    {
        var code = OidcClient.ReadCodeFromCallback("/?code=abc123&state=xyz", "xyz");
        Assert.Equal("abc123", code);
    }

    [Fact]
    public void A_mismatched_state_is_rejected()
    {
        // Otherwise another page could drive a code of its own into this listener.
        var exc = Assert.Throws<OidcException>(
            () => OidcClient.ReadCodeFromCallback("/?code=abc123&state=attacker", "xyz"));
        Assert.Contains("did not match", exc.Message);
    }

    [Fact]
    public void A_missing_state_is_rejected()
    {
        Assert.Throws<OidcException>(() => OidcClient.ReadCodeFromCallback("/?code=abc123", "xyz"));
    }

    [Fact]
    public void An_error_redirect_surfaces_the_provider_description()
    {
        var exc = Assert.Throws<OidcException>(() => OidcClient.ReadCodeFromCallback(
            "/?error=access_denied&error_description=User%20cancelled&state=xyz", "xyz"));
        Assert.Contains("User cancelled", exc.Message);
    }

    // --- token responses ----------------------------------------------------------

    [Fact]
    public void A_token_response_is_parsed_with_its_expiry()
    {
        var tokens = OidcClient.ParseTokenResponse(
            "{\"access_token\":\"at\",\"refresh_token\":\"rt\",\"expires_in\":3600}");

        Assert.Equal("at", tokens.AccessToken);
        Assert.Equal("rt", tokens.RefreshToken);
        Assert.False(tokens.IsExpired);
        Assert.True(tokens.ExpiresAt > DateTimeOffset.UtcNow.AddMinutes(50));
    }

    [Fact]
    public void An_expires_in_sent_as_a_string_is_accepted()
    {
        // Some providers do this; refusing would be a confusing failure at sign-in.
        var tokens = OidcClient.ParseTokenResponse("{\"access_token\":\"at\",\"expires_in\":\"3600\"}");
        Assert.False(tokens.IsExpired);
    }

    [Fact]
    public void A_token_about_to_expire_counts_as_expired()
    {
        var tokens = OidcClient.ParseTokenResponse("{\"access_token\":\"at\",\"expires_in\":30}");
        // Refreshed a minute early so a request in flight cannot race the expiry.
        Assert.True(tokens.IsExpired);
    }

    [Fact]
    public void A_response_with_no_access_token_is_an_error()
    {
        Assert.Throws<OidcException>(() => OidcClient.ParseTokenResponse("{\"token_type\":\"Bearer\"}"));
    }

    [Fact]
    public async Task Exchanging_a_code_sends_the_verifier_and_no_secret()
    {
        var provider = new FakeProvider();
        var tokens = await Client(provider).ExchangeCodeAsync("the-code", "the-verifier", "http://127.0.0.1:5000/");

        Assert.Equal("at-1", tokens.AccessToken);
        Assert.Equal("authorization_code", provider.LastForm["grant_type"]);
        Assert.Equal("the-code", provider.LastForm["code"]);
        Assert.Equal("the-verifier", provider.LastForm["code_verifier"]);
        Assert.False(provider.LastForm.ContainsKey("client_secret"));
    }

    [Fact]
    public async Task Refreshing_uses_the_refresh_grant()
    {
        var provider = new FakeProvider();
        await Client(provider).RefreshAsync("the-refresh-token");

        Assert.Equal("refresh_token", provider.LastForm["grant_type"]);
        Assert.Equal("the-refresh-token", provider.LastForm["refresh_token"]);
    }

    [Fact]
    public async Task A_rejected_refresh_reports_the_provider_reason()
    {
        var provider = new FakeProvider
        {
            TokenStatus = HttpStatusCode.BadRequest,
            TokenResponse = "{\"error\":\"invalid_grant\",\"error_description\":\"Token has expired\"}",
        };

        var exc = await Assert.ThrowsAsync<OidcException>(() => Client(provider).RefreshAsync("stale"));
        Assert.Contains("Token has expired", exc.Message);
    }

    [Fact]
    public async Task Discovery_is_fetched_once_and_reused()
    {
        var provider = new FakeProvider();
        var client = Client(provider);

        await client.AuthorizationEndpointAsync();
        await client.TokenEndpointAsync();
        await client.TokenEndpointAsync();

        Assert.Equal(1, provider.DiscoveryCalls);
    }

    [Fact]
    public async Task An_unreachable_provider_is_a_readable_error()
    {
        var client = new OidcClient(
            "https://idp.invalid", ClientId, null, new HttpClient(new AlwaysFails()));

        var exc = await Assert.ThrowsAsync<OidcException>(() => client.TokenEndpointAsync());
        Assert.Contains("Could not reach the identity provider", exc.Message);
    }

    private sealed class AlwaysFails : HttpMessageHandler
    {
        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken cancellationToken) =>
            throw new HttpRequestException("no route to host");
    }

    // --- the real loopback round trip ---------------------------------------------

    [Fact]
    public async Task A_full_sign_in_completes_over_a_real_loopback_socket()
    {
        var provider = new FakeProvider();
        var client = Client(provider);

        // Stand in for the browser: call the redirect_uri the way the provider would.
        using var browser = new HttpClient { Timeout = TimeSpan.FromSeconds(10) };
        var tokens = await client.SignInAsync(url =>
        {
            var redirect = ExtractRedirectUri(url);
            var state = ExtractQueryValue(url, "state");
            _ = Task.Run(async () =>
            {
                try { await browser.GetStringAsync(redirect + "?code=real-code&state=" + state); }
                catch (Exception) { /* the listener closes as soon as it has the code */ }
            });
        });

        Assert.Equal("at-1", tokens.AccessToken);
        Assert.Equal("rt-1", tokens.RefreshToken);
        Assert.Equal("real-code", provider.LastForm["code"]);
        // The redirect the provider was given must be the one redeemed, or the exchange fails.
        Assert.StartsWith("http://127.0.0.1:", provider.LastForm["redirect_uri"]);
    }

    [Fact]
    public async Task A_sign_in_that_is_never_completed_times_out_rather_than_hanging()
    {
        var provider = new FakeProvider();
        var client = Client(provider) ;
        client.SignInTimeout = TimeSpan.FromMilliseconds(400);

        var exc = await Assert.ThrowsAsync<OidcException>(
            () => client.SignInAsync(_ => { /* nobody ever opens the browser */ }));

        Assert.Contains("Timed out", exc.Message);
    }

    private static string ExtractRedirectUri(string authorizationUrl) =>
        ExtractQueryValue(authorizationUrl, "redirect_uri").TrimEnd('/');

    private static string ExtractQueryValue(string url, string key)
    {
        var query = url[(url.IndexOf('?') + 1)..];
        foreach (var pair in query.Split('&'))
        {
            var parts = pair.Split('=', 2);
            if (Uri.UnescapeDataString(parts[0]) == key)
            {
                return Uri.UnescapeDataString(parts[1]);
            }
        }

        throw new InvalidOperationException("No " + key + " in " + url);
    }
}
