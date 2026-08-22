using System;
using System.Linq;
using System.Threading.Tasks;
using PromptRedactionTray.Services;
using Xunit;

namespace PromptRedactionTray.Tests;

/// <summary>
/// Exercises the real client against a running instance. Skipped automatically when no
/// instance is reachable, so the suite still passes on a machine without the backend.
///
/// Point elsewhere with REDACTION_TEST_URL.
/// </summary>
public class LiveBackendTests
{
    private static string BaseUrl =>
        Environment.GetEnvironmentVariable("REDACTION_TEST_URL") ?? "http://127.0.0.1:8000";

    /// <summary>Set REDACTION_TEST_REQUIRE=1 to fail rather than skip when nothing answers.</summary>
    private static bool Required =>
        Environment.GetEnvironmentVariable("REDACTION_TEST_REQUIRE") == "1";

    private static async Task<RedactionClient?> TryConnectAsync()
    {
        var client = new RedactionClient { BaseUrl = BaseUrl };
        if (await client.IsReachableAsync())
        {
            return client;
        }

        client.Dispose();
        Assert.False(Required, "REDACTION_TEST_REQUIRE=1 but no instance answered at " + BaseUrl);
        return null;
    }

    [Fact]
    public async Task Redact_then_restore_round_trips_through_the_real_client()
    {
        var client = await TryConnectAsync();
        if (client is null)
        {
            return; // no instance running; nothing to assert
        }

        using (client)
        {
            const string original =
                "Please email Jane Doe at jane.doe@example.com or call +1 (415) 555-0182. " +
                "Their card is 4111 1111 1111 1111.";

            var policy = await client.GetPolicyAsync();
            var result = await client.RedactAsync(original, policy);

            // The server must not have kept anything: that is the whole point of the
            // stateless contract this client relies on.
            Assert.Null(result.SessionId);
            Assert.NotEmpty(result.Mapping);

            // No real value may survive into the text that would be sent to a model.
            foreach (var value in result.Mapping.Values)
            {
                Assert.DoesNotContain(value, result.RedactedText, StringComparison.Ordinal);
            }

            Assert.DoesNotContain("jane.doe@example.com", result.RedactedText, StringComparison.Ordinal);

            var store = new MappingStore();
            store.Add(result.Mapping);

            var restored = store.Restore(result.RedactedText);
            Assert.Equal(original, restored.Text);
        }
    }

    [Fact]
    public async Task Restore_survives_a_reworded_reply()
    {
        var client = await TryConnectAsync();
        if (client is null)
        {
            return;
        }

        using (client)
        {
            const string original =
                "Jane Doe emailed jane.doe@example.com about invoice 12 on +1 (415) 555-0182.";

            var policy = await client.GetPolicyAsync();
            var result = await client.RedactAsync(original, policy);

            var store = new MappingStore();
            store.Add(result.Mapping);

            // Simulate a model answering: same tokens, different sentence, reordered.
            var tokens = result.Mapping.Keys.OrderBy(k => k).ToList();
            Assert.True(tokens.Count >= 2, "expected at least two tokens to reorder");

            var reply = "I have contacted " + tokens[^1] + " and copied " + tokens[0] +
                        ". " + tokens[0] + " will follow up.";

            var restored = store.Restore(reply);

            foreach (var token in tokens.Take(1).Concat(tokens.TakeLast(1)))
            {
                Assert.DoesNotContain(token, restored.Text, StringComparison.Ordinal);
                Assert.Contains(result.Mapping[token], restored.Text, StringComparison.Ordinal);
            }
        }
    }

    [Fact]
    public async Task Applied_count_reflects_replacements_not_overlapping_findings()
    {
        var client = await TryConnectAsync();
        if (client is null)
        {
            return;
        }

        using (client)
        {
            // An email address also matches the URL recognizer, so the analyzer reports
            // more findings than the anonymizer ends up applying. The toast must report
            // the latter, or it tells the user more changed than actually did.
            const string text = "Escalation from Jane Doe, jane.doe@example.com, +1 (415) 555-0182.";

            var policy = await client.GetPolicyAsync();
            var result = await client.RedactAsync(text, policy);

            Assert.NotEmpty(result.Items);
            Assert.True(
                result.AppliedCount <= result.Findings.Count,
                "applied replacements cannot exceed raw findings");

            // Every entity type named in the summary must really appear in the output.
            foreach (var item in result.Items)
            {
                Assert.Contains(item.Text, result.RedactedText, StringComparison.Ordinal);
            }
        }
    }

    [Fact]
    public async Task An_unreachable_instance_reports_cleanly_rather_than_throwing()
    {
        using var client = new RedactionClient { BaseUrl = "http://127.0.0.1:59999" };

        Assert.False(await client.IsReachableAsync());

        var exc = await Assert.ThrowsAsync<RedactionClientException>(
            () => client.RedactAsync("some text", null));
        Assert.Contains("Could not reach", exc.Message, StringComparison.Ordinal);
    }
}
