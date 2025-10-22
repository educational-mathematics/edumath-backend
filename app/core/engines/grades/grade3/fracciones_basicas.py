from __future__ import annotations
from typing import Dict, Any, List, Tuple, Optional
import re, random, copy, json
from app.ai.gemini import generate_exercises_variant, generate_explanation
from app.core.engines.base import TopicEngine
from collections import Counter

# ========= RegEx que ya usabas en topics.py =========
FRACTION_RE      = re.compile(r"\b(\d{1,2})\s*/\s*(\d{1,2})\b")
_PLACEHOLDER_RE  = re.compile(r"(?:^|\s)(distractor|incorrecta)\b", re.IGNORECASE)
_FRAC_ONLY_RE    = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")
_PIVOT_RE = re.compile(r"(menores|mayores)(?:\s+o\s+iguales)?\s+que\s+(\d+)\s*/\s*(\d+)", re.IGNORECASE)

def _norm(s: str) -> str:
    return " ".join(str(s or "").split()).strip()

def _dedup_json(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen, out = set(), []
    for it in items or []:
        key = json.dumps(it, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            seen.add(key); out.append(it)
    return out

def _pair_bag(pairs) -> Counter:
    normed = []
    for p in (pairs or []):
        if isinstance(p, (list, tuple)) and len(p) == 2:
            a, b = _norm(p[0]), _norm(p[1])
            if a and b:
                normed.append((a, b))
    return Counter(normed)

def _extract_pivot(title: str, buckets: List[str]) -> Optional[str]:
    for txt in [title or "", *(buckets or [])]:
        m = _PIVOT_RE.search(txt or ""); 
        if m: return f"{int(m.group(2))}/{int(m.group(3))}"
    return None

def _looks_propias_impropias(title: str, buckets: List[str]) -> bool:
    txt = " ".join([title or ""] + list(buckets or [])).lower()
    return ("propia" in txt) or ("impropia" in txt)

def _synthesize_propias_impropias(constraints: Dict[str, Any]) -> tuple[List[str], Dict[str, List[str]], List[str]]:
    mn = int((constraints or {}).get("allowed_numbers", {}).get("min", 1))
    mx = int((constraints or {}).get("allowed_numbers", {}).get("max", 12))
    rng = random.Random()
    items: List[str] = []
    while len(items) < 6:
        a = rng.randint(mn, mx)
        b = rng.randint(max(a, mn+1), mx+3)
        s = f"{a}/{b}"
        if s not in items:
            items.append(s)
    buckets = ["Propias", "Impropias"]
    solution = {"Propias": [], "Impropias": []}
    for s in items:
        a,b = map(int, s.split("/"))
        (solution["Propias"] if a < b else solution["Impropias"]).append(s)
    return items, solution, buckets

def _sanitize_drag_item(it: Dict[str, Any], constraints: Dict[str, Any]) -> Dict[str, Any]:
    if (it or {}).get("type") != "drag_to_bucket":
        return it

    title   = it.get("title") or ""
    buckets = [b for b in (it.get("buckets") or []) if _norm(b)]
    solution= it.get("solution") or {}
    items   = [x for x in (it.get("items") or []) if _norm(x)]

    # 1) limpia solution y dedup por bucket
    clean_sol: Dict[str, List[str]] = {}
    for b in buckets:
        vals = [x for x in (solution.get(b) or []) if _norm(x)]
        seen, ded = set(), []
        for v in vals:
            vn = _norm(v)
            if vn not in seen:
                seen.add(vn); ded.append(v)
        clean_sol[b] = ded

    # 2) reconstruye items si faltan, uniendo desde la solución
    if not items:
        seen, rebuilt = set(), []
        for b in buckets:
            for x in clean_sol.get(b, []):
                xn = _norm(x)
                if xn and xn not in seen:
                    seen.add(xn); rebuilt.append(x)
        items = rebuilt

    # 3) Normalización de bucket con pivote:
    #    si hay un bucket "Mayores que a/b" y la tarjeta "a/b" está en items,
    #    renombra a "Mayores o iguales a a/b" y asegúrate que la solución lo incluya ahí.
    try:
        items_norm = { _norm(x): x for x in items }
        for i, b in enumerate(list(buckets)):
            m = _PIVOT_RE.search(b or "")
            if not m:
                continue
            kind, n_str, d_str = m.group(1).lower(), m.group(2), m.group(3)
            pivot_raw  = f"{int(n_str)}/{int(d_str)}"
            pivot_norm = _norm(pivot_raw)

            # Sólo aplicamos la regla a "mayores" (no a "menores")
            if kind != "mayores":
                continue

            pivot_in_items = pivot_norm in items_norm
            if not pivot_in_items:
                # si el pivot no está entre las tarjetas, no forzamos "o iguales"
                continue

            new_name = f"Mayores o iguales a {pivot_raw}"
            if b != new_name:
                # renombra el bucket y mueve la solución bajo el nombre nuevo
                buckets[i] = new_name
                clean_sol[new_name] = clean_sol.pop(b, [])

            # garantiza que el pivote esté en ese bucket y en ninguno otro
            if pivot_norm not in {_norm(x) for x in (clean_sol.get(new_name) or [])}:
                clean_sol[new_name] = (clean_sol.get(new_name) or []) + [items_norm[pivot_norm]]

            for j, other in enumerate(buckets):
                if j == i:
                    continue
                if pivot_norm in {_norm(x) for x in (clean_sol.get(other) or [])}:
                    clean_sol[other] = [x for x in clean_sol.get(other, []) if _norm(x) != pivot_norm]
    except Exception:
        # saneo "best-effort": nunca debe romper la limpieza
        pass

    # 4) si aún no hay buckets válidos, crea dos por defecto y reparte
    if len(buckets) < 2:
        buckets = ["Grupo A", "Grupo B"]
        mid = len(items) // 2
        clean_sol = {"Grupo A": items[:mid], "Grupo B": items[mid:]}

    # 5) valida que solution sea una partición de items (sin repetir)
    item_set = set(_norm(x) for x in items)
    fixed_sol: Dict[str, List[str]] = {b: [] for b in buckets}
    assigned = set()
    for b in buckets:
        for x in clean_sol.get(b, []):
            xn = _norm(x)
            if xn in item_set and xn not in assigned:
                fixed_sol[b].append(x)
                assigned.add(xn)

    # huérfanos (items no asignados) → primer bucket
    orphans = [x for x in items if _norm(x) not in assigned]
    if buckets and orphans:
        fixed_sol[buckets[0]].extend(orphans)

    out = dict(it)
    out["buckets"]  = buckets
    out["items"]    = items
    out["solution"] = fixed_sol
    # el título original se mantiene; no forzamos cambios en 'title'
    return out

def _sanitize_match_pairs(it: Dict[str, Any]) -> Dict[str, Any]:
    if (it or {}).get("type") != "match_pairs":
        return it
    pairs = it.get("pairs") or []
    clean: list[list[str]] = []
    for L, R in pairs:
        Ln = _norm(L)
        Rn = _norm(R)
        if Ln and Rn:
            clean.append([Ln, Rn])   # <- sin sufijos "(2)"
    out = dict(it)
    out["pairs"] = clean
    return out

def _strip_pivot_from_item(it: Dict[str, Any]) -> Dict[str, Any]:
    if (it or {}).get("type") != "drag_to_bucket":
        return it
    title = it.get("title") or ""
    buckets = it.get("buckets") or []
    solution = it.get("solution") or {}
    items = list(it.get("items") or [])

    pivot = _extract_pivot(title, buckets)
    if not pivot:
        return it  # nada que hacer

    pivot_n = _norm(pivot)

    # quita pivot de items
    items = [x for x in items if _norm(x) != pivot_n]

    # quita pivot de solution
    clean_sol = {}
    for b in buckets:
        vals = [x for x in (solution.get(b) or []) if _norm(x) != pivot_n]
        clean_sol[b] = vals

    # opcional: asegura que items sea la unión de solution (sin pivot), si items venía vacío
    if not it.get("items"):
        seen = set()
        rebuilt = []
        for b in buckets:
            for x in clean_sol.get(b, []):
                xn = _norm(x)
                if xn not in seen:
                    seen.add(xn)
                    rebuilt.append(x)
        items = rebuilt

    out = dict(it)
    out["items"] = items
    out["solution"] = clean_sol
    return out

# --- utilidades locales simples ---
def _only_kinesthetic(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for it in items or []:
        if (it or {}).get("type") in ("drag_to_bucket", "match_pairs"):
            out.append(dict(it))
    return out

def _dedup_list_str(seq: List[str]) -> List[str]:
    seen, out = set(), []
    for s in (seq or []):
        s2 = str(s).strip()
        if s2 and s2 not in seen:
            seen.add(s2); out.append(s2)
    return out

def _safe_drag(buckets: List[str], items: List[str], solution: Dict[str, List[str]]) -> Dict[str, Any]:
    B = _dedup_list_str(buckets or [])
    if len(B) < 2:
        B = ["Grupo A", "Grupo B"]

    pool = _dedup_list_str(items or [])
    if not pool:
        pool = _dedup_list_str(sum((solution or {}).values(), []))

    # Si aún está vacío, crea dos tarjetas dummy para no romper la UI
    if not pool:
        pool = ["A", "B"]

    fixed = {b: [] for b in B}
    assigned = set()
    for b in B:
        for x in _dedup_list_str((solution or {}).get(b) or []):
            if x in pool and x not in assigned:
                fixed[b].append(x)
                assigned.add(x)

    rest = [x for x in pool if x not in assigned]
    if rest:
        fixed[B[0]].extend(rest)

    return {
        "type": "drag_to_bucket",
        "title": "",
        "buckets": B,
        "items": pool,
        "solution": fixed,
        "explain": ""
    }

def _safe_pairs(pairs: List[List[str]], title: str = "Empareja") -> Dict[str, Any]:
    clean = []
    for p in pairs or []:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            continue
        a, b = _norm(p[0]), _norm(p[1])
        if a and b:
            clean.append([a, b])
    return {"type": "match_pairs", "title": title, "pairs": clean[:], "explain": ""}

class FraccionesBasicasEngine(TopicEngine):
    def build_session(
        self,
        context_json: Dict[str, Any],
        style: str,
        avoid_numbers: Optional[List[Tuple[int, int]]] = None,
        seed: Optional[int] = None
    ) -> Dict[str, Any]:
        self.context_json = context_json
        explanation = generate_explanation(context_json)

        if style == "kinestesico":
            items = self._make_kinesthetic_set(context_json, seed)
        else:
            items = generate_exercises_variant(context_json, style, avoid_numbers or [])
            items = self.sanitize_items(items)

        # garantiza 10
        items = items[:10]
        while len(items) < 10:
            items.append(_safe_pairs([["Concepto","Definición"],["Ejemplo","Explicación"]], "Relaciona"))

        return {
            "items": items,
            "explanation": explanation,
            "meta": {"topic_kind": "fracciones_basicas"}
        }

    def sanitize_items(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        constraints = getattr(self, "context_json", {}).get("constraints", {})
        fixed: List[Dict[str, Any]] = []
        for it in items or []:
            t = (it or {}).get("type")
            if t == "drag_to_bucket":
                fixed.append(_sanitize_drag_item(it, constraints))
            elif t == "match_pairs":
                fixed.append(_sanitize_match_pairs(it))
            else:
                # fallback no kinestésico → emparejar simple
                fixed.append(_safe_pairs([["Numerador","Partes tomadas"],["Denominador","Partes totales"]]))
        return fixed

    def validate_repair(self, items: List[Dict[str, Any]], context_json: Dict[str, Any]) -> List[Dict[str, Any]]:
        self.context_json = context_json
        fixed: List[Dict[str, Any]] = []
        for it in items or []:
            t = (it or {}).get("type")
            if t == "drag_to_bucket":
                fixed.append(_sanitize_drag_item(it, context_json.get("constraints", {})))
            elif t == "match_pairs":
                fixed.append(_sanitize_match_pairs(it))
            else:
                fixed.append(it)

        # si quedó corta y la sesión era kinestésica, rellena con set kinestésico
        kin_count = sum(1 for x in fixed if (x or {}).get("type") in ("drag_to_bucket","match_pairs"))
        if kin_count < 10:
            fixed = self._make_kinesthetic_set(context_json, seed=None)

        # a 10 exactos
        fixed = fixed[:10]
        while len(fixed) < 10:
            fixed.append(_safe_pairs([["Concepto","Definición"],["Ejemplo","Explicación"]]))
        return fixed

    def check_answer(self, item: Dict[str, Any], answer: Any) -> bool:
        t = item.get("type") or item.get("kind") or "multiple_choice"
        if t == "multiple_choice":
            try:
                return int(answer) == int(item.get("correct_index", -1))
            except Exception:
                return False
        if t == "match_pairs":
            try:
                sol_bag   = _pair_bag(item.get("pairs"))
                given_bag = _pair_bag(answer)
                return sol_bag == given_bag
            except Exception:
                return False
        if t == "drag_to_bucket":
            sol = item.get("solution", {}) or {}
            if not isinstance(answer, dict): return False
            if set(sol.keys()) != set(answer.keys()): return False
            for b in sol.keys():
                left  = set(_norm(x) for x in (answer.get(b) or []))
                right = set(_norm(x) for x in (sol.get(b) or []))
                if left != right: return False
            return True
        return False

    # ── Generación kinestésica robusta ────────────────────────────────────────
    def _make_kinesthetic_set(self, ctx: Dict[str, Any], seed: Optional[int]) -> List[Dict[str, Any]]:
        rng = random.Random(seed or random.randint(1, 10**9))

        # 1) toma banco kinestésico si existe
        bank = _only_kinesthetic(ctx.get("exercise_bank") or [])
        out: List[Dict[str, Any]] = []
        for it in bank:
            if it.get("type") == "drag_to_bucket":
                items = _dedup_list_str(it.get("items") or [])
                rng.shuffle(items)
                buckets = _dedup_list_str(it.get("buckets") or [])
                base = _safe_drag(buckets, items, it.get("solution") or {})
                base["title"] = it.get("title") or "Clasifica"
                base["explain"] = it.get("explain") or ""
                out.append(base)
            elif it.get("type") == "match_pairs":
                pairs = list(it.get("pairs") or [])
                rng.shuffle(pairs)
                out.append(_safe_pairs(pairs[:6], it.get("title") or "Empareja"))

        # 2) añade variaciones generadas (solo kinestésicas, sin IA)
        #    Nota: este engine es de fracciones; generamos variaciones simples y rápidas.
        while len(out) < 12:
            out.extend([
                self._gen_propias_impropias(rng),
                self._gen_equiv_half(rng),
                self._gen_mayor_menor_pivot(rng),
                self._gen_pairs_concept()
            ])

        # 3) sanitize + dedup + recorte a 10
        cleaned: List[Dict[str, Any]] = []
        for it in out:
            if it.get("type") == "drag_to_bucket":
                cleaned.append(_sanitize_drag_item(it, {}))
            elif it.get("type") == "match_pairs":
                cleaned.append(_sanitize_match_pairs(it))

        # dedup a nivel JSON para evitar repetidos exactos
        seen, unique = set(), []
        for it in cleaned:
            key = json.dumps(it, sort_keys=True, ensure_ascii=False)
            if key not in seen:
                seen.add(key); unique.append(it)

        rng.shuffle(unique)
        unique = [x for x in unique if x.get("type") in ("drag_to_bucket","match_pairs")]
        return unique[:10]

    # ── Generadores (rápidos) de fracciones ───────────────────────────────────
    def _rand_propia(self, rng, lo=1, hi=12) -> str:
        d = rng.randint(max(2, lo+1), max(3, hi))
        n = rng.randint(lo, d-1)
        return f"{n}/{d}"

    def _rand_impropia(self, rng, lo=1, hi=12) -> str:
        n = rng.randint(max(2, lo+1), max(3, hi))
        d = rng.randint(lo, n)
        return f"{n}/{d}"

    def _gen_equiv_half(self, rng) -> Dict[str, Any]:
        # equivalentes / no equivalentes a 1/2
        equiv = []
        for k in rng.sample([2,3,4,5,6], k=3):
            equiv.append(f"{k}/{2*k}")
        # un no-equivalente simple
        noeq = [f"1/{rng.choice([3,4,5,6])}"]
        items = _dedup_list_str(equiv + noeq)
        buckets = ["Equivalentes a 1/2", "No equivalentes"]
        sol = { buckets[0]: equiv, buckets[1]: [x for x in items if x not in equiv] }
        d = _safe_drag(buckets, items, sol)
        d["title"] = "Equivalentes a 1/2"; d["explain"] = "Multiplica o simplifica para decidir equivalencia."
        return d

    def _gen_propias_impropias(self, rng) -> Dict[str, Any]:
        props = [_norm(self._rand_propia(rng)) for _ in range(3)]
        impro = [_norm(self._rand_impropia(rng)) for _ in range(3)]
        items = _dedup_list_str(props + impro)
        buckets = ["Propias", "Impropias"]
        sol = {"Propias": [x for x in items if x in props], "Impropias": [x for x in items if x in impro]}
        d = _safe_drag(buckets, items, sol)
        d["title"] = "Clasifica: Propias o Impropias"
        d["explain"] = "Propias: numerador < denominador. Impropias: numerador ≥ denominador."
        return d

    def _gen_mayor_menor_pivot(self, rng) -> Dict[str, Any]:
        # Elegimos un denominador que deje margen y un numerador "medio"
        d = rng.randint(5, 10)
        n = rng.randint(2, d - 2)

        pivot = f"{n}/{d}"

        # Construimos menores y mayores SIN incluir el pivote
        menores_pool = list(range(1, n))
        mayores_pool = list(range(n + 1, d))

        # Garantiza al menos 1 en cada lado
        k_men = min(2, len(menores_pool)) or 1
        k_may = min(2, len(mayores_pool)) or 1

        menores = [f"{i}/{d}" for i in rng.sample(menores_pool, k=k_men)]
        mayores = [f"{i}/{d}" for i in rng.sample(mayores_pool, k=k_may)]

        # El pool que verá el alumno NO incluye el pivote
        items = _dedup_list_str(menores + mayores)

        b_may = f"Mayores o iguales a {pivot}"
        b_men = f"Menores que {pivot}"

        sol = {
            b_may: mayores[:],
            b_men: menores[:],
        }

        dct = _safe_drag([b_may, b_men], items, sol)
        dct["title"] = f"Compara con {pivot}: ¿Menores o Mayores?"
        dct["explain"] = f"Compara cada fracción con {pivot} y clasifícala."
        return dct

    def _gen_pairs_concept(self) -> Dict[str, Any]:
        pairs = [
            ["Numerador", "Partes tomadas"],
            ["Denominador", "Partes totales"],
            ["Equivalentes", "1/2 = 2/4"],
            ["Mismo denominador", "Mayor numerador → fracción mayor"],
        ]
        return _safe_pairs(pairs, "Empareja: conceptos básicos")

# ========= Helpers de fracciones =========

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
    """
    Asegura que correct_index apunte al *valor correcto* aun si barajamos.
    - Deduplica opciones preservando orden.
    - Si el valor correcto no está, lo añade.
    - Baraja y recalcula correct_index.
    """
    if (item or {}).get("type") != "multiple_choice":
        return item

    choices = [str(c) for c in (item.get("choices") or [])]
    correct_str = str(correct_value)

    # añade si falta
    if correct_str not in choices:
        choices.append(correct_str)

    # dedup preservando orden
    seen = set()
    dedup = []
    for c in choices:
        if c not in seen:
            seen.add(c)
            dedup.append(c)
    choices = dedup

    # barajar
    random.shuffle(choices)

    # fijar índice correcto
    item["choices"] = choices
    item["correct_index"] = choices.index(correct_str)
    return item

def _sanitize_mcq(item: dict) -> dict:
    if (item or {}).get("type") != "multiple_choice":
        return item

    choices = [str(c).strip() for c in (item.get("choices") or [])]
    ci = int(item.get("correct_index", -1))
    correct_val = choices[ci] if 0 <= ci < len(choices) else (choices[0] if choices else None)

    # ¿dominante “fracciones”?
    frac_mode = any(_FRAC_ONLY_RE.match(c) for c in choices if c)
    correct_frac = _parse_frac(correct_val) if frac_mode else None

    fixed = []
    for c in choices:
        if not c or _PLACEHOLDER_RE.search(c):
            fixed.append(_rand_frac_not_equiv(correct_frac) if frac_mode else str(random.randint(2, 20)))
        else:
            fixed.append(c)

    # completa hasta 4
    while len(fixed) < 4:
        fixed.append(_rand_frac_not_equiv(correct_frac) if frac_mode else str(random.randint(2, 20)))

    # dedup preservando orden
    seen, dedup = set(), []
    for c in fixed:
        if c not in seen:
            seen.add(c); dedup.append(c)
    fixed = dedup[:4]

    # si el valor correcto no está, lo añadimos (y luego barajamos)
    if correct_val not in fixed:
        fixed.append(correct_val)
        fixed = fixed[:4]

    item = dict(item)
    item["choices"] = fixed
    return _shuffle_choices_set_correct(item, correct_val)

# -------- Variación de enunciados/valores para banco --------

def _format_frac(num: int, den: int) -> str:
    return f"{num}/{den}"

def _variant_fraction_str(s: str, num_delta: int, den_delta: int, mn: int, mx: int) -> str:
    def repl(m):
        a = int(m.group(1)); b = int(m.group(2))
        a2 = max(mn, min(mx, a + num_delta))
        b2 = max(max(a2+1, mn+1), min(mx+6, b + den_delta))  # evita a2>=b2 (propias por defecto)
        if a2 >= b2:  # última defensa
            b2 = a2 + 1
        return f"{a2}/{b2}"
    return FRACTION_RE.sub(repl, s or "")

def _apply_variations_to_item(it: dict, seed: int, constraints: dict) -> dict:
    rnd = random.Random(seed)
    mn = int(constraints.get("allowed_numbers", {}).get("min", 1))
    mx = int(max(constraints.get("allowed_numbers", {}).get("max", 12), 4))

    num_delta = rnd.choice([-2, -1, 0, 1, 2])
    den_delta = rnd.choice([-1, 0, 1, 2])

    t = it.get("type")
    out = copy.deepcopy(it)

    if t == "multiple_choice":
        q = out.get("question", "")
        ch = out.get("choices", [])[:]
        q2 = _variant_fraction_str(q, num_delta, den_delta, mn, mx)
        ch2 = []
        for c in ch:
            ch2.append(_variant_fraction_str(str(c), num_delta, den_delta, mn, mx))
        # recalcular correct_index por texto si se movió
        correct_idx = int(out.get("correct_index", 0))
        correct_text = ch[correct_idx] if 0 <= correct_idx < len(ch) else None
        if correct_text:
            correct_text2 = _variant_fraction_str(str(correct_text), num_delta, den_delta, mn, mx)
            if correct_text2 in ch2:
                correct_idx2 = ch2.index(correct_text2)
            else:
                if ch2:
                    ch2[0] = correct_text2
                correct_idx2 = 0
            out["question"] = q2
            out["choices"] = ch2
            out["correct_index"] = correct_idx2
        return out

    if t == "match_pairs":
        pairs = []
        for L, R in out.get("pairs", []):
            pairs.append([
                _variant_fraction_str(str(L), num_delta, den_delta, mn, mx),
                _variant_fraction_str(str(R), num_delta, den_delta, mn, mx)
            ])
        out["pairs"] = pairs
        return out

    if t == "drag_to_bucket":
        items = [_variant_fraction_str(str(x), num_delta, den_delta, mn, mx) for x in out.get("items", [])]
        buckets = out.get("buckets", [])[:]
        sol = {b: [] for b in buckets}
        assigned = set()
        for b in buckets:
            for x in (out.get("solution", {}).get(b, []) or []):
                x2 = _variant_fraction_str(str(x), num_delta, den_delta, mn, mx)
                if x2 in items and x2 not in assigned:
                    sol[b].append(x2); assigned.add(x2)
        for x in items:
            if x not in assigned and buckets:
                sol[buckets[0]].append(x); assigned.add(x)
        out["items"] = items
        out["solution"] = sol
        return out

    return out

def _normalize_bank_item(it: dict) -> dict:
    if (it or {}).get("type") != "multiple_choice":
        return it
    base_choices = it.get("choices") or []
    ci = int(it.get("correct_index", -1))
    correct_val = base_choices[ci] if 0 <= ci < len(base_choices) else (base_choices[0] if base_choices else "Correcta")
    return _shuffle_choices_set_correct(dict(it), correct_val)

def _build_from_bank_variations(ctx: dict, style: str, seed: int) -> list[dict]:
    import re
    random.seed(seed)
    rng = random.Random(seed)

    bank = ctx.get("exercise_bank") or []
    limits = (ctx.get("constraints") or {}).get("allowed_numbers") or {}
    min_n = int(limits.get("min", 1))
    max_n = int(max(limits.get("max", 12), 4))

    def choices_dedup_shuffled_with_correct(options: list[str], correct: str, rng=None) -> list[str]:
        rng = rng or random
        pool = [str(o) for o in options] + [str(correct)]
        # dedup, preservando orden
        seen, dedup = set(), []
        for o in pool:
            if o not in seen:
                seen.add(o); dedup.append(o)
        while len(dedup) < 4:
            a = rng.randint(1, 9)
            b = rng.randint(2, 12)
            cand = f"{a}/{b}"
            if cand not in seen:
                seen.add(cand); dedup.append(cand)
        rng.shuffle(dedup)
        return dedup[:4]

    def make_mcq(q: str, options: list[str], correct: str, explain: str):
        item = {
            "type": "multiple_choice",
            "question": q,
            "choices": choices_dedup_shuffled_with_correct(options, correct, rng),
            "explain": explain,
        }
        return _shuffle_choices_set_correct(item, correct)

    # ---- Patrones ----
    sum_same_den_re       = re.compile(r"Resuelve:\s*(\d+)\s*/\s*(\d+)\s*([+\-])\s*(\d+)\s*/\s*\2")
    pizza_re              = re.compile(r"dividid[oa]\s+en\s+(\d+)\s+porciones?\s+iguales.*(?:come|comió|coloread[ao]s?)\s*(\d+)", re.IGNORECASE)
    rect_re               = re.compile(r"dividid[oa]\s+en\s+(\d+)\s+partes\s+iguales.*(?:coloread[ao]s?)\s*(\d+)", re.IGNORECASE)
    equiv_half_re         = re.compile(r"equivalente\s+a\s+1\s*/\s*2", re.IGNORECASE)
    denom_which_re        = re.compile(r"En la fracción\s+\d+\s*/\s*\d+.*(partes iguales está dividido|dividido el total)", re.IGNORECASE)
    numer_which_re        = re.compile(r"En la fracción\s+\d+\s*/\s*\d+.*(cuál número es el numerador|numerador)", re.IGNORECASE)
    bigger_same_den_re    = re.compile(r"¿Cuál de estas fracciones es la más grande\??", re.IGNORECASE)
    smallest_same_num_re  = re.compile(r"¿Cuál de estas fracciones es la más pequeña\??", re.IGNORECASE)
    half_pencils_re       = re.compile(r"la mitad\s*\(\s*1\s*/\s*2\s*\)\s+.*(\d+)\s+\w+", re.IGNORECASE)

    def vary_sum_same_den(orig_q: str):
        m = sum_same_den_re.search(orig_q)
        if not m: return None
        a = random.randint(min_n, max_n)
        b = random.randint(max(2, min_n), max_n)
        c = random.randint(min_n, max_n)
        op = random.choice(["+","-"])
        if op == "-" and a < c:
            a, c = c, a
        q = f"Resuelve: {a}/{b} {op} {c}/{b} = ?"
        num = a + c if op == "+" else a - c
        correct = f"{num}/{b}"
        distractors = {
            f"{num}/{2*b}",
            f"{a}/{b}",
            f"{abs(num-1)}/{b}",
            f"{num+1}/{b}",
        }
        distractors.discard(correct)
        options = random.sample(list(distractors), k=min(3, len(distractors)))
        return make_mcq(q, options, correct, "Con mismo denominador, opera numeradores y conserva el denominador.")

    def vary_colored_fraction(orig_q: str):
        if not (pizza_re.search(orig_q) or rect_re.search(orig_q)):
            return None
        total = random.randint(max(4, min_n), max_n)
        colored = random.randint(1, total - 1)
        if "pizza" in orig_q.lower():
            q = f"Una pizza está dividida en {total} porciones iguales y un niño se come {colored}. ¿Qué fracción representa lo que comió?"
        else:
            q = f"En un rectángulo dividido en {total} partes iguales, {colored} están coloreadas. ¿Qué fracción representa la parte coloreada?"
        correct = f"{colored}/{total}"
        wrongs = set([f"{total}/{colored}", f"{max(1, colored-1)}/{total}", f"{colored}/{max(2, total-1)}"])
        wrongs.discard(correct)
        options = list(wrongs)[:3]
        item = {"type":"multiple_choice","question":q,"choices":options,"explain":"Numerador = partes coloreadas; denominador = total de partes."}
        return _shuffle_choices_set_correct(item, correct)

    def vary_equiv_half(orig_q: str):
        if not equiv_half_re.search(orig_q): return None
        m = random.randint(2, 6)
        correct = f"{m}/{2*m}"
        wrongs = [f"{m}/{m}", f"{m-1}/{2*m}" if m>2 else f"{m+1}/{2*m}", f"{2*m}/{m}"]
        return make_mcq("¿Cuál de estas fracciones es equivalente a 1/2?", wrongs, correct, "Multiplica numerador y denominador por el mismo número.")

    def vary_denom_which(orig_q: str):
        if not denom_which_re.search(orig_q): return None
        a = random.randint(min_n, max_n-1)
        b = random.randint(a+1, max_n)
        q = f"En la fracción {a}/{b}, ¿cuál número indica en cuántas partes iguales está dividido el total?"
        correct = str(b)
        wrongs = [str(a), str(a+b), "2"]
        return make_mcq(q, wrongs, correct, "El denominador (abajo) indica el total de partes iguales.")

    def vary_numer_which(orig_q: str):
        if not numer_which_re.search(orig_q): return None
        a = random.randint(min_n, max_n-1)
        b = random.randint(a+1, max_n)
        q = f"En la fracción {a}/{b}, ¿cuál número es el numerador?"
        correct = str(a)
        wrongs = [str(b), str(a+b), "2"]
        return make_mcq(q, wrongs, correct, "El numerador (arriba) indica las partes consideradas.")

    def vary_bigger_same_den(orig_q: str):
        if not bigger_same_den_re.search(orig_q): return None
        d = random.randint(3, max_n)
        nums = random.sample(range(1, d), 4)
        correct = f"{max(nums)}/{d}"
        options = [f"{n}/{d}" for n in nums]
        item = {"type":"multiple_choice","question":"¿Cuál de estas fracciones es la más grande?","choices":options,"explain":"Mismo denominador: mayor numerador => fracción mayor."}
        return _shuffle_choices_set_correct(item, correct)

    def vary_smallest_same_num(orig_q: str):
        if not smallest_same_num_re.search(orig_q): return None
        n = 1
        denoms = random.sample(range(2, max_n+1), 4)
        correct = f"{n}/{max(denoms)}"
        options = [f"{n}/{d}" for d in denoms]
        item = {"type":"multiple_choice","question":"¿Cuál de estas fracciones es la más pequeña?","choices":options,"explain":"Mismo numerador: mayor denominador => fracción menor."}
        return _shuffle_choices_set_correct(item, correct)

    def vary_half_pencils(orig_q: str):
        if not half_pencils_re.search(orig_q): return None
        T = random.randrange(6, max(20, max_n*2), 2)  # par
        correct_num = T // 2
        q = f"Si tienes {T} lápices y le das la mitad (1/2) a un amigo, ¿cuántos lápices le diste?"
        wrongs = [str(T), str(T//3), str(max(1, correct_num-2))]
        return make_mcq(q, wrongs, str(correct_num), "La mitad de T es T/2.")

    def vary_generic_by_value(it: dict):
        return _normalize_bank_item(it)

    out = []
    for it in bank:
        if it.get("type") != "multiple_choice":
            out.append(dict(it)); continue

        q = it.get("question") or ""
        var = (
            vary_sum_same_den(q) or
            vary_colored_fraction(q) or
            vary_equiv_half(q) or
            vary_denom_which(q) or
            vary_numer_which(q) or
            vary_bigger_same_den(q) or
            vary_smallest_same_num(q) or
            vary_half_pencils(q)
        )
        out.append(var if var else vary_generic_by_value(it))

    return out[:10]

# =========================
# Kinestésico (drag & match)
# =========================

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
    # recorta a 6 pares por UX
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

def _random_fraction(min_den=3, max_den=12):
    import random
    b = random.randint(min_den, max_den)
    a = random.randint(1, b-1)
    return f"{a}/{b}"

def _random_propia_impropia_set(n=6):
    # mezcla de propias e impropias
    import random
    items = set()
    while len(items) < n:
        if random.random() < 0.5:  # propia
            b = random.randint(3, 12)
            a = random.randint(1, b-1)
        else:                       # impropia
            b = random.randint(3, 12)
            a = random.randint(b, b+4)
        items.add(f"{a}/{b}")
    items = list(items)
    sol = {
        "Propias": [x for x in items if int(x.split('/')[0]) <  int(x.split('/')[1])],
        "Impropias": [x for x in items if int(x.split('/')[0]) >= int(x.split('/')[1])],
    }
    return items, ["Propias", "Impropias"], sol

def _random_equiv_half_set(n=6):
    import random
    items = set()
    # algunos equivalentes y otros no
    while len(items) < n:
        if random.random() < 0.6:
            m = random.randint(2, 6)
            items.add(f"{m}/{2*m}")  # equivalente a 1/2
        else:
            a = random.randint(1, 9); b = random.randint(2, 12)
            if a*2 != b:  # no equivalente a 1/2
                items.add(f"{a}/{b}")
    items = list(items)
    sol = {
        "Equivalentes a 1/2": [x for x in items if int(x.split('/')[0])*2 == int(x.split('/')[1])],
        "No equivalentes":    [x for x in items if int(x.split('/')[0])*2 != int(x.split('/')[1])],
    }
    return items, ["Equivalentes a 1/2", "No equivalentes"], sol

def build_kinesthetic_set_from_ctx(ctx: dict, seed: int | None = None) -> list[dict]:
    import random
    rnd = random.Random(seed or "0xEDU3")

    out: list[dict] = []

    # 1) Usar setups declarados en el JSON como “plantillas” descriptivas
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
            # construye 3–6 pares a partir de 'concepts' y 'examples'
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

    # 2) Añadir del exercise_bank los que ya son kinestésicos
    for it in (ctx.get("exercise_bank") or []):
        if it.get("type") in ("drag_to_bucket", "match_pairs"):
            # clonar para no mutar ctx
            cloned = {
                "type": it["type"],
                "title": it.get("title"),
                "explain": it.get("explain", ""),
            }
            if it["type"] == "match_pairs":
                cloned["pairs"] = [list(p) for p in (it.get("pairs") or [])]
            else:
                cloned["items"] = list(it.get("items") or [])
                cloned["buckets"] = list(it.get("buckets") or [])
                cloned["solution"] = {k: list(v) for k, v in (it.get("solution") or {}).items()}
            out.append(cloned)

    # 3) Generar 2 actividades kinestésicas al vuelo (si faltan)
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

    # 4) Garantizar 10 items. Si sobran, recorta; si faltan, replica variaciones menores
    #    (barajar ítems/pares para no repetir exacto)
    rnd.shuffle(out)
    if len(out) >= 10:
        return out[:10]

    def _light_shuffle(x):
        y = dict(x)
        if y["type"] == "drag_to_bucket":
            items = y.get("items", [])[:]
            rnd.shuffle(items)
            y["items"] = items
            # solución coherente con items barajados
            sol = {b: [] for b in y.get("buckets", [])}
            for b, arr in (x.get("solution") or {}).items():
                for it in arr:
                    if it in items:
                        sol[b].append(it)
            y["solution"] = sol
        else:
            pairs = y.get("pairs", [])[:]
            rnd.shuffle(pairs)
            y["pairs"] = pairs[:6]
        return y

    while len(out) < 10 and out:
        out.append(_light_shuffle(out[len(out) % max(1, len(out))]))

    return out[:10]