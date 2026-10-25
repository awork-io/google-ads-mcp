"""Exercise stateless HTTP authentication with a fake Google token service."""

import asyncio
import contextlib
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import httpx2
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from starlette.testclient import TestClient

from ads_mcp import utils
from ads_mcp.external_auth import (
    ADS_SCOPE,
    ExternalGoogleTokenVerifier,
    create_external_auth,
)
from ads_mcp.tools.core import customers_mcp


class ExternalAuthTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.env = patch.dict(
            os.environ,
            {
                "GOOGLE_ADS_MCP_EXTERNAL_OAUTH": "true",
                "GOOGLE_ADS_MCP_BASE_URL": "https://ads.example.com",
                "GOOGLE_ADS_MCP_OAUTH_CLIENT_ID": "awork-client",
            },
            clear=True,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.auth = create_external_auth()
        self.google = httpx2.AsyncClient(
            transport=httpx2.MockTransport(self.google_response)
        )
        self.auth.token_verifier._http_client = self.google
        self.addCleanup(lambda: asyncio.run(self.google.aclose()))
        self.mcp = FastMCP("Ads test", auth=self.auth)
        self.mcp.mount(customers_mcp)
        self.client = self.stack.enter_context(
            TestClient(
                self.mcp.http_app(stateless_http=True, json_response=True)
            )
        )
        self.ads = self.stack.enter_context(
            patch.object(utils, "GoogleAdsClient")
        )

        def ads_client(**kwargs):
            token = kwargs["credentials"].token
            service = MagicMock()
            service.list_accessible_customers.return_value.resource_names = [
                "customers/" + token.removeprefix("user-")
            ]
            client = MagicMock()
            client.get_service.return_value = service
            return client

        self.ads.side_effect = ads_client
        self.adc = self.stack.enter_context(patch("google.auth.default"))

    def google_response(self, request):
        token = request.headers["authorization"].removeprefix("Bearer ")
        if request.url.path != "/tokeninfo":
            return httpx2.Response(200, json={"id": token})
        if token == "invalid":
            return httpx2.Response(401)
        return httpx2.Response(
            200,
            json={
                "aud": (
                    "other-client" if token == "wrong-app" else "awork-client"
                ),
                "sub": token,
                "scope": "openid" if token == "missing-scope" else ADS_SCOPE,
                "expires_in": -1 if token == "expired" else 3600,
            },
        )

    def call(self, token=None, method="tools/call"):
        headers = {"Accept": "application/json, text/event-stream"}
        if token:
            headers["Authorization"] = "Bearer " + token
        params = {"name": "list_accessible_customers", "arguments": {}}
        if method == "initialize":
            params = {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            }
        return self.client.post(
            "/mcp",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": method,
                "params": params,
            },
        )

    def test_discovery_points_to_google_and_has_no_local_oauth_routes(self):
        response = self.call()
        self.assertEqual(response.status_code, 401)
        self.assertIn(
            'resource_metadata="https://ads.example.com/.well-known/oauth-protected-resource/mcp"',
            response.headers["www-authenticate"],
        )
        metadata = self.client.get(
            "/.well-known/oauth-protected-resource/mcp"
        ).json()
        self.assertEqual(metadata["resource"], "https://ads.example.com/mcp")
        self.assertEqual(
            metadata["authorization_servers"], ["https://accounts.google.com"]
        )
        self.assertIn(ADS_SCOPE, metadata["scopes_supported"])
        for path in ("/authorize", "/token", "/register", "/auth/callback"):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.ads.assert_not_called()
        self.adc.assert_not_called()

    def test_invalid_credentials_never_reach_ads_or_default_credentials(self):
        for token in (None, "invalid", "wrong-app", "missing-scope", "expired"):
            with self.subTest(token=token):
                self.assertIn(self.call(token).status_code, (401, 403))
        self.ads.assert_not_called()
        self.adc.assert_not_called()

    def test_missing_client_id_rejects_all_tokens_without_google_call(self):
        verifier = ExternalGoogleTokenVerifier(
            audience=[], required_scopes=[ADS_SCOPE]
        )
        with patch.object(
            httpx2.AsyncClient, "get", side_effect=AssertionError("network")
        ):
            self.assertIsNone(asyncio.run(verifier.verify_token("user-1")))

    def test_parallel_users_and_refreshed_tokens_use_request_credentials(self):
        tokens = ["user-111", "user-222", "user-333", "user-111-refreshed"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses = list(pool.map(self.call, tokens))
        for token, response in zip(tokens, responses):
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()["result"]
            self.assertFalse(result.get("isError", False), result)
            self.assertEqual(
                result["structuredContent"]["result"],
                [token.removeprefix("user-")],
            )
            self.assertNotIn("mcp-session-id", response.headers)
        self.assertEqual(self.ads.call_count, len(tokens))
        self.adc.assert_not_called()

    def test_initialize_does_not_create_a_session_or_authorize_later_calls(
        self,
    ):
        response = self.call("user-111", method="initialize")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("mcp-session-id", response.headers)
        self.assertEqual(self.call().status_code, 401)
        self.assertEqual(self.call("user-222").status_code, 200)

    def test_tool_credentials_cannot_fall_back_to_server_identity(self):
        with patch(
            "fastmcp.server.dependencies.get_access_token", return_value=None
        ):
            with self.assertRaises(ToolError):
                utils._create_credentials()
        self.adc.assert_not_called()
