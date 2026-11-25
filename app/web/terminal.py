"""Interactive terminal routes (app/terminal.py): HTTP to list machines and
open, list and close sessions; /terminal/ws streams one session.

The socket authenticates like /hosts/{id}/stats (exact Origin, session
cookie, re-checked every 15 s) without its 30-minute close: a terminal
outlives sockets anyway. The session id travels in the first message,
never in the URL (handshake lines are logged unredacted).
"""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .. import accounts, terminal
from ..host_events import _origin
from ..security import client_ip
from .deps import _chat_auth, _json_object

router = APIRouter()
ws_router = APIRouter()
RECHECK_SECONDS = 15
SEND_TIMEOUT = 30


@router.get("/terminal/hosts")
async def terminal_hosts(request: Request):
    user, err = await asyncio.to_thread(_chat_auth, request)
    if err:
        return err
    items = await asyncio.to_thread(terminal.host_list, user["id"])
    return JSONResponse({"hosts": items}, headers={"Cache-Control": "no-store"})


@router.post("/terminal/sessions")
async def terminal_open(request: Request):
    user, err = await asyncio.to_thread(_chat_auth, request)
    if err:
        return err
    raw = await _json_object(request)
    if set(raw) - {"thread_id", "host_id", "shell", "cols", "rows"}:
        raise HTTPException(422, "Unsupported terminal fields")
    shell = raw.get("shell")
    if shell is not None and not isinstance(shell, str):
        raise HTTPException(400, "shell must be a string")
    thread_id = raw.get("thread_id")
    if thread_id is not None and (isinstance(thread_id, bool) or not isinstance(thread_id, (int, str))):
        raise HTTPException(400, "invalid thread_id")
    try:
        session = await terminal.open_session(
            user["id"], request.session.get("sid") or "", host_id=raw.get("host_id"), thread_id=thread_id,
            shell=shell or None, cols=raw.get("cols"), rows=raw.get("rows"),
            client_ip=client_ip(request), user_agent=request.headers.get("user-agent") or "",
        )
    except terminal.TerminalError as exc:
        raise HTTPException(exc.status_code, exc.detail) from None
    return JSONResponse(session.view(), status_code=201, headers={"Cache-Control": "no-store"})


@router.get("/terminal/sessions")
async def terminal_sessions(request: Request):
    user, err = await asyncio.to_thread(_chat_auth, request)
    if err:
        return err
    return JSONResponse({"sessions": terminal.list_for(user["id"])}, headers={"Cache-Control": "no-store"})


@router.delete("/terminal/sessions/{session_id}")
async def terminal_close(request: Request, session_id: str):
    user, err = await asyncio.to_thread(_chat_auth, request)
    if err:
        return err
    if not await terminal.close_where(user["id"], "closed", session_id=session_id):
        raise HTTPException(404, "terminal not found")
    return JSONResponse({"ok": True})


async def _sender(ws: WebSocket, client: terminal.Client) -> None:
    while True:
        await client.wake.wait()
        client.wake.clear()
        while client.items and (client.close_code is None or client.drain):
            item = client.items.popleft()
            async with asyncio.timeout(SEND_TIMEOUT):
                if isinstance(item, bytes):
                    client.queued -= len(item)
                    await ws.send_bytes(item)
                else:
                    await ws.send_json(item)
        if client.close_code is not None:
            await ws.close(code=client.close_code)
            return


async def _reader(ws: WebSocket, session: terminal.Session, client: terminal.Client) -> None:
    while client.close_code is None:
        message = await ws.receive()
        if message.get("type") == "websocket.disconnect":
            return
        if message.get("bytes") is not None:
            await session.write(message["bytes"])
            continue
        try:
            data = json.loads(message.get("text") or "")
        except (ValueError, RecursionError):
            continue
        kind = data.get("t") if isinstance(data, dict) else None
        if kind == "ack":
            session.ack(data.get("n"))
        elif kind == "resize":
            await session.resize(data.get("cols"), data.get("rows"))
        elif kind == "ping":
            client.push({"t": "pong"})
        elif kind == "close":
            await session.close("closed")


@ws_router.websocket("/terminal/ws")
async def terminal_socket(ws: WebSocket):
    expected = ("https" if ws.url.scheme == "wss" else "http") + "://" + ws.url.netloc
    uid = ws.session.get("uid")
    if _origin(ws.headers.get("origin", "")) != _origin(expected):
        await ws.close(code=4403)
        return
    if not uid or not await asyncio.to_thread(accounts.session_user, ws.session):
        await ws.close(code=4401)
        return
    await ws.accept()
    try:
        first = json.loads(await asyncio.wait_for(ws.receive_text(), RECHECK_SECONDS))
    except (ValueError, RecursionError, KeyError, asyncio.TimeoutError, WebSocketDisconnect, RuntimeError):
        await _close(ws, 4400)
        return
    session = terminal.get(first.get("id")) if isinstance(first, dict) and first.get("t") == "attach" else None
    if session is None or session.uid != int(uid):
        await _close(ws, 4404)
        return
    if not session.alive:
        try:
            await ws.send_json({"t": "exit", "code": session.exit_code, "reason": session.reason})
        except (RuntimeError, WebSocketDisconnect, OSError):
            pass
        await _close(ws, 4410)
        return
    client = terminal.Client()
    await session.resize(first.get("cols"), first.get("rows"))
    session.attach(client)
    sender = asyncio.create_task(_sender(ws, client))
    reader = asyncio.create_task(_reader(ws, session, client))
    try:
        checked = time.monotonic()
        while True:
            done, _ = await asyncio.wait({sender, reader}, timeout=RECHECK_SECONDS, return_when=asyncio.FIRST_COMPLETED)
            if sender in done:
                break
            if reader in done:
                if reader.exception() is not None or client.close_code is None:
                    break
                await asyncio.wait({sender}, timeout=SEND_TIMEOUT)  # it closes with the code it was given
                break
            if time.monotonic() - checked >= RECHECK_SECONDS:
                checked = time.monotonic()
                if not await asyncio.to_thread(accounts.session_user, ws.session):
                    client.end(4401)
                    await asyncio.wait({sender}, timeout=SEND_TIMEOUT)
                    break
    finally:
        session.detach(client)
        for task in (sender, reader):
            task.cancel()
        await asyncio.gather(sender, reader, return_exceptions=True)
        await _close(ws, 1000)


async def _close(ws: WebSocket, code: int) -> None:
    try:
        await ws.close(code=code)
    except (RuntimeError, WebSocketDisconnect, OSError):
        pass
