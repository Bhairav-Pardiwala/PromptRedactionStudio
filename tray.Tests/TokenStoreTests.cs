using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using PromptRedactionTray.Services;
using Xunit;

namespace PromptRedactionTray.Tests;

public class TokenStoreTests : IDisposable
{
    private readonly string _root;
    private readonly string _path;

    public TokenStoreTests()
    {
        _root = Path.Combine(Path.GetTempPath(), "prt-tokens-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(_root);
        _path = Path.Combine(_root, "tokens.json");
    }

    public void Dispose()
    {
        try { Directory.Delete(_root, recursive: true); } catch (IOException) { }
    }

    /// <summary>A provider whose token endpoint answers however the test wants.</summary>
    private sealed class RefreshHandler : HttpMessageHandler
    {
        public string Response { get; set; } =
            "{\"access_token\":\"fresh\",\"refresh_token\":\"rt-2\",\"expires_in\":3600}";

        public HttpStatusCode Status { get; set; } = HttpStatusCode.OK;

        public int RefreshCalls { get; private set; }

        protected override Task<HttpResponseMessage> SendAsync(
            HttpRequestMessage request, CancellationToken cancellationToken)
        {
            var url = request.RequestUri!.ToString();
            if (url.EndsWith("openid-configuration", StringComparison.Ordinal))
            {
                return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
                {
                    Content = new StringContent(
                        "{\"authorization_endpoint\":\"https://idp.example.com/authorize\"," +
                        "\"token_endpoint\":\"https://idp.example.com/token\"}"),
                });
            }

            RefreshCalls++;
            return Task.FromResult(new HttpResponseMessage(Status)
            {
                Content = new StringContent(Response),
            });
        }
    }

    private static (OidcClient Client, RefreshHandler Handler) Provider()
    {
        var handler = new RefreshHandler();
        return (new OidcClient("https://idp.example.com", "client", null, new HttpClient(handler)), handler);
    }

    private TokenStore Store(ITokenProtector? protector = null) =>
        new(_path, protector ?? new PlaintextTokenProtector());

    private static TokenSet Tokens(string access, string? refresh, int expiresInSeconds) =>
        OidcClient.ParseTokenResponse(
            "{\"access_token\":\"" + access + "\"" +
            (refresh is null ? "" : ",\"refresh_token\":\"" + refresh + "\"") +
            ",\"expires_in\":" + expiresInSeconds + "}");

    [Fact]
    public void A_fresh_store_has_no_session()
    {
        Assert.False(Store().HasSession);
    }

    [Fact]
    public async Task A_valid_access_token_is_returned_without_contacting_the_provider()
    {
        var (client, handler) = Provider();
        var store = Store();
        store.Accept(Tokens("at-1", "rt-1", 3600));

        Assert.Equal("at-1", await store.GetAccessTokenAsync(client));
        Assert.Equal(0, handler.RefreshCalls);
    }

    [Fact]
    public async Task An_expired_token_is_refreshed()
    {
        var (client, handler) = Provider();
        var store = Store();
        store.Accept(Tokens("at-1", "rt-1", 5));

        Assert.Equal("fresh", await store.GetAccessTokenAsync(client));
        Assert.Equal(1, handler.RefreshCalls);
    }

    [Fact]
    public async Task A_rotated_refresh_token_replaces_the_old_one()
    {
        // Providers that rotate refresh tokens invalidate the previous one, so keeping
        // the old value would break the renewal after next.
        var (client, _) = Provider();
        var store = Store();
        store.Accept(Tokens("at-1", "rt-1", 5));
        await store.GetAccessTokenAsync(client);

        var reloaded = Store();
        Assert.True(reloaded.HasSession);

        var (client2, handler2) = Provider();
        await reloaded.GetAccessTokenAsync(client2);
        Assert.Equal(1, handler2.RefreshCalls);
    }

    [Fact]
    public async Task A_refused_refresh_clears_the_session_rather_than_retrying_forever()
    {
        var (client, handler) = Provider();
        handler.Status = HttpStatusCode.BadRequest;
        handler.Response = "{\"error\":\"invalid_grant\"}";

        var store = Store();
        store.Accept(Tokens("at-1", "rt-1", 5));

        Assert.Null(await store.GetAccessTokenAsync(client));
        Assert.False(store.HasSession);
        Assert.False(File.Exists(_path));
    }

    [Fact]
    public async Task With_no_refresh_token_the_user_has_to_sign_in_again()
    {
        var (client, handler) = Provider();
        var store = Store();
        store.Accept(Tokens("at-1", null, 5));

        Assert.Null(await store.GetAccessTokenAsync(client));
        Assert.Equal(0, handler.RefreshCalls);
    }

    [Fact]
    public void The_refresh_token_survives_a_restart_but_the_access_token_does_not()
    {
        Store().Accept(Tokens("at-1", "rt-1", 3600));

        var restarted = Store();

        // Session survives, so no browser round trip on every reboot.
        Assert.True(restarted.HasSession);
        // The access token itself was never written.
        Assert.DoesNotContain("at-1", File.ReadAllText(_path));
    }

    [Fact]
    public void Signing_out_removes_the_file()
    {
        var store = Store();
        store.Accept(Tokens("at-1", "rt-1", 3600));
        Assert.True(File.Exists(_path));

        store.Forget();

        Assert.False(store.HasSession);
        Assert.False(File.Exists(_path));
    }

    [Fact]
    public void An_unreadable_token_file_just_means_signing_in_again()
    {
        File.WriteAllText(_path, "not json at all");
        Assert.False(Store().HasSession);
    }

    [Fact]
    public void The_plaintext_protector_admits_it_is_not_encrypting()
    {
        Assert.False(Store(new PlaintextTokenProtector()).IsProtectedAtRest);
    }

    [Fact]
    public void On_windows_the_refresh_token_is_encrypted_at_rest()
    {
        if (!RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
        {
            return;
        }

        var store = new TokenStore(_path, new DpapiTokenProtector());
        store.Accept(Tokens("at-1", "super-secret-refresh-token", 3600));

        var written = File.ReadAllText(_path);
        Assert.DoesNotContain("super-secret-refresh-token", written);
        Assert.Contains("\"Encrypted\":true", written);

        // And it round trips for the same user on the same machine.
        Assert.True(new TokenStore(_path, new DpapiTokenProtector()).HasSession);
    }

    [Fact]
    public void The_default_protector_encrypts_on_windows()
    {
        var expected = RuntimeInformation.IsOSPlatform(OSPlatform.Windows);
        Assert.Equal(expected, TokenStore.DefaultProtector().IsEncrypted);
    }
}
