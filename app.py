from flask import Flask
import os
from dotenv import load_dotenv
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps
from svglib.svglib import svg2rlg
from reportlab.graphics.renderPM import drawToPIL
from io import BytesIO

load_dotenv()

# Get the VU Key. See https://docs.vudials.com/
VU_Key = os.getenv("VU_KEY")
r = requests.get(f"http://localhost:5340/api/v0/dial/list", params={"key": VU_Key})
data = r.json()
# Just doing this via a hard code to start.
dial_UID = data["data"][0]["uid"]

# Location load. Requires 4 digits after the decimal.
lat = os.getenv("LOCATION_LAT")
lon = os.getenv("LOCATION_LON")

# Weather.gov API URL. See https://www.weather.gov/documentation/services-web-api for more information.
weatherGovURL = "https://api.weather.gov/"
r = requests.get(f"https://api.weather.gov/points/{lat},{lon}")
data = r.json()
forcastURL = data["properties"]["forecast"]
name = f'{data["properties"]["relativeLocation"]["properties"]["city"]}'

# NYC ranges from  8°F (−13 °C) and 97 °F. Ideally I would set this based on the location.
# Gotta find a free API for that one.
cityLow = 8
cityHigh = 97
cityUnit = "°F"

# Load in svg icon. This is from the font awesome free pack. See https://fontawesome.com/
weather_icon_path = "assets/images/temperature-half.svg"


def generateScale(low, high, unit, name, icon_path):
    img = Image.open("assets/images/blank.png")
    font = ImageFont.truetype("assets/font/Menlo Powerline.ttf", 12)
    _, _, w, h = ImageDraw.Draw(img).textbbox((0, 0), name, font=font)

    icon = svg2rlg(icon_path)
    icon = drawToPIL(
        icon,
    )

    icon = ImageOps.contain(icon, ((round(img.height / 4), round(img.height / 4))))
    icon = ImageOps.invert(icon)

    ImageDraw.Draw(img).text(
        (0, img.height - 14),
        fill="black",
        font=font,
        text=f"{low}{unit}",
    )
    ImageDraw.Draw(img).text(
        (img.width - 30, img.height - 14), fill="black", font=font, text=f"{high}{unit}"
    )
    ImageDraw.Draw(img).text(
        ((img.width - w) / 2, icon.height + 10),
        fill="black",
        font=font,
        text=name,
    )
    img.paste(icon, (round((img.width - icon.width) / 2), 5))

    image_file = BytesIO()
    img.save(image_file, "png")
    image_file.seek(0)
    return image_file


# Set Scale for first Dial on server boot.
weatherScale = generateScale(cityLow, cityHigh, cityUnit, name, weather_icon_path)
r = requests.post(
    f"http://localhost:5340/api/v0/dial/{dial_UID}/image/set",
    params={"key": VU_Key, "imgfile": "my_awesome_image.png"},
    files={"imgfile": weatherScale},
)


app = Flask(__name__)


@app.route("/")
@app.route("/updateWeather")
def home():
    r = requests.get(forcastURL)
    data = r.json()
    temp = data["properties"]["periods"][0]["temperature"]
    value = (temp - cityLow) / (cityHigh - cityLow) * 100
    r = requests.get(
        f"http://localhost:5340/api/v0/dial/{dial_UID}/set",
        params={"key": VU_Key, "value": value},
    )

    return r.json()
