import base64
import io

from PIL import Image


def convert_image_to_base64(image_path: str, quality: int = 85) -> tuple[str, int, int]:
    """
    Открывает изображение любого формата, конвертирует в JPEG,
    возвращает строку base64 для отправки на сервер, а также ширину и высоту.
    """
    with Image.open(image_path) as img:
        # Обработка прозрачности и цветовых режимов для JPEG
        if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.convert("RGBA").split()[3])
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        img = _resize_image(img)
        width, height = img.size

        # Сохранение в буфер памяти
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=quality)

        image_bytes = buffer.getvalue()
        image = base64.b64encode(image_bytes).decode('utf-8')

        return image, width, height

def _resize_image(img: Image.Image, min_size: int = 512, max_size: int = 1536) -> Image.Image:
    width, height = img.size

    # 1. Если картинка меньше минимального размера, пропорционально увеличиваем её
    if width < min_size or height < min_size:
        ratio = min_size / min(width, height)
        new_width = int(width * ratio)
        new_height = int(height * ratio)
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        width, height = img.size

    # 2. Если картинка больше максимального размера, пропорционально уменьшаем её
    elif width > max_size or height > max_size:
        ratio = max_size / max(width, height)
        new_width = int(width * ratio)
        new_height = int(height * ratio)
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
        width, height = img.size

    # 3. Подгоняем точный размер под кратность 8 (требование многих нейросетей)
    new_width = (width // 8) * 8
    new_height = (height // 8) * 8

    if new_width != width or new_height != height:
        img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

    return img
