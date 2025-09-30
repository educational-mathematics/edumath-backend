from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
import io, wave, base64, os, requests

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
TTS_MODEL = os.getenv("TTS_MODEL", "gemini-2.5-flash-tts").strip()

router = APIRouter(prefix="/ai", tags=["ai"])

def _wav_from_pcm16_mono_24k(pcm: bytes) -> io.BytesIO:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)   # 16-bit
        wf.setframerate(24000)
        wf.writeframes(pcm)
    buf.seek(0)
    return buf

@router.post("/tts")
def tts(body: dict):
    text  = (body.get("text") or "").strip()
    voice = (body.get("voice") or "").strip()  # opcional, no garantizado por API

    if not text:
        raise HTTPException(400, "text requerido")
    if not GEMINI_API_KEY:
        raise HTTPException(503, "Gemini TTS no disponible (sin API key)")

    # Hint de voz por prompt (workaround estable).
    # Ej: "Usa una voz española peruana natural (femenina)."
    voice_hint = f"\n\n[VOZ PREFERIDA]: {voice}" if voice else ""

    payload = {
        "contents": [{
            "role": "user",
            "parts": [{"text": text + voice_hint}]
        }],
        "generationConfig": {
            # ← clave correcta hoy
            "responseMimeType": "audio/wav"
        }
    }

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{TTS_MODEL}:generateContent"
    r = requests.post(url, params={"key": GEMINI_API_KEY}, json=payload, timeout=25)

    if r.status_code != 200:
        # Log útil en consola para depurar
        print(f"[/ai/tts] non-200: {r.status_code} {r.text[:400]}")
        raise HTTPException(502, f"TTS error {r.status_code}")

    data = r.json()
    # El audio viene base64 en inlineData.data
    try:
        b64 = (
            data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
        )
    except Exception:
        raise HTTPException(502, "TTS vacío")

    raw = base64.b64decode(b64)
    # Si ya te devuelve WAV, puedes responderlo directo.
    # Muchos modelos devuelven WAV ya listo; por compatibilidad lo envolvemos igual.
    buf = _wav_from_pcm16_mono_24k(raw)
    return StreamingResponse(buf, media_type="audio/wav")