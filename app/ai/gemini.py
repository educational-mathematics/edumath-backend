# app/ai/gemini.py
import os, json, random, re, base64
from typing import List, Dict, Any
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ------------------ Config ------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
MODEL_NAME     = os.getenv("MODEL_NAME", "gemini-2.5-flash").strip()
IMAGE_MODEL    = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image-preview").strip()
TTS_MODEL      = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts").strip()
AI_ENABLED     = bool(GEMINI_API_KEY) and bool(MODEL_NAME)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
FRACTION_RE = re.compile(r"\b(\d{1,2})\s*/\s*(\d{1,2})\b", re.IGNORECASE)
_OPCION_RE = re.compile(r"^\s*Opción\s+\d+\s*$", re.IGNORECASE)

# Session con reintentos (para 429/5xx)
_session = requests.Session()
_session.mount(
    "https://",
    HTTPAdapter(
        max_retries=Retry(
            total=3,
            backoff_factor=1.2,
            status_forcelist=(408, 409, 429, 500, 502, 503, 504),
            allowed_methods=frozenset(["POST"]),
            raise_on_status=False,
        )
    ),
)

def _post_genai(model: str, payload: dict, timeout: int = 60) -> dict:
    """model es el id (p.ej. 'gemini-2.5-flash'), NO una URL."""
    if model.startswith("http"):
        parts = model.split("/models/")
        model = parts[-1].split(":")[0] if len(parts) > 1 else model
    url = f"{BASE_URL}/{model}:generateContent"
    resp = _session.post(url, params={"key": GEMINI_API_KEY}, json=payload, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"[gemini] non-200: {resp.status_code} body={resp.text[:400]}")
    return resp.json()

# ------------------ Utils ------------------
def _short(text: str, n=280) -> str:
    t = (text or "").strip()
    return t if len(t) <= n else (t[:n-1] + "…")

def _seems_fractions(ctx: Dict[str, Any]) -> bool:
    slug = (ctx.get("slug") or "").lower()
    title = (ctx.get("title") or "").lower()
    if "fraccion" in slug or "fracción" in slug or "fraccion" in title or "fracción" in title:
        return True
    blob = " ".join([c.get("text","") for c in ctx.get("concepts", [])] +
                    [e.get("explain","") for e in ctx.get("examples", [])])
    return bool(FRACTION_RE.search(blob))

def _unique_list(seq: List[Any]) -> List[str]:
    out, seen = [], set()
    for x in (seq or []):
        if x is None:
            continue
        s = str(x).strip()
        if not s or s in seen:
            continue
        seen.add(s); out.append(s)
    return out

# ------------------ Builders seguros ------------------
def _mcq(question: str, correct: str, distractors: List[str], explain: str = "") -> Dict[str, Any]:
    ds = []
    for d in (distractors or []):
        d = (d or "").strip()
        if d and d != correct and d not in ds:
            ds.append(d)
    while len(ds) < 3:
        ds.append(f"Distractor {len(ds)+1}")
    # 4 únicas
    seen, choices = set(), []
    for x in [correct] + ds:
        if x not in seen:
            choices.append(x); seen.add(x)
    while len(choices) < 4:
        choices.append(f"Opción {len(choices)+1}")
    choices = choices[:4]
    random.shuffle(choices)
    return {
        "type": "multiple_choice",
        "question": (question or "").strip() or "Elige la opción correcta",
        "choices": choices,
        "correct_index": choices.index(correct) if correct in choices else 0,
        "explain": (explain or "Revisa los conceptos del material para justificar la respuesta.").strip()
    }

def _pairs(pairs: List[List[str]], title="Empareja conceptos") -> Dict[str, Any]:
    clean = []
    seen = set()
    for p in (pairs or []):
        if not (isinstance(p, (list, tuple)) and len(p) == 2):
            continue
        L, R = (p[0] or "").strip(), (p[1] or "").strip()
        if not L or not R:
            continue
        key = (L, R)
        if key not in seen:
            clean.append([L, R]); seen.add(key)
    if len(clean) < 2:
        clean = [["A","A"], ["B","B"]]
    return {
        "type":"match_pairs",
        "title": title or "Empareja",
        "pairs": clean[:6],
        "explain": "Relaciona cada elemento con su par correspondiente."
    }

def _to_mcq_from_bucket(item: Dict[str, Any]) -> Dict[str, Any]:
    """Convierte un drag_to_bucket en MCQ segura para evitar imposibles en UI."""
    title = (item.get("title") or "Clasifica").strip()
    buckets = item.get("buckets") or []
    solution = item.get("solution") or {}
    items = _unique_list(item.get("items") or [])
    if not buckets or not items or not solution:
        return _mcq(title, items[0] if items else "Correcta", items[1:4] if items else [])
    # elige bucket más poblado y arma la MCQ
    bucket = max(buckets, key=lambda b: len(solution.get(b, [])))
    correct_pool = list(solution.get(bucket, []))
    other_pool = [x for x in items if x not in correct_pool]
    if not correct_pool:
        correct_pool = items[:1]; other_pool = items[1:]
    correct = random.choice(correct_pool)
    distract = other_pool[:3]
    q = f"Según “{title}”, ¿cuál pertenece a «{bucket}»?"
    return _mcq(q, correct, distract, "Identifica el criterio y elige un ejemplo que lo cumpla.")

# ------------------ Sanitización única ------------------
def _fix_mcq(it: Dict[str, Any]) -> Dict[str, Any]:
    q = (it.get("question") or "").strip() or "Elige la opción correcta"
    choices = _unique_list(it.get("choices") or [])
    # quita placeholders "Opción N"
    choices = [c for c in choices if not _OPCION_RE.match(c)]
    while len(choices) < 4:
        choices.append(f"Alternativa {len(choices)+1}")
    choices = choices[:4]
    idx = it.get("correct_index")
    if not isinstance(idx, int) or not (0 <= idx < len(choices)):
        # intenta por texto "correct"
        correct_text = (it.get("correct") or "").strip()
        idx = choices.index(correct_text) if correct_text in choices else 0
    return {
        "type": "multiple_choice",
        "question": q,
        "choices": choices,
        "correct_index": idx,
        "explain": (it.get("explain") or "Revisa el concepto clave.").strip()
    }

def _fix_pairs(it: Dict[str, Any]) -> Dict[str, Any]:
    pairs = []
    for p in (it.get("pairs") or []):
        if isinstance(p, (list, tuple)) and len(p) == 2:
            L, R = str(p[0]).strip(), str(p[1]).strip()
            if L and R:
                pairs.append([L, R])
    if len(pairs) < 2:
        pairs = [["A","A"], ["B","B"]]
    return {
        "type": "match_pairs",
        "title": (it.get("title") or "Empareja").strip(),
        "pairs": pairs[:6],
        "explain": (it.get("explain") or "Relaciona cada elemento con su par correspondiente.").strip()
    }

def _sanitize_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for it in (items or []):
        t = (it or {}).get("type")
        if t == "multiple_choice":
            out.append(_fix_mcq(dict(it)))
        elif t == "match_pairs":
            out.append(_fix_pairs(dict(it)))
        elif t == "drag_to_bucket":
            # 🚫 Convertimos buckets a MCQ para evitar “imposibles” en el front
            out.append(_to_mcq_from_bucket(dict(it)))
        else:
            # fallback seguro
            out.append(_fix_mcq({
                "question": str(it)[:140] or "Elige la opción correcta.",
                "choices": ["Correcta","Incorrecta 1","Incorrecta 2","Incorrecta 3"],
                "correct_index": 0,
                "explain": "Selecciona la alternativa válida."
            }))
    # siempre 10 items
    while len(out) < 10:
        out.append(_fix_mcq({
            "question":"Elige la opción correcta.",
            "choices":["Correcta","Incorrecta 1","Incorrecta 2","Incorrecta 3"],
            "correct_index":0
        }))
    return out[:10]

# ------------------ Fallbacks ------------------
def fallback_generate_explanation(ctx: Dict[str, Any]) -> str:
    parts = []
    for c in ctx.get("concepts", []):
        txt = (c.get("text") or "").strip()
        if txt: parts.append(txt)
    for e in ctx.get("examples", []):
        txt = (e.get("explain") or "").strip()
        if txt: parts.append(txt)
    base = " ".join(parts[:2]) if parts else (ctx.get("title") or "Este tema") + ": repasa la definición y ejemplos clave."
    return _short("Explicación breve: " + base, 380)

def _fallback_exercises_fractions(ctx: Dict[str, Any], style: str, avoid_numbers: List[int]|None=None) -> List[Dict[str, Any]]:
    allowed = ctx.get("constraints", {}).get("allowed_numbers", {"min": 1, "max": 12})
    min_n = int(allowed.get("min", 1))
    max_n = int(allowed.get("max", 12))
    avoid = set(avoid_numbers or [])

    def pick_frac():
        for _ in range(50):
            a = random.randint(min_n, max_n)
            b = random.randint(max(a+1, min_n+1), max(a+4, min_n+2))
            if a == b: continue
            if a in avoid or b in avoid: continue
            return a,b
        a = random.randint(min_n, max_n)
        b = max(a+1, random.randint(min_n+1, max_n+4))
        return a,b

    items: List[Dict[str, Any]] = []

    # 1-4 equivalencias
    for _ in range(4):
        a,b = pick_frac()
        k = random.choice([2,3,4])
        correct = f"{a*k}/{b*k}"
        distract = [f"{a}/{b+k}", f"{a+k}/{b}", f"{a*k}/{b+k}"]
        q = f"¿Cuál es equivalente a {a}/{b}?"
        items.append(_mcq(q, correct, distract, "Multiplica numerador y denominador por el mismo número."))

    # 5-7 comparar
    for _ in range(3):
        if random.choice([True, False]):
            den = random.randint(min_n+1, max_n+4)
            a1 = random.randint(min_n, den-1)
            a2 = random.randint(min_n, den-1)
            while a2 == a1: a2 = random.randint(min_n, den-1)
            q = f"¿Cuál es mayor: {a1}/{den} o {a2}/{den}?"
            correct = f"{max(a1,a2)}/{den}"
            distract = [f"{min(a1,a2)}/{den}", f"{a1}/{den+1}", f"{a2}/{den+1}"]
            items.append(_mcq(q, correct, distract, "Mismo denominador: mayor numerador → mayor valor."))
        else:
            num = random.randint(min_n, max_n)
            b1 = random.randint(num+1, num+6)
            b2 = random.randint(num+1, num+6)
            while b2 == b1: b2 = random.randint(num+1, num+6)
            q = f"¿Cuál es mayor: {num}/{b1} o {num}/{b2}?"
            correct = f"{num}/{min(b1,b2)}"
            distract = [f"{num}/{max(b1,b2)}", f"{num+1}/{b1}", f"{num+1}/{b2}"]
            items.append(_mcq(q, correct, distract, "Mismo numerador: menor denominador → mayor valor."))

    # 8 pares equivalentes
    pairs = []
    for _ in range(3):
        a,b = pick_frac()
        k = random.choice([2,3,4])
        pairs.append([f"{a}/{b}", f"{a*k}/{b*k}"])
    items.append(_pairs(pairs, "Empareja fracciones equivalentes"))

    # 9: impropia
    choices9 = []
    while len(choices9) < 4:
        a, b = pick_frac()
        s = f"{a}/{b}"
        if s not in choices9: choices9.append(s)
    impropias = [x for x in choices9 if int(x.split('/')[0]) >= int(x.split('/')[1])]
    if not impropias:
        a, b = max_n, min_n
        impropias = [f"{a}/{max(a,b)}"]
        choices9[0] = impropias[0]
    items.append(_mcq("¿Cuál es impropia (a≥b)?", impropias[0],
                      [x for x in choices9 if x != impropias[0]],
                      "Impropia: numerador mayor o igual al denominador."))

    # 10: propia
    choices10 = []
    while len(choices10) < 4:
        a, b = pick_frac()
        s = f"{a}/{b}"
        if s not in choices10: choices10.append(s)
    propias = [x for x in choices10 if int(x.split('/')[0]) < int(x.split('/')[1])]
    if not propias:
        a, b = min_n, max_n
        propias = [f"{a}/{max(a+1,b)}"]
        choices10[0] = propias[0]
    items.append(_mcq("¿Cuál es propia (a<b)?", propias[0],
                        [x for x in choices10 if x != propias[0]],
                        "Propia: numerador menor que el denominador."))

    return _sanitize_items(items)

def _fallback_exercises_generic(ctx: Dict[str, Any], style: str) -> List[Dict[str, Any]]:
    concepts = [c.get("text","").strip() for c in ctx.get("concepts", []) if c.get("text")]
    examples = [e.get("explain","").strip() for e in ctx.get("examples", []) if e.get("explain")]
    base = concepts or examples or ["Repasa definición y ejemplos clave."]

    items: List[Dict[str, Any]] = []
    for _ in range(7):
        src = random.choice(base)
        q = f"Según el material, ¿cuál opción describe mejor: “{_short(src, 120)}”?"
        items.append(_mcq(q, "Afirmación coherente con el concepto.",
                            ["Afirmación parcialmente relacionada pero incorrecta.",
                            "Afirmación contradictoria con el concepto.",
                            "Afirmación irrelevante."],
                            "Identifica la idea principal."))

    if concepts[:3]:
        pairs = []
        for c in concepts[:3]:
            key = _short(c.split(".")[0], 40)
            val = _short(c, 80)
            if key and val: pairs.append([key, val])
        items.append(_pairs(pairs, "Relaciona concepto con su idea"))

    # En lugar de drag_to_bucket (que puede romper UI), generamos otra MCQ
    while len(items) < 10:
        src = random.choice(base)
        items.append(_mcq(f"Elige la opción correcta sobre: “{_short(src, 110)}”",
                            "Enunciado correcto",
                            ["Variación incorrecta","Conclusión inválida","Dato no respaldado"]))
    return _sanitize_items(items)

def fallback_generate_exercises(ctx: Dict[str, Any], style: str, avoid_numbers=None) -> List[Dict[str, Any]]:
    base = _fallback_exercises_fractions(ctx, style, avoid_numbers) if _seems_fractions(ctx) else _fallback_exercises_generic(ctx, style)
    return _sanitize_items(base)

# ------------------ IA: ejercicios ------------------
def generate_exercises_variant(ctx: dict, style: str, avoid_numbers=None) -> list[dict]:
    if not AI_ENABLED:
        return fallback_generate_exercises(ctx, style, avoid_numbers)

    try:
        prompt = {
            "task": "Genera 10 ejercicios alineados al tema y estilo VAK",
            "style": style,
            "avoid_numbers": avoid_numbers or [],
            "context_json": ctx,
            "must_follow": [
                "Usa SOLO el contenido del JSON de contexto.",
                "Devuelve JSON válido: un array con 10 objetos.",
                "Tipos permitidos: 'multiple_choice'|'match_pairs'|'drag_to_bucket'.",
                "multiple_choice: question, choices(4 únicas), correct_index, explain.",
                "match_pairs: title, pairs [[L,R],...].",
                "drag_to_bucket: title, items[], buckets[], solution{bucket:[items]} (partición válida)."
            ]
        }

        data = _post_genai(
            MODEL_NAME,
            {"contents": [{"parts": [{"text": json.dumps(prompt, ensure_ascii=False)}]}]} ,
            timeout=60,
        )

        text = (data.get("candidates", [{}])[0]
                    .get("content", {}).get("parts", [{}])[0]
                    .get("text", "") or "").strip()
        if not text:
            raise RuntimeError("empty text from model")

        try:
            parsed = json.loads(text)
        except Exception:
            t2 = text.strip().strip("`")
            t2 = re.sub(r"^json", "", t2, flags=re.I).strip()
            parsed = json.loads(t2)

        # Post-validación fuerte
        if not isinstance(parsed, list) or len(parsed) == 0:
            raise RuntimeError("parsed is not a non-empty list")

        items = _sanitize_items(parsed)
        if not isinstance(items, list) or len(items) == 0:
            raise RuntimeError("sanitized empty, forcing fallback")

        # completa a 10 si hace falta
        while len(items) < 10:
            items.append({
                "type": "multiple_choice",
                "question": "Elige la opción correcta.",
                "choices": ["Correcta", "Incorrecta 1", "Incorrecta 2", "Incorrecta 3"],
                "correct_index": 0,
                "explain": "Revisa el concepto clave."
            })
        return items[:10]

    except Exception as e:
        print(f"[gemini] exception: {e} -> fallback")
        return fallback_generate_exercises(ctx, style, avoid_numbers)
# ------------------ IA: explicación ------------------
def generate_explanation(ctx: Dict[str, Any]) -> str:
    """
    Explicación de 4-7 oraciones, parafraseada del JSON (sin copiar literal).
    """
    if not AI_ENABLED:
        return fallback_generate_explanation(ctx)
    try:
        payload = {
            "contents":[{"parts":[{"text": json.dumps({
                "instruction": (
                    "Escribe una explicación de 4 a 7 oraciones, clara y motivadora para primaria. "
                    "No copies texto literal del JSON. Parafrasea y complementa con un ejemplo simple. "
                    "Usa solo la información del contexto. Devuelve solo el texto."
                ),
                "context_json": ctx
            }, ensure_ascii=False)}]}]
        }
        data = _post_genai(MODEL_NAME, payload, timeout=40)
        text = (data.get("candidates",[{}])[0].get("content",{}).get("parts",[{}])[0].get("text","") or "").strip()
        return text or fallback_generate_explanation(ctx)
    except Exception as e:
        print("[gemini.explanation] exception:", e)
        return fallback_generate_explanation(ctx)

# ------------------ IA: imagen (visual) ------------------
def generate_one_image_png(prompt: str) -> bytes | None:
    if not AI_ENABLED or not IMAGE_MODEL:
        return None
    try:
        data = _post_genai(IMAGE_MODEL, {"contents": [{"parts":[{"text": prompt}]}]}, timeout=60)
        parts = data.get("candidates",[{}])[0].get("content",{}).get("parts",[])
        for p in parts:
            inline = p.get("inlineData") or p.get("inline_data")
            if inline and inline.get("data"):
                return base64.b64decode(inline["data"])
        return None
    except Exception as e:
        print(f"[gemini] image exception: {e}")
        return None
