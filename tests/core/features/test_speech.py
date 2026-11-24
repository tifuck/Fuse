"""Speech to text: off unless the site flag and an enabled, complete source
row say otherwise; the key is stored encrypted and kept when not resent;
only short mono 16-bit WAV is forwarded; upstream errors surface. HTTP is
faked with httpx.MockTransport."""
import asyncio
import io
import json
import wave

import httpx
import pytest

from app import accounts, db, speech


def wav(seconds=0.5, channels=1, rate=16000, width=2):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(b"\0" * width * channels * int(rate * seconds))
    return buf.getvalue()


@pytest.fixture
def server(monkeypatch):
    """The speech server: records each request, answers with handler(request)."""
    seen = []
    state = {"reply": lambda request: httpx.Response(200, json={"text": "  hello there "})}

    def handle(request):
        seen.append(request)
        return state["reply"](request)

    monkeypatch.setattr(speech, "_client", lambda cfg, timeout: httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    return seen, state


def test_only_short_mono_16_bit_wav_is_accepted():
    speech.check_wav(speech.silence_wav())
    speech.check_wav(wav(rate=48000))
    for bad in (b"not audio", wav(channels=2), wav(width=1), wav(rate=96000), wav(seconds=speech.MAX_SECONDS + 1)):
        with pytest.raises(ValueError):
            speech.check_wav(bad)


def test_speech_is_off_until_the_flag_and_a_complete_enabled_source(fresh_db, monkeypatch):
    monkeypatch.setattr(speech.settings, "speech_enabled", True)
    assert speech.available() is False  # no source row
    speech.save(base_url="https://stt.test", model="", api_key="", verify=True, enabled=True)
    assert speech.available() is False  # no model
    speech.save(base_url="https://stt.test", model="whisper", api_key="", verify=True, enabled=False)
    assert speech.available() is False  # switched off by the admin
    speech.save(base_url="https://stt.test", model="whisper", api_key="", verify=True, enabled=True)
    assert speech.available() is True
    monkeypatch.setattr(speech.settings, "speech_enabled", False)
    assert speech.available() is False
    # One source row however often it is saved.
    assert db.fetchone("SELECT COUNT(*) AS n FROM sources WHERE type='speech'")["n"] == 1


def test_the_key_is_encrypted_and_kept_when_not_resent(fresh_db, monkeypatch, server):
    monkeypatch.setattr(speech.settings, "speech_enabled", True)
    speech.save(base_url="https://stt.test:8000", model="parakeet", api_key="sk-speech-secret", verify=False, enabled=True)
    stored = db.fetchone("SELECT secret FROM credentials WHERE source_id=?", (speech.ensure_source(),))["secret"]
    assert "sk-speech-secret" not in stored
    speech.save(base_url="https://stt.test:8000", model="parakeet", api_key="", verify=False, enabled=True)
    view = speech.admin_view()
    assert view["key_set"] is True and "sk-speech-secret" not in json.dumps(view)

    seen, _ = server
    assert asyncio.run(speech.transcribe(speech.silence_wav())) == "hello there"
    [request] = seen
    assert str(request.url) == "https://stt.test:8000/v1/audio/transcriptions"
    assert request.headers["authorization"] == "Bearer sk-speech-secret"
    assert b'name="model"\r\n\r\nparakeet' in request.content and b'filename="audio.wav"' in request.content


def test_a_new_key_updates_its_credential_in_place(fresh_db):
    speech.save(base_url="https://stt.test", model="m", api_key="sk-speech-one", verify=True, enabled=True)
    sid = speech.ensure_source()
    [first] = db.fetchall("SELECT id FROM credentials WHERE source_id=?", (sid,))
    speech.save(base_url="https://stt.test", model="m", api_key="sk-speech-two", verify=True, enabled=True)
    [row] = db.fetchall("SELECT * FROM credentials WHERE source_id=?", (sid,))
    assert row["id"] == first["id"] and accounts.cred_secret(row) == "sk-speech-two"


def test_upstream_errors_and_a_switched_off_service_raise(fresh_db, monkeypatch, server):
    monkeypatch.setattr(speech.settings, "speech_enabled", True)
    with pytest.raises(speech.SpeechError):
        asyncio.run(speech.transcribe(speech.silence_wav()))  # nothing configured: nothing sent
    seen, state = server
    assert seen == []
    speech.save(base_url="https://stt.test", model="m", api_key="", verify=True, enabled=True)
    state["reply"] = lambda request: httpx.Response(403, json={"detail": "no"})
    with pytest.raises(speech.SpeechError, match="403"):
        asyncio.run(speech.transcribe(speech.silence_wav()))
    state["reply"] = lambda request: httpx.Response(200, text="not json")
    with pytest.raises(speech.SpeechError):
        asyncio.run(speech.transcribe(speech.silence_wav()))
    assert "authorization" not in seen[0].headers  # no key, no header
