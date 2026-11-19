"""Static files: content versions and preload lists (app/static_assets.py) and
how /static serves an already compressed file (app.main.VersionedStaticFiles).
"""
import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.routing import Mount
from starlette.testclient import TestClient

from app import static_assets


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """Writes files into an empty static/ that the module reads instead of the
    real one, with nothing cached and every read re-stat'ed."""
    monkeypatch.setattr(static_assets, "ROOT", tmp_path)
    monkeypatch.setattr(static_assets, "_ROOT_REAL", tmp_path.resolve())
    monkeypatch.setattr(static_assets, "STAT_TTL", 0)
    monkeypatch.setattr(static_assets, "_frozen", False)
    for cache in ("_files", "_versions", "_bodies", "_gzipped", "_preload_lists"):
        monkeypatch.setattr(static_assets, cache, {})

    def write(rel, text):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    return write


def test_an_import_call_is_versioned_but_not_preloaded(tree):
    tree("entry.js", 'import "./eager.js";\nconst theme = () => import("./lazy/index.js");\n')
    tree("eager.js", "export const a = 1;\n")
    tree("lazy/index.js", 'import "./part.js";\nexport const b = 1;\n')
    tree("lazy/part.js", "export const c = 1;\n")

    # Neither the import() target nor what only it reaches is fetched up front.
    assert static_assets.preloads("entry.js") == [static_assets.asset_url("eager.js")]
    # The page names the lazy graph itself when it will need it.
    assert static_assets.preloads("lazy/index.js") == [static_assets.asset_url("lazy/part.js")]

    lazy = static_assets.version("lazy/index.js")
    assert f'import("./lazy/index.js?v={lazy}")' in static_assets.rewritten("entry.js")[1].decode()

    # A changed lazy module still re-versions everything that can load it.
    before = static_assets.version("entry.js")
    tree("lazy/part.js", "export const c = 22;\n")
    assert static_assets.version("lazy/index.js") != lazy
    assert static_assets.version("entry.js") != before


def test_a_module_also_imported_statically_is_preloaded(tree):
    tree("entry.js", 'import "./a.js";\nconst later = () => import("./shared.js");\n')
    tree("a.js", 'import "./shared.js";\n')
    tree("shared.js", "export const s = 1;\n")
    tree("both.js", 'import "./shared.js";\nconst again = () => import("./shared.js");\n')

    assert static_assets.preloads("entry.js") == [static_assets.asset_url("a.js"), static_assets.asset_url("shared.js")]
    assert static_assets.preloads("both.js") == [static_assets.asset_url("shared.js")]


def test_a_gz_file_is_served_as_it_is():
    """The spell dictionary is gzip on disk and the page inflates it itself:
    compressing it again cost a pass on the server and a second in the browser."""
    from app import main

    app = Starlette(
        routes=[Mount("/static", main.VersionedStaticFiles(directory=str(static_assets.ROOT)))],
        middleware=[Middleware(GZipMiddleware, minimum_size=1024)],
    )
    raw = (static_assets.ROOT / "dict" / "en-US.bin.gz").read_bytes()
    got = TestClient(app).get("/static/dict/en-US.bin.gz", headers={"accept-encoding": "gzip"})

    assert got.status_code == 200
    assert "content-encoding" not in got.headers
    assert got.content == raw
    # The same middleware does compress a plain file of that size.
    plain = TestClient(app).get("/static/dict/LICENSE", headers={"accept-encoding": "gzip"})
    assert plain.headers.get("content-encoding") == "gzip"
