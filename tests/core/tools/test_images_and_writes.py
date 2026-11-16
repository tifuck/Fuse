"""Image decoding rules, durable writes under cancellation, and in-place edits
of hard-linked files."""
import asyncio
import io
import json
import os
import threading

import pytest
from PIL import Image

from app import file_images, workspace
from app.async_work import durable_call


def png(image):
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def test_decoding_rules_photos_as_jpeg_ui_as_png_and_bounded_sizes():
    # Gradients with grain: like a photo, large as PNG and small as JPEG.
    gradient = Image.linear_gradient("L").resize((500, 500))
    grain = Image.effect_noise((500, 500), 12)
    channels = [Image.blend(c, grain, 0.25) for c in (gradient, gradient.rotate(90), gradient.rotate(45))]
    inputs = {
        "photo": png(Image.merge("RGB", channels)),
        "ui": png(Image.new("RGB", (800, 600), "white")),
        "wide": png(Image.new("RGB", (5000, 100), "blue")),
        # A tiny file that claims 81 megapixels: refused, never expanded.
        "bomb": png(Image.new("1", (9000, 9000))),
    }

    async def decode_all():
        results = await asyncio.gather(*(file_images.decode_image(data) for data in inputs.values()),
                                       file_images.decode_image(inputs["ui"], 3), return_exceptions=True)
        return dict(zip([*inputs, "frame"], results))

    out = asyncio.run(decode_all())

    assert out["photo"]["mime"] == "image/jpeg" and out["photo"]["format"] == "PNG"
    assert out["ui"]["mime"] == "image/png"
    assert (out["wide"]["width"], out["wide"]["original_width"]) == (2048, 5000)
    assert isinstance(out["bomb"], ValueError)
    assert isinstance(out["frame"], ValueError) and "frame range" in str(out["frame"])


def test_images_are_validated_before_any_decoder_runs():
    with pytest.raises(ValueError, match="unsupported image format"):
        asyncio.run(file_images.decode_image(b"%PDF-1.7 not an image"))
    with pytest.raises(ValueError, match="frame"):
        asyncio.run(file_images.decode_image(png(Image.new("RGB", (2, 2))), -1))
    with pytest.raises(ValueError, match="20 MiB"):
        asyncio.run(file_images.decode_image(b"\x89PNG\r\n\x1a\n" + b"\0" * file_images.MAX_SOURCE))


def test_a_cancelled_caller_still_lets_its_write_finish():
    started, proceed, finished = threading.Event(), threading.Event(), []

    def write():
        started.set()
        proceed.wait(5)
        finished.append("committed")
        return "ok"

    async def body():
        task = asyncio.create_task(durable_call(write))
        await asyncio.to_thread(started.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()  # the cancellation waits for the write
        proceed.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(body())
    assert finished == ["committed"]

    def boom():
        raise KeyError("x")

    with pytest.raises(KeyError):
        asyncio.run(durable_call(boom))
    assert asyncio.run(durable_call(lambda a, b=0: a + b, 2, b=3)) == 5


def test_a_hard_linked_file_is_edited_in_place_so_every_name_sees_it(ssh_host):
    path = os.path.join(ssh_host.root, "config.ini")
    alias = os.path.join(ssh_host.root, "config.link")
    with open(path, "w") as f:
        f.write("mode = dev\n")
    os.link(path, alias)

    text, rec = asyncio.run(workspace.execute(
        "search_replace", json.dumps({"path": "config.ini", "old_string": "dev", "new_string": "prod"}),
        user_id=ssh_host.uid, bound_host_id=ssh_host.host_id, bound_cwd=ssh_host.root))

    assert rec["status"] == "done"
    assert os.stat(path).st_ino == os.stat(alias).st_ino
    with open(alias) as f:
        assert f.read() == "mode = prod\n"


def test_windows_paths_are_confined_to_the_windows_root(fresh_db, monkeypatch):
    from app import agent_hub
    from tests.core.helpers import quick_user
    from tests.core.tools._support import make_host

    uid = quick_user()
    host = make_host(uid, "win", default_cwd="C:\\proj", auth_type="agent", agent_os="windows", agent_version="1.9.0")
    calls = []

    async def connector(*args, **kwargs):
        calls.append(args)
        return {"ok": True, "exit": 0, "out": "", "err": "", "error": ""}

    monkeypatch.setattr(agent_hub, "call", connector)
    for path in ("..\\other\\x.txt", "D:\\data\\x.txt", "c:\\PROJ\\..\\secret.txt", "\\\\server\\share\\x"):
        text, rec = asyncio.run(workspace.execute("read_file", json.dumps({"path": path}), user_id=uid,
                                                  bound_host_id=host, bound_cwd="C:\\proj"))
        assert rec["status"] == "error" and "outside the project root" in text, path
    assert calls == []
