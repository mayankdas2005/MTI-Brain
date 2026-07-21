"""Integration tests for middleware (SecurityHeaders, RequestID, Timing, CORS)."""

import pytest


@pytest.mark.integration
class TestSecurityHeaders:
    async def test_x_content_type_options(self, authed_client):
        resp = await authed_client.get("/api/v1/projects")
        assert resp.headers.get("x-content-type-options") == "nosniff"

    async def test_x_frame_options(self, authed_client):
        resp = await authed_client.get("/api/v1/projects")
        assert resp.headers.get("x-frame-options") == "DENY"

    async def test_referrer_policy(self, authed_client):
        resp = await authed_client.get("/api/v1/projects")
        assert "referrer-policy" in resp.headers


@pytest.mark.integration
class TestRequestIDMiddleware:
    async def test_response_has_request_id(self, authed_client):
        resp = await authed_client.get("/api/v1/projects")
        assert resp.headers.get("x-request-id")

    async def test_request_id_is_unique(self, authed_client):
        r1 = await authed_client.get("/api/v1/projects")
        r2 = await authed_client.get("/api/v1/projects")
        assert r1.headers["x-request-id"] != r2.headers["x-request-id"]


@pytest.mark.integration
class TestTimingMiddleware:
    async def test_response_has_response_time(self, authed_client):
        resp = await authed_client.get("/api/v1/projects")
        assert resp.headers.get("x-response-time")

    async def test_response_time_is_valid(self, authed_client):
        resp = await authed_client.get("/api/v1/projects")
        val = resp.headers.get("x-response-time", "")
        assert val.endswith("ms")


@pytest.mark.integration
class TestCORS:
    async def test_cors_preflight(self, client):
        resp = await client.options(
            "/api/v1/projects",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers
