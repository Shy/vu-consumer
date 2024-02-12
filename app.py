from flask import Flask
import os
import datetime
from dotenv import load_dotenv
import requests
import pendulum
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from PIL import Image, ImageDraw, ImageFont, ImageOps
from svglib.svglib import svg2rlg
from reportlab.graphics.renderPM import drawToPIL
from flask_apscheduler import APScheduler
from io import BytesIO

load_dotenv()


class Config:
    SCHEDULER_API_ENABLED = True


SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

# Get the VU Key. See https://docs.vudials.com/
VU_Key = os.getenv("VU_KEY")
r = requests.get(f"http://localhost:5340/api/v0/dial/list", params={"key": VU_Key})
data = r.json()

# Just doing this via a hard code to start.
weather_dial_UID = data["data"][0]["uid"]
cal_dial_UID = data["data"][1]["uid"]

# Location load. Requires 4 digits after the decimal.
lat = os.getenv("LOCATION_LAT")
lon = os.getenv("LOCATION_LON")

# Weather.gov API URL. See https://www.weather.gov/documentation/services-web-api for more information.
weatherGovURL = "https://api.weather.gov/"
r = requests.get(f"https://api.weather.gov/points/{lat},{lon}")
data = r.json()
forcastURL = data["properties"]["forecast"]
name = f'{data["properties"]["relativeLocation"]["properties"]["city"]}'

# NYC ranges from  8°F (−13 °C) and 97 °F. Ideally I .would set this based on the location.
# Gotta find a free API for that one.
cityLow = 8
cityHigh = 97
cityUnit = "°F"

# Load in svg icon. This is from the font awesome free pack. See https://fontawesome.com/
weather_icon_path = "assets/images/temperature-half.svg"
cal_icon_path = "assets/images/cal.svg"
clock_icon_path = "assets/images/clock.svg"


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
    f"http://localhost:5340/api/v0/dial/{weather_dial_UID}/image/set",
    params={"key": VU_Key, "imgfile": "my_awesome_image.png"},
    files={"imgfile": weatherScale},
)

app = Flask(__name__)
app.config.from_object(Config())
scheduler = APScheduler()
scheduler.init_app(app)


# @app.route("/")
# @app.route("/updateWeather")
@scheduler.task("interval", id="updateWeather", seconds=120, misfire_grace_time=900)
def updateWeather():
    print("Updating Weather")
    r = requests.get(forcastURL)
    data = r.json()
    temp = data["properties"]["periods"][0]["temperature"]
    value = (temp - cityLow) / (cityHigh - cityLow) * 100
    r = requests.get(
        f"http://localhost:5340/api/v0/dial/{weather_dial_UID}/set",
        params={"key": VU_Key, "value": value},
    )

    return r.json()


# @app.route("/updateEvents")
@scheduler.task("interval", id="updateEvent", seconds=60, misfire_grace_time=900)
def updateEvent():
    print("Updating Events")
    calIDs = [
        "primary",
        "shy@hackny.org",
        "t8c5iip0pbh62sfvjm8nhn2mko5sgn2o@import.calendar.google.com",
    ]
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    try:
        service = build("calendar", "v3", credentials=creds)
        # Call the Calendar API
        now = datetime.datetime.utcnow().isoformat() + "Z"  # 'Z' indicates UTC time
        nextEvent = {}
        for calendarId in calIDs:
            events_result = (
                service.events()
                .list(
                    calendarId=calendarId,
                    timeMin=now,
                    maxResults=2,
                    eventTypes="default",
                    singleEvents=True,
                    orderBy="startTime",
                )
                .execute()
            )
            incoming_Event = events_result.get("items", [])
            eventIndex = 0
            if pendulum.parse(incoming_Event[0]["start"]["dateTime"]) <= pendulum.now():
                eventIndex = 1
            if nextEvent:
                if (
                    nextEvent["start"]["dateTime"]
                    > incoming_Event[eventIndex]["start"]["dateTime"]
                ):
                    nextEvent = incoming_Event[eventIndex]
            else:
                nextEvent = incoming_Event[eventIndex]
        if not nextEvent:
            print("No upcoming events found.")
            return 400

        eventStart = pendulum.parse(nextEvent["start"]["dateTime"])
        timeRemaining = eventStart.diff(pendulum.now()).in_minutes()
        print(timeRemaining)
        if timeRemaining >= 360:
            # Greater than 6 hours.
            calScale = generateScale(
                6, 0, " Hrs", f"{nextEvent['summary']}", cal_icon_path
            )
            r = requests.post(
                f"http://localhost:5340/api/v0/dial/{cal_dial_UID}/image/set",
                params={"key": VU_Key, "imgfile": "my_awesome_image.png"},
                files={"imgfile": calScale},
            )
            r = requests.get(
                f"http://localhost:5340/api/v0/dial/{cal_dial_UID}/set",
                params={"key": VU_Key, "value": 0},
            )
            r = requests.get(
                f"http://localhost:5340/api/v0/dial/{cal_dial_UID}/backlight",
                params={"key": VU_Key, "red": 0, "green": 0, "blue": 0},
            )
            scheduler.modify_job("updateEvent", trigger="interval", minutes=60)
        elif timeRemaining >= 60:
            # Between 6 and 1 hour remain.
            calScale = generateScale(
                6, 0, " Hrs", f"{nextEvent['summary']}", cal_icon_path
            )
            r = requests.post(
                f"http://localhost:5340/api/v0/dial/{cal_dial_UID}/image/set",
                params={"key": VU_Key, "imgfile": "my_awesome_image.png"},
                files={"imgfile": calScale},
            )
            r = requests.get(
                f"http://localhost:5340/api/v0/dial/{cal_dial_UID}/set",
                params={"key": VU_Key, "value": 100 * (1 - (timeRemaining / 60 / 6))},
            )
            r = requests.get(
                f"http://localhost:5340/api/v0/dial/{cal_dial_UID}/backlight",
                params={"key": VU_Key, "red": 0, "green": 0, "blue": 0},
            )
            scheduler.modify_job("updateEvent", trigger="interval", minutes=15)
        elif timeRemaining > 10:
            # Less than an hour, more than 10 minutes.
            calScale = generateScale(
                60, 0, " Min", f"{nextEvent['summary']}", clock_icon_path
            )
            r = requests.post(
                f"http://localhost:5340/api/v0/dial/{cal_dial_UID}/image/set",
                params={"key": VU_Key, "imgfile": "my_awesome_image.png"},
                files={"imgfile": calScale},
            )
            r = requests.get(
                f"http://localhost:5340/api/v0/dial/{cal_dial_UID}/set",
                params={"key": VU_Key, "value": 100 * ((60 - timeRemaining) / 60)},
            )
            r = requests.get(
                f"http://localhost:5340/api/v0/dial/{cal_dial_UID}/backlight",
                params={"key": VU_Key, "red": 0, "green": 0, "blue": 0},
            )
            scheduler.modify_job("updateEvent", trigger="interval", minutes=1)
        else:
            # 10 minutes or less.
            calScale = generateScale(
                60, 0, " Min", f"{nextEvent['summary']}", clock_icon_path
            )
            print(100 * ((60 - timeRemaining) / 60))
            r = requests.get(
                f"http://localhost:5340/api/v0/dial/{cal_dial_UID}/set",
                params={"key": VU_Key, "value": 100 * ((60 - timeRemaining) / 60)},
            )
            r = requests.get(
                f"http://localhost:5340/api/v0/dial/{cal_dial_UID}/backlight",
                params={
                    "key": VU_Key,
                    "red": 100 * ((60 - timeRemaining) / 60),
                    "green": 20,
                    "blue": 40,
                },
            )
            scheduler.modify_job("updateEvent", trigger="interval", seconds=30)
        return r.json()

    except HttpError as error:
        print(f"An error occurred: {error}")


scheduler.start()
