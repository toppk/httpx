import functools
import typing
from base64 import b64encode

from .config import DEFAULT_MAX_REDIRECTS
from .exceptions import RedirectBodyUnavailable, RedirectLoop, TooManyRedirects
from .models import URL, AsyncRequest, AsyncResponse, Cookies, Headers
from .status_codes import codes

Responder = typing.Callable[[AsyncRequest], typing.Coroutine[None, None, AsyncResponse]]
Middleware = typing.Callable[
    [AsyncRequest, typing.Callable], typing.Coroutine[None, None, AsyncResponse]
]


def basic_auth(
    username: typing.Union[str, bytes], password: typing.Union[str, bytes]
) -> Middleware:
    if isinstance(username, str):
        username = username.encode("latin1")

    if isinstance(password, str):
        password = password.encode("latin1")

    userpass = b":".join((username, password))
    token = b64encode(userpass).decode().strip()
    print(username, password, userpass, token)
    authorization_header = f"Basic {token}"

    async def dispatch(request: AsyncRequest, get_response: Responder) -> AsyncResponse:
        request.headers["Authorization"] = authorization_header
        return await get_response(request)

    return dispatch


def custom_auth(auth: typing.Callable[[AsyncRequest], AsyncRequest]) -> Middleware:
    async def dispatch(request: AsyncRequest, get_response: Responder) -> AsyncResponse:
        request = auth(request)
        return await get_response(request)

    return dispatch


def redirect(
    allow_redirects: bool = True,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    cookies: typing.Optional[Cookies] = None,
) -> Middleware:
    return Redirect(
        allow_redirects=allow_redirects, max_redirects=max_redirects, cookies=cookies
    ).dispatch


class Redirect:
    def __init__(
        self,
        allow_redirects: bool = True,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        cookies: typing.Optional[Cookies] = None,
    ):
        self.allow_redirects = allow_redirects
        self.max_redirects = max_redirects
        self.cookies = cookies
        self.history: typing.List[AsyncResponse] = []

    async def dispatch(
        self, request: AsyncRequest, get_response: Responder
    ) -> AsyncResponse:
        if len(self.history) > self.max_redirects:
            raise TooManyRedirects()
        if request.url in (response.url for response in self.history):
            raise RedirectLoop()

        response = await get_response(request)
        response.history = list(self.history)

        if not response.is_redirect:
            return response

        self.history.append(response)
        next_request = self.build_redirect_request(request, response)

        if self.allow_redirects:
            return await self.dispatch(next_request, get_response)

        response.next = functools.partial(self.dispatch, next_request, get_response)
        return response

    def build_redirect_request(
        self, request: AsyncRequest, response: AsyncResponse
    ) -> AsyncRequest:
        method = self.redirect_method(request, response)
        url = self.redirect_url(request, response)
        headers = self.redirect_headers(request, url)  # TODO: merge headers?
        content = self.redirect_content(request, method)
        cookies = Cookies(self.cookies)
        cookies.update(request.cookies)
        return AsyncRequest(
            method=method, url=url, headers=headers, data=content, cookies=cookies
        )

    def redirect_method(self, request: AsyncRequest, response: AsyncResponse) -> str:
        """
        When being redirected we may want to change the method of the request
        based on certain specs or browser behavior.
        """
        method = request.method

        # https://tools.ietf.org/html/rfc7231#section-6.4.4
        if response.status_code == codes.SEE_OTHER and method != "HEAD":
            method = "GET"

        # Do what the browsers do, despite standards...
        # Turn 302s into GETs.
        if response.status_code == codes.FOUND and method != "HEAD":
            method = "GET"

        # If a POST is responded to with a 301, turn it into a GET.
        # This bizarre behaviour is explained in 'requests' issue 1704.
        if response.status_code == codes.MOVED_PERMANENTLY and method == "POST":
            method = "GET"

        return method

    def redirect_url(self, request: AsyncRequest, response: AsyncResponse) -> URL:
        """
        Return the URL for the redirect to follow.
        """
        location = response.headers["Location"]

        url = URL(location, allow_relative=True)

        # Facilitate relative 'Location' headers, as allowed by RFC 7231.
        # (e.g. '/path/to/resource' instead of 'http://domain.tld/path/to/resource')
        if url.is_relative_url:
            url = request.url.join(url)

        # Attach previous fragment if needed (RFC 7231 7.1.2)
        if request.url.fragment and not url.fragment:
            url = url.copy_with(fragment=request.url.fragment)

        return url

    def redirect_headers(self, request: AsyncRequest, url: URL) -> Headers:
        """
        Strip Authorization headers when responses are redirected away from
        the origin.
        """
        headers = Headers(request.headers)
        if url.origin != request.url.origin:
            del headers["Authorization"]
            del headers["host"]
        return headers

    def redirect_content(self, request: AsyncRequest, method: str) -> bytes:
        """
        Return the body that should be used for the redirect request.
        """
        if method != request.method and method == "GET":
            return b""
        if request.is_streaming:
            raise RedirectBodyUnavailable()
        return request.content
