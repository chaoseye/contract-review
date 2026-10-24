"""Подготовка картинок для протокола: прозрачный фон у сканов, обрезка пустых краёв, поворот печати."""

import hashlib
import io
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageMath, ImageOps, ImageStat

STAMP_OPACITY = 0.85
STAMP_MIN_ANGLE = 2.0
STAMP_MAX_ANGLE = 6.0
MAX_SIDE_PX = 1200
# Бумага на скане не бывает чисто белой: всё, что светлее этого уровня по каждому каналу, — фон.
PAPER_LEVEL = 235
# Почти прозрачное: такие края обрезаются, такой фон не закрывает текст под картинкой.
FAINT_ALPHA = 16


def _load_rgba(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    scale = max(image.size) / MAX_SIDE_PX
    if scale > 1:
        # Большой JPEG читается сразу уменьшенным, но не меньше нужного: так в несколько раз быстрее.
        image.draft(None, (int(image.width / scale), int(image.height / scale)))
    image.load()
    # Фото с телефона хранит поворот в EXIF: браузер его учитывает, протокол — тоже.
    image = ImageOps.exif_transpose(image).convert("RGBA")
    if max(image.size) > MAX_SIDE_PX:
        image.thumbnail((MAX_SIDE_PX, MAX_SIDE_PX), Image.Resampling.LANCZOS)
    return image


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _trim(image: Image.Image) -> Image.Image:
    box = image.getchannel("A").point(lambda alpha: 255 if alpha > FAINT_ALPHA else 0).getbbox()
    return image.crop(box) if box else image


def _unmixed(args: dict[str, Any]) -> Any:
    """Канал цвета чернил без примеси бумаги: скан = alpha × цвет + (1 − alpha) × белый."""
    scan, alpha = args["float"](args["scan"]), args["float"](args["alpha"])
    return args["convert"](255 - (255 - scan) * 255 / args["max"](alpha, 1), "L")


def _paper_to_alpha(image: Image.Image) -> Image.Image:
    """Скан без прозрачности (JPEG, PNG без прозрачного фона) — как чернила на прозрачной плёнке.

    Белый и почти белый фон становится прозрачным, чернила — непрозрачными по своей густоте, а цвет
    пересчитывается так, что поверх белого картинка выглядит как скан. Линия подписи и «М.П.» видны сквозь
    бумагу, как под настоящими подписью и оттиском. Картинку, где уже есть прозрачность, не трогаем.
    """
    if image.getchannel("A").getextrema()[0] < 255:
        return image
    scan = image.convert("RGB").point(lambda value: min(255, value * 255 // PAPER_LEVEL))
    red, green, blue = scan.split()
    alpha = ImageChops.invert(ImageChops.darker(ImageChops.darker(red, green), blue))
    colors = [ImageMath.lambda_eval(_unmixed, scan=channel, alpha=alpha) for channel in (red, green, blue)]
    return Image.merge("RGBA", (*colors, alpha))


def covers_text(data: bytes) -> bool:
    """Фон картинки и после подготовки не прозрачный: в протоколе он закроет линию подписи и «М.П.».

    Так бывает у фото на серой бумаге и у PNG с непрозрачным цветным фоном. Судим по краю картинки: там
    бумага, даже если росчерк или оттиск местами касаются края.
    """
    alpha = _paper_to_alpha(_load_rgba(data)).getchannel("A")
    frame = Image.new("L", alpha.size, 0)
    ImageDraw.Draw(frame).rectangle((0, 0, alpha.width - 1, alpha.height - 1), outline=255)
    return ImageStat.Stat(alpha, frame).median[0] > FAINT_ALPHA


def stamp_angle(seed: str) -> float:
    """Угол поворота печати: от 2 до 6°, постоянный для одного договора — повторный выпуск выглядит так же."""
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    fraction = int.from_bytes(digest[:4], "big") / 0xFFFFFFFF
    return STAMP_MIN_ANGLE + (STAMP_MAX_ANGLE - STAMP_MIN_ANGLE) * fraction


def prepare_signature(data: bytes) -> tuple[bytes, float]:
    """PNG подписи с прозрачным фоном, без пустых полей, и отношение высоты к ширине."""
    image = _trim(_paper_to_alpha(_load_rgba(data)))
    return _png(image), image.height / image.width


def prepare_stamp(data: bytes, seed: str) -> tuple[bytes, float]:
    """PNG печати: фон прозрачный, оттиск повёрнут, непрозрачность 85 %; и отношение высоты к ширине."""
    image = _trim(_paper_to_alpha(_load_rgba(data)))
    # Поворот против часовой стрелки на небольшой угол, как у оттиска, поставленного от руки.
    image = image.rotate(stamp_angle(seed), resample=Image.Resampling.BICUBIC, expand=True)
    alpha = image.getchannel("A").point(lambda value: int(value * STAMP_OPACITY))
    image.putalpha(alpha)
    image = _trim(image)
    return _png(image), image.height / image.width


def image_ratio(data: bytes) -> float:
    image = Image.open(io.BytesIO(data))
    return image.height / image.width
