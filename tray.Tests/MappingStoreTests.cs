using System;
using System.Collections.Generic;
using PromptRedactionTray.Services;
using Xunit;

namespace PromptRedactionTray.Tests;

public class MappingStoreTests
{
    private static Dictionary<string, string> Mapping(params (string Token, string Original)[] pairs)
    {
        var dict = new Dictionary<string, string>();
        foreach (var (token, original) in pairs)
        {
            dict[token] = original;
        }

        return dict;
    }

    [Fact]
    public void Restore_replaces_every_known_token()
    {
        var store = new MappingStore();
        store.Add(Mapping(
            ("<PERSON_1>", "Jane Doe"),
            ("<EMAIL_ADDRESS_1>", "jane.doe@example.com")));

        var result = store.Restore("Contact <PERSON_1> at <EMAIL_ADDRESS_1> today.");

        Assert.Equal("Contact Jane Doe at jane.doe@example.com today.", result.Text);
        Assert.Equal(2, result.Replacements);
        Assert.Equal(2, result.DistinctTokens);
    }

    [Fact]
    public void Restore_counts_repeated_occurrences_of_one_token()
    {
        var store = new MappingStore();
        store.Add(Mapping(("<PERSON_1>", "Jane Doe")));

        var result = store.Restore("<PERSON_1> replied. Ask <PERSON_1> again.");

        Assert.Equal("Jane Doe replied. Ask Jane Doe again.", result.Text);
        Assert.Equal(2, result.Replacements);
        Assert.Equal(1, result.DistinctTokens);
    }

    [Fact]
    public void Restore_works_on_reworded_text_in_any_order()
    {
        // The real case: a model's reply reuses the tokens in a different arrangement.
        var store = new MappingStore();
        store.Add(Mapping(
            ("<PERSON_1>", "Jane Doe"),
            ("<PERSON_2>", "John Roe"),
            ("<EMAIL_ADDRESS_1>", "jane.doe@example.com")));

        var reply = "Dear <PERSON_1>, <PERSON_2> has emailed <EMAIL_ADDRESS_1> already.";
        var result = store.Restore(reply);

        Assert.Equal("Dear Jane Doe, John Roe has emailed jane.doe@example.com already.", result.Text);
        Assert.Equal(3, result.DistinctTokens);
    }

    [Fact]
    public void Double_digit_tokens_are_not_eaten_by_single_digit_ones()
    {
        // Naive replacement would turn <PERSON_10> into "Jane Doe0" by matching <PERSON_1>
        // first. Tokens must be replaced longest-first.
        var store = new MappingStore();
        store.Add(Mapping(
            ("<PERSON_1>", "Jane Doe"),
            ("<PERSON_10>", "Riley Poe")));

        var result = store.Restore("<PERSON_10> met <PERSON_1>.");

        Assert.Equal("Riley Poe met Jane Doe.", result.Text);
    }

    [Fact]
    public void The_most_recent_redaction_wins_a_token_collision()
    {
        // Two separate redactions both produce <PERSON_1> for different people. Restoring
        // text the user just worked with should give the newer value, not the older one.
        var store = new MappingStore();
        store.Add(Mapping(("<PERSON_1>", "Jane Doe")));
        store.Add(Mapping(("<PERSON_1>", "Riley Poe")));

        var result = store.Restore("Ask <PERSON_1> about it.");

        Assert.Equal("Ask Riley Poe about it.", result.Text);
    }

    [Fact]
    public void Unknown_tokens_are_left_alone()
    {
        var store = new MappingStore();
        store.Add(Mapping(("<PERSON_1>", "Jane Doe")));

        var result = store.Restore("Nothing here matches <PERSON_9>.");

        Assert.Equal("Nothing here matches <PERSON_9>.", result.Text);
        Assert.Equal(0, result.Replacements);
    }

    [Fact]
    public void Expired_entries_are_dropped()
    {
        var store = new MappingStore(ttl: TimeSpan.Zero);
        store.Add(Mapping(("<PERSON_1>", "Jane Doe")));

        Assert.Equal(0, store.Count);
        Assert.Equal(0, store.Restore("<PERSON_1>").Replacements);
    }

    [Fact]
    public void Clear_forgets_everything()
    {
        var store = new MappingStore();
        store.Add(Mapping(("<PERSON_1>", "Jane Doe")));
        Assert.Equal(1, store.Count);

        store.Clear();

        Assert.Equal(0, store.Count);
        Assert.Equal(0, store.Restore("<PERSON_1>").Replacements);
    }

    [Fact]
    public void Empty_and_null_input_are_handled()
    {
        var store = new MappingStore();
        store.Add(new Dictionary<string, string>());

        Assert.Equal(0, store.Count);
        Assert.Equal(string.Empty, store.Restore(string.Empty).Text);
        Assert.Equal(string.Empty, store.Restore(null!).Text);
    }

    [Fact]
    public void Older_entries_are_evicted_past_the_cap()
    {
        var store = new MappingStore(maxEntries: 2);
        store.Add(Mapping(("<A_1>", "first")));
        store.Add(Mapping(("<B_1>", "second")));
        store.Add(Mapping(("<C_1>", "third")));

        Assert.Equal(0, store.Restore("<A_1>").Replacements);
        Assert.Equal(1, store.Restore("<C_1>").Replacements);
    }
}
