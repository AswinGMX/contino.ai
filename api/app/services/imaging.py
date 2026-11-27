"""Pillow helpers. Captions and boards are drawn in code, never by the image model."""
from __future__ import annotations

import io
import math
import textwrap
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont, ImageOps

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_UPLOAD_PIXELS = 40_000_000

_FONT_CANDIDATES = {
    False: ["DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "Arial.ttf"],
    True: ["DejaVuSans-Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
           "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "Arial Bold.ttf"],
}


@lru_cache(maxsize=32)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for cand in _FONT_CANDIDATES[bold]:
        try:
            return ImageFont.truetype(cand, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def open_image(data: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(data))
    img.load()
    return ImageOps.exif_transpose(img).convert("RGB")


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def validate_upload(data: bytes) -> bytes:
    """Validate an uploaded image and normalise it to PNG. Raises ValueError."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("image is larger than 20 MB")
    Image.MAX_IMAGE_PIXELS = MAX_UPLOAD_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.verify()
        img = open_image(data)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"not a valid image: {exc}") from exc
    if max(img.size) > 2048:
        img.thumbnail((2048, 2048), Image.LANCZOS)
    return to_png(img)


def crop_to_aspect(data: bytes, aspect: tuple[int, int] = (16, 9)) -> bytes:
    img = open_image(data)
    w, h = img.size
    target = aspect[0] / aspect[1]
    if abs(w / h - target) < 0.01:
        return to_png(img)
    if w / h > target:
        nw = int(round(h * target))
        x = (w - nw) // 2
        img = img.crop((x, 0, x + nw, h))
    else:
        nh = int(round(w / target))
        y = (h - nh) // 2
        img = img.crop((0, y, w, y + nh))
    return to_png(img)


def downscale(data: bytes, max_side: int, fmt: str = "JPEG") -> bytes:
    img = open_image(data)
    img.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=85)
    return buf.getvalue()


def _wrap(draw: ImageDraw.ImageDraw, text: str, fnt, max_w: int, max_lines: int) -> list[str]:
    if not text:
        return []
    avg = max(1, draw.textlength("abcdefghijklmnopqrstuvwxyz", font=fnt) / 26)
    width_chars = max(10, int(max_w / avg))
    lines = textwrap.wrap(text, width=width_chars)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1].rstrip(" .,;") + "…"
    return lines


def caption_panel(data: bytes, shot_id: str, action: str, dialogue: str, width: int = 960) -> bytes:
    """Return the panel scaled to `width` with a caption strip: shot ID, action, dialogue."""
    img = open_image(data)
    h = int(round(width * img.height / img.width))
    img = img.resize((width, h), Image.LANCZOS)
    pad = 18
    f_id, f_txt, f_dlg = font(26, True), font(22), font(22)
    probe = ImageDraw.Draw(img)
    action_lines = _wrap(probe, action, f_txt, width - 2 * pad, 3)
    dlg_lines = _wrap(probe, f"“{dialogue}”" if dialogue else "", f_dlg, width - 2 * pad, 2)
    line_h = 30
    cap_h = pad + 34 + line_h * (len(action_lines) + len(dlg_lines)) + pad
    out = Image.new("RGB", (width, h + cap_h), "white")
    out.paste(img, (0, 0))
    d = ImageDraw.Draw(out)
    y = h + pad
    d.text((pad, y), shot_id, fill=(20, 20, 20), font=f_id)
    y += 34
    for ln in action_lines:
        d.text((pad, y), ln, fill=(40, 40, 40), font=f_txt)
        y += line_h
    for ln in dlg_lines:
        d.text((pad, y), ln, fill=(120, 60, 20), font=f_dlg)
        y += line_h
    d.rectangle([0, 0, width - 1, h + cap_h - 1], outline=(30, 30, 30), width=2)
    return to_png(out)


def compose_board(title: str, captioned: list[bytes], columns: int | None = None) -> bytes:
    """Lay captioned panels out on one board, in shot order."""
    if not captioned:
        raise ValueError("no panels to compose")
    imgs = [open_image(b) for b in captioned]
    cols = columns or (2 if len(imgs) <= 4 else 3)
    rows = math.ceil(len(imgs) / cols)
    cell_w = max(i.width for i in imgs)
    row_heights = [max(i.height for i in imgs[r * cols:(r + 1) * cols]) for r in range(rows)]
    gap, margin, header = 24, 40, 90
    W = margin * 2 + cols * cell_w + (cols - 1) * gap
    H = margin * 2 + header + sum(row_heights) + (rows - 1) * gap
    board = Image.new("RGB", (W, H), (245, 243, 238))
    d = ImageDraw.Draw(board)
    d.text((margin, margin), title, fill=(20, 20, 20), font=font(44, True))
    y = margin + header
    for r in range(rows):
        x = margin
        for img in imgs[r * cols:(r + 1) * cols]:
            board.paste(img, (x, y))
            x += cell_w + gap
        y += row_heights[r] + gap
    return to_png(board)


SHEET_LAYOUTS = {1: (1, 1), 2: (2, 1), 3: (3, 1), 4: (2, 2), 5: (3, 2), 6: (3, 2), 7: (4, 2), 8: (4, 2),
                 9: (3, 3), 10: (4, 3), 11: (4, 3), 12: (4, 3)}


def sheet_layout(n: int) -> tuple[int, int]:
    """(columns, rows) for an expression sheet with n cells."""
    if n in SHEET_LAYOUTS:
        return SHEET_LAYOUTS[n]
    cols = math.ceil(math.sqrt(n * 1.5))
    return cols, math.ceil(n / cols)


def crop_grid(data: bytes, cols: int, rows: int, n: int, inset: float = 0.02) -> list[bytes]:
    """Split a cols x rows sheet into its first n cells (left-to-right, top-to-bottom).

    A small inset trims any gutter or neighbour bleed at the cell edges.
    """
    img = open_image(data)
    cw, ch = img.width / cols, img.height / rows
    dx, dy = cw * inset, ch * inset
    out = []
    for i in range(n):
        r, c = divmod(i, cols)
        box = (int(c * cw + dx), int(r * ch + dy), int((c + 1) * cw - dx), int((r + 1) * ch - dy))
        out.append(to_png(img.crop(box)))
    return out
