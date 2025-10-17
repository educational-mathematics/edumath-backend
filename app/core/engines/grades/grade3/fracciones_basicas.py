from __future__ import annotations
from typing import Dict, Any, List, Tuple, Optional
import re, random, copy

# ========= RegEx que ya usabas en topics.py =========
FRACTION_RE      = re.compile(r"\b(\d{1,2})\s*/\s*(\d{1,2})\b")
_PLACEHOLDER_RE  = re.compile(r"(?:^|\s)(distractor|incorrecta)\b", re.IGNORECASE)
_FRAC_ONLY_RE    = re.compile(r"^\s*\d+\s*/\s*\d+\s*$")

# ========= Helpers de fracciones (MOVIDOS tal cual) =========

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
    """
    Genera un set variado a partir del exercise_bank con reglas para fracciones:
    - suma/resta mismo denominador
    - pizza/rectángulo dividid* en N partes ... k coloreadas
    - equivalente a 1/2
    - “¿cuál es el denominador?” / “¿cuál es el numerador?”
    - mayor con mismo denominador / menor con mismo numerador=1
    - mitad de T lápices
    En todos los casos baraja opciones y fija correct_index por *valor*.
    """
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