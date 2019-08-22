import asyncio
import functools
import inspect

import pytest
import trustme
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    PrivateFormat,
)
from uvicorn.config import Config
from uvicorn.main import Server

from httpx.concurrency.asyncio import AsyncioBackend

try:
    from httpx.concurrency.trio import TrioBackend
except ImportError:  # pragma: no cover
    TrioBackend = None  # type: ignore


@pytest.fixture(
    params=[
        pytest.param(AsyncioBackend, marks=pytest.mark.asyncio),
        pytest.param(TrioBackend, marks=pytest.mark.asyncio),
    ]
)
def backend(request):
    backend_cls = request.param
    if backend_cls is None:  # pragma: no cover
        pytest.skip()
    backend = backend_cls()
    return backend


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    """
    Test functions that use a concurrency backend other than asyncio must be run
    in a separate thread.
    """
    if "backend" not in pyfuncitem.fixturenames:
        return

    backend = pyfuncitem.funcargs["backend"]
    assert backend is not None

    if isinstance(backend, AsyncioBackend):
        return

    func = pyfuncitem.obj
    assert inspect.iscoroutinefunction(func)

    @functools.wraps(func)
    async def wrapped(**kwargs):
        asyncio_backend = AsyncioBackend()
        await asyncio_backend.run_in_threadpool(backend.run, func, **kwargs)

    pyfuncitem.obj = wrapped


async def app(scope, receive, send):
    assert scope["type"] == "http"
    if scope["path"] == "/slow_response":
        await slow_response(scope, receive, send)
    elif scope["path"].startswith("/status"):
        await status_code(scope, receive, send)
    elif scope["path"].startswith("/echo_body"):
        await echo_body(scope, receive, send)
    else:
        await hello_world(scope, receive, send)


async def hello_world(scope, receive, send):
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [[b"content-type", b"text/plain"]],
        }
    )
    await send({"type": "http.response.body", "body": b"Hello, world!"})


async def slow_response(scope, receive, send):
    await asyncio.sleep(0.1)
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [[b"content-type", b"text/plain"]],
        }
    )
    await send({"type": "http.response.body", "body": b"Hello, world!"})


async def status_code(scope, receive, send):
    status_code = int(scope["path"].replace("/status/", ""))
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [[b"content-type", b"text/plain"]],
        }
    )
    await send({"type": "http.response.body", "body": b"Hello, world!"})


async def echo_body(scope, receive, send):
    body = b""
    more_body = True

    while more_body:
        message = await receive()
        body += message.get("body", b"")
        more_body = message.get("more_body", False)

    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [[b"content-type", b"text/plain"]],
        }
    )
    await send({"type": "http.response.body", "body": body})


class CAWithPKEncryption(trustme.CA):
    """Implementation of trustme.CA() that can emit
    private keys that are encrypted with a password.
    """

    @property
    def encrypted_private_key_pem(self):
        return trustme.Blob(
            self._private_key.private_bytes(
                Encoding.PEM,
                PrivateFormat.TraditionalOpenSSL,
                BestAvailableEncryption(password=b"password"),
            )
        )


@pytest.fixture
def example_cert():
    ca = CAWithPKEncryption()
    ca.issue_cert("example.org")
    return ca


@pytest.fixture
def cert_pem_file(example_cert):
    with example_cert.cert_pem.tempfile() as tmp:
        yield tmp


@pytest.fixture
def cert_private_key_file(example_cert):
    with example_cert.private_key_pem.tempfile() as tmp:
        yield tmp


@pytest.fixture
def cert_encrypted_private_key_file(example_cert):
    with example_cert.encrypted_private_key_pem.tempfile() as tmp:
        yield tmp


@pytest.fixture
async def server():
    config = Config(app=app, lifespan="off")
    server = Server(config=config)
    task = asyncio.ensure_future(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.0001)
        yield server
    finally:
        server.should_exit = True
        await task


@pytest.fixture
def restart(backend):
    async def asyncio_restart(server):
        await server.shutdown()
        await server.startup()

    if isinstance(backend, AsyncioBackend):
        return asyncio_restart

    # Uvicorn runs on asyncio, so if we're not running on asyncio during a test
    # we must spawn a new loop and do shutdown/startup there.
    async def restart(server):
        asyncio_backend = AsyncioBackend()
        asyncio_backend.run(asyncio_restart, server)

    return restart


@pytest.fixture
async def https_server(cert_pem_file, cert_private_key_file):
    config = Config(
        app=app,
        lifespan="off",
        ssl_certfile=cert_pem_file,
        ssl_keyfile=cert_private_key_file,
        port=8001,
    )
    server = Server(config=config)
    task = asyncio.ensure_future(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.0001)
        yield server
    finally:
        server.should_exit = True
        await task
