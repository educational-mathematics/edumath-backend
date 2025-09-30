from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse
import io, wave, base64, os, requests

from app.ai.gemini import GEMINI_API_KEY, TTS_MODEL

router = APIRouter(prefix="/ai", tags=["ai"])

TTS_MODEL = os.getenv("TTS_MODEL", "gemini-2.5-flash-preview-tts").strip()

@router.post("/tts")
def tts(body: dict):
    text = (body.get("text") or "").strip()
    voice = (body.get("voice") or "Kore").strip()

    if not text:
        # Nada que sintetizar
        return Response(status_code=204)
    if not GEMINI_API_KEY:
        # Sin API key: no rompas el flujo del front
        return Response(status_code=204)

    try:
        # Payload recomendado para TTS: respuesta directa en WAV y voz predefinida
        payload = {
            "contents": [
                {"parts": [{"text": text}]}
            ],
            "generationConfig": {
                # clave importante para audio
                "responseMimeType": "audio/wav",
                # configuración de la voz (prebuilt)
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": voice}
                }
            }
        }

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{TTS_MODEL}:generateContent"
        r = requests.post(url, params={"key": GEMINI_API_KEY}, json=payload, timeout=30)

        if r.status_code != 200:
            # No rompas la UI si hay 429/502/lo que sea: devolvemos 204 (sin contenido)
            # Para debug local, puedes imprimir el cuerpo:
            try:
                print("[/ai/tts] non-200:", r.status_code, r.text[:300])
            except Exception:
                pass
            return Response(status_code=204)

        data = r.json()
        # Audio inline en base64
        b64 = (
            data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("inlineData", {})
                .get("data")
        )

        if not b64:
            # Sin audio: no rompas la UI
            return Response(status_code=204)

        # Gemini devuelve WAV directamente (porque pedimos responseMimeType=audio/wav).
        wav_bytes = base64.b64decode(b64)
        # Si quieres validar como WAV, puedes retornarlo directo.
        return StreamingResponse(io.BytesIO(wav_bytes), media_type="audio/wav")

    except Exception as e:
        # Cualquier error de red/parseo → 204
        try:
            print("[/ai/tts] exception:", e)
        except Exception:
            pass
        return Response(status_code=204)