using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace PromptRedactionTray.Services;

/// <summary>Encrypts a secret at rest, as far as the platform allows.</summary>
public interface ITokenProtector
{
    string Protect(string plaintext);

    string? Unprotect(string stored);

    /// <summary>False when the platform gave us nothing better than plaintext.</summary>
    bool IsEncrypted { get; }
}

/// <summary>
/// Windows DPAPI, scoped to the signed-in user. This stops another account on the same
/// machine reading the file. It cannot stop the signed-in user reading it -- and should
/// not pretend to, because that user is the one the token belongs to.
/// </summary>
[SupportedOSPlatform("windows")]
public sealed class DpapiTokenProtector : ITokenProtector
{
    private static readonly byte[] Entropy = Encoding.UTF8.GetBytes("PromptRedactionTray.tokens.v1");

    public bool IsEncrypted => true;

    public string Protect(string plaintext) => Convert.ToBase64String(
        ProtectedData.Protect(Encoding.UTF8.GetBytes(plaintext), Entropy, DataProtectionScope.CurrentUser));

    public string? Unprotect(string stored)
    {
        try
        {
            var bytes = ProtectedData.Unprotect(
                Convert.FromBase64String(stored), Entropy, DataProtectionScope.CurrentUser);
            return Encoding.UTF8.GetString(bytes);
        }
        catch (Exception)
        {
            // Copied from another machine or another account: unusable, not fatal.
            return null;
        }
    }
}

/// <summary>Fallback where no OS keystore is wired up. Honest about offering nothing.</summary>
public sealed class PlaintextTokenProtector : ITokenProtector
{
    public bool IsEncrypted => false;

    public string Protect(string plaintext) => plaintext;

    public string? Unprotect(string stored) => stored;
}

/// <summary>
/// Holds the tokens for a signed-in session and keeps them fresh.
///
/// The access token stays in memory only. The refresh token is persisted, because the
/// alternative is sending the user back to the browser on every reboot -- which is the
/// kind of friction that gets a security control switched off. It is a credential, not
/// prompt content, so it does not touch the "nothing is written to disk" rule that
/// covers the token to original mapping in <see cref="MappingStore"/>.
/// </summary>
public sealed class TokenStore
{
    private readonly string _path;
    private readonly ITokenProtector _protector;
    private readonly SemaphoreSlim _gate = new(1, 1);

    private TokenSet? _tokens;
    private string? _refreshToken;

    public TokenStore(string? path = null, ITokenProtector? protector = null)
    {
        _path = path ?? Path.Combine(
            Path.GetDirectoryName(SettingsPaths.Default.UserFile) ?? ".", "tokens.json");
        _protector = protector ?? DefaultProtector();
        _refreshToken = ReadRefreshToken();
    }

    public static ITokenProtector DefaultProtector() =>
        RuntimeInformation.IsOSPlatform(OSPlatform.Windows)
            ? new DpapiTokenProtector()
            : new PlaintextTokenProtector();

    /// <summary>True when a sign-in has happened and can still be renewed silently.</summary>
    public bool HasSession => _tokens is not null || _refreshToken is not null;

    /// <summary>False when the refresh token is sitting on disk unencrypted.</summary>
    public bool IsProtectedAtRest => _protector.IsEncrypted;

    public void Accept(TokenSet tokens)
    {
        _tokens = tokens;
        if (!string.IsNullOrEmpty(tokens.RefreshToken))
        {
            _refreshToken = tokens.RefreshToken;
            WriteRefreshToken(tokens.RefreshToken!);
        }
    }

    /// <summary>
    /// A usable access token, refreshing if needed. Null means the user must sign in --
    /// there is no token and no way to get one without them.
    /// </summary>
    public async Task<string?> GetAccessTokenAsync(OidcClient client, CancellationToken ct = default)
    {
        await _gate.WaitAsync(ct).ConfigureAwait(false);
        try
        {
            if (_tokens is not null && !_tokens.IsExpired)
            {
                return _tokens.AccessToken;
            }

            if (_refreshToken is null)
            {
                return null;
            }

            try
            {
                var refreshed = await client.RefreshAsync(_refreshToken, ct).ConfigureAwait(false);
                _tokens = refreshed;
                if (!string.IsNullOrEmpty(refreshed.RefreshToken))
                {
                    // Providers that rotate refresh tokens hand back a new one each time;
                    // keeping the old one would break the next renewal.
                    _refreshToken = refreshed.RefreshToken;
                    WriteRefreshToken(refreshed.RefreshToken!);
                }

                return refreshed.AccessToken;
            }
            catch (OidcException)
            {
                // Revoked, expired, or the user was removed. Forget it and ask for a
                // fresh sign-in rather than retrying a token that will never work.
                Forget();
                return null;
            }
        }
        finally
        {
            _gate.Release();
        }
    }

    /// <summary>Drop the session, in memory and on disk.</summary>
    public void Forget()
    {
        _tokens = null;
        _refreshToken = null;
        try
        {
            if (File.Exists(_path))
            {
                File.Delete(_path);
            }
        }
        catch (IOException)
        {
            // Best effort; the in-memory copy is gone either way.
        }
    }

    private sealed class Stored
    {
        public string? RefreshToken { get; set; }

        public bool Encrypted { get; set; }
    }

    private string? ReadRefreshToken()
    {
        try
        {
            if (!File.Exists(_path))
            {
                return null;
            }

            var stored = JsonSerializer.Deserialize<Stored>(File.ReadAllText(_path));
            if (stored?.RefreshToken is null)
            {
                return null;
            }

            return stored.Encrypted ? _protector.Unprotect(stored.RefreshToken) : stored.RefreshToken;
        }
        catch (Exception)
        {
            // An unreadable token file just means signing in again.
            return null;
        }
    }

    private void WriteRefreshToken(string refreshToken)
    {
        try
        {
            var directory = Path.GetDirectoryName(_path);
            if (!string.IsNullOrEmpty(directory))
            {
                Directory.CreateDirectory(directory);
            }

            var stored = new Stored
            {
                RefreshToken = _protector.Protect(refreshToken),
                Encrypted = _protector.IsEncrypted,
            };

            File.WriteAllText(_path, JsonSerializer.Serialize(stored));
            RestrictToOwner(_path);
        }
        catch (Exception)
        {
            // Failing to persist costs a sign-in next launch, which is not worth a crash.
        }
    }

    /// <summary>On Unix, keep the file to the owner. Windows relies on DPAPI instead.</summary>
    private static void RestrictToOwner(string path)
    {
        if (RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
        {
            return;
        }

        try
        {
            File.SetUnixFileMode(path, UnixFileMode.UserRead | UnixFileMode.UserWrite);
        }
        catch (Exception)
        {
            // Not every filesystem supports it.
        }
    }
}
