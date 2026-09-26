using System;
using System.Collections.Generic;
using System.Net;
using System.Net.Http;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Threading;
using System.Threading.Tasks;

namespace PromptRedactionTray.Services;

/// <summary>
/// Talks to a Prompt Redaction Studio instance.
///
/// Every call passes store_session: false, so the server keeps no mapping and there is
/// nothing for an unauthenticated /api/restore to hand back. Restoring happens locally
/// in <see cref="MappingStore"/>.
/// </summary>
public sealed class RedactionClient : IDisposable
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        PropertyNameCaseInsensitive = true,
    };

    private readonly HttpClient _http;

    public RedactionClient(HttpClient? http = null)
    {
        // Redirects are off deliberately. An SSO reverse proxy answers an unauthenticated
        // API call with a 302 to its login page; following that lands us on a 200 of HTML,
        // which reads as success and fails much later as a JSON parse error. Better to see
        // the 3xx and say what it means.
        _http = http ?? new HttpClient(new HttpClientHandler { AllowAutoRedirect = false })
        {
            Timeout = TimeSpan.FromSeconds(30),
        };
    }

    /// <summary>
    /// Supplies a bearer token when the instance sits behind an OAuth proxy. Called with
    /// true to force a renewal, which is what happens after a 401.
    /// </summary>
    public Func<bool, CancellationToken, Task<string?>>? AccessTokenProvider { get; set; }

    /// <summary>Base URL of the instance, e.g. https://redaction.corp.example.com</summary>
    public string BaseUrl { get; set; } = "http://127.0.0.1:8000";

    /// <summary>Optional shared key; sent only when the instance has one configured.</summary>
    public string? ApiKey { get; set; }

    public async Task<bool> IsReachableAsync(CancellationToken cancellationToken = default)
    {
        try
        {
            using var request = await BuildRequestAsync(
                HttpMethod.Get, "/api/health", forceRefresh: false, cancellationToken).ConfigureAwait(false);
            using var response = await _http.SendAsync(request, cancellationToken).ConfigureAwait(false);
            if (!response.IsSuccessStatusCode)
            {
                return false;
            }

            // Checking the status code alone is not enough. A sign-in proxy can answer with
            // a perfectly good 200 that happens to be an HTML login page, and reporting
            // "connected" over an instance that cannot serve a single request is worse than
            // reporting nothing at all.
            var body = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
            using var document = JsonDocument.Parse(body);
            return document.RootElement.TryGetProperty("status", out var status)
                && status.ValueKind == JsonValueKind.String
                && status.GetString() == "ok";
        }
        catch (Exception)
        {
            // Unreachable is an expected state, not an error worth surfacing as a crash.
            return false;
        }
    }

    public async Task<PolicyResponse> GetPolicyAsync(CancellationToken cancellationToken = default)
    {
        using var response = await SendAsync(
            HttpMethod.Get, "/api/policy", null, cancellationToken).ConfigureAwait(false);
        var policy = await response.Content
            .ReadFromJsonAsync<PolicyResponse>(JsonOptions, cancellationToken)
            .ConfigureAwait(false);
        return policy ?? throw new RedactionClientException("The instance returned an empty policy.");
    }

    public async Task<RedactResponse> RedactAsync(
        string text,
        PolicyResponse? policy,
        CancellationToken cancellationToken = default)
    {
        var payload = new Dictionary<string, object?>
        {
            ["text"] = text,
            ["store_session"] = false,
            ["return_explanations"] = false,
        };

        if (policy is not null)
        {
            payload["engine"] = policy.Engine;
            payload["language"] = policy.Language;
            payload["entities"] = policy.Entities;
            payload["score_threshold"] = policy.ScoreThreshold;
            payload["detect_organization"] = policy.DetectOrganization;
            payload["allow_list"] = policy.AllowList;
            payload["allow_list_match"] = policy.AllowListMatch;
            payload["custom_recognizers"] = policy.CustomRecognizers;
            payload["default_operator"] = policy.DefaultOperator;
            payload["per_entity_operators"] = policy.PerEntityOperators;
        }

        using var response = await SendAsync(
            HttpMethod.Post,
            "/api/redact",
            () => JsonContent.Create(payload, options: JsonOptions),
            cancellationToken).ConfigureAwait(false);
        var result = await response.Content
            .ReadFromJsonAsync<RedactResponse>(JsonOptions, cancellationToken)
            .ConfigureAwait(false);
        return result ?? throw new RedactionClientException("The instance returned an empty response.");
    }

    private async Task<HttpRequestMessage> BuildRequestAsync(
        HttpMethod method, string path, bool forceRefresh, CancellationToken cancellationToken)
    {
        var baseUrl = BaseUrl.TrimEnd('/');
        var request = new HttpRequestMessage(method, baseUrl + path);

        if (!string.IsNullOrWhiteSpace(ApiKey))
        {
            request.Headers.Add("X-Redaction-Key", ApiKey);
        }

        // Both can be present: the proxy may want the bearer while the instance behind it
        // still wants its own key.
        if (AccessTokenProvider is not null)
        {
            var token = await AccessTokenProvider(forceRefresh, cancellationToken).ConfigureAwait(false);
            if (!string.IsNullOrWhiteSpace(token))
            {
                request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", token);
            }
        }

        return request;
    }

    /// <summary>Send, turning every failure mode into one exception type with a readable message.</summary>
    private async Task<HttpResponseMessage> SendAsync(
        HttpMethod method,
        string path,
        Func<HttpContent>? content,
        CancellationToken cancellationToken)
    {
        var response = await AttemptAsync(method, path, content, false, cancellationToken)
            .ConfigureAwait(false);

        // One retry with a freshly minted token: an access token that expired mid-session
        // should not make the user sign in again.
        if (response.StatusCode == HttpStatusCode.Unauthorized && AccessTokenProvider is not null)
        {
            response.Dispose();
            response = await AttemptAsync(method, path, content, true, cancellationToken)
                .ConfigureAwait(false);
        }

        return Interpret(response, await ReadDetailAsync(response, cancellationToken).ConfigureAwait(false));
    }

    private async Task<HttpResponseMessage> AttemptAsync(
        HttpMethod method,
        string path,
        Func<HttpContent>? content,
        bool forceRefresh,
        CancellationToken cancellationToken)
    {
        using var request = await BuildRequestAsync(method, path, forceRefresh, cancellationToken)
            .ConfigureAwait(false);
        if (content is not null)
        {
            request.Content = content();
        }

        try
        {
            return await _http.SendAsync(request, cancellationToken).ConfigureAwait(false);
        }
        catch (TaskCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            throw new RedactionClientException("The instance did not respond in time.");
        }
        catch (HttpRequestException exc)
        {
            throw new RedactionClientException(
                "Could not reach the instance at " + BaseUrl + ". " + exc.Message);
        }

    }

    /// <summary>FastAPI puts the readable reason in {"detail": "..."}; fall back to the status.</summary>
    private static async Task<string> ReadDetailAsync(
        HttpResponseMessage response, CancellationToken cancellationToken)
    {
        if (response.IsSuccessStatusCode)
        {
            return string.Empty;
        }

        try
        {
            var body = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
            using var document = JsonDocument.Parse(body);
            return document.RootElement.TryGetProperty("detail", out var element)
                ? element.ToString()
                : response.ReasonPhrase ?? response.StatusCode.ToString();
        }
        catch (Exception)
        {
            return response.ReasonPhrase ?? response.StatusCode.ToString();
        }
    }

    private HttpResponseMessage Interpret(HttpResponseMessage response, string detail)
    {
        if (response.IsSuccessStatusCode)
        {
            return response;
        }

        // Read everything off the response before disposing it. StatusCode happens to
        // survive disposal today -- only the setters check -- but that is an internal
        // detail of HttpResponseMessage, not a promise.
        var status = (int)response.StatusCode;
        var location = response.Headers.Location?.ToString();
        var mediaType = response.Content.Headers.ContentType?.MediaType;
        response.Dispose();

        // A sign-in proxy rejects an unauthenticated API call in one of two ways, and
        // neither looks like an API error: a redirect to the provider, or a page of HTML.
        // oauth2-proxy in bearer-token mode answers 403 with its own sign-in page, so
        // matching only on 3xx would leave the user reading "Forbidden".
        var isRedirect = status is >= 300 and < 400;
        var isHtml = mediaType is not null
            && mediaType.Contains("html", StringComparison.OrdinalIgnoreCase);

        if (isRedirect || isHtml)
        {
            throw new RedactionClientException(
                "The instance at " + BaseUrl + " answered this API call with "
                + (isRedirect
                    ? "a redirect" + (location is null ? "" : " to " + location)
                    : "a web page (HTTP " + status + ")")
                + " rather than data, which means it sits behind a sign-in proxy that did "
                + "not accept this client. Configure the proxy to accept bearer tokens, or "
                + "sign in from the tray menu.");
        }

        if (status == 401)
        {
            throw new RedactionClientException(
                AccessTokenProvider is null
                    ? "The instance rejected the API key: " + detail
                    : "The instance rejected the sign-in: " + detail);
        }

        throw new RedactionClientException(detail);
    }

    public void Dispose() => _http.Dispose();
}

public sealed class RedactionClientException : Exception
{
    public RedactionClientException(string message) : base(message)
    {
    }
}

public sealed class PolicyResponse
{
    public string Engine { get; set; } = "spacy_lg";
    public string Language { get; set; } = "en";
    public List<string> Entities { get; set; } = new();
    public double ScoreThreshold { get; set; } = 0.35;
    public bool DetectOrganization { get; set; }
    public Dictionary<string, object?> DefaultOperator { get; set; } = new();
    public Dictionary<string, object?> PerEntityOperators { get; set; } = new();
    public List<string> AllowList { get; set; } = new();
    public string AllowListMatch { get; set; } = "exact";
    public List<object> CustomRecognizers { get; set; } = new();
    public bool Locked { get; set; }
    public string Source { get; set; } = "defaults";
}

public sealed class RedactResponse
{
    public string? SessionId { get; set; }
    public string OriginalText { get; set; } = string.Empty;
    public string RedactedText { get; set; } = string.Empty;

    /// <summary>Everything the analyzer detected, including spans that overlap.</summary>
    public List<Finding> Findings { get; set; } = new();

    /// <summary>The replacements actually applied, after Presidio resolved overlaps.</summary>
    public List<AppliedItem> Items { get; set; } = new();

    public Dictionary<string, string> Mapping { get; set; } = new();
    public bool Reversible { get; set; }

    /// <summary>
    /// How many replacements the text actually received.
    ///
    /// Deliberately not Findings.Count: the analyzer reports overlapping spans (an email
    /// address also matches the URL recognizer), and the anonymizer merges those before
    /// rewriting. Reporting findings would tell the user six things changed when four did.
    /// </summary>
    [JsonIgnore]
    public int AppliedCount => Items.Count;
}

public sealed class AppliedItem
{
    public string EntityType { get; set; } = string.Empty;
    public string Text { get; set; } = string.Empty;
    public string Operator { get; set; } = string.Empty;
}

public sealed class Finding
{
    public string EntityType { get; set; } = string.Empty;
    public double Score { get; set; }
    public string Text { get; set; } = string.Empty;
}
