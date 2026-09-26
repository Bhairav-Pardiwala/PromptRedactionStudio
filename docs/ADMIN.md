# Administrator guide

Deploying Prompt Redaction Studio to a team: one server, many desktop clients.

Setting it up for yourself on one machine? You don't need this page —
[`Start.cmd` / `./start.sh`](../README.md#just-run-it) is the whole install, and everything
here is off by default.

---

## Choose a setup

| | **Setup A — shared key** | **Setup B — single sign-on** |
|---|---|---|
| Sign-in | one key, shared by everyone | each person's own account (Entra ID or any OIDC provider) |
| Choose it when | the server is on a private network or VPN | the server is reachable from wherever your users are, or a security review needs per-user access |
| You need | a server and HTTPS | a server, two app registrations, two oauth2-proxy containers |
| Who did what | not recorded | per user, in the proxy's logs |
| Removing someone | rotate the key for everyone | disable their account |

Follow one setup from start to finish. The [reference](#reference) after them explains
every setting.

---

## Setup A — shared key

**1. Generate two keys.** One for everyday use, one for the admin-only "clear everything" action.

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"   # run twice
```

**2. Start the server.**

```bash
docker run -d --name redaction-server --restart unless-stopped \
  -p 127.0.0.1:8000:8000 \
  -e REDACTION_API_KEY='<first key>' \
  -e REDACTION_ADMIN_KEY='<second key>' \
  bhairavpardiwala/prompt-redaction-studio
```

**3. Put HTTPS in front of it.** Point your existing reverse proxy or load balancer at
`127.0.0.1:8000` and give it a hostname, e.g. `https://redaction.corp.example.com`.
[Why this is not optional](#https-is-required).

**4. Deploy the desktop client.** Push this file to every machine as
`%ProgramData%\PromptRedactionTray\managed.json`
([macOS and Linux paths](#where-the-files-live)):

```json
{
  "InstanceUrl": "https://redaction.corp.example.com",
  "ApiKey": "<first key>"
}
```

**5. Check it.** Right-click the tray icon — the status line reads **Connected**. Open the
website — it asks once for the key.

Keep the second key with your operations team. Employees never need it.

---

## Setup B — single sign-on with Microsoft Entra ID

You will end up with:

| Address | Serves | Signs in by |
|---|---|---|
| `https://redaction.corp.example.com` | browsers | Entra login page |
| `https://redaction.corp.example.com:8443` | the desktop client | Entra token from the tray's own sign-in |
| the server | nothing directly | reachable only from the two proxies |

Using a different identity provider? Follow the same steps with
[these differences](#other-identity-providers).

### 1. Register the API

In the Entra admin center → **App registrations** → **New registration**:

1. Name `Prompt Redaction API`, single tenant, no redirect URI → **Register**.
   Note the **Application (client) ID** and **Directory (tenant) ID**.
2. **Expose an API** → Application ID URI → **Add** (accept `api://<api-client-id>`) →
   **Add a scope**: name `redact`, consent *Admins and users*, **Enabled**.
3. **Manifest** → set `requestedAccessTokenVersion` to `2` → **Save**.
   [Skipping this breaks every request](#entra-v1-and-v2-tokens).
4. **Authentication** → **Add a platform** → **Web** → redirect URI
   `https://redaction.corp.example.com/oauth2/callback`.
5. **Certificates & secrets** → **New client secret** → copy the **Value** (not the Secret
   ID) into a file on the server, e.g. `/etc/redaction/client-secret`.

### 2. Register the desktop client

1. **New registration**: name `Prompt Redaction Tray`, single tenant, redirect URI platform
   **Public client/native (mobile & desktop)**, value `http://127.0.0.1/` → **Register**.
   Note its client ID.
2. **Authentication** → **Allow public client flows** → **Yes** → **Save**.
3. **API permissions** → **Add a permission** → **APIs my organization uses** → search
   `Prompt Redaction API` → **Delegated** → tick `redact` → **Add** →
   **Grant admin consent**.

No client secret for this one — it is a desktop app, and PKCE takes the place of a secret.

### 3. Start the server and both proxies

```bash
TENANT=<tenant-id>
API=<api-client-id>
docker network create redaction

# The server. No published port: only the proxies can reach it.
docker run -d --name redaction-server --network redaction --restart unless-stopped \
  bhairavpardiwala/prompt-redaction-studio

# Proxy for browsers: Entra login page, then a cookie session.
docker run -d --name redaction-web --network redaction --restart unless-stopped \
  -p 443:4443 -v /etc/redaction:/etc/redaction:ro \
  quay.io/oauth2-proxy/oauth2-proxy:v7.15.4 \
  --provider=oidc \
  --oidc-issuer-url=https://login.microsoftonline.com/$TENANT/v2.0 \
  --client-id=$API \
  --client-secret-file=/etc/redaction/client-secret \
  --cookie-secret=$(python -c "import secrets; print(secrets.token_urlsafe(24))") \
  --email-domain='*' --oidc-email-claim=sub \
  --skip-jwt-bearer-tokens=true \
  --https-address=0.0.0.0:4443 \
  --tls-cert-file=/etc/redaction/web.crt --tls-key-file=/etc/redaction/web.key \
  --redirect-url=https://redaction.corp.example.com/oauth2/callback \
  --upstream=http://redaction-server:8000

# Proxy for the desktop client: bearer tokens only, never a login page.
docker run -d --name redaction-api --network redaction --restart unless-stopped \
  -p 8443:4443 -v /etc/redaction:/etc/redaction:ro \
  quay.io/oauth2-proxy/oauth2-proxy:v7.15.4 \
  --provider=oidc \
  --oidc-issuer-url=https://login.microsoftonline.com/$TENANT/v2.0 \
  --client-id=$API \
  --client-secret-file=/etc/redaction/client-secret \
  --cookie-secret=$(python -c "import secrets; print(secrets.token_urlsafe(24))") \
  --email-domain='*' --oidc-email-claim=sub \
  --skip-jwt-bearer-tokens=true --bearer-token-login-fallback=false \
  --https-address=0.0.0.0:4443 \
  --tls-cert-file=/etc/redaction/web.crt --tls-key-file=/etc/redaction/web.key \
  --upstream=http://redaction-server:8000
```

[Why two proxies](#why-two-proxies).

### 4. Deploy the desktop client

Push this file to every machine as `%ProgramData%\PromptRedactionTray\managed.json`
([macOS and Linux paths](#where-the-files-live)):

```json
{
  "InstanceUrl": "https://redaction.corp.example.com:8443",
  "WebUiUrl": "https://redaction.corp.example.com",
  "OidcIssuer": "https://login.microsoftonline.com/<tenant-id>/v2.0",
  "OidcClientId": "<tray-client-id>",
  "OidcScope": "api://<api-client-id>/redact"
}
```

### 5. Check it

- **Website:** open `https://redaction.corp.example.com` → **Sign in with OpenID Connect**
  → Entra → the redaction page, with no key prompt.
- **Desktop client:** right-click the tray icon → **Sign in…** → a browser tab signs in
  and closes → the status line reads **Connected** → copy text, press **Ctrl+Alt+R**,
  paste.

Sign-in works but requests fail? See [Troubleshooting](#troubleshooting) — it is almost
always the token version or audience.

### Other identity providers

The steps are the same; only the registrations differ.

- **Okta:** the API is an *API Services* / custom authorization server with a `redact`
  scope; the desktop client is a **Native Application** with grant types Authorization Code
  and Refresh Token, redirect `http://127.0.0.1/`. Issuer
  `https://<org>.okta.com/oauth2/<server-id>`.
- **Keycloak, Auth0, Google and others:** any provider with a standard
  `/.well-known/openid-configuration` works. Register a confidential web client for the
  browser proxy and a public client with the loopback redirect and refresh tokens for the
  desktop client.
- If the token's `aud` is not the proxies' `--client-id`, add
  `--oidc-extra-audience=<the aud value>` to both proxies.

---

## Reference

### Server configuration

Every variable is optional. With none set, the server is open — the single-user default.

| Variable | Effect |
|---|---|
| `REDACTION_API_KEY` | Requires header `X-Redaction-Key` on `/api/analyze`, `/api/redact`, `/api/restore`, `/api/policy`, `DELETE /api/sessions/{id}`. |
| `REDACTION_ADMIN_KEY` | Requires header `X-Redaction-Admin-Key` on `POST /api/sessions/clear`, which discards every client's mapping. Unset: falls back to `REDACTION_API_KEY`. |
| `REDACTION_ENABLE_DOCS` | `1` / `0` forces the `/docs` API pages on or off. Unset: on without an API key, off with one. |
| `REDACTION_POLICY_FILE` | YAML of org-wide redaction defaults — see [Central policy](#central-policy). |

`/api/health` and `/api/config` stay open: load balancers probe the first, and the website
needs the second to know whether to ask for a key. Neither returns a key or prompt content.

To confirm a key took effect, ask the instance rather than reading its logs:
`curl -s https://redaction.corp.example.com/api/config` reports `auth_required` and
`admin_auth_required` as the running server sees them. The server never logs a key.

**Memory:** about 1 GB once the large language model is loaded.
`--build-arg INCLUDE_LARGE_MODEL=false` halves it, at a real cost in detection quality.

### Desktop client configuration

| Field | Environment variable | Meaning |
|---|---|---|
| `InstanceUrl` | `PRT_INSTANCE_URL` | Where the client sends requests. |
| `WebUiUrl` | `PRT_WEB_UI_URL` | Where **Open web UI** goes. Leave out when one address serves both. |
| `ApiKey` | `PRT_API_KEY` | Setup A only. |
| `OidcIssuer` | `PRT_OIDC_ISSUER` | Setup B. Tenant-specific, e.g. `https://login.microsoftonline.com/<tenant-id>/v2.0`. |
| `OidcClientId` | `PRT_OIDC_CLIENT_ID` | Setup B. The desktop client's registration. |
| `OidcScope` | `PRT_OIDC_SCOPE` | Setup B. The API's scope. `openid profile offline_access` are added automatically. |
| `RedactHotkey`, `RestoreHotkey` | — | Default `Ctrl+Alt+R`, `Ctrl+Alt+U`. |
| `StartWithWindows` | — | Start at sign-in (Windows). |

Settings are merged **per field**, most significant first: `managed.json` → environment
variables → the user's own `settings.json` → defaults. A value from `managed.json` is never
copied into the user's file, and removing `managed.json` hands control back to the user.

The identity provider values are not secrets — a desktop client's ID appears in the address
bar on every sign-in. The Setup A `ApiKey` *is* a secret.

#### Where the files live

| | `managed.json` (you deploy) | `settings.json` (per user) |
|---|---|---|
| Windows | `%ProgramData%\PromptRedactionTray\` | `%APPDATA%\PromptRedactionTray\` |
| macOS | `/Library/Application Support/PromptRedactionTray/` | `~/.config/PromptRedactionTray/` |
| Linux | `/etc/promptredactiontray/` | `~/.config/PromptRedactionTray/` |

Deploy `managed.json` with your existing tooling — Intune, Group Policy, Jamf, Ansible.
Readable by everyone, writable only by administrators.

#### What is stored on each machine

| File | Contents |
|---|---|
| `settings.json` | URL, hotkeys, and in Setup A the key — plain text |
| `tokens.json` | Setup B refresh token. Windows: encrypted for that user (DPAPI). macOS/Linux: owner-only file, **not encrypted** |
| `log.txt` | Events and counts only |

No redaction mapping — the thing that would undo a redaction — is ever written to disk, on
the client or the server. It lives in memory for an hour.

### Central policy

```yaml
# REDACTION_POLICY_FILE
score_threshold: 0.4
entities: [PERSON, EMAIL_ADDRESS, PHONE_NUMBER, CREDIT_CARD, US_SSN]
default_operator: { type: placeholder }
per_entity_operators:
  CREDIT_CARD: { type: hash, params: { hash_type: sha256 } }
allow_list: [Acme Corp]
```

Omitted keys keep their defaults; unknown keys are ignored and logged. **The policy is
advisory:** the desktop client follows it, but the server does not enforce it, so someone
calling the API directly can switch redaction off.

### Distributing the desktop client

Releases carry self-contained executables for Windows, macOS and Linux with SHA-256
checksums and build attestations:

```bash
gh attestation verify --owner Bhairav-Pardiwala PromptRedactionTray-win-x64.exe
```

They are **not code-signed**: sign them or allow-list them in your tooling. On macOS, ship
the whole extracted folder, not the bare binary.

### Notes on the design

#### HTTPS is required

The server speaks plain HTTP. Without HTTPS in front, every prompt, every token mapping and
the key cross the network in clear text.

#### Run one server process

The website's restore data lives in the memory of the process that created it. A second
worker or replica makes restore fail for about half of browser sessions. The desktop client
is unaffected. Scale the machine, not the process count.

#### A shared key is access control, not attribution

Setup A proves a request was allowed; it cannot say who made it. For that, use Setup B.

#### Why two proxies

Browsers and the desktop client sign in differently. Browsers need oauth2-proxy's login
page and a cookie; the desktop client sends an Entra token and must never be redirected to
a login page it cannot complete. oauth2-proxy also serves either HTTPS or HTTP from one
container, not both. One proxy per kind of client keeps each configuration simple.

#### Tested configuration

Setup B was tested end to end against a real Entra tenant with oauth2-proxy v7.15.4: the
desktop client's own sign-in, a real redaction through the proxy, then a silent token
refresh. How the desktop-client proxy answers:

| Request | Response |
|---|---|
| Valid token | `200`, reaches the server |
| No token, expired token, wrong audience, malformed token | `403` with an HTML page |

All rejections look the same, which is why the desktop client reports "behind a sign-in
proxy" for any of them, and why [Troubleshooting](#troubleshooting) starts with the token.

#### Entra v1 and v2 tokens

Unless the API registration's manifest sets `requestedAccessTokenVersion` to `2`, Entra
issues v1 tokens, whose issuer (`https://sts.windows.net/<tenant>/`) does not match the
proxy's configured issuer. Every request then fails with `403`. In v2 tokens, `aud` is the
API's **client ID GUID**, not its `api://` URI.

#### Other Entra details

- Use the tenant-specific issuer, never `/common` — the `/common` discovery document returns
  a placeholder issuer (`{tenantid}`) that no real token can match.
- Entra's discovery document doesn't advertise PKCE; the desktop client uses it anyway.
- `--oidc-email-claim=sub` is needed because Entra tokens often carry no `email` claim.
- Device code sign-in is not used and need not be allowed. It is phishable, and many
  tenants block it.

#### Other proxies

- **Cloudflare Access:** works for the desktop client with Access *service tokens*, which
  are Cloudflare's own credentials rather than Entra tokens.
- **Entra Application Proxy:** fine as a tunnel with pre-authentication set to
  *Passthrough*, with oauth2-proxy behind it doing the sign-in. As the authenticator itself
  it is built for browsers; not tested with the desktop client.

---

## Known limitations

- **No audit trail.** The server does not record who redacted or restored what. In Setup B,
  the proxy's access logs are the only record.
- **Central policy is not enforced by the server.**
- **Sign-in tokens are not encrypted on macOS and Linux** — owner-only file permissions only.
- **Binaries are not code-signed.**
- **`managed.json` values can be overridden** for the current session in the Settings window.
  They are restored on the next start.
- **Not security-audited.** A redaction tool, not a compliance product.

---

## Troubleshooting

The desktop client logs to `%APPDATA%\PromptRedactionTray\log.txt` (Windows) or
`~/.config/PromptRedactionTray/log.txt`: which settings sources were used and any file that
failed to load. It never logs clipboard contents or mappings.

| Symptom | Fix |
|---|---|
| Sign-in works, every request fails | Decode the token locally. `ver` must be `2.0` — if it is `1.0`, set `requestedAccessTokenVersion` to `2` on the API registration. `aud` must be the API's client ID GUID. |
| *"…sits behind a sign-in proxy that did not accept this client"* | The proxy refused the request: not signed in, token expired, wrong audience, or the proxy is missing `--skip-jwt-bearer-tokens=true`. |
| `AADSTS50011` redirect URI mismatch | The error names the URI that was sent. From the desktop client (`http://127.0.0.1:<port>/`): add `http://127.0.0.1/` under *Mobile and desktop* on the **tray** registration. From a browser (`…/oauth2/callback`): add that exact URL under *Web* on the **API** registration. |
| `AADSTS7000215` invalid client secret | The proxy's secret file holds the Secret **ID**, not the **Value**. |
| **Open web UI** lands on a sign-in page that fails | Set `WebUiUrl` to the browser address. |
| *"The instance rejected the API key"* | Setup A: the client's `ApiKey` doesn't match the server's `REDACTION_API_KEY`. |
| *"Settings could not be read"* | A settings file has a syntax error; the log names it. The client is running on defaults. |
| Tray shows **Unreachable**, but the website works | Expected until the tray signs in: the browser has a session, the tray doesn't. |
| Restore fails for website users only | More than one server process — [run one](#run-one-server-process). |
