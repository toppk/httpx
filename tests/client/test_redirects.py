import json
from urllib.parse import parse_qs

import pytest

from httpx import (
    URL,
    AsyncClient,
    AsyncDispatcher,
    AsyncRequest,
    AsyncResponse,
    CertTypes,
    RedirectBodyUnavailable,
    RedirectLoop,
    TimeoutTypes,
    TooManyRedirects,
    VerifyTypes,
    codes,
)


class MockDispatch(AsyncDispatcher):
    async def send(
        self,
        request: AsyncRequest,
        verify: VerifyTypes = None,
        cert: CertTypes = None,
        timeout: TimeoutTypes = None,
    ) -> AsyncResponse:
        if request.url.path == "/redirect_301":
            status_code = codes.MOVED_PERMANENTLY
            headers = {"location": "https://example.org/"}
            return AsyncResponse(status_code, headers=headers, request=request)

        elif request.url.path == "/redirect_302":
            status_code = codes.FOUND
            headers = {"location": "https://example.org/"}
            return AsyncResponse(status_code, headers=headers, request=request)

        elif request.url.path == "/redirect_303":
            status_code = codes.SEE_OTHER
            headers = {"location": "https://example.org/"}
            return AsyncResponse(status_code, headers=headers, request=request)

        elif request.url.path == "/relative_redirect":
            headers = {"location": "/"}
            return AsyncResponse(codes.SEE_OTHER, headers=headers, request=request)

        elif request.url.path == "/no_scheme_redirect":
            headers = {"location": "//example.org/"}
            return AsyncResponse(codes.SEE_OTHER, headers=headers, request=request)

        elif request.url.path == "/multiple_redirects":
            params = parse_qs(request.url.query)
            count = int(params.get("count", "0")[0])
            redirect_count = count - 1
            code = codes.SEE_OTHER if count else codes.OK
            location = "/multiple_redirects"
            if redirect_count:
                location += "?count=" + str(redirect_count)
            headers = {"location": location} if count else {}
            return AsyncResponse(code, headers=headers, request=request)

        if request.url.path == "/redirect_loop":
            headers = {"location": "/redirect_loop"}
            return AsyncResponse(codes.SEE_OTHER, headers=headers, request=request)

        elif request.url.path == "/cross_domain":
            headers = {"location": "https://example.org/cross_domain_target"}
            return AsyncResponse(codes.SEE_OTHER, headers=headers, request=request)

        elif request.url.path == "/cross_domain_target":
            headers = dict(request.headers.items())
            content = json.dumps({"headers": headers}).encode()
            return AsyncResponse(codes.OK, content=content, request=request)

        elif request.url.path == "/redirect_body":
            await request.read()
            headers = {"location": "/redirect_body_target"}
            return AsyncResponse(
                codes.PERMANENT_REDIRECT, headers=headers, request=request
            )

        elif request.url.path == "/redirect_body_target":
            content = await request.read()
            body = json.dumps({"body": content.decode()}).encode()
            return AsyncResponse(codes.OK, content=body, request=request)

        elif request.url.path == "/cross_subdomain":
            if request.headers["host"] != "www.example.org":
                headers = {"location": "https://www.example.org/cross_subdomain"}
                return AsyncResponse(
                    codes.PERMANENT_REDIRECT, headers=headers, request=request
                )
            else:
                return AsyncResponse(
                    codes.OK, content=b"Hello, world!", request=request
                )

        return AsyncResponse(codes.OK, content=b"Hello, world!", request=request)


async def test_redirect_301(backend):
    client = AsyncClient(dispatch=MockDispatch(), backend=backend)
    response = await client.post("https://example.org/redirect_301")
    assert response.status_code == codes.OK
    assert response.url == URL("https://example.org/")
    assert len(response.history) == 1


async def test_redirect_302(backend):
    client = AsyncClient(dispatch=MockDispatch(), backend=backend)
    response = await client.post("https://example.org/redirect_302")
    assert response.status_code == codes.OK
    assert response.url == URL("https://example.org/")
    assert len(response.history) == 1


async def test_redirect_303(backend):
    client = AsyncClient(dispatch=MockDispatch(), backend=backend)
    response = await client.get("https://example.org/redirect_303")
    assert response.status_code == codes.OK
    assert response.url == URL("https://example.org/")
    assert len(response.history) == 1


async def test_disallow_redirects(backend):
    client = AsyncClient(dispatch=MockDispatch(), backend=backend)
    response = await client.post(
        "https://example.org/redirect_303", allow_redirects=False
    )
    assert response.status_code == codes.SEE_OTHER
    assert response.url == URL("https://example.org/redirect_303")
    assert response.is_redirect is True
    assert len(response.history) == 0

    response = await response.next()
    assert response.status_code == codes.OK
    assert response.url == URL("https://example.org/")
    assert response.is_redirect is False
    assert len(response.history) == 1


async def test_relative_redirect(backend):
    client = AsyncClient(dispatch=MockDispatch(), backend=backend)
    response = await client.get("https://example.org/relative_redirect")
    assert response.status_code == codes.OK
    assert response.url == URL("https://example.org/")
    assert len(response.history) == 1


async def test_no_scheme_redirect(backend):
    client = AsyncClient(dispatch=MockDispatch(), backend=backend)
    response = await client.get("https://example.org/no_scheme_redirect")
    assert response.status_code == codes.OK
    assert response.url == URL("https://example.org/")
    assert len(response.history) == 1


async def test_fragment_redirect(backend):
    client = AsyncClient(dispatch=MockDispatch(), backend=backend)
    response = await client.get("https://example.org/relative_redirect#fragment")
    assert response.status_code == codes.OK
    assert response.url == URL("https://example.org/#fragment")
    assert len(response.history) == 1


async def test_multiple_redirects(backend):
    client = AsyncClient(dispatch=MockDispatch(), backend=backend)
    response = await client.get("https://example.org/multiple_redirects?count=20")
    assert response.status_code == codes.OK
    assert response.url == URL("https://example.org/multiple_redirects")
    assert len(response.history) == 20
    assert response.history[0].url == URL(
        "https://example.org/multiple_redirects?count=20"
    )
    assert response.history[1].url == URL(
        "https://example.org/multiple_redirects?count=19"
    )
    assert len(response.history[0].history) == 0
    assert len(response.history[1].history) == 1


async def test_too_many_redirects(backend):
    client = AsyncClient(dispatch=MockDispatch(), backend=backend)
    with pytest.raises(TooManyRedirects):
        await client.get("https://example.org/multiple_redirects?count=21")


async def test_too_many_redirects_calling_next(backend):
    client = AsyncClient(dispatch=MockDispatch(), backend=backend)
    url = "https://example.org/multiple_redirects?count=21"
    response = await client.get(url, allow_redirects=False)
    with pytest.raises(TooManyRedirects):
        while response.is_redirect:
            response = await response.next()


async def test_redirect_loop(backend):
    client = AsyncClient(dispatch=MockDispatch(), backend=backend)
    with pytest.raises(RedirectLoop):
        await client.get("https://example.org/redirect_loop")


async def test_redirect_loop_calling_next(backend):
    client = AsyncClient(dispatch=MockDispatch(), backend=backend)
    url = "https://example.org/redirect_loop"
    response = await client.get(url, allow_redirects=False)
    with pytest.raises(RedirectLoop):
        while response.is_redirect:
            response = await response.next()


async def test_cross_domain_redirect(backend):
    client = AsyncClient(dispatch=MockDispatch(), backend=backend)
    url = "https://example.com/cross_domain"
    headers = {"Authorization": "abc"}
    response = await client.get(url, headers=headers)
    assert response.url == URL("https://example.org/cross_domain_target")
    assert "authorization" not in response.json()["headers"]


async def test_same_domain_redirect(backend):
    client = AsyncClient(dispatch=MockDispatch(), backend=backend)
    url = "https://example.org/cross_domain"
    headers = {"Authorization": "abc"}
    response = await client.get(url, headers=headers)
    assert response.url == URL("https://example.org/cross_domain_target")
    assert response.json()["headers"]["authorization"] == "abc"


async def test_body_redirect(backend):
    client = AsyncClient(dispatch=MockDispatch(), backend=backend)
    url = "https://example.org/redirect_body"
    data = b"Example request body"
    response = await client.post(url, data=data)
    assert response.url == URL("https://example.org/redirect_body_target")
    assert response.json() == {"body": "Example request body"}


async def test_cannot_redirect_streaming_body(backend):
    client = AsyncClient(dispatch=MockDispatch(), backend=backend)
    url = "https://example.org/redirect_body"

    async def streaming_body():
        yield b"Example request body"

    with pytest.raises(RedirectBodyUnavailable):
        await client.post(url, data=streaming_body())


async def test_cross_dubdomain_redirect(backend):
    client = AsyncClient(dispatch=MockDispatch(), backend=backend)
    url = "https://example.com/cross_subdomain"
    response = await client.get(url)
    assert response.url == URL("https://www.example.org/cross_subdomain")
