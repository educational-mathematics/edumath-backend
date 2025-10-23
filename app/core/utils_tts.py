# app/core/utils_tts.py
from pathlib import Path
import os, requests, logging, io, wave, math, struct, tempfile

log = logging.getLogger("tts")
TTS_ENDPOINT = "http://localhost:8000/ai/tts"

def _is_valid_wav(path: Path) -> bool:
    try:
        if not path.exists() or path.stat().st_size < 4096:
            return False
        with wave.open(str(path), "rb") as wf:
            return wf.getnframes() > 0 and wf.getframerate() >= 8000
    except Exception:
        return False

def _write_emergency_beep(path: Path, sr: int = 24000, dur: float = 0.4, f: float = 880.0):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sr)
        n = int(sr * dur)
        for i in range(n):
            t = i / sr
            amp = 0.25 * (1.0 - min(1.0, abs(2*(t/dur)-1)))  # fade in/out
            val = int(amp * 32767 * math.sin(2*math.pi*f*t))
            wf.writeframes(struct.pack("<h", val))

def make_tts(text: str, out_path: Path, voice: str = ""):
    voice = (voice or os.getenv("TTS_VOICE", "es-ES-Standard-A")).strip()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with requests.post(
            TTS_ENDPOINT,
            json={"text": text or "", "voice": voice},
            timeout=60,
            stream=True,
        ) as r:
            ok = (r.status_code == 200)
            ct = (r.headers.get("Content-Type") or "").lower()
            if ok and ("audio" in ct or "octet-stream" in ct):
                with tempfile.NamedTemporaryFile(delete=False, dir=str(out_path.parent), suffix=".wav") as tf:
                    for chunk in r.iter_content(chunk_size=65536):
                        if chunk:
                            tf.write(chunk)
                    temp_path = Path(tf.name)
                if _is_valid_wav(temp_path):
                    temp_path.replace(out_path)
                    return
                else:
                    log.warning("[utils_tts] WAV inválido (ct=%s, size=%s)", ct, temp_path.stat().st_size)
                    temp_path.unlink(missing_ok=True)
            else:
                log.warning("[utils_tts] HTTP %s content-type=%s", r.status_code, ct)
    except Exception as e:
        log.warning("[utils_tts] TTS error: %s", e)

    # fallback: beep para no dejar el audio mudo
    _write_emergency_beep(out_path)

def tts_url_for(session_id: int, name: str) -> str:
    """
    Si PUBLIC_BACKEND_ORIGIN está seteado (p.ej. http://localhost:8000),
    devolvemos URL absoluta; si no, relativa como siempre.
    """
    rel = f"/static/tts/sess-{session_id}-{name}.wav"
    origin = os.getenv("PUBLIC_BACKEND_ORIGIN", "").rstrip("/")
    return f"{origin}{rel}" if origin else rel