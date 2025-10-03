from pathlib import Path
import os, requests, logging

log = logging.getLogger("tts")

def make_tts(text: str, out_path: Path, voice: str = ""):
    """
    Llama a tu endpoint interno /ai/tts para generar audio WAV.
    No levanta error si falla.
    """
    voice = (voice or os.getenv("TTS_VOICE", "es-ES-Standard-A")).strip()
    try:
        r = requests.post(
            "http://localhost:8000/ai/tts",
            json={"text": text or "", "voice": voice},
            timeout=30,
        )
        if r.status_code == 200 and r.content:
            out_path.write_bytes(r.content)
    except Exception as e:
        log.warning("[utils_tts.make_tts] TTS error: %s", e)

def tts_url_for(session_id: int, name: str) -> str:
    return f"/static/tts/sess-{session_id}-{name}.wav"