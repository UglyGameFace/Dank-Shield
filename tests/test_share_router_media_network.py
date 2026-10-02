from __future__ import annotations

import asyncio

import pytest

from stoney_verify import share_router_media_network as network


def test_safe_media_headers_strip_secrets_and_keep_range() -> None:
    headers = network.safe_media_headers(
        {
            "User-Agent": "Provider UA",
            "Referer": "https://provider.example/",
            "Range": "bytes=10-20",
            "Cookie": "secret",
            "Authorization": "Bearer secret",
        }
    )
    assert headers == {
        "User-Agent": "Provider UA",
        "Referer": "https://provider.example/",
        "Range": "bytes=10-20",
    }


def test_public_get_rejects_private_redirect_before_second_request() -> None:
    class Response:
        status = 302
        headers = {"Location": "http://127.0.0.1/private"}
        url = "https://public.example/start"

        def release(self) -> None:
            pass

    class Session:
        def __init__(self) -> None:
            self.calls = []

        async def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            return Response()

    session = Session()

    async def scenario():
        with pytest.raises(network.UnsafeMediaURL):
            await network.public_get(
                session,
                "https://public.example/start",
            )

    asyncio.run(scenario())
    assert len(session.calls) == 1


def test_public_get_follows_safe_relative_redirect_without_auto_redirect() -> None:
    class Response:
        def __init__(self, status, url, location=""):
            self.status = status
            self.url = url
            self.headers = {"Location": location} if location else {}

        def release(self) -> None:
            pass

    class Session:
        def __init__(self) -> None:
            self.calls = []

        async def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            if len(self.calls) == 1:
                return Response(302, url, "/next")
            return Response(200, url)

    session = Session()

    async def scenario():
        response, final_url = await network.public_get(
            session,
            "https://public.example/start",
            headers={"Cookie": "drop", "Range": "bytes=0-9"},
        )
        return response, final_url

    response, final_url = asyncio.run(scenario())
    assert response.status == 200
    assert final_url == "https://public.example/next"
    assert len(session.calls) == 2
    assert all(call[2]["allow_redirects"] is False for call in session.calls)
    assert all(call[2]["headers"] == {"Range": "bytes=0-9"} for call in session.calls)


def test_public_only_dns_resolver_rejects_private_answer(monkeypatch) -> None:
    class FakeResolver:
        async def resolve(self, host, port=0, family=0):
            return [{"host": "10.0.0.7"}]

        async def close(self):
            return None

    async def scenario():
        resolver = network.PublicOnlyDNSResolver()
        resolver._resolver = FakeResolver()
        try:
            with pytest.raises(OSError, match="non-public"):
                await resolver.resolve("public.example", 443)
        finally:
            await resolver.close()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("value", "safe"),
    [
        ("https://cdn.example.com/video.mp4", True),
        ("https://127.0.0.1/video.mp4", False),
        ("http://169.254.169.254/latest/meta-data", False),
        ("https://user:pass@example.com/video.mp4", False),
        ("https://example.com:8443/video.mp4", False),
    ],
)
def test_shared_network_url_policy(value: str, safe: bool) -> None:
    assert network.is_safe_media_download_url(value) is safe
