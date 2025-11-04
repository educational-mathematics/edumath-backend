from __future__ import annotations
from typing import Dict, Any, List, Tuple, Optional
import re, random, copy, json, logging

# IA
from app.ai.gemini import (
    generate_exercises_variant,
    generate_explanation,
    fallback_generate_exercises,
)

# Base
from app.core.engines.base import TopicEngine

log = logging.getLogger(__name__)

# ========= RegEx / utilidades comunes =========
FRACTION_RE      = re.compile(r"\b(\d{1,2})\s*/\s*(\d{1,2})\b")
_PLACEHOLDER_RE  = re.compile(r"(?:^|\s)(distractor|incorrecta)\b", re.IGNORECASE)
_FRAC_ONLY_RE    = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")
_PIVOT_RE        = re.compile(r"(menores|mayores)(?:\s+o\s+iguales)?\s+que\s+(\d+)\s*/\s*(\d+)", re.IGNORECASE)

def _norm(s: str) -> str:
    return " ".join(str(s or "").split()).strip()

# =========================
# Helpers de fracciones
# =========================

def _synth_question_from_choices(opts: list[str]) -> str:
    """Devuelve un enunciado coherente a partir de las opciones."""
    # ¿todas son fracciones?
    parsed = []
    for s in (opts or []):
        m = FRACTION_RE.match(str(s).strip())
        if not m:
            parsed = []
            break
        a, b = int(m.group(1)), int(m.group(2))
        parsed.append((a, b))

    if parsed:
        nums = [a for a, _ in parsed]
        dens = [b for _, b in parsed]
        if len(set(dens)) == 1:
            return "¿Cuál de estas fracciones es la más grande?"
        if len(set(nums)) == 1:
            return "¿Cuál de estas fracciones es la más pequeña?"
        return "¿Cuál de estas fracciones es mayor?"
    return "Elige la opción correcta."

def _ensure_mcq_question(item: dict) -> dict:
    if (item or {}).get("type") != "multiple_choice":
        return item
    q = (item.get("question") or "").strip().lower()
    if not q or q.startswith("elige la opción correcta"):
        choices = [str(c).strip() for c in (item.get("choices") or []) if str(c).strip()]
        item = dict(item)
        item["question"] = _synth_question_from_choices(choices)
    return item

def _argmax_frac_index(opts: list[str]) -> int | None:
    """Devuelve el índice de la fracción mayor en opts; None si alguna opción no es a/b."""
    best = None
    for i, s in enumerate(opts or []):
        m = FRACTION_RE.match(str(s).strip())
        if not m:
            return None
        a, b = int(m.group(1)), int(m.group(2))
        val = a / b
        if (best is None) or (val > best[0]):
            best = (val, i)
    return None if best is None else best[1]

def _argmin_frac_index(opts: list[str]) -> int | None:
    """Devuelve el índice de la fracción menor en opts; None si alguna opción no es a/b."""
    worst = None
    for i, s in enumerate(opts or []):
        m = FRACTION_RE.match(str(s).strip())
        if not m:
            return None
        a, b = int(m.group(1)), int(m.group(2))
        val = a / b
        if (worst is None) or (val < worst[0]):
            worst = (val, i)
    return None if worst is None else worst[1]

def _parse_frac(s: str) -> tuple[int,int] | None:
    m = re.match(r"^\s*(\d+)\s*/\s*(\d+)\s*$", str(s or ""))
    if not m: return None
    a, b = int(m.group(1)), int(m.group(2))
    return (a, b) if b != 0 else None

def _rand_frac_not_equiv(to_avoid: tuple[int,int] | None) -> str:
    for _ in range(50):
        a = random.randint(1, 9)
        b = random.randint(2, 12)
        if to_avoid is None:
            return f"{a}/{b}"
        aa, bb = to_avoid
        if a * bb != aa * b:  # no equivalente al correcto
            return f"{a}/{b}"
    return "1/3"

def _shuffle_choices_set_correct(item: dict, correct_value) -> dict:
    if (item or {}).get("type") != "multiple_choice":
        return item
    choices = [str(c) for c in (item.get("choices") or [])]
    correct_str = str(correct_value)
    if correct_str not in choices:
        choices.append(correct_str)
    seen = set(); dedup = []
    for c in choices:
        if c not in seen:
            seen.add(c); dedup.append(c)
    random.shuffle(dedup)
    item["choices"] = dedup
    item["correct_index"] = dedup.index(correct_str)
    return item

def _sanitize_mcq(item: dict) -> dict:
    if (item or {}).get("type") != "multiple_choice":
        return item

    choices = [str(c).strip() for c in (item.get("choices") or [])]
    ci = int(item.get("correct_index", -1))
    correct_val = choices[ci] if 0 <= ci < len(choices) else (choices[0] if choices else None)

    qtxt = (item.get("question") or "").lower()
    frac_mode = ("fraccion" in qtxt) or ("fracción" in qtxt) or any(_FRAC_ONLY_RE.match(c) for c in choices if c)
    correct_frac = _parse_frac(correct_val) if frac_mode else None

    fixed = []
    for c in choices:
        if not c or _PLACEHOLDER_RE.search(c):
            fixed.append(_rand_frac_not_equiv(correct_frac) if frac_mode else str(random.randint(2, 20)))
        else:
            fixed.append(c)

    while len(fixed) < 4:
        fixed.append(_rand_frac_not_equiv(correct_frac) if frac_mode else str(random.randint(2, 20)))

    seen, dedup = set(), []
    for c in fixed:
        if c not in seen:
            seen.add(c); dedup.append(c)
    fixed = dedup[:4]

    if correct_val not in fixed:
        fixed.append(correct_val); fixed = fixed[:4]

    item = dict(item)
    item["choices"] = fixed
    return _shuffle_choices_set_correct(item, correct_val)

# =========================
# Helpers KINESTÉSICO
# =========================

def _extract_pivot(title: str, buckets: List[str]) -> Optional[str]:
    for txt in [title or "", *(buckets or [])]:
        m = _PIVOT_RE.search(txt or "")
        if m: return f"{int(m.group(2))}/{int(m.group(3))}"
    return None

def _sanitize_drag_item(it: Dict[str, Any], constraints: Dict[str, Any]) -> Dict[str, Any]:
    if (it or {}).get("type") != "drag_to_bucket":
        return it

    title   = it.get("title") or ""
    buckets = [b for b in (it.get("buckets") or []) if _norm(b)]
    solution= it.get("solution") or {}
    items   = [x for x in (it.get("items") or []) if _norm(x)]

    clean_sol: Dict[str, List[str]] = {}
    for b in buckets:
        vals = [x for x in (solution.get(b) or []) if _norm(x)]
        seen, ded = set(), []
        for v in vals:
            vn = _norm(v)
            if vn not in seen:
                seen.add(vn); ded.append(v)
        clean_sol[b] = ded

    if not items:
        seen, rebuilt = set(), []
        for b in buckets:
            for x in clean_sol.get(b, []):
                xn = _norm(x)
                if xn not in seen:
                    seen.add(xn); rebuilt.append(x)
        items = rebuilt

    # partición consistente
    B = buckets[:] if len(buckets) >= 2 else ["Grupo A", "Grupo B"]
    pool = { _norm(x): x for x in items }
    fixed = {b: [] for b in B}
    assigned = set()
    for b in B:
        for x in clean_sol.get(b, []):
            xn = _norm(x)
            if xn in pool and xn not in assigned:
                fixed[b].append(pool[xn]); assigned.add(xn)

    # huérfanos → primer bucket
    for xn, x in pool.items():
        if xn not in assigned:
            fixed[B[0]].append(x)

    out = dict(it)
    out["title"]    = title
    out["buckets"]  = B
    out["items"]    = list(pool.values())
    out["solution"] = fixed
    return out

def _sanitize_match_pairs(it: Dict[str, Any]) -> Dict[str, Any]:
    if (it or {}).get("type") != "match_pairs":
        return it
    pairs = it.get("pairs") or []
    clean: list[list[str]] = []
    for p in pairs:
        if isinstance(p, (list, tuple)) and len(p) == 2:
            L = _norm(p[0]); R = _norm(p[1])
            if L and R:
                clean.append([L, R])
    out = dict(it)
    out["pairs"] = clean if len(clean) >= 2 else [["Fracción", "Parte de un todo"], ["Numerador","Partes tomadas"]]
    return out

def _strip_pivot_from_item(it: Dict[str, Any]) -> Dict[str, Any]:
    if (it or {}).get("type") != "drag_to_bucket":
        return it
    buckets  = it.get("buckets") or []
    solution = it.get("solution") or {}
    items    = list(it.get("items") or [])

    pivot = _extract_pivot(it.get("title") or "", buckets)
    if not pivot:
        return it

    pivot_n = _norm(pivot)
    items = [x for x in items if _norm(x) != pivot_n]
    clean_sol = {b: [x for x in (solution.get(b) or []) if _norm(x) != pivot_n] for b in buckets}

    if not it.get("items"):
        seen = set(); rebuilt = []
        for b in buckets:
            for x in clean_sol.get(b, []):
                xn = _norm(x)
                if xn not in seen:
                    seen.add(xn); rebuilt.append(x)
        items = rebuilt

    out = dict(it)
    out["items"] = items
    out["solution"] = clean_sol
    return out

def _make_drag_to_bucket(title: str, items: list[str], buckets: list[str], solution: dict[str, list[str]], explain: str):
    return {
        "type": "drag_to_bucket",
        "title": title,
        "items": list(items),
        "buckets": list(buckets),
        "solution": {k: list(v) for k, v in solution.items()},
        "explain": explain,
    }

def _make_match_pairs(title: str, pairs: list[list[str]], explain: str):
    pairs2 = []
    seen = set()
    for p in pairs[:10]:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            continue
        L = str(p[0]).strip(); R = str(p[1]).strip()
        if not L or not R:
            continue
        key = (L, R)
        if key not in seen:
            pairs2.append([L, R]); seen.add(key)
        if len(pairs2) >= 6:
            break
    if len(pairs2) < 2:
        pairs2 = [["Fracción", "Parte de un todo"], ["Numerador", "Partes tomadas"]]
    return {
        "type": "match_pairs",
        "title": title or "Empareja",
        "pairs": pairs2,
        "explain": explain or "Relaciona cada concepto con su ejemplo correcto."
    }

def _random_propia_impropia_set(n=6):
    items = set()
    while len(items) < n:
        b = random.randint(3, 12)
        if random.random() < 0.5:  # propia
            a = random.randint(1, b-1)
        else:                      # impropia
            a = random.randint(b, b+4)
        items.add(f"{a}/{b}")
    items = list(items)
    sol = {
        "Propias": [x for x in items if int(x.split('/')[0]) <  int(x.split('/')[1])],
        "Impropias": [x for x in items if int(x.split('/')[0]) >= int(x.split('/')[1])],
    }
    return items, ["Propias", "Impropias"], sol

def _random_equiv_half_set(n=6):
    items = set()
    while len(items) < n:
        if random.random() < 0.6:
            m = random.randint(2, 6)
            items.add(f"{m}/{2*m}")  # equivalente a 1/2
        else:
            a = random.randint(1, 9); b = random.randint(2, 12)
            if a*2 != b:
                items.add(f"{a}/{b}")
    items = list(items)
    sol = {
        "Equivalentes a 1/2": [x for x in items if int(x.split('/')[0])*2 == int(x.split('/')[1])],
        "No equivalentes":    [x for x in items if int(x.split('/')[0])*2 != int(x.split('/')[1])],
    }
    return items, ["Equivalentes a 1/2", "No equivalentes"], sol

def build_kinesthetic_set_from_ctx(ctx: dict, seed: int | None = None) -> list[dict]:
    rnd = random.Random(seed or "0xEDU3")
    out: list[dict] = []

    setups = (ctx.get("kinesthetic_setups") or [])[:]
    rnd.shuffle(setups)
    for s in setups:
        text = (s or "").lower()
        if "equivalentes a 1/2" in text:
            items, buckets, sol = _random_equiv_half_set(n=6 + rnd.randint(0,2))
            out.append(_make_drag_to_bucket(
                "Equivalentes a 1/2 vs No equivalentes",
                items, buckets, sol,
                "Multiplica o simplifica para decidir equivalencia con 1/2."
            ))
        elif "propias" in text and "impropias" in text:
            items, buckets, sol = _random_propia_impropia_set(n=6 + rnd.randint(0,2))
            out.append(_make_drag_to_bucket(
                "Clasifica como Propias o Impropias",
                items, buckets, sol,
                "Propias: numerador < denominador. Impropias: numerador ≥ denominador."
            ))
        elif "emparejar" in text or "empareja" in text:
            pairs = []
            for c in (ctx.get("concepts") or [])[:3]:
                key = (c.get("id") or "Concepto").capitalize()
                val = (c.get("text") or "").strip()
                if key and val:
                    pairs.append([key, val])
            for e in (ctx.get("examples") or [])[:2]:
                k = (e.get("given") or "").strip() or "Ejemplo"
                v = (e.get("explain") or "").strip()
                if k and v:
                    pairs.append([k, v])
            out.append(_make_match_pairs("Empareja concepto con ejemplo", pairs, "Relaciona el concepto con su ejemplo."))

    # 2 extras si faltan
    if sum(1 for x in out if x["type"] == "drag_to_bucket") < 2:
        items, buckets, sol = _random_propia_impropia_set(n=6 + rnd.randint(0,2))
        out.append(_make_drag_to_bucket(
            "Propias vs Impropias (extra)",
            items, buckets, sol,
            "Propias: numerador < denominador. Impropias: numerador ≥ denominador."
        ))
        items2, buckets2, sol2 = _random_equiv_half_set(n=6 + rnd.randint(0,2))
        out.append(_make_drag_to_bucket(
            "¿Equivalente a 1/2? (extra)",
            items2, buckets2, sol2,
            "Multiplica o simplifica para decidir equivalencia con 1/2."
        ))

    rnd.shuffle(out)
    return out[:10]

# =========================
# Engine
# =========================

class FraccionesBasicasEngine(TopicEngine):

    # -------- Fallback mínimo de MCQ --------
    def _fallback_mcq(self) -> Dict[str, Any]:
        return {
            "type": "multiple_choice",
            "question": "Elige la opción correcta.",
            "choices": ["Correcta", "Incorrecta 1", "Incorrecta 2", "Incorrecta 3"],
            "correct_index": 0,
            "explain": "Lee con atención el enunciado."
        }
        
    def _minimal_mcq_guard(self, items: list[dict]) -> list[dict]:
        import random, re
        FRACTION_RE = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")
        PLACEHOLDERS = {
            "correcta","incorrecta 1","incorrecta 2","incorrecta 3",
            "opción 1","opción 2","opción 3","opción 4",
            "alternativa 1","alternativa 2","alternativa 3","alternativa 4",
            "correct"
        }

        def rand_frac():
            a = random.randint(1, 9); b = random.randint(2, 12)
            if a >= b: b = a + 1
            return f"{a}/{b}"

        out = []
        for it in (items or []):
            t = (it or {}).get("type")
            if t != "multiple_choice":
                # no tocamos kinestésico ni pairs
                out.append(dict(it)); 
                continue

            q = (it.get("question") or "").strip()
            ch = [str(c).strip() for c in (it.get("choices") or []) if str(c).strip()]
            ci = it.get("correct_index", None)

            # quita placeholders triviales
            ch = [c for c in ch if c.lower() not in PLACEHOLDERS]

            # si la IA no puso nada, crea un set mínimo pero sin tocar el enunciado
            if not ch:
                ch = [rand_frac(), rand_frac(), rand_frac(), rand_frac()]

            # dedup preservando orden
            seen, dedup = set(), []
            for c in ch:
                if c not in seen:
                    seen.add(c); dedup.append(c)
            ch = dedup[:]

            # completa a 4
            while len(ch) < 4:
                cand = rand_frac() if any(FRACTION_RE.match(x) for x in ch) else str(random.randint(2, 20))
                if cand not in ch:
                    ch.append(cand)

            # corrige índice: si viene fuera de rango, toma 0
            if not isinstance(ci, int) or not (0 <= ci < len(ch)):
                # si el json traía 'correct', úsalo
                cfield = str(it.get("correct") or "")
                ci = ch.index(cfield) if cfield in ch else 0

            # baraja pero mantén la correcta por valor
            correct_value = ch[ci]
            random.shuffle(ch)
            if correct_value not in ch:
                ch[0] = correct_value
            ci2 = ch.index(correct_value)

            out.append({
                "type": "multiple_choice",
                "question": q,                    # ← NO tocamos el enunciado de la IA
                "choices": ch,
                "correct_index": ci2,
                "explain": (it.get("explain") or "").strip(),
                "styles": ["visual","auditivo"],
            })
        return out

    # -------- Finalizador robusto de MCQ --------
    def _finalize_mcqs(self, items: list[dict]) -> list[dict]:
        import random, re
    
        DENOM_Q_RE = re.compile(r"(denominador|n(ú|u)mero\s+de\s+abajo)", re.IGNORECASE)
        NUMER_Q_RE = re.compile(r"(numerador|n(ú|u)mero\s+de\s+arriba)", re.IGNORECASE)
        EQUIV_HALF_RE = re.compile(r"equivalente\s+a\s+1\s*/\s*2", re.IGNORECASE)
        FRACTION_WORD_RE = re.compile(r"fracci(ó|o)n(?:es)?", re.IGNORECASE)
        MAYOR_FRACTION_RE = re.compile(r"(cu(a|á)l.*fracci(ó|o)n.*(mayor|m(a|á)s grande))", re.IGNORECASE)
    
        out = []
        for it in (items or []):
            if (it or {}).get("type") != "multiple_choice":
                continue
            
            q_raw = (it.get("question") or "").strip()
            choices = [str(c).strip() for c in (it.get("choices") or []) if str(c).strip()]
            ci = it.get("correct_index", None)
    
            # 1) localizar texto correcto
            correct_text = None
            if isinstance(ci, int) and 0 <= ci < len(choices):
                correct_text = choices[ci]
            else:
                cfield = str(it.get("correct") or "").strip()
                if cfield and cfield in choices:
                    correct_text = cfield
    
            if not choices:
                choices = ["1/2", "1/3", "2/3", "3/4"]
            if not correct_text:
                correct_text = choices[0]
    
            # 2) quitar placeholders
            _ph = {
                "correcta","incorrecta 1","incorrecta 2","incorrecta 3",
                "opción 1","opción 2","opción 3","opción 4",
                "alternativa 1","alternativa 2","alternativa 3","alternativa 4",
                "correct","correcta 1"
            }
            cleaned = [c for c in choices if c.lower() not in _ph]
            choices = cleaned or choices
    
            # 3) reglas específicas de contenido (si el enunciado trae pistas)
            m_frac_in_q = FRACTION_RE.search(q_raw)
            if m_frac_in_q and (DENOM_Q_RE.search(q_raw) or NUMER_Q_RE.search(q_raw)):
                a, b = int(m_frac_in_q.group(1)), int(m_frac_in_q.group(2))
                if DENOM_Q_RE.search(q_raw):
                    correct_text = str(b)
                    distract = {str(a), str(max(1, b-1)), str(b+1), str(a+b)}
                else:
                    correct_text = str(a)
                    distract = {str(b), str(max(1, a-1)), str(a+1), str(a+b)}
                distract.discard(correct_text)
                choices = [correct_text] + list(distract)
                # asegurar 4 únicas
                seen, uniq = set(), []
                for c in choices:
                    if c not in seen:
                        seen.add(c); uniq.append(c)
                while len(uniq) < 4:
                    nxt = str(random.randint(2, 20))
                    if nxt not in seen:
                        seen.add(nxt); uniq.append(nxt)
                choices = uniq[:4]
    
            elif EQUIV_HALF_RE.search(q_raw):
                def eq_half(s):
                    m = FRACTION_RE.search(s or "")
                    return bool(m and int(m.group(1))*2 == int(m.group(2)))
                if not any(eq_half(c) for c in choices):
                    m = random.randint(2, 6)
                    correct_text = f"{m}/{2*m}"
                    choices = (choices[:3] + [correct_text])[:4]
    
            elif (FRACTION_WORD_RE.search(q_raw) or MAYOR_FRACTION_RE.search(q_raw)) and not any(FRACTION_RE.search(c) for c in choices):
                d = random.randint(4, 12)
                nums = random.sample(range(1, d), 4)
                choices = [f"{n}/{d}" for n in nums]
                correct_text = f"{max(nums)}/{d}"
    
            # 4) Garantía: presencia de correcta + 4 únicas
            if correct_text not in choices:
                choices.append(correct_text)
            seen, uniq = set(), []
            for c in choices:
                if c not in seen:
                    seen.add(c); uniq.append(c)
            while len(uniq) < 4:
                nxt = f"{len(uniq)+1}/{len(uniq)+2}"
                if nxt not in seen:
                    seen.add(nxt); uniq.append(nxt)
            choices = uniq[:4]
    
            # 5) Barajar y fijar índice correcto
            random.shuffle(choices)
            if correct_text not in choices:
                choices[0] = correct_text
            ci2 = choices.index(correct_text)
    
            # 6) **SÍNTESIS DE ENUNCIADO** cuando viene vacío o genérico
            def _synthesize_question_from_choices(opts: list[str]) -> str:
                # si todas son fracciones, intenta una pregunta del dominio
                parsed = []
                for s in opts:
                    m = FRACTION_RE.match(s)
                    if not m:
                        parsed = []
                        break
                    a, b = int(m.group(1)), int(m.group(2))
                    parsed.append((a, b))
                if parsed:
                    nums = [a for a, _ in parsed]
                    dens = [b for _, b in parsed]
                    if len(set(dens)) == 1:              # mismo denominador → mayor numerador
                        return "¿Cuál de estas fracciones es la más grande?"
                    if len(set(nums)) == 1:              # mismo numerador → menor denominador
                        return "¿Cuál de estas fracciones es la más pequeña?"
                    return "¿Cuál de estas fracciones es mayor?"
                # fallback neutro si no son fracciones
                return "Elige la opción correcta."
    
            q = (it.get("question") or "").strip()
            if not q or q.lower().startswith("elige la opción correcta"):
                q = _synth_question_from_choices(choices)
    
            explain = (it.get("explain") or "").strip() or "Justifica tu respuesta con la regla indicada en el enunciado."
    
            out.append({
                "type": "multiple_choice",
                "question": q,
                "choices": choices,
                "correct_index": ci2,
                "explain": explain,
                "styles": ["visual","auditivo"],
            })
        return out

    # --------- Builder de sesión (SIEMPRE IA) ----------
    def build_session(
        self,
        context_json: Dict[str, Any],
        style: str,
        avoid_numbers: Optional[List[Tuple[int, int]]] = None,
        seed: Optional[int] = None,
        reuse_mode: Optional[str] = None,   # ignorado
    ) -> Dict[str, Any]:    

        explanation = generate_explanation(context_json)    

        # 1) Genera con IA (fallback si falla)
        import threading, time

        raw_items = None
        _exc = [None]

        def _call_ia():
            try:
                r = generate_exercises_variant(context_json, style, avoid_numbers or [])
                # saneo básico de contrato
                if not isinstance(r, list):
                    raise RuntimeError("IA devolvió un tipo no-lista")
                raw_items_list = [x for x in r if isinstance(x, dict)]
                if not raw_items_list:
                    raise RuntimeError("IA devolvió lista vacía")
                # coloca en cierre exterior
                nonlocal raw_items
                raw_items = raw_items_list
            except Exception as e:
                _exc[0] = e

        th = threading.Thread(target=_call_ia, daemon=True)
        th.start()
        th.join(timeout=90)          # 90a

        if raw_items is None:        # timeout o error
            if _exc[0]:
                log.warning("IA falló: %s — uso fallback local", _exc[0])
            else:
                log.warning("IA timeout (>90s) — uso fallback local")
            from app.ai.gemini import fallback_generate_exercises
            raw_items = fallback_generate_exercises(context_json, style, avoid_numbers or [])

        items: List[Dict[str, Any]] = []    

        # 2) Estilo kinestésico: mantener drag/match, limpiar, completar (SIN CAMBIOS)
        if (style or "").lower().strip() == "kinestesico":
            for it in (raw_items or []):
                t = (it or {}).get("type")
                if t == "drag_to_bucket":
                    it2 = _strip_pivot_from_item(dict(it))
                    it3 = _sanitize_drag_item(it2, context_json.get("constraints", {}))
                    items.append(it3)
                elif t == "match_pairs":
                    items.append(_sanitize_match_pairs(dict(it)))
                # ignorar MCQ   

            if len(items) < 10:
                extra = build_kinesthetic_set_from_ctx(context_json, seed)
                seen = {json.dumps(x, sort_keys=True, ensure_ascii=False) for x in items}
                for ex in extra:
                    k = json.dumps(ex, sort_keys=True, ensure_ascii=False)
                    if k not in seen:
                        items.append(ex); seen.add(k)
                    if len(items) >= 10: break  

            items = items[:10]  

        # 3) Visual/Auditivo: usar IA TAL CUAL + guardia mínima (no “embellecer”)
        else:
            mcq = [x for x in (raw_items or []) if (x or {}).get("type") == "multiple_choice"]
            items = self._minimal_mcq_guard(mcq)    

            # completa a 10 con MCQ de fallback, pasadas por la misma guardia
            while len(items) < 10:
                items += self._minimal_mcq_guard([self._fallback_mcq()])
            items = items[:10]  

        return {
            "items": items,
            "explanation": explanation,
            "meta": {"topic_kind": "fracciones_basicas"}
        }

    # --------- Sanitizado final en reuse / reparación ---------
    def validate_repair(self, items: list[dict], ctx: dict) -> list[dict]:
        out = []
        for it in (items or []):
            t = (it or {}).get("type")
            if t == "multiple_choice":
                fixed = _sanitize_mcq(dict(it))
                # “equivalente a 1/2”: asegura opción válida
                qlow = (fixed.get("question") or "").lower()
                if "equivalente a 1/2" in qlow or "equivalente a 1 / 2" in qlow:
                    def is_equiv_half(txt: str) -> bool:
                        m = FRACTION_RE.search(txt or "")
                        return bool(m and int(m.group(1)) * 2 == int(m.group(2)))
                    eqs = [c for c in fixed["choices"] if is_equiv_half(c)]
                    correct = eqs[0] if eqs else "2/4"
                    fixed = _shuffle_choices_set_correct(fixed, correct)
                    if not fixed.get("explain"):
                        fixed["explain"] = "Multiplica numerador y denominador por el mismo número."
                out.append(fixed)
            elif t == "drag_to_bucket":
                it2 = _strip_pivot_from_item(dict(it))
                out.append(_sanitize_drag_item(it2, ctx.get("constraints", {})))
            elif t == "match_pairs":
                out.append(_sanitize_match_pairs(dict(it)))
            else:
                out.append(it)
        return out
