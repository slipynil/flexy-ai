import base64

FILE_PATH = "./data/generated/"

def save_base64_image(image_data, output_filename="output.jpg") -> str:
    output_filepath = FILE_PATH + output_filename
    # Если строка содержит префикс вроде "data:image/jpeg;base64,...", его нужно отрезать:
    if "," in image_data:
        image_data = image_data.split(",")[1]

    # Декодируем Base64 в байты
    image_bytes = base64.b64decode(image_data)

    # Сохраняем байты как картинку
    with open(output_filepath, "wb") as f:
        f.write(image_bytes)
    return output_filepath
