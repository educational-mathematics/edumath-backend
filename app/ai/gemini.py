# app/ai/gemini.py
import os, json, random, re, base64
from typing import List, Dict, Any, Tuple

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
MODEL_NAME = os.getenv("MODEL_NAME", "gemini-2.5-flash").strip()
IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image-preview").strip()
TTS_MODEL   = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts").strip()
AI_ENABLED = bool(GEMINI_API_KEY) and bool(MODEL_NAME)

FRACTION_RE = re.compile(r"\b(\d{1,2})\s*/\s*(\d{1,2})\b", re.IGNORECASE)

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

# ---------- Helpers de construcción segura ----------

def _unique_fill(ops: List[str], want=4) -> List[str]:
    seen, out = set(), []
    for x in ops:
        x = (x or "").strip()
        if x and x not in seen:
            out.append(x); seen.add(x)
    while len(out) < want:
        out.append(f"Opción {len(out)+1}")
    return out[:want]

def _mcq(question: str, correct: str, distractors: List[str], explain: str = "") -> Dict[str, Any]:
    # re-genera distractores si chocan con correct
    ds = []
    for d in distractors:
        d = (d or "").strip()
        if d and d != correct and d not in ds:
            ds.append(d)
    while len(ds) < 3:
        ds.append(f"Distractor {len(ds)+1}")
    choices = _unique_fill([correct] + ds, 4)
    # si por deduplicación se perdió el correct, lo insertamos forzado
    if correct not in choices:
        choices[0] = correct
    random.shuffle(choices)
    return {
        "type": "multiple_choice",
        "question": (question or "").strip(),
        "choices": choices,
        "correct_index": choices.index(correct),
        "explain": (explain or "Revisa los conceptos del material para justificar la respuesta.").strip()
    }

def _pairs(pairs: List[List[str]], title="Empareja conceptos") -> Dict[str, Any]:
    clean = []
    seen = set()
    for p in pairs:
        if not (isinstance(p, (list, tuple)) and len(p) == 2): 
            continue
        L, R = (p[0] or "").strip(), (p[1] or "").strip()
        if not L or not R: 
            continue
        key = (L, R)
        if key not in seen:
            clean.append([L, R]); seen.add(key)
    if not clean:
        clean = [["A","A"], ["B","B"], ["C","C"]]
    return {"type":"match_pairs","title":title,"pairs":clean,
            "explain":"Relaciona cada elemento con su par correspondiente."}

def _buckets(items: List[str], buckets: List[str], solution: Dict[str, List[str]], title="Clasifica") -> Dict[str, Any]:
    # Normaliza: cada item debe pertenecer a EXACTAMENTE un bucket, sin duplicados.
    items = [i.strip() for i in items if i and i.strip()]
    items = _unique_fill(items, want=len(items) or 6)  # mantiene los existentes
    buckets = [b.strip() for b in buckets if b and b.strip()]
    if not buckets: buckets = ["Grupo A","Grupo B"]
    sol = {b: [] for b in buckets}

    seen = set()
    for b, lst in (solution or {}).items():
        if b not in sol: 
            continue
        for i in (lst or []):
            i = (i or "").strip()
            if i and i in items and i not in seen:
                sol[b].append(i); seen.add(i)

    # Asigna items faltantes a buckets con menos carga (para evitar imposibles vacíos)
    idx = 0
    for i in items:
        if i not in seen:
            sol[buckets[idx % len(buckets)]].append(i)
            idx += 1

    # Garantiza al menos 1 por bucket
    for b in buckets:
        if not sol[b]:
            sol[b].append(items[0])

    return {"type":"drag_to_bucket","title":title,"items":items,"buckets":buckets,
            "solution":sol,"explain":"Organiza según los criterios indicados."}

# ---------- Fallbacks temáticos ----------

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
            if a == b: 
                continue
            if a in avoid or b in avoid: 
                continue
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

    # 9 clasificar propias vs impropias
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

    # 10 MCQ genérico de concepto
    items.append(_mcq("Selecciona la afirmación correcta sobre fracciones.",
                      "Dos fracciones equivalentes representan la misma cantidad.",
                      ["Sumar denominadores no suma fracciones.",
                       "Cambiar solo el numerador mantiene el valor.",
                       "b puede ser 0 en una fracción."]))
    return items[:10]

def _fallback_exercises_generic(ctx: Dict[str, Any], style: str) -> List[Dict[str, Any]]:
    concepts = [c.get("text","").strip() for c in ctx.get("concepts", []) if c.get("text")]
    examples  = [e.get("explain","").strip() for e in ctx.get("examples", []) if e.get("explain")]
    base = concepts or examples or ["Repasa definición y ejemplos clave."]

    items: List[Dict,] = []
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

    buckets = ["Definiciones", "Ejemplos"]
    pool = []
    sol  = {buckets[0]: [], buckets[1]: []}
    for s in (concepts[:3] + examples[:3]):
        tag = buckets[0] if s in concepts else buckets[1]
        text = _short(s, 80)
        sol[tag].append(text); pool.append(text)
    if pool:
        items.append(_buckets(pool, buckets, sol, "Clasifica definiciones y ejemplos"))

    while len(items) < 10:
        src = random.choice(base)
        items.append(_mcq(f"Elige la opción correcta sobre: “{_short(src, 110)}”",
                          "Enunciado correcto",
                          ["Variación incorrecta","Conclusión inválida","Dato no respaldado"]))
    return items[:10]

def fallback_generate_exercises(ctx: Dict[str, Any], style: str, avoid_numbers: List[int]|None=None) -> List[Dict[str, Any]]:
    return _fallback_exercises_fractions(ctx, style, avoid_numbers) if _seems_fractions(ctx) else _fallback_exercises_generic(ctx, style)

# ---------- Sanitización post-IA ----------

def _sanitize_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normaliza y corrige items IA para evitar imposibles y duplicados."""
    out = []
    for it in (items or []):
        t = (it.get("type") or "").strip()
        if t == "multiple_choice":
            q = it.get("question","")
            ch = it.get("choices",[])
            ci = it.get("correct_index",0)
            exp= it.get("explain","")
            # seguridad
            if not isinstance(ch, list) or len(ch) < 1:
                continue
            correct = ""
            try:
                correct = ch[int(ci)]
            except Exception:
                correct = ch[0]
            # reconstruye para asegurar unique y index
            # añade algunos distractores si faltan
            dists = [x for i,x in enumerate(ch) if i != ci]
            safe = _mcq(q, correct, dists, exp)
            out.append(safe)

        elif t == "match_pairs":
            pairs = it.get("pairs", [])
            title = it.get("title") or "Empareja"
            out.append(_pairs(pairs, title))

        elif t == "drag_to_bucket":
            itemsL = it.get("items", [])
            buckets = it.get("buckets", [])
            solution = it.get("solution", {})
            title = it.get("title") or "Clasifica"
            out.append(_buckets(itemsL, buckets, solution, title))
        else:
            # descarta tipos no soportados
            pass
    return out[:10]

# ---------- IA: ejercicios ----------

def _post_genai(endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    import requests
    r = requests.post(endpoint, params={"key": GEMINI_API_KEY}, json=payload, timeout=25)
    if r.status_code != 200:
        try_text = r.text[:400]
        print(f"[gemini] non-200: {r.status_code} body={try_text}")
        return {}
    return r.json()

def generate_exercises_variant(ctx: Dict[str, Any], style: str, avoid_numbers: List[int]|None=None) -> List[Dict[str, Any]]:
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
                "drag_to_bucket: title, items[], buckets[], solution{bucket:[items]} con partición válida (cada item en 1 bucket)."
            ]
        }
        data = _post_genai(
            f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent",
            {"contents": [{"parts":[{"text": json.dumps(prompt, ensure_ascii=False)}]}]}
        )
        text = (data.get("candidates",[{}])[0].get("content",{}).get("parts",[{}])[0].get("text","") or "").strip()
        if not text:
            return fallback_generate_exercises(ctx, style, avoid_numbers)

        try:
            parsed = json.loads(text)
        except Exception:
            # a veces devuelve ```json ... ```
            text2 = text.strip().strip("`")
            text2 = re.sub(r"^json", "", text2, flags=re.I).strip()
            parsed = json.loads(text2)

        items = _sanitize_items(parsed)
        if len(items) < 10:
            items += fallback_generate_exercises(ctx, style, avoid_numbers)[len(items):]
        return items[:10]

    except Exception as e:
        print(f"[gemini] exception: {e}")
        return fallback_generate_exercises(ctx, style, avoid_numbers)

# ---------- IA: explicación breve ----------

def generate_explanation(ctx: Dict[str, Any]) -> str:
    """
    Explicación corta (4 a 7 oraciones), clara y amigable.
    No copia literal el JSON: parafrasea y complementa con ejemplos simples.
    """
    if AI_ENABLED:
        try:
            import requests, json
            prompt = {
                "instruction": (
                    "Escribe una explicación de 4 a 7 oraciones, clara y motivadora para primaria. "
                    "No copies texto literal del JSON. Parafrasea y complementa con un ejemplo muy simple. "
                    "Usa solo la información del contexto. Devuelve solo el texto."
                ),
                "context_json": ctx
            }
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL_NAME}:generateContent",
                params={"key": GEMINI_API_KEY},
                json={"contents":[{"parts":[{"text": json.dumps(prompt, ensure_ascii=False)}]}]},
                timeout=25
            )
            if r.status_code == 200:
                data = r.json()
                text = data.get("candidates",[{}])[0].get("content",{}).get("parts",[{}])[0].get("text","").strip()
                if text:
                    return text 
        except Exception as e:
            print("[gemini.explanation] exception:", e)

    # fallback
    return fallback_generate_explanation(ctx)

# ---------- IA: imagen simple (para estilo visual) ----------

def generate_one_image_png(prompt: str) -> bytes | None:
    if not AI_ENABLED or not IMAGE_MODEL:
        return None
    try:
        data = _post_genai(
            f"https://generativelanguage.googleapis.com/v1beta/models/{IMAGE_MODEL}:generateContent",
            {"contents": [{"parts":[{"text": prompt}]}]}
        )
        parts = data.get("candidates",[{}])[0].get("content",{}).get("parts",[])
        for p in parts:
            if "inlineData" in p or "inline_data" in p:
                b64 = p.get("inlineData",{}).get("data") or p.get("inline_data",{}).get("data")
                if b64:
                    return base64.b64decode(b64)
        return None
    except Exception as e:
        print(f"[gemini] image exception: {e}")
        return None