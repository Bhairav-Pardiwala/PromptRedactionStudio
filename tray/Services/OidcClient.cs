using System;
using System.Collections.Generic;
using System.Globalization;
using System.Net;
using System.Net.Http;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace PromptRedactionTray.Services;

/// <summary>A set of tokens as the provider issued them.</summary>
public sealed class TokenSet
{
    public required string AccessToken { get; init; }

    public string? RefreshToken { get; init; }

    public DateTimeOffset ExpiresAt { get; init; }

    /// <summary>Refreshed a minute early, so a request never races the expiry.</summary>
    public bool IsExpired => DateTimeOffset.UtcNow >= ExpiresAt - TimeSpan.FromSeconds(60);
}

public sealed class OidcException : Exception
{
    public OidcException(string message, Exception? inner = null) : base(message, inner)
    {
    }
}

/// <summary>
/// Authorization code + PKCE against a loopback redirect -- the flow RFC 8252 specifies
/// for native applications.
///
/// Not device code: this machine has a browser, and device code is both phishable (the
/// victim is handed a legitimate code to enter) and increasingly blocked outright by
/// Conditional Access policy. Not a client secret either: a desktop application is a
/// public client and cannot keep one, which is the reason PKCE exists.
///
/// Everything here talks to the identity provider, never to the redaction instance.
/// </summary>
public sealed class OidcClient
{
    private static readonly JsonSerializerOptions Json = new() { PropertyNameCaseInsensitive = true };

    private readonly HttpClient _http;
    private readonly string _issuer;
    private readonly string _clientId;
    private readonly string _scope;

    private Dictionary<string, JsonElement>? _discovery;

    public OidcClient(string issuer, string clientId, string? scope, HttpClient? http = null)
    {
        _issuer = (issuer ?? throw new ArgumentNullException(nameof(issuer))).TrimEnd('/');
        _clientId = clientId ?? throw new ArgumentNullException(nameof(clientId));
        // "offline_access" is what earns a refresh token; without it the user is sent
        // back to the browser every hour.
        _scope = BuildScope(scope);
        _http = http ?? new HttpClient { Timeout = TimeSpan.FromSeconds(30) };
    }

    /// <summary>
    /// The configured API scope plus the OIDC scopes the flow depends on. They are added,
    /// not replaced: an administrator writes the scope that names their API, and should
    /// not also have to know that leaving out offline_access means no refresh token.
    /// </summary>
    public static string BuildScope(string? configured)
    {
        var scopes = new List<string>();
        foreach (var part in (configured ?? string.Empty).Split(' ', StringSplitOptions.RemoveEmptyEntries))
        {
            if (!scopes.Contains(part))
            {
                scopes.Add(part);
            }
        }

        foreach (var required in new[] { "openid", "profile", "offline_access" })
        {
            if (!scopes.Contains(required))
            {
                scopes.Add(required);
            }
        }

        return string.Join(" ", scopes);
    }

    /// <summary>How long to wait for the person to finish signing in before giving up.</summary>
    public TimeSpan SignInTimeout { get; set; } = TimeSpan.FromMinutes(3);

    // ----------------------------------------------------------------- discovery

    public async Task<string> AuthorizationEndpointAsync(CancellationToken ct = default) =>
        await EndpointAsync("authorization_endpoint", ct).ConfigureAwait(false);

    public async Task<string> TokenEndpointAsync(CancellationToken ct = default) =>
        await EndpointAsync("token_endpoint", ct).ConfigureAwait(false);

    private async Task<string> EndpointAsync(string name, CancellationToken ct)
    {
        var document = await DiscoveryAsync(ct).ConfigureAwait(false);
        if (!document.TryGetValue(name, out var value) || value.ValueKind != JsonValueKind.String)
        {
            throw new OidcException(
                "The provider's discovery document has no " + name + ". Check " + nameof(AppSettings.OidcIssuer) + ".");
        }

        return value.GetString()!;
    }

    private async Task<Dictionary<string, JsonElement>> DiscoveryAsync(CancellationToken ct)
    {
        if (_discovery is not null)
        {
            return _discovery;
        }

        var url = _issuer + "/.well-known/openid-configuration";
        try
        {
            using var response = await _http.GetAsync(url, ct).ConfigureAwait(false);
            response.EnsureSuccessStatusCode();
            var body = await response.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
            _discovery = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(body, Json)
                ?? throw new OidcException("The provider returned an empty discovery document.");
            return _discovery;
        }
        catch (Exception exc) when (exc is not OidcException)
        {
            throw new OidcException("Could not reach the identity provider at " + url + ".", exc);
        }
    }

    // ----------------------------------------------------------------------- PKCE

    /// <summary>A PKCE verifier and its S256 challenge.</summary>
    public static (string Verifier, string Challenge) CreatePkcePair()
    {
        var verifier = RandomUrlSafe(32);
        using var sha = SHA256.Create();
        var challenge = Base64Url(sha.ComputeHash(Encoding.ASCII.GetBytes(verifier)));
        return (verifier, challenge);
    }

    public static string RandomUrlSafe(int bytes) => Base64Url(RandomNumberGenerator.GetBytes(bytes));

    private static string Base64Url(byte[] value) =>
        Convert.ToBase64String(value).TrimEnd('=').Replace('+', '-').Replace('/', '_');

    /// <summary>Assemble the authorization URL the browser is sent to.</summary>
    public string BuildAuthorizationUrl(
        string authorizationEndpoint, string redirectUri, string challenge, string state)
    {
        var query = new Dictionary<string, string>
        {
            ["client_id"] = _clientId,
            ["response_type"] = "code",
            ["redirect_uri"] = redirectUri,
            ["scope"] = _scope,
            ["state"] = state,
            ["code_challenge"] = challenge,
            ["code_challenge_method"] = "S256",
        };

        var separator = authorizationEndpoint.Contains('?') ? "&" : "?";
        return authorizationEndpoint + separator + Encode(query);
    }

    private static string Encode(Dictionary<string, string> values)
    {
        var parts = new List<string>();
        foreach (var (key, value) in values)
        {
            parts.Add(Uri.EscapeDataString(key) + "=" + Uri.EscapeDataString(value));
        }

        return string.Join("&", parts);
    }

    /// <summary>
    /// Pull the code out of the path the browser was redirected to, checking the state
    /// so another page cannot feed us a code of its own.
    /// </summary>
    public static string ReadCodeFromCallback(string requestTarget, string expectedState)
    {
        var index = requestTarget.IndexOf('?');
        var query = index >= 0 ? requestTarget[(index + 1)..] : string.Empty;

        var values = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (var pair in query.Split('&', StringSplitOptions.RemoveEmptyEntries))
        {
            var split = pair.Split('=', 2);
            values[Uri.UnescapeDataString(split[0])] =
                split.Length > 1 ? Uri.UnescapeDataString(split[1].Replace('+', ' ')) : string.Empty;
        }

        if (values.TryGetValue("error", out var error))
        {
            values.TryGetValue("error_description", out var description);
            throw new OidcException("The identity provider refused the sign-in: " + (description ?? error));
        }

        if (!values.TryGetValue("state", out var state) || state != expectedState)
        {
            throw new OidcException("The sign-in response did not match this request and was discarded.");
        }

        if (!values.TryGetValue("code", out var code) || string.IsNullOrEmpty(code))
        {
            throw new OidcException("The identity provider returned no authorization code.");
        }

        return code;
    }

    // -------------------------------------------------------------------- sign in

    /// <summary>
    /// Run the full flow: listen on a loopback port, send the browser to the provider,
    /// wait for the redirect, then trade the code for tokens.
    /// </summary>
    public async Task<TokenSet> SignInAsync(Action<string> openBrowser, CancellationToken ct = default)
    {
        var (verifier, challenge) = CreatePkcePair();
        var state = RandomUrlSafe(16);

        // Port 0 asks the OS for a free one. RFC 8252 tells providers to accept any port
        // on the loopback address, so nothing needs registering per machine.
        var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        try
        {
            var port = ((IPEndPoint)listener.LocalEndpoint).Port;
            var redirectUri = "http://127.0.0.1:" + port.ToString(CultureInfo.InvariantCulture) + "/";

            var authorizationEndpoint = await AuthorizationEndpointAsync(ct).ConfigureAwait(false);
            openBrowser(BuildAuthorizationUrl(authorizationEndpoint, redirectUri, challenge, state));

            var code = await AwaitCallbackAsync(listener, state, ct).ConfigureAwait(false);
            return await ExchangeCodeAsync(code, verifier, redirectUri, ct).ConfigureAwait(false);
        }
        finally
        {
            listener.Stop();
        }
    }

    private async Task<string> AwaitCallbackAsync(TcpListener listener, string state, CancellationToken ct)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(SignInTimeout);

        TcpClient client;
        try
        {
            client = await listener.AcceptTcpClientAsync(timeout.Token).ConfigureAwait(false);
        }
        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
        {
            throw new OidcException("Timed out waiting for the sign-in to finish in the browser.");
        }

        using (client)
        await using (var stream = client.GetStream())
        {
            // Only the request line is needed, and it arrives in the first packet.
            var buffer = new byte[8192];
            var read = await stream.ReadAsync(buffer, ct).ConfigureAwait(false);
            var request = Encoding.ASCII.GetString(buffer, 0, read);
            var requestLine = request.Split('\r', '\n')[0];
            var parts = requestLine.Split(' ');
            var target = parts.Length > 1 ? parts[1] : "/";

            string code;
            string page;
            try
            {
                code = ReadCodeFromCallback(target, state);
                page = "<h2>Signed in</h2><p>You can close this tab and return to the app.</p>";
            }
            catch (OidcException exc)
            {
                // Tell the person in the browser too; the tray toast alone is easy to miss.
                page = "<h2>Sign-in failed</h2><p>" + WebUtility.HtmlEncode(exc.Message) + "</p>";
                await WriteResponseAsync(stream, page, ct).ConfigureAwait(false);
                throw;
            }

            await WriteResponseAsync(stream, page, ct).ConfigureAwait(false);
            return code;
        }
    }

    private static async Task WriteResponseAsync(NetworkStream stream, string body, CancellationToken ct)
    {
        var html = "<!doctype html><html><head><meta charset=\"utf-8\">"
            + "<title>Prompt Redaction Studio</title></head>"
            + "<body style=\"font-family:system-ui;padding:40px\">" + body + "</body></html>";
        var bytes = Encoding.UTF8.GetBytes(html);
        var header = "HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n"
            + "Content-Length: " + bytes.Length.ToString(CultureInfo.InvariantCulture) + "\r\n"
            + "Connection: close\r\n\r\n";

        await stream.WriteAsync(Encoding.ASCII.GetBytes(header), ct).ConfigureAwait(false);
        await stream.WriteAsync(bytes, ct).ConfigureAwait(false);
        await stream.FlushAsync(ct).ConfigureAwait(false);
    }

    // --------------------------------------------------------------- token exchange

    public async Task<TokenSet> ExchangeCodeAsync(
        string code, string verifier, string redirectUri, CancellationToken ct = default)
    {
        return await PostTokenAsync(new Dictionary<string, string>
        {
            ["grant_type"] = "authorization_code",
            ["client_id"] = _clientId,
            ["code"] = code,
            ["redirect_uri"] = redirectUri,
            ["code_verifier"] = verifier,
        }, ct).ConfigureAwait(false);
    }

    public async Task<TokenSet> RefreshAsync(string refreshToken, CancellationToken ct = default)
    {
        return await PostTokenAsync(new Dictionary<string, string>
        {
            ["grant_type"] = "refresh_token",
            ["client_id"] = _clientId,
            ["refresh_token"] = refreshToken,
            ["scope"] = _scope,
        }, ct).ConfigureAwait(false);
    }

    private async Task<TokenSet> PostTokenAsync(Dictionary<string, string> form, CancellationToken ct)
    {
        var endpoint = await TokenEndpointAsync(ct).ConfigureAwait(false);

        HttpResponseMessage response;
        string body;
        try
        {
            using var content = new FormUrlEncodedContent(form);
            response = await _http.PostAsync(endpoint, content, ct).ConfigureAwait(false);
            body = await response.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
        }
        catch (Exception exc)
        {
            throw new OidcException("Could not reach the identity provider's token endpoint.", exc);
        }

        using (response)
        {
            if (!response.IsSuccessStatusCode)
            {
                throw new OidcException("The identity provider rejected the request: " + DescribeError(body));
            }
        }

        return ParseTokenResponse(body);
    }

    private static string DescribeError(string body)
    {
        try
        {
            var parsed = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(body, Json);
            if (parsed is not null && parsed.TryGetValue("error_description", out var description))
            {
                return description.ToString();
            }

            if (parsed is not null && parsed.TryGetValue("error", out var error))
            {
                return error.ToString();
            }
        }
        catch (JsonException)
        {
            // Fall through to the raw body.
        }

        return string.IsNullOrWhiteSpace(body) ? "no reason given" : body;
    }

    /// <summary>Turn a token endpoint's JSON into a <see cref="TokenSet"/>.</summary>
    public static TokenSet ParseTokenResponse(string body)
    {
        Dictionary<string, JsonElement>? parsed;
        try
        {
            parsed = JsonSerializer.Deserialize<Dictionary<string, JsonElement>>(body, Json);
        }
        catch (JsonException exc)
        {
            throw new OidcException("The identity provider's response was not valid JSON.", exc);
        }

        if (parsed is null || !parsed.TryGetValue("access_token", out var accessToken)
            || accessToken.ValueKind != JsonValueKind.String)
        {
            throw new OidcException("The identity provider returned no access token.");
        }

        var lifetime = TimeSpan.FromHours(1);
        if (parsed.TryGetValue("expires_in", out var expires))
        {
            if (expires.ValueKind == JsonValueKind.Number && expires.TryGetInt32(out var seconds))
            {
                lifetime = TimeSpan.FromSeconds(seconds);
            }
            else if (expires.ValueKind == JsonValueKind.String
                && int.TryParse(expires.GetString(), NumberStyles.Integer, CultureInfo.InvariantCulture, out var text))
            {
                // Some providers send expires_in as a string. Accept both.
                lifetime = TimeSpan.FromSeconds(text);
            }
        }

        string? refresh = null;
        if (parsed.TryGetValue("refresh_token", out var refreshToken)
            && refreshToken.ValueKind == JsonValueKind.String)
        {
            refresh = refreshToken.GetString();
        }

        return new TokenSet
        {
            AccessToken = accessToken.GetString()!,
            RefreshToken = refresh,
            ExpiresAt = DateTimeOffset.UtcNow + lifetime,
        };
    }
}
