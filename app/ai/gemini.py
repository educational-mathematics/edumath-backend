# app/ai/gemini.py
import os, json, random, re, base64
from typing import List, Dict, Any
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ------------------ Config ------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
MODEL_NAME     = os.getenv("MODEL_NAME", "gemini-2.5-flash").strip()
IMAGE_MODEL    = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image").strip()
TTS_MODEL      = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts").strip()
AI_ENABLED     = bool(GEMINI_API_KEY) and bool(MODEL_NAME)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
FRACTION_RE = re.compile(r"\b(\d{1,2})\s*/\s*(\d{1,2})\b", re.IGNORECASE)
_OPCION_RE = re.compile(r"^\s*Opción\s+\d+\s*$", re.IGNORECASE)

print(f"[gemini] MODEL={MODEL_NAME!r} AI_ENABLED={AI_ENABLED} KEY_SET={'YES' if GEMINI_API_KEY else 'NO'}")

def ensure_ai_ready():
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY no está definido (AI_DISABLED).")
    if not MODEL_NAME:
        raise RuntimeError("MODEL_NAME no está definido (AI_DISABLED).")

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

def _call_gemini_json(prompt_text: str,
                      model: str | None = None,
                      timeout: int = 60,
                      temperature: float = 0.3) -> dict | list:
    """
    Envía un prompt en texto y espera una respuesta en formato JSON.
    - Extrae el texto del primer candidato/parte.
    - Limpia cercas de código ``` y prefijos tipo 'json'.
    - Intenta parsear; si falla, recorta desde el primer '{' o '[' al último '}' o ']'.
    Retorna dict o list (según lo que devuelva el modelo).
    """
    if not AI_ENABLED:
        # Devuelve un esqueleto “vacío” seguro
        return {"paragraphs": [], "examples": []}

    ensure_ai_ready()
    model = (model or MODEL_NAME).strip()

    payload = {
        "generationConfig": {"temperature": temperature},
        "contents": [{"parts": [{"text": prompt_text}]}],
    }

    data = _post_genai(model, payload, timeout=timeout)

    # ---- extraer texto de la respuesta ----
    text = ""
    try:
        cand = (data.get("candidates") or [])[0]
        parts = (cand.get("content") or {}).get("parts") or []
        # Concatena todos los .text por si vinieran fragmentados
        text = "".join([p.get("text", "") for p in parts if isinstance(p, dict)]).strip()
    except Exception:
        text = ""

    if not text:
        raise RuntimeError("Gemini devolvió texto vacío o sin partes .text")

    # ---- limpieza de fences y prefijos ----
    t = text.strip()
    # quita ```json ... ``` o ``` ... ```
    if t.startswith("```"):
        t = t.strip("`").strip()
        # si quedó 'json' al inicio, bórralo
        if t.lower().startswith("json"):
            t = t[4:].strip()

    # a veces ponen la palabra 'json' como primera línea
    if re.match(r"^\s*json\s*[\r\n]", t, re.I):
        t = re.sub(r"^\s*json\s*[\r\n]+", "", t, flags=re.I)

    # intento 1: parse directo
    try:
        return json.loads(t)
    except Exception:
        pass

    # intento 2: recortar al primer/último bloque JSON
    first = min([i for i in [t.find("{"), t.find("[")] if i != -1], default=-1)
    last_brace = t.rfind("}")
    last_brack = t.rfind("]")
    last = max(last_brace, last_brack)
    if first != -1 and last != -1 and last > first:
        snippet = t[first:last+1].strip()
        try:
            return json.loads(snippet)
        except Exception:
            pass

    # si todo falla, lanza error para que el caller decida (o use fallback)
    raise RuntimeError(f"No se pudo parsear JSON de Gemini. Texto recibido (recortado): {t[:400]}")

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

def _mcq_to_drag_kinesthetic(it: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convierte una MCQ en una actividad kinestésica de arrastrar:
      - buckets: ["Correcta", "Incorrecta"]
      - items: las 4 opciones
      - solution: la correcta -> "Correcta", las demás -> "Incorrecta"
    """
    if (it or {}).get("type") != "multiple_choice":
        return it
    ch = [str(c).strip() for c in (it.get("choices") or []) if str(c).strip()]
    if len(ch) < 2:
        ch = ["Opción 1", "Opción 2", "Opción 3", "Opción 4"][:4]
    idx = it.get("correct_index", 0)
    try:
        correct = ch[int(idx)] if 0 <= int(idx) < len(ch) else ch[0]
    except Exception:
        correct = ch[0]
    buckets = ["Correcta", "Incorrecta"]
    sol = {"Correcta": [correct], "Incorrecta": [x for x in ch if x != correct]}
    title = (it.get("question") or "Arrastra la opción correcta a su caja.").strip()
    return {
        "type": "drag_to_bucket",
        "title": title,
        "items": ch,
        "buckets": buckets,
        "solution": sol,
        "explain": (it.get("explain") or "Clasifica: la correcta va en 'Correcta'.").strip()
    }

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

def _sanitize_items(items: List[Dict[str, Any]], style: str | None = None) -> List[Dict[str, Any]]:
    style = (style or "").lower().strip()
    out: List[Dict[str, Any]] = []

    for it in (items or []):
        t = (it or {}).get("type")

        if style == "kinestesico":
            # 1) Mantén match_pairs/drag_to_bucket
            if t == "match_pairs":
                out.append(_fix_pairs(dict(it)))
            elif t == "drag_to_bucket":
                # Acepta tal cual (con limpieza mínima si quisieras)
                out.append({
                    "type":"drag_to_bucket",
                    "title": (it.get("title") or "Clasifica").strip(),
                    "items": list(it.get("items") or []),
                    "buckets": list(it.get("buckets") or []),
                    "solution": {k:list(v) for k,v in (it.get("solution") or {}).items()},
                    "explain": (it.get("explain") or "Arrastra cada tarjeta a su caja.").strip()
                })
            elif t == "multiple_choice":
                # 2) Convierte MCQ → Drag kinestésico
                out.append(_mcq_to_drag_kinesthetic(_fix_mcq(dict(it))))
            else:
                # 3) Fallback kinestésico mínimo: convierte a drag
                out.append(_mcq_to_drag_kinesthetic(_fix_mcq({
                    "type":"multiple_choice",
                    "question":"Elige la opción correcta.",
                    "choices":["Correcta","Incorrecta 1","Incorrecta 2","Incorrecta 3"],
                    "correct_index":0,
                    "explain":"Clasifica: la correcta va en 'Correcta'."
                })))
            continue

        # ---- Visual / Auditivo (comportamiento previo) ----
        if t == "multiple_choice":
            out.append(_fix_mcq(dict(it)))
        elif t == "match_pairs":
            out.append(_fix_pairs(dict(it)))
        elif t == "drag_to_bucket":
            # Antes se convertía a MCQ por "imposibles". Ahora lo mantenemos
            # para permitir kinestesia en visual si el front lo soporta.
            out.append({
                "type":"drag_to_bucket",
                "title": (it.get("title") or "Clasifica").strip(),
                "items": list(it.get("items") or []),
                "buckets": list(it.get("buckets") or []),
                "solution": {k:list(v) for k,v in (it.get("solution") or {}).items()},
                "explain": (it.get("explain") or "Arrastra cada tarjeta a su caja.").strip()
            })
        else:
            out.append(_fix_mcq({
                "question": str(it)[:140] or "Elige la opción correcta.",
                "choices": ["Correcta","Incorrecta 1","Incorrecta 2","Incorrecta 3"],
                "correct_index": 0,
                "explain": "Selecciona la alternativa válida."
            }))

    # Normaliza a 10
    while len(out) < 10:
        if style == "kinestesico":
            out.append(_mcq_to_drag_kinesthetic({
                "type":"multiple_choice",
                "question":"Elige la opción correcta.",
                "choices":["Correcta","Incorrecta 1","Incorrecta 2","Incorrecta 3"],
                "correct_index":0
            }))
        else:
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
    if (style or "").lower() == "kinestesico":
        return _fallback_exercises_kinesthetic(ctx)
    
    base = _fallback_exercises_fractions(ctx, style, avoid_numbers) if _seems_fractions(ctx) else _fallback_exercises_generic(ctx, style)
    return _sanitize_items(base, style)

def _fallback_exercises_kinesthetic(ctx: Dict[str, Any]) -> List[Dict[str, Any]]:
    # 3 actividades base + relleno hasta 10
    items: List[Dict[str, Any]] = []

    # 1) Equivalentes a 1/2
    eq_items = ["1/2","2/4","3/6","3/5","4/8","5/10"]
    eq_sol = {
        "Equivalentes a 1/2": ["1/2","2/4","3/6","4/8","5/10"],
        "No equivalentes": ["3/5"]
    }
    items.append({
        "type":"drag_to_bucket",
        "title":"Equivalentes a 1/2 vs No equivalentes",
        "items": eq_items,
        "buckets": ["Equivalentes a 1/2","No equivalentes"],
        "solution": eq_sol,
        "explain":"Multiplica/simplifica para decidir equivalencia con 1/2."
    })

    # 2) Propias vs Impropias
    pi_items = ["3/4","5/3","7/8","9/7","2/3","4/4"]
    pi_sol = {
        "Propias": ["3/4","7/8","2/3"],
        "Impropias": ["5/3","9/7","4/4"]
    }
    items.append({
        "type":"drag_to_bucket",
        "title":"Clasifica como Propias o Impropias",
        "items": pi_items,
        "buckets": ["Propias","Impropias"],
        "solution": pi_sol,
        "explain":"Propias: numerador < denominador. Impropias: numerador ≥ denominador."
    })

    # 3) Empareja conceptos ↔ ejemplo
    pairs = []
    for c in (ctx.get("concepts") or [])[:3]:
        key = (c.get("id") or "Concepto").capitalize()
        val = (c.get("text") or "").strip()
        if key and val:
            pairs.append([key, val])
    for e in (ctx.get("examples") or [])[:2]:
        k = (e.get("given") or "Ejemplo").strip()
        v = (e.get("explain") or "").strip()
        if k and v:
            pairs.append([k, v])
    items.append({
        "type":"match_pairs",
        "title":"Empareja concepto con ejemplo",
        "pairs": pairs[:6] if pairs else [["Fracción","Parte de un todo"],["Numerador","Partes tomadas"]],
        "explain":"Relaciona cada concepto con su ejemplo."
    })

    return _sanitize_items(items, style="kinestesico")

# ------------------ IA: ejercicios ------------------
def generate_exercises_variant(ctx: dict, style: str, avoid_numbers=None) -> list[dict]:
    ensure_ai_ready()
    print(f"[gemini] generate_exercises_variant → usando IA con modelo={MODEL_NAME}, style={style}")

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
                "Para estilo 'kinestesico', NO devuelvas 'multiple_choice', solo 'match_pairs' y 'drag_to_bucket'.",
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
            raise RuntimeError("Gemini devolvió texto vacío")

        try:
            parsed = json.loads(text)
        except Exception:
            t2 = text.strip().strip("`")
            t2 = re.sub(r"^json", "", t2, flags=re.I).strip()
            parsed = json.loads(t2)

        if not isinstance(parsed, list) or len(parsed) == 0:
            raise RuntimeError("Gemini devolvió un JSON que no es lista no vacía")

        items = _sanitize_items(parsed, style)
        if not isinstance(items, list) or len(items) == 0:
            raise RuntimeError("Sanitización dejó la lista vacía")

        # Normaliza a 10 sin usar fallback local silencioso
        if len(items) > 10:
            items = items[:10]
        elif len(items) < 10:
            # rellena con clones ligeros de los ya generados (pero no con fallback de JSON)
            base = items[:]
            i = 0
            while len(items) < 10 and base:
                items.append(base[i % len(base)])
                i += 1

        print(f"[gemini] IA generó {len(items)} items")
        return items[:10]

    except Exception as e:
        # Muy importante: NO hacer fallback aquí. Dejar que el caller vea el error.
        print(f"[gemini] ERROR IA: {e}")
        raise
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

def generate_assistant_explanation(context_json: dict, style: str) -> dict:

    prompt = f"""
Eres un asistente pedagógico para primaria.
Contexto ESTRICTO del tema (no inventes fuera de esto):
{json.dumps(context_json, ensure_ascii=False)}

Tarea:
- Redacta una EXPLICACIÓN LARGA y CLARA para un estudiante de primaria.
- 4 a 5 párrafos, cada uno ~4 líneas (no más de 450 caracteres por párrafo).
- Incluye al final 2 a 3 ejemplos numéricos explicados PASO A PASO.
- Estilo: {"VISUAL (menciona apoyos visuales simples, comparaciones)" if style=="visual" else "AUDITIVO (frases cortas, ritmo oral, transiciones suaves)"}.
- No pongas viñetas; devuélvelo estructurado en JSON.

FORMATO JSON ESTRICTO:
{{
  "paragraphs": [{{"text": "..."}} , ...],
  "examples": [{{"title":"...", "text":"..."}}, ...]
}}
"""
    data = _call_gemini_json(prompt)
    # saneo mínimo
    paras = [p for p in (data.get("paragraphs") or []) if (p.get("text") or "").strip()]
    exs   = [e for e in (data.get("examples") or []) if (e.get("text") or "").strip()]
    return {"paragraphs": paras[:5], "examples": exs[:3]}

def build_visual_image_prompt(context_json: dict, paragraph_text: str, *, allow_short_title=True) -> str:
    """
    Crea un prompt para imágenes educativas SIN texto explicativo.
    Permite solo un título corto (1–4 palabras) y numerales/símbolos sencillos (p.ej. '1/4').
    """
    topic = (context_json.get("title") or "").strip()
    # extrae posible etiqueta muy corta del párrafo (fallback)
    candidate_title = topic or "Fracciones básicas"

    return (
        "Ilustración educativa simple en 2D, estilo plano y limpio, colores suaves, "
        "pensada para niños de primaria. Muestra el CONCEPTO de forma visual (diagramas, "
        "rebanadas, barras fraccionarias o figuras), con alta legibilidad y sin texto corrido. "
        f"{'Incluye un título corto: ' + candidate_title + '.' if allow_short_title else ''} "
        "REGLAS DE TEXTO: NO coloques oraciones ni explicaciones dentro de la imagen. "
        "Solo se permiten: (a) un título muy corto (1–4 palabras) y (b) números/símbolos como '1/4'. "
        "Evita párrafos, notas, globos o banners con frases largas. Español correcto en cualquier rótulo. "
        "Composición centrada, contraste suficiente, sin marcas de agua."
    )