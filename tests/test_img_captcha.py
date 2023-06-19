import base64
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFont
from twocaptcha import TwoCaptcha

# URL of the image
image_url = "https://storage.googleapis.com/dream-machines-output/37ec125c-ace6-41dc-b74a-b5c0770a9fb3/0_0.png"

# List of strings
strings = ["mashrum", "forest", "monkey", "city", "cat"]

# Font size and color
font_size = 20
font_color = (255, 255, 255)

# Load the image from the URL
response = requests.get(image_url)

image = Image.open(BytesIO(response.content))
image = image.resize((image.width // 6, image.height // 6))
# Create a draw object
draw = ImageDraw.Draw(image)

# Get the size of the image
width, height = image.size

font = ImageFont.truetype("Helvetica", 8)

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
num_rows = (len(strings) + num_columns - 1) // num_columns

# Loop through strings and draw boxes
boxes = {}
for i, string in enumerate(strings):
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
image_bytes = image_bytes.getvalue()
with open("output.png", "wb") as f:
    f.write(image_bytes)

# Print the dictionary of text and its boxes' coordinates
print(boxes)


solver = TwoCaptcha("45941e63a623fe175d6bde370db7ce85")
b = base64.b64encode(image_bytes)
res = solver.coordinates(
    b.decode("utf-8"),
    hintText="Please select which description best fits the image",
    lang="en",
)
print(res)
if "code" in res and ":" in res["code"]:
    coords = res["code"].split(":")[1]
    x, y = coords.split(",")
    _, x = x.split("=")
    _, y = y.split("=")
    for k, v in boxes.items():
        if (v[0] < int(x) < v[2]) and (v[1] < int(y) < v[3]):
            print(k)
            break
