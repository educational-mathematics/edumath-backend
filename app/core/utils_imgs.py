# app/core/utils_imgs.py
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import re, os, io, math, random

from app.core.settings_static import GEN_DIR, static_url_for

# --- patrones ---
_FRAC_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
_PCT_RE  = re.compile(r"(\d+(?:\.\d+)?)\s*%")

_NL_DEN_NUM_RE = re.compile(
    r"dividid[oa]\s+en\s+(\d+)\s+partes?.*?(\d+)\s+parte[s]?\s+(?:sombread|coloread)",
    re.IGNORECASE
)
_NL_NUM_DEN_RE = re.compile(
    r"(\d+)\s+de\s+(\d+)\s+(?:partes?|secciones?|porciones?).*?(?:sombread|coloread)",
    re.IGNORECASE
)

def _draw_fraction_bar_png(n: int, d: int, size=(1200, 400)) -> Image.Image:
    # (igual a tu versión actual de barras, con borde redondeado)
    w, h = size
    img = Image.new("RGB", size, color=(255,255,255))
    draw = ImageDraw.Draw(img)
    margin, gap = 40, 8
    cell_h = 140
    y = h//2 - cell_h//2
    den = max(1, d)
    cell_w = (w - 2*margin - (den-1)*gap) / den

    draw.rounded_rectangle([20, 20, w-20, h-20], radius=24, outline=(229,231,235), width=2, fill=(255,255,255))

    for k in range(den):
        x = margin + k*(cell_w+gap)
        fill = (96,165,250) if k < n else (229,231,235)
        draw.rectangle([x, y, x+cell_w, y+cell_h], fill=fill, outline=(31,41,55), width=2)

    label = f"{max(0,min(n,den))}/{den}"
    try:
        font = ImageFont.truetype("arial.ttf", 48)
    except:
        font = None
    tw, th = (draw.textbbox((0,0), label, font=font)[2:] if font else (len(label)*20, 40))
    draw.text(((w-tw)/2, y+cell_h+24), label, fill=(17,24,39), font=font)
    return img

def _draw_percent_grid_png(percent: int, size=(1200, 400)) -> Image.Image:
    """Rejilla 10×10; sombrea `percent` celdas (por filas)."""
    percent = max(0, min(100, percent))
    w, h = size
    img = Image.new("RGB", size, color=(255,255,255))
    draw = ImageDraw.Draw(img)

    draw.rounded_rectangle([20, 20, w-20, h-20], radius=24, outline=(229,231,235), width=2, fill=(255,255,255))

    grid_w, grid_h = w - 120, h - 180
    left, top = 60, 140
    cols = rows = 10
    gap = 2
    cw = (grid_w - (cols-1)*gap) / cols
    ch = (grid_h - (rows-1)*gap) / rows

    filled = round(percent)  # número de celdas a sombrear
    k = 0
    for r in range(rows):
        for c in range(cols):
            x = left + c*(cw+gap)
            y = top  + r*(ch+gap)
            fill = (96,165,250) if k < filled else (229,231,235)
            draw.rectangle([x, y, x+cw, y+ch], fill=fill, outline=(31,41,55), width=2)
            k += 1
    return img

def _draw_title_sub(img: Image.Image, title: str, sub: str):
    draw = ImageDraw.Draw(img)
    try:
        font_t = ImageFont.truetype("arial.ttf", 35)
        font_s = ImageFont.truetype("arial.ttf", 20)
    except:
        font_t = font_s = None
    draw.text((40, 30), title, fill=(30,58,138), font=font_t)
    draw.text((40, 90), sub,   fill=(55,65,81), font=font_s)
    
def ensure_fraction_png(n: int, d: int, name: str) -> str:
    d = max(1, int(d)); n = max(0, min(int(n), d))
    out = (GEN_DIR / f"{name}.png").resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        img = _draw_fraction_bar_png(n, d)
        img.save(out, "PNG")
    return static_url_for(out)

def ensure_percent_png(pct: float, name: str) -> str:
    out = (GEN_DIR / f"{name}.png").resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        img = _draw_percent_grid_png(float(pct))
        img.save(out, "PNG")
    return static_url_for(out)

# ------------- EXPLANATION FIGURE (topic-aware) -------------
def make_explanation_figure_png(topic_slug: str, topic_id: int, user_id: int, base_text: str | None = None) -> str:
    """
    Genera una figura didáctica CONSISTENTE por tema y usuario (sin IA).
    Cachea por (user_id, topic_slug). Nunca se sobreescribe si ya existe.
    """
    name = f"expl-{user_id}-{topic_slug}.png"
    out = (GEN_DIR / name).resolve()
    if out.exists():
        return static_url_for(out)

    # --- Selección por tema (slug) ---
    slug = (topic_slug or "").strip().lower()
    if slug == "fracciones-basicas":
        # 1) intenta extraer primera a/b; 2) fallback 3/5
        n = d = None
        m = _FRAC_RE.search(base_text or "")
        if m:
            n, d = int(m.group(1)), int(m.group(2))
        if not m or d == 0:
            d = random.randint(4, 8)
            n = random.randint(1, d-1)
        img = _draw_fraction_bar_png(n, d)
        _draw_title_sub(
            img,
            "Fracciones: partes de un todo",
            "El numerador (arriba) indica cuántas partes tomamos; el denominador (abajo), en cuántas partes iguales está dividido el total."
        )

    elif slug == "porcentajes":
        # elige un % representativo para la portada (p.ej., 50%)
        img = _draw_percent_grid_png(50)
        _draw_title_sub(
            img,
            "Porcentajes: partes de cien",
            "Un porcentaje N% significa N de cada 100. 50% = mitad; 25% = un cuarto; 10% = diez de cien."
        )
    else:
        # Fallback genérico: barra 1/2 con título neutro
        img = _draw_fraction_bar_png(1, 2)
        _draw_title_sub(
            img,
            "Concepto: representación visual",
            "Figura de apoyo generada automáticamente."
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "PNG")
    return static_url_for(out)

# ------------- Visual helpers per item ----------------------
def decorate_visuals_for_items(items: list, topic_id: int, user_id: int, topic_slug: str | None = None) -> None:
    """
    Genera imagen de apoyo por pregunta SOLO en la primera pasada.
    - Si hay fracción a/b -> barra n/d
    - Si hay porcentaje N% -> grilla 10x10 con N celdas
    """
    for i, it in enumerate(items or []):
        if (it or {}).get("type") != "multiple_choice":
            continue
        if it.get("imageUrl"):
            continue

        # 1) fracción en pregunta o en la correcta
        q = (it.get("question") or "")
        m = _FRAC_RE.search(q)
        if not m:
            ch = it.get("choices") or []
            ci = it.get("correct_index")
            if isinstance(ci, int) and 0 <= ci < len(ch):
                m = _FRAC_RE.search(str(ch[ci]) or "")

        if m:
            n, d = int(m.group(1)), int(m.group(2))
            it["imageUrl"] = ensure_fraction_png(
                max(0, min(n, d)), max(1, d),
                name=f"qfrac-{user_id}-{topic_id}-{i}"
            )
            continue

        # 2) porcentaje en pregunta o en la correcta
        mp = _PCT_RE.search(q)
        if not mp:
            ch = it.get("choices") or []
            ci = it.get("correct_index")
            if isinstance(ci, int) and 0 <= ci < len(ch):
                mp = _PCT_RE.search(str(ch[ci]) or "")
        if mp:
            pct = float(mp.group(1))
            it["imageUrl"] = ensure_percent_png(
                pct, name=f"qpct-{user_id}-{topic_id}-{i}"
            )

# ------------- pick image from JSON context -----------------
def pick_visual_expl_image_from_ctx(ctx: dict) -> str | None:
    imgs = (ctx.get("visual_assets") or {}).get("images") or []
    if imgs:
        u = imgs[0]
        return u if u.startswith("/") else f"/static/{u.lstrip('/')}"
    cover = (ctx.get("cover") or "").strip()
    if cover:
        return cover if cover.startswith("/") else f"/static/{cover.lstrip('/')}"
    return None

# ------------- save raw PNG bytes ---------------------------
def save_png_return_url(topic_slug: str, png_bytes: bytes) -> str:
    name = f"{topic_slug}-{random.randint(10_000, 99_999)}.png"
    out = (GEN_DIR / name).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png_bytes)
    return static_url_for(out)
