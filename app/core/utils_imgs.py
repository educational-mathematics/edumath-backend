# app/core/utils_imgs.py
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import re, os, io, math, random

from app.core.settings_static import GEN_DIR, static_url_for

# --- Utilitario: primer "a/b" en un texto ---
_FRAC_RE = re.compile(r"(\d+)\s*/\s*(\d+)")

def find_first_fraction(text: str) -> tuple[int,int] | None:
    if not text:
        return None
    m = _FRAC_RE.search(text)
    if not m:
        return None
    a = int(m.group(1)); b = int(m.group(2))
    if b == 0: 
        return None
    return (a, b)

# --- PNG con barritas de fracción: d celdas, n coloreadas ---
def _draw_fraction_bar_png(n: int, d: int, size=(1200, 360)) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size, color=(255,255,255))
    draw = ImageDraw.Draw(img)
    margin, gap = 40, 8
    cell_w = (w - 2*margin - (d-1)*gap) / d
    cell_h = 140
    y = h//2 - cell_h//2

    # marco
    draw.rounded_rectangle([20, 20, w-20, h-20], radius=24, outline=(229,231,235), width=2, fill=(255,255,255))

    for k in range(d):
        x = margin + k*(cell_w+gap)
        fill = (96,165,250) if k < n else (229,231,235)
        draw.rectangle([x, y, x+cell_w, y+cell_h], fill=fill, outline=(31,41,55), width=2)

    # texto n/d
    label = f"{n}/{d}"
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except:
        font = None

    if font:
        bbox = draw.textbbox((0, 0), label, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    else:
        # fallback sin font → valores aproximados
        tw, th = (len(label) * 20, 40)

    draw.text(((w-tw)/2, y+cell_h+24), label, fill=(17,24,39), font=font)
    return img

def ensure_fraction_png(n: int, d: int, name: str) -> str:
    """
    Genera si no existe: /static/gen/{name}.png con barra n/d.
    Devuelve URL servible /static/gen/....
    """
    if d <= 0:
        d = 1
    n = max(0, min(n, d))
    out = (GEN_DIR / f"{name}.png").resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        img = _draw_fraction_bar_png(n, d)
        img.save(out, "PNG")
    return static_url_for(out)  # -> "/static/gen/....png"

def make_explanation_figure_png(topic_id: int, user_id: int, base_text: str | None = None) -> str:
    """
    Hace una figura PNG didáctica para la explicación inicial.
    1) Si encuentra la primera fracción en base_text, la dibuja (barra).
    2) Si no, usa una fracción simple aleatoria como 3/5, 2/3, etc.
    Cachea por (user_id, topic_id).
    """
    name = f"expl-{user_id}-{topic_id}"
    out = (GEN_DIR / f"{name}.png").resolve()
    if out.exists():
        return static_url_for(out)

    # elegir fracción
    frac = find_first_fraction(base_text or "")
    if not frac:
        # fallback simple
        den = random.randint(4, 8)
        num = random.randint(1, den-1)
        frac = (num, den)
    n, d = frac

    img = _draw_fraction_bar_png(n, d, size=(1200, 400))

    # título sutil
    draw = ImageDraw.Draw(img)
    try:
        font_t = ImageFont.truetype("arial.ttf", 35)
        font_s = ImageFont.truetype("arial.ttf", 20)
    except:
        font_t = font_s = None

    title = "Fracciones: partes de un todo"
    sub   = "El numerador (arriba) indica cuántas partes tomamos; el denominador (abajo), en cuántas partes iguales está dividido el total."
    draw.text((40, 30), title, fill=(30,58,138), font=font_t)
    draw.text((40, 90), sub,   fill=(55,65,81), font=font_s)

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    return static_url_for(out)

def decorate_visuals_for_items(items: list, topic_id: int, user_id: int) -> None:
    """
    Para cada MCQ, si detecta fracciones, genera un PNG (no SVG) y setea it['imageUrl'].
    """
    for i, it in enumerate(items or []):
        if (it or {}).get("type") != "multiple_choice":
            continue

        q = (it.get("question") or "")
        # si ya tiene imageUrl, respeta
        if it.get("imageUrl"):
            continue

        # busca fracción en enunciado o en la opción correcta
        m = _FRAC_RE.search(q)
        if not m:
            ch = it.get("choices") or []
            ci = it.get("correct_index")
            if isinstance(ci, int) and 0 <= ci < len(ch):
                m = _FRAC_RE.search(str(ch[ci]) or "")

        if m:
            n, d = int(m.group(1)), int(m.group(2))
            it["imageUrl"] = ensure_fraction_png(n, d, name=f"qfrac-{user_id}-{topic_id}-{i}")

def pick_visual_expl_image_from_ctx(ctx: dict) -> str | None:
    """
    Usa una imagen declarada en el JSON si existe.
    """
    imgs = (ctx.get("visual_assets") or {}).get("images") or []
    if imgs:
        url = imgs[0]
        return url if url.startswith("/") else f"/static/{url.lstrip('/')}"
    cover = (ctx.get("cover") or "").strip()
    if cover:
        return cover if cover.startswith("/") else f"/static/{cover.lstrip('/')}"
    return None

def save_png_return_url(topic_slug: str, png_bytes: bytes) -> str:
    """
    Guarda bytes PNG en /static/gen y devuelve URL servible.
    """
    from app.core.settings_static import STATIC_DIR
    gen = (STATIC_DIR / "gen").resolve()
    gen.mkdir(parents=True, exist_ok=True)
    name = f"{topic_slug}-{random.randint(10_000, 99_999)}.png"
    out = (gen / name).resolve()
    out.write_bytes(png_bytes)
    return f"/static/gen/{name}"
