from __future__ import annotations

import asyncio
import re
import socket
from pathlib import Path

import uvicorn
from app.main import create_app
from websockets.asyncio.client import connect

ROOT = Path(__file__).resolve().parents[4]
VITE_CONFIG = ROOT / "apps" / "web" / "vite.config.ts"
NGINX_CONFIG = ROOT / "apps" / "web" / "nginx.conf"
SESSION_LIST_PAGE = ROOT / "apps" / "web" / "src" / "pages" / "SessionListPage.tsx"


def test_vite_config_enables_websocket_proxy() -> None:
    content = VITE_CONFIG.read_text(encoding="utf-8")
    assert "/api" in content, "Vite config must define proxy for /api"
    assert re.search(r"ws:\s*true", content), (
        "Vite proxy for /api must specify 'ws: true' to support WebSocket upgrade"
    )


def test_nginx_config_supports_websocket_upgrade_headers() -> None:
    content = NGINX_CONFIG.read_text(encoding="utf-8")
    assert "location /api/" in content, "Nginx config must define location /api/"
    assert "proxy_set_header Upgrade" in content, (
        "Nginx config must forward Upgrade header for WebSocket proxying"
    )
    assert "proxy_set_header Connection" in content, (
        "Nginx config must forward Connection header for WebSocket proxying"
    )


def test_session_list_page_subscribes_to_commands_resource() -> None:
    content = SESSION_LIST_PAGE.read_text(encoding="utf-8")
    assert "useRealtimeRefresh" in content
    # SessionListPage must subscribe to 'commands' so ACK updates trigger refetch
    assert "'commands'" in content or '"commands"' in content, (
        "SessionListPage must include 'commands' in useRealtimeRefresh resources"
    )


class _TcpProxy:
    """Lightweight TCP reverse proxy simulating Vite/Nginx forwarding to backend."""

    def __init__(self, backend_port: int):
        self.backend_port = backend_port
        self.proxy_port = 0
        self.server: asyncio.Server | None = None

    async def _handle_client(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
    ) -> None:
        try:
            backend_reader, backend_writer = await asyncio.open_connection(
                "127.0.0.1", self.backend_port
            )
        except OSError:
            client_writer.close()
            return

        async def pipe(r: asyncio.StreamReader, w: asyncio.StreamWriter):
            try:
                while True:
                    data = await r.read(4096)
                    if not data:
                        break
                    w.write(data)
                    await w.drain()
            except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                pass
            finally:
                w.close()

        t1 = asyncio.create_task(pipe(client_reader, backend_writer))
        t2 = asyncio.create_task(pipe(backend_reader, client_writer))
        await asyncio.gather(t1, t2, return_exceptions=True)

    async def start(self) -> None:
        self.server = await asyncio.start_server(
            self._handle_client, "127.0.0.1", 0
        )
        self.proxy_port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()


def test_websocket_connection_and_ping_pong_through_proxy_url(tmp_path: Path) -> None:
    """Verifies WebSocket handshake and message exchange through a reverse proxy URL."""
    async def scenario():
        db_path = tmp_path / "ws_proxy.db"
        fastapi_app = create_app(db_path)

        # 1. Start FastAPI backend with uvicorn on random loopback port
        backend_sock = socket.socket()
        backend_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        backend_sock.bind(("127.0.0.1", 0))
        backend_port = backend_sock.getsockname()[1]
        backend_sock.listen()
        backend_sock.setblocking(False)

        uvicorn_server = uvicorn.Server(
            uvicorn.Config(
                fastapi_app,
                host="127.0.0.1",
                port=backend_port,
                log_level="error",
            )
        )
        backend_task = asyncio.create_task(uvicorn_server.serve(sockets=[backend_sock]))

        # Wait for backend to be ready
        while not uvicorn_server.started:
            await asyncio.sleep(0.02)

        # 2. Start TCP/WebSocket reverse proxy pointing to backend
        proxy = _TcpProxy(backend_port)
        await proxy.start()

        try:
            # 3. Connect client to the PROXY URL (simulating frontend ws://localhost:3000/api/v1/ws)
            proxy_ws_url = f"ws://127.0.0.1:{proxy.proxy_port}/api/v1/ws"
            async with connect(proxy_ws_url) as ws:
                # First message from backend on connection
                init_msg = await ws.recv()
                assert '"type":"connected"' in init_msg or '"type": "connected"' in init_msg

                # Send ping through proxy
                await ws.send('{"type":"ping"}')

                # Receive pong from backend through proxy
                reply = await ws.recv()
                assert '"type":"pong"' in reply or '"type": "pong"' in reply

        finally:
            await proxy.stop()
            uvicorn_server.should_exit = True
            await asyncio.wait_for(backend_task, timeout=5)

    asyncio.run(scenario())
