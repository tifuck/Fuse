"""Providers area helpers: a scripted upstream, SSE bodies, and source rows.

``Upstream`` stands in for every provider host: a test registers responses by
method and URL fragment, and every request the app sent is kept with its
decoded JSON body, so payload translation is asserted on the wire.
"""
import json

import httpx

from app import accounts, db


class Upstream:
    def __init__(self):
        self.routes = []
        self.requests = []
        self.unmatched = []

    def on(self, method, fragment, *responses):
        """Answer `method` requests whose URL contains `fragment`. Each
        response is an httpx.Response, a callable(request) returning one, or
        an exception to raise; several are served in order, the last repeats."""
        self.routes.append((method.upper(), fragment, list(responses)))
        return self

    def handle(self, request):
        body = None
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = request.content
        self.requests.append((request.method, str(request.url), request.headers, body))
        for method, fragment, responses in self.routes:
            if method == request.method and fragment in str(request.url):
                answer = responses.pop(0) if len(responses) > 1 else responses[0]
                if isinstance(answer, Exception):
                    raise answer
                return answer(request) if callable(answer) else answer
        self.unmatched.append(f"{request.method} {request.url}")
        return httpx.Response(599, json={"error": "no route in the test upstream"})

    def sent(self, fragment, method="POST"):
        """JSON bodies of the requests to URLs containing `fragment`."""
        return [body for m, url, _h, body in self.requests if m == method and fragment in url]

    def headers(self, fragment, method="POST"):
        return [h for m, url, h, _b in self.requests if m == method and fragment in url]

    def calls(self, fragment, method=None):
        return [url for m, url, _h, _b in self.requests if fragment in url and (method is None or m == method)]


def sse(*objs, done=True):
    """An SSE body: one data frame per object, then [DONE]."""
    out = b"".join(b"data: " + json.dumps(o).encode() + b"\n\n" for o in objs)
    return out + (b"data: [DONE]\n\n" if done else b"")


def sse_response(*objs, done=True, headers=None):
    return httpx.Response(200, content=sse(*objs, done=done),
                          headers={"content-type": "text/event-stream", **(headers or {})})


def completion(text="ok", usage=None, **extra):
    return {"id": "c1", "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": usage or {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}, **extra}


def delta(text):
    return {"choices": [{"index": 0, "delta": {"content": text}}]}


def source(kind, config=None, name=None, enabled=True):
    with db.tx() as conn:
        conn.execute("INSERT INTO sources (type, name, enabled, config, created_at) VALUES (?,?,?,?,?)",
                     (kind, name or kind, 1 if enabled else 0, json.dumps(config or {}), db.now()))
        return int(db.last_id(conn))


def key(source_id, secret, owner=None, extra=None, label="key"):
    return int(accounts.add_credential(source_id, label, secret, extra=extra or {}, donated_by=owner))


def route(model, source_id, upstream_model=None):
    db.execute("INSERT INTO model_routes (model, source_id, upstream_model) VALUES (?,?,?)",
               (model, source_id, upstream_model))


def cred_row(cred_id):
    return db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))


def events():
    return db.fetchall("SELECT * FROM usage_events ORDER BY id")


async def drain(stream):
    return b"".join([chunk async for chunk in stream])


def frames_of(raw: bytes):
    """Decoded JSON objects of an SSE body (comments and [DONE] skipped)."""
    out = []
    for line in raw.decode().splitlines():
        line = line.strip()
        if line.startswith("data:") and line[5:].strip() not in ("", "[DONE]"):
            out.append(json.loads(line[5:].strip()))
    return out
