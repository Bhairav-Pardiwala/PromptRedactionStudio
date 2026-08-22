using System;
using System.Collections.Generic;
using System.Net.Http;
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
        _http = http ?? new HttpClient { Timeout = TimeSpan.FromSeconds(30) };
    }

    /// <summary>Base URL of the instance, e.g. https://redaction.corp.example.com</summary>
    public string BaseUrl { get; set; } = "http://127.0.0.1:8000";

    /// <summary>Optional shared key; sent only when the instance has one configured.</summary>
    public string? ApiKey { get; set; }

    public async Task<bool> IsReachableAsync(CancellationToken cancellationToken = default)
    {
        try
        {
            using var request = BuildRequest(HttpMethod.Get, "/api/health");
            using var response = await _http.SendAsync(request, cancellationToken).ConfigureAwait(false);
            return response.IsSuccessStatusCode;
        }
        catch (Exception)
        {
            // Unreachable is an expected state, not an error worth surfacing as a crash.
            return false;
        }
    }

    public async Task<PolicyResponse> GetPolicyAsync(CancellationToken cancellationToken = default)
    {
        using var request = BuildRequest(HttpMethod.Get, "/api/policy");
        using var response = await SendAsync(request, cancellationToken).ConfigureAwait(false);
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

        using var request = BuildRequest(HttpMethod.Post, "/api/redact");
        request.Content = JsonContent.Create(payload, options: JsonOptions);

        using var response = await SendAsync(request, cancellationToken).ConfigureAwait(false);
        var result = await response.Content
            .ReadFromJsonAsync<RedactResponse>(JsonOptions, cancellationToken)
            .ConfigureAwait(false);
        return result ?? throw new RedactionClientException("The instance returned an empty response.");
    }

    private HttpRequestMessage BuildRequest(HttpMethod method, string path)
    {
        var baseUrl = BaseUrl.TrimEnd('/');
        var request = new HttpRequestMessage(method, baseUrl + path);
        if (!string.IsNullOrWhiteSpace(ApiKey))
        {
            request.Headers.Add("X-Redaction-Key", ApiKey);
        }

        return request;
    }

    /// <summary>Send, turning every failure mode into one exception type with a readable message.</summary>
    private async Task<HttpResponseMessage> SendAsync(
        HttpRequestMessage request,
        CancellationToken cancellationToken)
    {
        HttpResponseMessage response;
        try
        {
            response = await _http.SendAsync(request, cancellationToken).ConfigureAwait(false);
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

        if (response.IsSuccessStatusCode)
        {
            return response;
        }

        // FastAPI puts the readable reason in {"detail": "..."}; fall back to the status.
        string detail;
        try
        {
            var body = await response.Content.ReadAsStringAsync(cancellationToken).ConfigureAwait(false);
            using var document = JsonDocument.Parse(body);
            detail = document.RootElement.TryGetProperty("detail", out var element)
                ? element.ToString()
                : response.ReasonPhrase ?? response.StatusCode.ToString();
        }
        catch (Exception)
        {
            detail = response.ReasonPhrase ?? response.StatusCode.ToString();
        }

        response.Dispose();

        if ((int)response.StatusCode == 401)
        {
            throw new RedactionClientException("The instance rejected the API key: " + detail);
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
    public List<Finding> Findings { get; set; } = new();
    public Dictionary<string, string> Mapping { get; set; } = new();
    public bool Reversible { get; set; }

    [JsonIgnore]
    public int FindingCount => Findings.Count;
}

public sealed class Finding
{
    public string EntityType { get; set; } = string.Empty;
    public double Score { get; set; }
    public string Text { get; set; } = string.Empty;
}
