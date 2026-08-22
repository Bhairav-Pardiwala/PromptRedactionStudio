using System;
using System.Collections.Generic;
using System.Linq;

namespace PromptRedactionTray.Services;

/// <summary>
/// Holds token -> original mappings from recent redactions, in memory only.
///
/// This is the key that undoes a redaction, so it is deliberately never written to disk
/// and expires on a TTL. Keeping it here rather than on the server means a shared
/// instance never accumulates anyone's real values.
/// </summary>
public sealed class MappingStore
{
    /// <summary>One redaction's worth of tokens, with the time it was produced.</summary>
    private sealed record Entry(DateTimeOffset CreatedAt, IReadOnlyDictionary<string, string> Mapping);

    private readonly List<Entry> _entries = new();
    private readonly object _lock = new();
    private readonly TimeSpan _ttl;
    private readonly int _maxEntries;

    public MappingStore(TimeSpan? ttl = null, int maxEntries = 50)
    {
        _ttl = ttl ?? TimeSpan.FromHours(1);
        _maxEntries = maxEntries;
    }

    public void Add(IReadOnlyDictionary<string, string> mapping)
    {
        if (mapping is null || mapping.Count == 0)
        {
            return;
        }

        lock (_lock)
        {
            Prune();
            _entries.Add(new Entry(DateTimeOffset.UtcNow, new Dictionary<string, string>(mapping)));
            while (_entries.Count > _maxEntries)
            {
                _entries.RemoveAt(0);
            }
        }
    }

    /// <summary>
    /// Replace every known token in <paramref name="text"/> with its original value.
    ///
    /// Separate redactions both produce &lt;PERSON_1&gt;, for different people, so a single
    /// flattened dictionary would restore the wrong name. Entries are therefore searched
    /// newest-first and the most recent mapping containing a token wins -- which matches
    /// what someone actually means when they restore text they just worked with.
    /// </summary>
    public RestoreResult Restore(string text)
    {
        if (string.IsNullOrEmpty(text))
        {
            return new RestoreResult(text ?? string.Empty, 0, 0);
        }

        List<Entry> snapshot;
        lock (_lock)
        {
            Prune();
            snapshot = _entries.AsEnumerable().Reverse().ToList();
        }

        var resolved = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (var entry in snapshot)
        {
            foreach (var pair in entry.Mapping)
            {
                // First writer wins, and we are iterating newest-first.
                if (!resolved.ContainsKey(pair.Key))
                {
                    resolved[pair.Key] = pair.Value;
                }
            }
        }

        var replaced = 0;
        var distinctTokens = 0;
        var result = text;

        // Longest token first, so <PERSON_10> is not partially eaten by <PERSON_1>.
        foreach (var pair in resolved.OrderByDescending(p => p.Key.Length))
        {
            var occurrences = CountOccurrences(result, pair.Key);
            if (occurrences == 0)
            {
                continue;
            }

            result = result.Replace(pair.Key, pair.Value, StringComparison.Ordinal);
            replaced += occurrences;
            distinctTokens++;
        }

        return new RestoreResult(result, replaced, distinctTokens);
    }

    public int Count
    {
        get
        {
            lock (_lock)
            {
                Prune();
                return _entries.Sum(e => e.Mapping.Count);
            }
        }
    }

    public void Clear()
    {
        lock (_lock)
        {
            _entries.Clear();
        }
    }

    /// <summary>Drop entries past their TTL. Callers must hold the lock.</summary>
    private void Prune()
    {
        var cutoff = DateTimeOffset.UtcNow - _ttl;
        _entries.RemoveAll(entry => entry.CreatedAt < cutoff);
    }

    private static int CountOccurrences(string haystack, string needle)
    {
        if (string.IsNullOrEmpty(needle))
        {
            return 0;
        }

        var count = 0;
        var index = haystack.IndexOf(needle, StringComparison.Ordinal);
        while (index >= 0)
        {
            count++;
            index = haystack.IndexOf(needle, index + needle.Length, StringComparison.Ordinal);
        }

        return count;
    }
}

/// <param name="Text">The text with tokens replaced.</param>
/// <param name="Replacements">Total occurrences replaced.</param>
/// <param name="DistinctTokens">How many different tokens were found.</param>
public readonly record struct RestoreResult(string Text, int Replacements, int DistinctTokens);
