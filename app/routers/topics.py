# app/routers/topics.py
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import select, func
from pathlib import Path
import json, os, logging, uuid, requests

import math, random

from app.db import get_db
from app.deps import get_current_user
from app.models.user import User
from app.models.topic import Topic
from app.models.user_topic import UserTopic
from app.models.topic_session import TopicSession

from app.ai.gemini import generate_one_image_png
from app.ai.variation_utils import extract_used_fractions

from app.ai.gemini import (
    generate_explanation,
    generate_exercises_variant,
    fallback_generate_exercises,
    fallback_generate_explanation,
)
from app.ai.variation_utils import extract_used_fractions

# insignias / puntos
from app.domain.badges.service import on_points_changed
from app.models.badge import Badge
from app.models.user_badge import UserBadge

import re, random, copy

from typing import Tuple
from random import Random, randint

FRACTION_RE = re.compile(r"\b(\d{1,2})\s*/\s*(\d{1,2})\b")

log = logging.getLogger("topics")

APP_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_DIR.parent
CONTENT_DIR = Path(os.getenv("CONTENT_DIR", APP_DIR / "content"))

STATIC_DIR = (APP_DIR / "static").resolve()
STATIC_DIR.mkdir(parents=True, exist_ok=True)

TTS_DIR = (STATIC_DIR / "tts")
TTS_DIR.mkdir(parents=True, exist_ok=True)

STATIC_GEN = Path(os.getenv("STATIC_GEN_DIR", STATIC_DIR / "generated"))
STATIC_GEN.mkdir(parents=True, exist_ok=True)


# -------------------------------
# Helpers
# -------------------------------

def _format_frac(num: int, den: int) -> str:
    return f"{num}/{den}"

def _mcq_sum_same_den_variant(item: dict, rng) -> dict:
    """
    Detecta 'Resuelve: a/b + c/b = ?' y genera una variante coherente:
    - cambia a,c,b
    - recalcula respuesta correcta
    - rehace distractores y explica
    """
    q = (item.get("question") or "").strip()
    m = re.match(r"^Resuelve:\s*(\d+)\s*/\s*(\d+)\s*\+\s*(\d+)\s*/\s*\2\s*=\s*\?$", q)
    if not m:
        return item  # no es este patrón

    a = int(m.group(1)); b = int(m.group(2)); c = int(m.group(3))

    # nueva variante
    b2 = rng.randint(5, 12)
    a2 = rng.randint(1, b2 - 1)
    c2 = rng.randint(1, b2 - 1)

    # correcta
    num = a2 + c2
    den = b2
    correct = _format_frac(num, den)

    # distractores típicos
    d1 = _format_frac(num, den*2)              # denom mal (duplica)
    d2 = _format_frac(abs(a2-c2), den)         # resta en vez de suma
    d3 = _format_frac(den, num) if num != 0 else "0/1"  # invertida

    choices = [d1, correct, d2, d3]
    # barajar manteniendo índice correcto
    idx = 0
    for i in range(len(choices)-1, 0, -1):
        j = rng.randint(0, i)
        choices[i], choices[j] = choices[j], choices[i]
    correct_index = choices.index(correct)

    exp = f"Denominador igual: suma numeradores {a2} + {c2} = {num}; denominador {den}."

    new_item = dict(item)
    new_item["question"] = f"Resuelve: {a2}/{b2} + {c2}/{b2} = ?"
    new_item["choices"] = choices
    new_item["correct_index"] = correct_index
    new_item["explain"] = exp
    return new_item

def _vary_mcq(item: dict, rng) -> dict:
    """Aplica reglas de variación conocidas; si no matchea, devuelve el item original."""
    if item.get("type") != "multiple_choice":
        return item
    # patrones soportados
    j = _mcq_sum_same_den_variant(item, rng)
    return j

def _filter_items_for_style(items: list[dict], style: str) -> list[dict]:
    out = []
    for it in (items or []):
        styles = it.get("styles")
        if not styles or style in styles:
            out.append(copy.deepcopy(it))
    return out

def _pick_10(items: list[dict]) -> list[dict]:
    # Mantén proporción, prioriza MCQ y luego variedad
    if len(items) <= 10:
        return items[:10]
    # simple: shuffle + take 10
    tmp = items[:]
    random.shuffle(tmp)
    return tmp[:10]

def _bump_num(val: int, delta: int, mn: int, mx: int) -> int:
    return max(mn, min(mx, val + delta))

def _variant_fraction_str(s: str, num_delta: int, den_delta: int, mn: int, mx: int) -> str:
    def repl(m):
        a = int(m.group(1)); b = int(m.group(2))
        a2 = _bump_num(a, num_delta, mn, mx)
        b2 = _bump_num(b, den_delta, max(a2+1, mn+1), mx+6)  # evita a2>=b2 para “propias” típicas
        if a2 >= b2:  # última defensa: fuerza propia salvo cuando el texto diga lo contrario
            b2 = a2 + 1
        return f"{a2}/{b2}"
    return FRACTION_RE.sub(repl, s)

def _apply_variations_to_item(it: dict, seed: int, constraints: dict) -> dict:
    rnd = random.Random(seed)
    mn = int(constraints.get("allowed_numbers", {}).get("min", 1))
    mx = int(constraints.get("allowed_numbers", {}).get("max", 12))

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
            # re-hallarlo en ch2; si no está, forzar posición 0
            if correct_text2 in ch2:
                correct_idx2 = ch2.index(correct_text2)
            else:
                # coloca correcto en 0
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
        # solution coherente con “items”
        sol = {b: [] for b in buckets}
        assigned = set()
        for b in buckets:
            for x in (out.get("solution", {}).get(b, []) or []):
                x2 = _variant_fraction_str(str(x), num_delta, den_delta, mn, mx)
                if x2 in items and x2 not in assigned:
                    sol[b].append(x2); assigned.add(x2)
        # reparte huérfanos
        for x in items:
            if x not in assigned and buckets:
                sol[buckets[0]].append(x); assigned.add(x)
        out["items"] = items
        out["solution"] = sol
        return out

    return out

def _shuffle_choices_set_correct(item, correct_value) -> dict:
    """
    Asegura que correct_index apunte al *valor correcto* aun si barajamos.
    - Deduplica opciones preservando orden.
    - Si el valor correcto no está, lo añade.
    - Baraja y recalcula correct_index.
    """
    if item.get("type") != "multiple_choice":
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

def _normalize_bank_item(it: dict) -> dict:
    if (it or {}).get("type") != "multiple_choice":
        return it
    # El valor correcto del banco es choices[correct_index] antes de barajar
    base_choices = it.get("choices") or []
    ci = int(it.get("correct_index", -1))
    correct_val = base_choices[ci] if 0 <= ci < len(base_choices) else None
    if correct_val is None:
        # si hay problema, fuerza primera opción como correcta para no romper flujo
        correct_val = base_choices[0] if base_choices else "Correcta"
    return _shuffle_choices_set_correct(dict(it), correct_val)


def _build_from_bank(ctx: dict, style: str) -> list[dict]:
    bank = ctx.get("exercise_bank") or []
    out = []
    for it in bank:
        if style not in (it.get("styles") or ["visual", "auditivo", "kinestesico"]):
            # si el ítem declara estilos y no incluye el actual, igual lo tomamos,
            # pero podrías filtrarlo si prefieres.
            pass
        if it.get("type") == "multiple_choice":
            out.append(_normalize_bank_item(it))
        else:
            out.append(dict(it))
    return out[:10]

def _build_from_bank_variations(ctx: dict, style: str, seed: int) -> list[dict]:
    """
    Genera un set variado a partir del exercise_bank:
    - Suma/resta de fracciones con mismo denominador (ya soportado)
    - "Pizza/rectángulo ... dividid* en N partes ... k coloreadas" -> k/N
    - "¿equivalente a 1/2?" -> m/(2m) con distractores
    - "En la fracción a/b, ¿cuál número indica ... dividido el total?" -> b (denominador)
    - "En la fracción a/b, ¿cuál es el numerador?" -> a
    - "¿Cuál es la más grande?" (mismo denominador) -> mayor numerador
    - "¿Cuál es la más pequeña?" (mismo numerador=1) -> mayor denominador
    - "Tienes T lápices, la mitad" -> T/2
    En todos los casos, se barajan opciones y se fija correct_index por *valor*.
    """
    import re, random
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
        # si quedaron <4, rellena con distractores plausibles
        while len(dedup) < 4:
            # genera fracciones simples aleatorias como relleno
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
            "choices": choices_dedup_shuffled_with_correct(options, correct),
            "explain": explain,
        }
        return _shuffle_choices_set_correct(item, correct)

    # ---- Handlers ----
    sum_same_den_re = re.compile(r"Resuelve:\s*(\d+)\s*/\s*(\d+)\s*([+\-])\s*(\d+)\s*/\s*\2")
    pizza_re = re.compile(r"dividid[oa]\s+en\s+(\d+)\s+porciones?\s+iguales.*(?:come|comió|coloread[ao]s?)\s*(\d+)", re.IGNORECASE)
    rect_re  = re.compile(r"dividid[oa]\s+en\s+(\d+)\s+partes\s+iguales.*(?:coloread[ao]s?)\s*(\d+)", re.IGNORECASE)
    equiv_half_re = re.compile(r"equivalente\s+a\s+1\s*/\s*2", re.IGNORECASE)
    denom_which_re = re.compile(r"En la fracción\s+\d+\s*/\s*\d+.*(partes iguales está dividido|dividido el total)", re.IGNORECASE)
    numer_which_re = re.compile(r"En la fracción\s+\d+\s*/\s*\d+.*(cuál número es el numerador|numerador)", re.IGNORECASE)
    bigger_same_den_re = re.compile(r"¿Cuál de estas fracciones es la más grande\??", re.IGNORECASE)
    smallest_same_num_re = re.compile(r"¿Cuál de estas fracciones es la más pequeña\??", re.IGNORECASE)
    half_pencils_re = re.compile(r"la mitad\s*\(\s*1\s*/\s*2\s*\)\s+.*(\d+)\s+\w+", re.IGNORECASE)

    def vary_sum_same_den(orig_q: str):
        m = sum_same_den_re.search(orig_q)
        if not m: return None
        a = rng.randint(min_n, max_n)
        b = rng.randint(max(2, min_n), max_n)
        c = rng.randint(min_n, max_n)
        op = rng.choice(["+","-"])
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
        options = rng.sample(list(distractors), k=min(3, len(distractors)))
        return make_mcq(q, options, correct, "Con mismo denominador, opera numeradores y conserva el denominador.")

    def vary_colored_fraction(orig_q: str):
        # detecta si es una consigna de "dividido en N partes / porciones" (pizza o rectángulo)
        if not (pizza_re.search(orig_q) or rect_re.search(orig_q)):
            return None

        # elige total y coloreadas / comidas plausibles
        total = rng.randint(max(4, min_n), max_n)
        colored = rng.randint(1, total - 1)

        # arma el enunciado neutro (no dependemos del texto original)
        if "pizza" in orig_q.lower():
            q = f"Una pizza está dividida en {total} porciones iguales y un niño se come {colored}. ¿Qué fracción representa lo que comió?"
        else:
            q = f"En un rectángulo dividido en {total} partes iguales, {colored} están coloreadas. ¿Qué fracción representa la parte coloreada?"

        correct = f"{colored}/{total}"

        # tres distractores razonables
        wrongs = set()
        wrongs.add(f"{total}/{colored}")             # invertida
        wrongs.add(f"{max(1, colored-1)}/{total}")   # numerador cercano
        wrongs.add(f"{colored}/{max(2, total-1)}")   # denominador cercano
        if correct in wrongs:
            wrongs.discard(correct)

        options = list(wrongs)[:3]
        item = {
            "type": "multiple_choice",
            "question": q,
            "choices": choices_dedup_shuffled_with_correct(options, correct, rng),
            "explain": "Numerador = partes coloreadas; denominador = total de partes.",
        }
        return _shuffle_choices_set_correct(item, correct)

    def vary_equiv_half(orig_q: str):
        if not equiv_half_re.search(orig_q): return None
        m = rng.randint(2, 6)
        correct = f"{m}/{2*m}"
        wrongs = [f"{m}/{m}", f"{m-1}/{2*m}" if m>2 else f"{m+1}/{2*m}", f"{2*m}/{m}"]
        return make_mcq("¿Cuál de estas fracciones es equivalente a 1/2?", wrongs, correct, "Multiplica numerador y denominador por el mismo número.")

    def vary_denom_which(orig_q: str):
        if not denom_which_re.search(orig_q): return None
        a = rng.randint(min_n, max_n-1)
        b = rng.randint(a+1, max_n)
        q = f"En la fracción {a}/{b}, ¿cuál número indica en cuántas partes iguales está dividido el total?"
        correct = str(b)
        wrongs = [str(a), str(a+b), "2"]
        return make_mcq(q, wrongs, correct, "El denominador (abajo) indica el total de partes iguales.")

    def vary_numer_which(orig_q: str):
        if not numer_which_re.search(orig_q): return None
        a = rng.randint(min_n, max_n-1)
        b = rng.randint(a+1, max_n)
        q = f"En la fracción {a}/{b}, ¿cuál número es el numerador?"
        correct = str(a)
        wrongs = [str(b), str(a+b), "2"]
        return make_mcq(q, wrongs, correct, "El numerador (arriba) indica las partes consideradas.")

    def vary_bigger_same_den(orig_q: str):
        if not bigger_same_den_re.search(orig_q): return None
        d = rng.randint(3, max_n)
        nums = rng.sample(range(1, d), 4)
        correct = f"{max(nums)}/{d}"
        options = [f"{n}/{d}" for n in nums]
        item = {"type":"multiple_choice","question":"¿Cuál de estas fracciones es la más grande?","choices":options,"explain":"Mismo denominador: mayor numerador => fracción mayor."}
        return _shuffle_choices_set_correct(item, correct)

    def vary_smallest_same_num(orig_q: str):
        if not smallest_same_num_re.search(orig_q): return None
        n = 1
        denoms = rng.sample(range(2, max_n+1), 4)
        correct = f"{n}/{max(denoms)}"
        options = [f"{n}/{d}" for d in denoms]
        item = {"type":"multiple_choice","question":"¿Cuál de estas fracciones es la más pequeña?","choices":options,"explain":"Mismo numerador: mayor denominador => fracción menor."}
        return _shuffle_choices_set_correct(item, correct)

    def vary_half_pencils(orig_q: str):
        if not half_pencils_re.search(orig_q): return None
        T = rng.randrange(6, max(20, max_n*2), 2)  # par
        correct_num = T // 2
        q = f"Si tienes {T} lápices y le das la mitad (1/2) a un amigo, ¿cuántos lápices le diste?"
        wrongs = [str(T), str(T//3), str(max(1, correct_num-2))]
        return make_mcq(q, wrongs, str(correct_num), "La mitad de T es T/2.")

    def vary_generic_by_value(it: dict):
        # fallback: barajar manteniendo correcto por valor
        return _normalize_bank_item(it)

    out = []
    for it in bank:
        if (it.get("styles") and style not in it.get("styles")):
            # si un ítem declara estilos y no incluye el actual, aún podemos incluirlo,
            # pero si quieres filtrar por estilo, descomenta la siguiente línea:
            # continue
            pass

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

def _make_tts(text: str, out_path: Path, voice: str = ""):
    """
    Llama a tu endpoint interno /ai/tts para generar audio WAV.
    Si falla, no levanta error (solo no genera audio).
    """
    voice = (voice or os.getenv("TTS_VOICE", "es-ES-Standard-A")).strip()
    try:
        r = requests.post(
            "http://localhost:8000/ai/tts",
            json={"text": text, "voice": voice},
            timeout=30,
        )
        if r.status_code == 200 and r.content:
            out_path.write_bytes(r.content)
        else:
            # 204 o 5xx: sin audio
            pass
    except Exception as e:
        log.warning("[topics._make_tts] TTS error: %s", e)


def _tts_url_for(session_id: int, name: str) -> str:
    return f"/static/tts/sess-{session_id}-{name}.wav"


def _neutralize_audio_words(question: str) -> str:
    if not question:
        return question
    q = question.strip()
    lowers = q.lower()
    if lowers.startswith("escucha ") or "escucha atentamente" in lowers or "te dictan" in lowers:
        return (
            q.replace("Escucha atentamente ", "Lee atentamente ")
                .replace("Escucha ", "Lee ")
                .replace("te dictan", "se presentan")
        )
    return q


def _save_png_return_url(topic_slug: str, png_bytes: bytes) -> str:
    name = f"{topic_slug}-{uuid.uuid4().hex[:8]}.png"
    out = STATIC_GEN / name
    out.write_bytes(png_bytes)
    return f"/static/generated/{name}"

def _resolve_or_generate_visual_image(db: Session, ut: UserTopic, ctx: dict, topic_slug: str) -> str | None:
    """
    Devuelve una URL (string) a la imagen de explicación para estilo visual.
    Reutiliza cache en user_topics.cached_visual_image_url si existe.
    Si no hay cache:
      - usa primero ctx['visual_assets']['image_urls'][0] si está
      - sino intenta IA (generate_one_image_png) con el primer prompt disponible
      - si la IA produce bytes, se guardan en /static/generated y se cachea la URL en ut
    """
    try:
        if ut.cached_visual_image_url:
            return ut.cached_visual_image_url

        # 1) si el JSON ya trae una imagen estática, úsala
        va = (ctx.get("visual_assets") or {})
        preset_urls = va.get("image_urls") or []
        if preset_urls:
            ut.cached_visual_image_url = preset_urls[0]
            db.add(ut); db.commit(); db.refresh(ut)
            return ut.cached_visual_image_url

        # 2) intenta IA con prompts del JSON
        prompts = va.get("image_prompts") or []
        prompt = prompts[0] if prompts else None
        if prompt:
            png = generate_one_image_png(prompt)
            if png:
                url = _save_png_return_url(topic_slug, png)
                ut.cached_visual_image_url = url
                db.add(ut); db.commit(); db.refresh(ut)
                return url

    except Exception as e:
        log.warning("visual image resolve/gen failed: %s", e)

    return None

def resolve_context_path(grade: int, slug: str) -> Path:
    p = CONTENT_DIR / f"grade-{grade}" / f"{slug}.json"
    if p.exists():
        return p
    fallback = REPO_ROOT / "content" / f"grade-{grade}" / f"{slug}.json"
    if fallback.exists():
        return fallback
    return p  # para que el 404 muestre la ruta esperada


# -------------------------------
# Core: abrir/continuar sesión
# -------------------------------
def _open_session_core(
    db: Session,
    me: User,
    ut: UserTopic,
    t: Topic,
    force_new: bool = False
):
    style = ut.recommended_style or (me.vak_style or "visual")

    # Contexto
    path = resolve_context_path(t.grade, t.slug)
    if not path.exists():
        log.warning("open_session: contexto no encontrado. grade=%s slug=%s path=%s", t.grade, t.slug, str(path))
        raise HTTPException(404, f"Contexto no encontrado: {path}")

    ctx = json.loads(path.read_text(encoding="utf-8"))

    # Buscar última sesión
    last = db.execute(
        select(TopicSession)
        .where(TopicSession.user_id == me.id, TopicSession.topic_id == t.id)
        .order_by(TopicSession.id.desc())
    ).scalars().first()

    need_new = force_new or (not last) or (last.current_index >= 10)

    times_opened = int(ut.times_opened or 0)
    must_use_ai_first_run = (not bool(ut.ai_seed_done)) and (times_opened == 0)

    # Para evitar repetir fracciones, mirar últimas 5 sesiones
    prev = db.execute(
        select(TopicSession)
        .where(TopicSession.user_id == me.id, TopicSession.topic_id == t.id)
        .order_by(TopicSession.id.desc()).limit(5)
    ).scalars().all()
    avoid_numbers: list[int] = []
    for s in prev:
        if s.items:
            avoid_numbers += extract_used_fractions(s.items)

    explanation = None
    visual_img_url = None

    try:
        if need_new:
            items = []
            explanation = None

            if must_use_ai_first_run:
                # === 1ª VEZ: O IA O NADA (no banco) ===
                avoid_numbers = []
                try:
                    prev = db.execute(
                        select(TopicSession)
                        .where(TopicSession.user_id == me.id, TopicSession.topic_id == t.id)
                        .order_by(TopicSession.id.desc()).limit(5)
                    ).scalars().all()
                    for s in prev:
                        if s.items:
                            avoid_numbers += extract_used_fractions(s.items)
                except Exception as e:
                    log.warning("avoid_numbers calc failed: %s", e)
                    avoid_numbers = []

                # reintento con IA (x3)
                ai_ok = False
                last_err = None
                for _ in range(3):
                    try:
                        items = generate_exercises_variant(ctx, style=style, avoid_numbers=avoid_numbers)
                        explanation = generate_explanation(ctx)
                        ai_ok = bool(items) and bool(explanation)
                        if ai_ok: break
                    except Exception as e:
                        last_err = e

                if not ai_ok:
                    log.warning("AI first run failed (no fallback). err=%s", last_err)
                    # NO marcamos times_opened ni ai_seed_done
                    raise HTTPException(503, "IA temporalmente no disponible. Inténtalo de nuevo.")

                # cachea para siguientes veces
                ut.ai_seed_done = True
                ut.cached_explanation = explanation
                try:
                    exp_path = (TTS_DIR / f"sess-expl-{me.id}-{t.id}.wav")
                    if not exp_path.exists():
                        _make_tts(explanation, exp_path, voice=os.getenv("TTS_VOICE", "es-ES-Standard-A"))
                    if exp_path.exists():
                        ut.cached_expl_audio_url = f"/static/tts/sess-expl-{me.id}-{t.id}.wav"
                except Exception as e:
                    log.warning("tts cache fail: %s", e)

                # imagen visual opcional
                try:
                    if not ut.cached_visual_image_url:
                        vprompts = (ctx.get("visual_assets") or {}).get("image_prompts") or []
                        ut.cached_visual_image_url = ut.cached_visual_image_url or None
                except Exception as e:
                    log.warning("visual image gen failed: %s", e)

                db.add(ut); db.commit(); db.refresh(ut)

            else:
                # === REPETICIONES: SIEMPRE con variaciones nuevas ===
                # evita repetir números usados recientemente (ya calculado arriba en avoid_numbers)

                # bump del contador y seed nuevo determinístico distinto cada vez
                ut.bank_variant_counter = int(ut.bank_variant_counter or 0) + 1
                bank_version = int(ut.bank_version or 1)
                new_seed = bank_version * 1_000_003 + ut.bank_variant_counter
                ut.bank_variation_seed = new_seed
                db.add(ut); db.commit(); db.refresh(ut)

                # genera los ítems variando TODO el banco
                # (si tu helper no acepta avoid_numbers, simplemente lo ignora)
                try:
                    items = _build_from_bank_variations(ctx, style, new_seed)
                except TypeError:
                    # por si la firma antigua no coincide
                    items = _build_from_bank_variations(ctx, style, seed=new_seed)

                explanation = ut.cached_explanation or (ctx.get("explanation_variants") or [None])[0]

            # normaliza a 10 items
            if not isinstance(items, list): items = []
            if len(items) > 10: items = items[:10]
            while len(items) < 10:
                items.append({
                    "type": "multiple_choice",
                    "question": "Elige la opción correcta.",
                    "choices": ["Correcta", "Incorrecta 1", "Incorrecta 2", "Incorrecta 3"],
                    "correct_index": 0,
                    "explain": "Revisa el concepto clave."
                })

            last = TopicSession(
                user_id=me.id, topic_id=t.id, style_used=style,
                items=items,
                results=[{"correct": None, "attempts": 0} for _ in range(10)],
                current_index=0,
                explanation=explanation
            )
            db.add(last); db.commit(); db.refresh(last)

            # registra apertura SOLO cuando se creó la sesión
            ut.times_opened = times_opened + 1
            db.add(ut); db.commit(); db.refresh(ut)
        else:
            explanation = last.explanation or (ut.cached_explanation or None)

    except Exception as e:
        log.error("open_session_core failed: %s", e)
        raise HTTPException(500, "No se pudo abrir el tema, intenta de nuevo.")

    # --- Audio para la explicación si es auditivo ---
    explanation_audio_url = None
    try:
        if last.style_used == "auditivo":
            # Prioriza cache de UserTopic
            if ut.cached_expl_audio_url:
                explanation_audio_url = ut.cached_expl_audio_url
            else:
                exp_path = (TTS_DIR / f"sess-{last.id}-explanation.wav")
                if not exp_path.exists():
                    _make_tts(last.explanation or explanation or "", exp_path, voice=os.getenv("TTS_VOICE", "es-ES-Standard-A"))
                if exp_path.exists():
                    explanation_audio_url = _tts_url_for(last.id, "explanation")
    except Exception as e:
        log.warning("tts explanation fail: %s", e)

    # Normaliza preguntas “auditivas” si no hay tts
    if last.style_used == "auditivo":
        changed = False
        for idx, it in enumerate(last.items or []):
            if it.get("type") == "multiple_choice":
                q = it.get("question") or ""
                q2 = _neutralize_audio_words(q)
                if q2 != q:
                    it["question"] = q2
                    changed = True
        if changed:
            db.add(last); db.commit(); db.refresh(last)

    progress_in_session = min((last.current_index or 0) * 10, 100)

    # Si no resolvimos antes, intenta recuperar de cache ahora
    if visual_img_url is None:
        visual_img_url = ut.cached_visual_image_url or None

    return {
        "sessionId": last.id,
        "title": t.title,
        "style": last.style_used,
        "explanation": explanation or last.explanation,
        "explanationAudioUrl": explanation_audio_url if style == "auditivo" else None,
        "explanationImageUrl": (ut.cached_visual_image_url or None) if style == "visual" else None,
        "currentIndex": last.current_index,
        "items": last.items,
        "progressInSession": progress_in_session,
    }

# -------------------------------
# Router
# -------------------------------
router = APIRouter(prefix="/topics", tags=["topics"])


@router.get("/catalog")
def catalog(db: Session = Depends(get_db)):
    rows = db.execute(select(Topic)).scalars().all()
    out: dict[int, list] = {}
    for t in rows:
        cover = (t.cover_url or "").strip()
        if cover and not cover.startswith("/"):
            cover = "/" + cover
        out.setdefault(int(t.grade), []).append({
            "id": t.id,
            "slug": t.slug,
            "title": t.title,
            "coverUrl": cover,
        })
    return out

@router.get("/my")
def my_topics(db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    q = db.execute(
        select(UserTopic, Topic)
        .where(UserTopic.user_id == me.id)
        .join(Topic, Topic.id == UserTopic.topic_id)
    ).all()
    res = []
    for ut, t in q:
        res.append({
            "userTopicId": ut.id,
            "topicId": t.id,
            "slug": t.slug,
            "title": t.title,
            "coverUrl": t.cover_url,
            "progressPct": ut.progress_pct,
            "recommendedStyle": ut.recommended_style,
            "completedCount": ut.completed_count
        })
    return res


@router.post("/add/{topic_id}")
def add_topic(topic_id: int, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    exists = db.execute(
        select(UserTopic.id).where(UserTopic.user_id == me.id, UserTopic.topic_id == topic_id)
    ).scalar_one_or_none()
    if exists:
        return {"ok": True, "userTopicId": exists}

    ut = UserTopic(
        user_id=me.id,
        topic_id=topic_id,
        progress_pct=0,
        completed_count=0,
        total_time_sec=0,
        attempts_total=0,
        errors_total=0,
        best_score_pct=0,
        last_score_pct=0,
        last_time_sec=0,
        ai_seed_done=False,
        cached_explanation=None,
        cached_expl_audio_url=None,
        cached_visual_image_url=None,
        bank_variation_seed=None,
        times_opened=0,
        bank_version=1,
        bank_variant_counter=0,
    )
    db.add(ut); db.commit(); db.refresh(ut)
    return {"ok": True, "userTopicId": ut.id}

@router.post("/{user_topic_id}/open")
def open_session(user_topic_id: int, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    ut = db.get(UserTopic, user_topic_id)
    if not ut or ut.user_id != me.id:
        raise HTTPException(404, "Tema no encontrado")
    t = db.get(Topic, ut.topic_id)
    if not t:
        raise HTTPException(404, "Topic asociado no existe")
    return _open_session_core(db, me, ut, t)


@router.post("/slug/{slug}/open")
def open_session_by_slug(
    slug: str,
    reset: bool = False,
    db: Session = Depends(get_db),
    me: User = Depends(get_current_user),
):
    t = db.execute(select(Topic).where(Topic.slug == slug)).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Tema no encontrado")

    ut = db.execute(
        select(UserTopic).where(UserTopic.user_id == me.id, UserTopic.topic_id == t.id)
    ).scalar_one_or_none()
    if not ut:
        raise HTTPException(404, "Aún no añadiste este tema")

    already_completed = (int(ut.progress_pct or 0) >= 100)

    # ⚡ Ruta rápida: si ya lo completó y NO pidió reset → NO generes nada
    if already_completed and not reset:
        return {
            "alreadyCompleted": True,
            "title": t.title,
            "style": ut.recommended_style or (me.vak_style or "visual"),
            "sessionId": None,
            "currentIndex": 0,
            "items": [],
            "progressInSession": 100,
            "explanation": None,
            "explanationAudioUrl": None,
        }

    # Si pidió reset (o no estaba completo), abre/crea sesión normal
    payload = _open_session_core(db, me, ut, t, force_new=reset)
    payload["alreadyCompleted"] = already_completed
    return payload


@router.post("/session/{session_id}/answer")
def answer(session_id: int, body: dict, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    sess = db.get(TopicSession, session_id)
    if not sess or sess.user_id != me.id:
        raise HTTPException(404)

    idx = int(body.get("index", 0))
    total_items = len(sess.items or [])
    if idx < 0 or idx >= total_items:
        raise HTTPException(400, "Índice fuera de rango")
    if idx != int(sess.current_index or 0):
        raise HTTPException(400, "Índice fuera de secuencia")

    answer = body.get("answer")
    item = (sess.items or [])[idx] or {}

    def check(it, ans):
        t = it.get("type")
        if t == "multiple_choice":
            try:
                return int(ans) == int(it.get("correct_index", -1))
            except:
                return False
        if t == "match_pairs":
            return ans == it.get("pairs")
        if t == "drag_to_bucket":
            sol = it.get("solution") or {}
            if not isinstance(ans, dict) or not isinstance(sol, dict):
                return False
            if set(ans.keys()) != set(sol.keys()):
                return False
            for b in sol.keys():
                if set(ans.get(b) or []) != set(sol.get(b) or []):
                    return False
            return True
        return False

    # 🔢 SIEMPRE cuenta intento
    sess.attempts_cnt = int(sess.attempts_cnt or 0) + 1
    sess.results[idx]["attempts"] = int(sess.results[idx].get("attempts") or 0) + 1

    correct = check(item, answer)
    sess.results[idx]["correct"] = bool(correct)

    if not correct:
        # ❌ fallo → suma errores y marca en el ítem (para reglas posteriores)
        item["__wrongAttempts"] = int(item.get("__wrongAttempts") or 0) + 1
        sess.mistakes_cnt = int(sess.mistakes_cnt or 0) + 1
        feedback = item.get("explain", "Revisa el concepto clave y vuelve a intentar.")
    else:
        # ✅ acierto → avanza índice y suma aciertos
        sess.score_raw = int(sess.score_raw or 0) + 1
        sess.current_index = min(int(sess.current_index or 0) + 1, total_items)
        feedback = None

    # 🎯 precisión por intentos
    sess.score_pct = round(100.0 * (int(sess.score_raw or 0) / max(1, int(sess.attempts_cnt or 0))))

    finished = (int(sess.current_index or 0) >= total_items)

    # Recomendación simple
    total_answered = min(int(sess.current_index or 0), total_items)
    wrong = sum(1 for r in (sess.results[:total_answered] if sess.results else []) if r.get("correct") is False)
    recommended = None
    if total_answered >= 5 and (wrong / max(1, total_answered)) > 0.4:
        nxt = {"visual": "auditivo", "auditivo": "kinestesico", "kinestesico": "visual"}
        recommended = nxt.get(sess.style_used, "visual")
        ut = db.execute(
            select(UserTopic).where(UserTopic.user_id == me.id, UserTopic.topic_id == sess.topic_id)
        ).scalar_one_or_none()
        if ut and ut.recommended_style != recommended:
            ut.recommended_style = recommended
            db.add(ut)

    db.add(sess); db.commit(); db.refresh(sess)
    return {
        "correct": correct,
        "feedback": feedback,
        "nextIndex": int(sess.current_index or 0),
        "recommendedStyle": recommended,
        "finished": finished
    }


@router.post("/session/{session_id}/finish")
def finish(session_id: int, body: dict | None = None, db: Session = Depends(get_db), me: User = Depends(get_current_user)):
    """
    Marca la sesión como completada, actualiza estadísticas en UserTopic y otorga puntos.
    body opcional: {"timeSec": number}
    Bonus:
      +50 si NO hubo ningún error (mistakes_cnt == 0)
    """
    sess = db.get(TopicSession, session_id)
    if not sess or sess.user_id != me.id:
        raise HTTPException(404, "Sesión no encontrada")

    total_items = len(sess.items or [])
    if int(sess.current_index or 0) < total_items:
        raise HTTPException(400, "Sesión incompleta")

    # guarda tiempo
    time_sec = 0
    if isinstance(body, dict):
        try:
            time_sec = int(body.get("timeSec") or 0)
        except:
            time_sec = 0
    sess.elapsed_sec = int(time_sec or 0)

    # ---- UserTopic ----
    ut = db.execute(
        select(UserTopic).where(UserTopic.user_id == me.id, UserTopic.topic_id == sess.topic_id)
    ).scalar_one_or_none()
    if not ut:
        raise HTTPException(404, "Tema del usuario no encontrado")

    ut.progress_pct    = 100
    ut.completed_count = int(ut.completed_count or 0) + 1
    ut.attempts_total  = int(ut.attempts_total or 0) + int(sess.attempts_cnt or 0)
    ut.errors_total    = int(ut.errors_total  or 0) + int(sess.mistakes_cnt or 0)
    ut.last_score_pct  = int(sess.score_pct or 0)        # precisión %
    ut.last_time_sec   = int(sess.elapsed_sec or 0)

    if ut.best_score_pct is None or int(sess.score_pct or 0) > int(ut.best_score_pct or 0):
        ut.best_score_pct = int(sess.score_pct or 0)
    if sess.elapsed_sec and (ut.best_time_sec is None or int(sess.elapsed_sec) < int(ut.best_time_sec or 0)):
        ut.best_time_sec = int(sess.elapsed_sec)

    db.add(ut)

    # ---- Puntos / Insignias ----
    awarded = []
    if not bool(sess.points_awarded):
        me_db = db.get(User, me.id)
        old_points = int(me_db.points or 0)

        # Base 100 + bonus “sin errores”
        bonus = 50 if int(sess.mistakes_cnt or 0) == 0 else 0
        new_points = old_points + 100 + bonus
        me_db.points = new_points

        sess.points_awarded = True
        db.add(sess); db.add(me_db)
        db.commit()

        slugs = on_points_changed(db, me.id, old_points=old_points, new_points=new_points)
        if slugs:
            rows = db.execute(select(Badge).where(Badge.slug.in_(slugs))).scalars().all()
            for b in rows:
                owners = db.execute(
                    select(func.count(func.distinct(UserBadge.user_id))).where(UserBadge.badge_id == b.id)
                ).scalar_one() or 0
                total_users = db.execute(select(func.count(User.id))).scalar_one() or 1
                rarity = round(owners * 100.0 / total_users, 2)
                awarded.append({
                    "id": b.id,
                    "slug": b.slug,
                    "title": b.title,
                    "imageUrl": b.image_url,
                    "rarityPct": rarity,
                    "owned": True
                })
    else:
        db.commit()

    total_attempts = int(sess.attempts_cnt or 0)
    total_correct  = int(sess.score_raw or 0)
    mistakes       = int(sess.mistakes_cnt or 0)
    precision_pct  = round(100.0 * total_correct / max(1, total_attempts))

    me_db2 = db.get(User, me.id)
    return {
        "ok": True,
        "awardedBadges": awarded,
        "points": int(me_db2.points or 0),
        "timeSec": int(sess.elapsed_sec or 0),
        "mistakes": mistakes,
        "precisionPct": precision_pct
    }