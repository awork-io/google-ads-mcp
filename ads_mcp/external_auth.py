"""Google token verification for clients that own OAuth and token refresh."""

import os
import time

from fastmcp.server.auth import AccessToken, RemoteAuthProvider
from fastmcp.server.auth.providers.google import GoogleTokenVerifier
from pydantic import AnyHttpUrl
from starlette.responses import JSONResponse
from starlette.routing import Route

GOOGLE_ISSUER = "https://accounts.google.com"
ADS_SCOPE = "https://www.googleapis.com/auth/adwords"
OAUTH_SCOPES = ["openid", "email", "profile", ADS_SCOPE]


def uses_external_oauth() -> bool:
    """Return whether the client owns Google sign-in and refresh."""
    return os.environ.get("GOOGLE_ADS_MCP_EXTERNAL_OAUTH", "").lower() == "true"


class ExternalGoogleTokenVerifier(GoogleTokenVerifier):
    """Reject all tokens until an allowed Google OAuth client ID is configured."""

    async def verify_token(self, token: str) -> AccessToken | None:
        if not self.audience:
            return None
        verified = await super().verify_token(token)
        if (
            verified is None
            or verified.expires_at is None
            or verified.expires_at <= time.time()
        ):
            return None
        return verified


class ExternalGoogleAuthProvider(RemoteAuthProvider):
    """Publish Google discovery without a local OAuth server or token store."""

    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        routes = super().get_routes(mcp_path)
        payload = {
            "resource": str(self._get_resource_url(mcp_path)),
            # Google's OIDC issuer has no trailing slash. AnyHttpUrl adds one.
            "authorization_servers": [GOOGLE_ISSUER],
            "scopes_supported": OAUTH_SCOPES,
            "bearer_methods_supported": ["header"],
            "resource_name": "Google Ads MCP",
        }

        async def metadata(_request):
            return JSONResponse(
                payload,
                headers={
                    "Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET, OPTIONS",
                    "Cache-Control": "public, max-age=3600",
                },
            )

        return [
            Route(route.path, metadata, methods=["GET", "OPTIONS"])
            for route in routes
        ]


def create_external_auth() -> ExternalGoogleAuthProvider:
    """Configure stateless authentication; no client secret is needed here."""
    client_id = os.environ.get("GOOGLE_ADS_MCP_OAUTH_CLIENT_ID", "").strip()
    verifier = ExternalGoogleTokenVerifier(
        audience=client_id or [], required_scopes=[ADS_SCOPE]
    )
    return ExternalGoogleAuthProvider(
        token_verifier=verifier,
        authorization_servers=[AnyHttpUrl(GOOGLE_ISSUER)],
        base_url=os.environ["GOOGLE_ADS_MCP_BASE_URL"],
        scopes_supported=OAUTH_SCOPES,
        challenge_scopes=OAUTH_SCOPES,
        resource_name="Google Ads MCP",
    )
