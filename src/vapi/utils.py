import base64
from io import BytesIO
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

font_size = 20
font_color = (255, 255, 255)
# Define box size and padding
box_width = 50
box_height = 15
padding = 6
num_columns = 3
# Define button colors
button_color = (220, 220, 220)
button_outline = (128, 128, 128)
button_text = (0, 0, 0)
button_highlight = (255, 255, 255)


def img2captcha(
    img: BytesIO, labels: List[str], save_debug: bool = False
) -> Tuple[bytes, dict]:
    image = Image.open(img)
    image = image.resize((image.width // 6, image.height // 6))
    # Create a draw object
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("DejaVuSerif", 8)

    # Loop through strings and draw boxes
    boxes = {}
    for i, string in enumerate(labels):
        row = i // num_columns
        col = i % num_columns
        x = padding + col * (box_width + padding)
        y = image.size[1] - (padding + box_height) * (row + 1)

        button_rect = (x, y, x + box_width, y + box_height)
        draw.rectangle(button_rect, fill=button_color, outline=button_outline)

        text_size = draw.textsize(string, font=font)
        text_x = x + (box_width - text_size[0]) // 2
        text_y = y + (box_height - text_size[1]) // 2
        draw.text((text_x, text_y), string, font=font, fill=button_text)

        boxes[string] = (x, y, x + box_width, y + box_height)

    image_bytes = BytesIO()
    image.save(image_bytes, format="PNG")
    result = image_bytes.getvalue()

    if save_debug:
        with open("output.png", "wb") as f:
            f.write(result)
    return base64.b64encode(result), boxes
