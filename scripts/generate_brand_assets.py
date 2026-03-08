from __future__ import annotations

import os
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

SIZE = 1024
BG_TOP = (47, 196, 117)
BG_MID = (23, 155, 84)
BG_BOTTOM = (12, 111, 59)
WHITE = (255, 255, 255, 255)
SOFT_WHITE = (238, 251, 244, 230)
PALE_WHITE = (230, 247, 237, 180)
ACCENT = (20, 143, 76, 255)
SHADOW = (10, 74, 41, 96)


def load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates: list[Path] = []
    windir = os.environ.get("WINDIR")
    if windir:
        fonts_dir = Path(windir) / "Fonts"
        candidates.extend(
            [
                fonts_dir / ("segoeuib.ttf" if bold else "segoeui.ttf"),
                fonts_dir / ("arialbd.ttf" if bold else "arial.ttf"),
            ]
        )

    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def vertical_gradient(width: int, height: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    image = Image.new("RGBA", (width, height))
    pixels = image.load()
    for y in range(height):
        ratio = y / max(height - 1, 1)
        if ratio < 0.52:
            mix = ratio / 0.52
            color = tuple(int(top[i] * (1 - mix) + BG_MID[i] * mix) for i in range(3))
        else:
            mix = (ratio - 0.52) / 0.48
            color = tuple(int(BG_MID[i] * (1 - mix) + bottom[i] * mix) for i in range(3))
        for x in range(width):
            pixels[x, y] = color + (255,)
    return image


def rounded_gradient_card(width: int, height: int, radius: int) -> Image.Image:
    base = vertical_gradient(width, height, BG_TOP, BG_BOTTOM)
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)

    card = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    card.paste(base, (0, 0), mask)

    border = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(border).rounded_rectangle((3, 3, width - 4, height - 4), radius=max(radius - 8, 8), outline=(255, 255, 255, 42), width=4)

    glow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((14, 6, int(width * 0.95), int(height * 0.44)), fill=(169, 255, 211, 54))
    glow = glow.filter(ImageFilter.GaussianBlur(max(width, height) // 16))

    result = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    result.alpha_composite(card)
    result.alpha_composite(glow)
    result.alpha_composite(border)
    return result


def draw_book_icon(size: int) -> Image.Image:
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon)
    scale = size / 1024

    def pt(x: int, y: int) -> tuple[int, int]:
        return round(x * scale), round(y * scale)

    width_frame = max(4, round(28 * scale))
    width_center = max(4, round(22 * scale))
    width_line = max(3, round(22 * scale))
    width_outline = max(2, round(10 * scale))

    draw.line([pt(230, 330), pt(230, 612), pt(440, 612)], fill=WHITE, width=width_frame, joint="curve")
    draw.line([pt(794, 330), pt(794, 612), pt(584, 612)], fill=WHITE, width=width_frame, joint="curve")

    left_page = [pt(278, 252), pt(430, 252), pt(430, 346), pt(392, 312), pt(356, 346), pt(356, 590), pt(316, 584), pt(278, 578)]
    draw.polygon(left_page, fill=WHITE)
    draw.rectangle((*pt(278, 252), *pt(356, 578)), fill=WHITE)
    draw.polygon([pt(356, 578), pt(430, 590), pt(490, 652), pt(356, 606)], fill=WHITE)

    right_page = [pt(536, 326), pt(576, 288), pt(650, 264), pt(724, 252), pt(724, 582), pt(690, 586), pt(650, 592), pt(604, 604), pt(536, 664)]
    draw.polygon(right_page, fill=WHITE)
    draw.rectangle((*pt(536, 326), *pt(724, 582)), fill=WHITE)

    draw.line((*pt(512, 330), *pt(512, 664)), fill=WHITE, width=width_center)
    draw.polygon([pt(356, 252), pt(430, 252), pt(430, 342), pt(393, 308), pt(356, 342)], fill=ACCENT)

    line_color = (18, 140, 74, 255)
    draw.arc((*pt(575, 335), *pt(742, 382)), start=188, end=332, fill=line_color, width=width_line)
    draw.arc((*pt(570, 409), *pt(742, 458)), start=188, end=332, fill=line_color, width=width_line)
    draw.arc((*pt(565, 483), *pt(734, 532)), start=188, end=332, fill=line_color, width=width_line)
    draw.arc((*pt(582, 553), *pt(708, 594)), start=188, end=325, fill=line_color, width=width_line)

    draw.arc((*pt(312, 584), *pt(494, 684)), start=208, end=300, fill=PALE_WHITE, width=width_outline)
    draw.arc((*pt(530, 584), *pt(712, 684)), start=240, end=332, fill=PALE_WHITE, width=width_outline)
    return icon


def render_app_icon() -> Image.Image:
    background = rounded_gradient_card(SIZE, SIZE, 164)
    book = draw_book_icon(SIZE)

    shadow_alpha = book.getchannel("A").filter(ImageFilter.GaussianBlur(16))
    shadow = Image.new("RGBA", (SIZE, SIZE), SHADOW)
    shadow.putalpha(shadow_alpha)
    shadow = ImageChops.offset(shadow, 0, 18)

    result = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    result.alpha_composite(background)
    result.alpha_composite(shadow)
    result.alpha_composite(book)
    return result


def _measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def render_wizard_sidebar() -> Image.Image:
    width, height = 164, 314
    canvas = rounded_gradient_card(width, height, 28)
    draw = ImageDraw.Draw(canvas)

    badge = Image.new("RGBA", (104, 104), (0, 0, 0, 0))
    badge_draw = ImageDraw.Draw(badge)
    badge_draw.rounded_rectangle((0, 0, 103, 103), radius=28, fill=(19, 127, 69, 255))
    badge_draw.rounded_rectangle((1, 1, 102, 102), radius=27, outline=(255, 255, 255, 34), width=2)

    icon = render_app_icon().resize((72, 72), Image.LANCZOS)
    badge.alpha_composite(icon, (16, 16))
    canvas.alpha_composite(badge, (30, 36))

    title_font = load_font(22, bold=True)
    subtitle_font = load_font(13, bold=False)
    pill_font = load_font(12, bold=True)

    draw.text((24, 160), "GZHReader", fill=WHITE, font=title_font)
    draw.text((24, 194), "RSS Daily Briefing", fill=SOFT_WHITE, font=subtitle_font)
    draw.text((24, 214), "for WeChat subscriptions", fill=PALE_WHITE, font=subtitle_font)

    pill_x, pill_y, pill_w, pill_h = 24, 252, 116, 30
    draw.rounded_rectangle((pill_x, pill_y, pill_x + pill_w, pill_y + pill_h), radius=15, fill=(255, 255, 255, 34), outline=(255, 255, 255, 48), width=1)
    pill_text = "zhiwuyazhe_fjr"
    tw, th = _measure(draw, pill_text, pill_font)
    draw.text((pill_x + (pill_w - tw) / 2, pill_y + (pill_h - th) / 2 - 1), pill_text, fill=WHITE, font=pill_font)

    draw.line((20, 296, 144, 296), fill=(255, 255, 255, 40), width=2)
    return canvas.convert("RGB")


def render_wizard_small() -> Image.Image:
    size = 55
    canvas = rounded_gradient_card(size, size, 14)
    icon = render_app_icon().resize((34, 34), Image.LANCZOS)
    canvas.alpha_composite(icon, (10, 10))
    return canvas.convert("RGB")


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    assets = root / "packaging" / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    image = render_app_icon()
    png_path = assets / "gzhreader-icon.png"
    ico_path = assets / "gzhreader.ico"
    wizard_path = assets / "wizard-sidebar.bmp"
    wizard_small_path = assets / "wizard-small.bmp"

    image.save(png_path)
    image.save(ico_path, sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    render_wizard_sidebar().save(wizard_path)
    render_wizard_small().save(wizard_small_path)

    print(png_path)
    print(ico_path)
    print(wizard_path)
    print(wizard_small_path)


if __name__ == "__main__":
    main()
