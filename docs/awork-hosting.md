# awork hosting

The public endpoint is `https://google-ads-mcp.awork.com/mcp`.
Source: [awork-io/google-ads-mcp](https://github.com/awork-io/google-ads-mcp).

## Azure resources

| Resource | Value |
| --- | --- |
| Subscription | awork (`f337ef60-cbc1-4fd0-9638-26284a20a205`) |
| Container App / resource group | `google-ads-mcp` / `awork-global` |
| Environment | `awork-mcp-vnet-v3`, Germany West Central |
| Image | `awork.azurecr.io/integrations/google-ads-mcp` |
| Identity | `google-ads-mcp`, with AcrPull on the awork registry |
| App resources | 0.5 CPU, 1 GiB, 1–3 replicas |
| Front Door profile / resource group | `awork-global` / `awork-global-new` |
| Front Door endpoint | `production` |
| Origin group / origin / route | `google-ads-mcp` |
| Custom domain | `google-ads-mcp-awork-com` |
| TLS certificate | Existing `awork-awork-com-wildcard-latest` secret |
| Health probe | HTTPS GET `/healthz` |

Front Door forwards all paths to the Container App over HTTPS, with the
Container App hostname as the origin Host header. Caching is disabled. The
existing wildcard DNS resolves the public hostname to Front Door. No existing
MCP route or rule set was changed.

The [Container App manifest](azure-containerapp.yaml) records the deployed image
digest and configuration. The current build is ACR run `dn55`, from source
commit `dbd7661`. The server allows the existing production Google Workspace
OAuth client ID. No client secret or user tokens are stored on the server.
Without a public OAuth client ID, all MCP requests fail closed.

## Update the server

Pull upstream changes, keep awork's changes, and run the focused checks before
building. Choose a new immutable tag, then:

```sh
uv sync --locked --extra dev
uv run nox -s lint
uv run python -m unittest discover --buffer -s tests -p '*_test.py'
az acr build -r awork -t integrations/google-ads-mcp:NEW_TAG --platform linux/amd64 .
az acr repository show -n awork --image integrations/google-ads-mcp:NEW_TAG --query digest -o tsv
```

Update the manifest to the returned digest and deploy it:

```sh
az containerapp update -g awork-global -n google-ads-mcp --yaml docs/azure-containerapp.yaml
```

Preserve the public client ID in the manifest on future deployments. Never add
the client secret to this manifest.

## Google OAuth and public release

1. Reuse the production Google Workspace OAuth web app and confirm Google Ads
   API access for its Google Cloud project. Keep awork's callback:
   `https://app.awork.com/api/v1/agents/connectors/auth/callback`.
2. Configure the consent screen for `openid`, `email`, `profile`, and
   `https://www.googleapis.com/auth/adwords`. Allow the intended audience.
3. `GOOGLE_ADS_MCP_OAUTH_CLIENT_ID` is set on the Container App to the production
   app's public client ID. The server accepts only tokens issued to this app.
   It does not need a client secret, refresh token, storage volume, or signing key.
4. awork's production connector configuration reuses the existing public client
   ID and `GoogleMCPClientSecretProduction` reference. Deploy that configuration
   with the preset. Develop still has pending `GoogleAdsMCPClientIdDevelop` and
   `GoogleAdsMCPClientSecretDevelop` references. The shared endpoint expects the
   production OAuth app ID. Use a separate deployment for a different app ID.
5. Enable the `google-ads` preset in awork and deploy the connector/config changes.
6. Test sign-in through connector setup, tool discovery, an account read for
   each of two users, refresh, reconnect, and a container revision/restart.
   Confirm users only see accounts allowed by their own Google identity.

awork stores and refreshes each connection's tokens. Each MCP request carries
the current Google access token; the server validates it and passes it to the
Google Ads client for that request. The Google Ads `adwords` scope is broad,
but the official MCP tools exposed by this fork are read-only.

## Verification

On 25 October 2026, the public hostname passed health and protected-resource
discovery checks. MCP initialize returned 401 for missing and dummy tokens, with
the correct metadata challenge and no session ID. Local OAuth routes returned
404. Revision `google-ads-mcp--0000002` now serves all traffic and registers the
production OAuth client ID. Before this configuration change, token validation
rejected every request before contacting Google. Health and invalid-token checks
still pass after the change.
The Python 3.12 suite ran 74 tests (three optional storage tests skipped), both
smoke tests passed, and formatting passed.

The HTTP tests mock only Google token validation and Google Ads responses. They
exercise discovery, missing/invalid/expired/wrong-app/missing-scope tokens,
parallel users, replacement access tokens, and missing-token rejection after
initialize. They also prove that no session ID is created and server credentials
are never used in external mode. Full Google sign-in, account reads, and refresh
still need live validation with an authorized account.
