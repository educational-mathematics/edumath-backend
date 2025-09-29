# app/ai/gemini.py
import os, json, random, re
from typing import List, Dict, Any

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-2.5-flash").strip()
AI_ENABLED = bool(GEMINI_API_KEY) and bool(MODEL_NAME)

# -------------------------
# Utilidades comunes
# -------------------------
FRACTION_RE = re.compile(r"\b(\d{1,2})\s*/\s*(\d{1,2})\b", re.IGNORECASE)

def _short(text: str, n=280) -> str:
    t = (text or "").strip()
    return t if len(t) <= n else (t[:n-1] + "…")

def _ctx_slug(ctx: Dict[str, Any]) -> str:
    return (ctx.get("slug") or "").lower()

def _seems_fractions(ctx: Dict[str, Any]) -> bool:
    # heurística: slug o title contienen "fraccion", o hay a/b en conceptos/ejemplos
    if "fraccion" in _ctx_slug(ctx) or "fracción" in _ctx_slug(ctx):
        return True
    title = (ctx.get("title") or "").lower()
    if "fraccion" in title or "fracción" in title:
        return True
    blob = " ".join([c.get("text","") for c in ctx.get("concepts", [])] +
                    [e.get("explain","") for e in ctx.get("examples", [])])
    return bool(FRACTION_RE.search(blob))

# -------------------------
# Fallbacks genéricos (no IA)
# -------------------------
def fallback_generate_explanation(ctx: Dict[str, Any]) -> str:
    parts = []
    for c in ctx.get("concepts", []):
        txt = (c.get("text") or "").strip()
        if txt: parts.append(txt)
    for e in ctx.get("examples", []):
        txt = (e.get("explain") or "").strip()
        if txt: parts.append(txt)
    if parts:
        # toma 2 piezas y condénsalas
        base = " ".join(parts[:2])
    else:
        base = (ctx.get("title") or "Este tema") + ": repasa la definición y ejemplos clave."
    return _short(base, 320)

def _mcq(question: str, correct: str, distractors: List[str], explain: str = "") -> Dict[str, Any]:
    choices = [correct] + distractors
    random.shuffle(choices)
    return {
        "type": "multiple_choice",
        "question": question,
        "choices": choices,
        "correct_index": choices.index(correct),
        "explain": explain or "Revisa los conceptos del material para justificar la respuesta."
    }

def _pairs(pairs: List[List[str]], title="Empareja conceptos") -> Dict[str, Any]:
    return {
        "type": "match_pairs",
        "title": title,
        "pairs": pairs,
        "explain": "Relaciona cada elemento con su par correspondiente según lo visto en el material."
    }

def _buckets(items: List[str], buckets: List[str], solution: Dict[str, List[str]], title="Clasifica") -> Dict[str, Any]:
    return {
        "type": "drag_to_bucket",
        "title": title,
        "items": items,
        "buckets": buckets,
        "solution": solution,
        "explain": "Organiza según los criterios indicados."
    }

def _fallback_exercises_fractions(ctx: Dict[str, Any], style: str, avoid_numbers: List[int]|None=None) -> List[Dict[str, Any]]:
    # Generador sencillo pero útil para fracciones (como el que tenías),
    # usando constraints si existen.
    allowed = ctx.get("constraints", {}).get("allowed_numbers", {"min": 1, "max": 12})
    min_n = int(allowed.get("min", 1))
    max_n = int(allowed.get("max", 12))
    avoid = set(avoid_numbers or [])

    def pick_frac():
        for _ in range(50):
            a = random.randint(min_n, max_n)
            b = random.randint(min_n+1, max_n+4)
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
        avoid.update([a,b,a*k,b*k])

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
            avoid.update([a1,a2,den])
        else:
            num = random.randint(min_n, max_n)
            b1 = random.randint(num+1, num+6)
            b2 = random.randint(num+1, num+6)
            while b2 == b1: b2 = random.randint(num+1, num+6)
            q = f"¿Cuál es mayor: {num}/{b1} o {num}/{b2}?"
            correct = f"{num}/{min(b1,b2)}"
            distract = [f"{num}/{max(b1,b2)}", f"{num+1}/{b1}", f"{num+1}/{b2}"]
            items.append(_mcq(q, correct, distract, "Mismo numerador: menor denominador → mayor valor."))
            avoid.update([num,b1,b2])

    # 8 pares equivalentes
    pairs = []
    for _ in range(3):
        a,b = pick_frac()
        k = random.choice([2,3,4])
        pairs.append([f"{a}/{b}", f"{a*k}/{b*k}"])
        avoid.update([a,b,a*k,b*k])
    items.append(_pairs(pairs, "Empareja fracciones equivalentes"))

    # 9-10 buckets propias vs impropias
    pool = []
    for _ in range(6):
        a,b = pick_frac()
        pool.append((a,b))
        avoid.update([a,b])
    labels = ["Propias (a<b)", "Impropias (a≥b)"]
    sol = {labels[0]: [], labels[1]: []}
    items_str = []
    for (a,b) in pool:
        s = f"{a}/{b}"
        items_str.append(s)
        (sol[labels[0]] if a < b else sol[labels[1]]).append(s)
    items.append(_buckets(items_str, labels, sol, "Clasifica fracciones"))

    return items[:10]

def _fallback_exercises_generic(ctx: Dict[str, Any], style: str) -> List[Dict[str, Any]]:
    # Genera 10 ítems a partir de concepts/examples (tema agnóstico)
    concepts = [c.get("text","").strip() for c in ctx.get("concepts", []) if c.get("text")]
    examples = [e.get("explain","").strip() for e in ctx.get("examples", []) if e.get("explain")]
    title = ctx.get("title") or "El tema"

    base = concepts or examples or [f"{title}: repasa la definición y ejemplos clave."]
    items: List[Dict,str] = []

    # 1-7 MCQ
    for i in range(7):
        src = random.choice(base)
        # pregunta tipo comprensión
        q = f"Según el material, ¿cuál opción describe mejor lo siguiente?\n“{_short(src, 140)}”"
        correct = "Afirmación coherente con el concepto."
        distractors = [
            "Afirmación parcialmente relacionada pero incorrecta.",
            "Afirmación contradictoria con el concepto.",
            "Afirmación irrelevante."
        ]
        items.append(_mcq(q, correct, distractors, "Identifica la idea principal del concepto explicado."))

    # 8 pares (concepto ↔ idea)
    pairs = []
    for c in concepts[:3]:
        key = _short(c.split(".")[0], 40)
        val = _short(c, 80)
        pairs.append([key, val])
    if pairs:
        items.append(_pairs(pairs, "Relaciona concepto con su idea"))

    # 9-10 buckets por “tipo de enunciado”
    buckets = ["Definiciones", "Ejemplos"]
    pool = []
    sol = {buckets[0]: [], buckets[1]: []}
    for s in (concepts[:3] + examples[:3]):
        tag = buckets[0] if s in concepts else buckets[1]
        sol[tag].append(_short(s, 80))
        pool.append(_short(s, 80))
    if pool:
        items.append(_buckets(pool, buckets, sol, "Clasifica en definiciones / ejemplos"))

    # si quedaron menos de 10, completa con MCQ “genéricos”
    while len(items) < 10:
        src = random.choice(base)
        q = f"Elige la opción correcta sobre: “{_short(src, 120)}”"
        items.append(_mcq(q, "Enunciado correcto", ["Variación incorrecta", "Conclusión inválida", "Dato no respaldado"]))

    return items[:10]

def fallback_generate_exercises(ctx: Dict[str, Any], style: str, avoid_numbers: List[int]|None=None) -> List[Dict[str, Any]]:
    return _fallback_exercises_fractions(ctx, style, avoid_numbers) if _seems_fractions(ctx) else _fallback_exercises_generic(ctx, style)

# -------------------------
# IA real (con validación) + fallback
# -------------------------
def generate_exercises_variant(ctx: Dict[str, Any], style: str, avoid_numbers: List[int]|None=None) -> List[Dict[str, Any]]:
    if not AI_ENABLED:
        return fallback_generate_exercises(ctx, style, avoid_numbers)

    try:
        import requests
        prompt = {
            "instruction": "Genera 10 ejercicios alineados al tema y estilo VAK.",
            "style": style,
            "avoid_numbers": avoid_numbers or [],
            "context_json": ctx,  # 👈 TODO: el modelo debe usar SOLO este contenido
            "output_schema": "array of TopicItem {type:'multiple_choice'|'match_pairs'|'drag_to_bucket', ...}",
            "constraints": [
                "No inventes contenido fuera del JSON.",
                "Devuelve JSON válido, SOLO el array de 10 items.",
                "Para 'multiple_choice': incluye 'question','choices'(4), 'correct_index', 'explain'.",
                "Para 'match_pairs': incluye 'title','pairs': [[L,R],...].",
                "Para 'drag_to_bucket': incluye 'title','items':[], 'buckets':[], 'solution':{bucket:[items]}."
            ]
        }
        resp = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent",
            params={"key": GEMINI_API_KEY},
            json={"contents": [{"parts":[{"text": json.dumps(prompt)}]}]},
            timeout=25
        )
        if resp.status_code != 200:
            # log y fallback
            try_text = resp.text[:400]
            print(f"[gemini] non-200: {resp.status_code} body={try_text}")
            return fallback_generate_exercises(ctx, style, avoid_numbers)

        data = resp.json()
        text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        if not text:
            return fallback_generate_exercises(ctx, style, avoid_numbers)
        parsed = json.loads(text)
        # sanity checks
        if not isinstance(parsed, list) or len(parsed) < 1:
            return fallback_generate_exercises(ctx, style, avoid_numbers)
        return parsed[:10]

    except Exception as e:
        print(f"[gemini] exception: {e}")
        return fallback_generate_exercises(ctx, style, avoid_numbers)

def generate_explanation(ctx: Dict[str, Any]) -> str:
    if not AI_ENABLED:
        return fallback_generate_explanation(ctx)
    try:
        # Aquí puedes llamar a TTS/explicación real; por ahora devuelve texto breve
        return fallback_generate_explanation(ctx)
    except Exception as e:
        print(f"[gemini] explanation exception: {e}")
        return fallback_generate_explanation(ctx)