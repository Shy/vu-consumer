from flask import Flask, redirect, url_for
import os
import datetime
from dotenv import load_dotenv
import requests
from flask_dance.contrib.spotify import make_spotify_blueprint, spotify
import pendulum
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from PIL import Image, ImageDraw, ImageFont, ImageOps
from requests_oauthlib import OAuth2Session
from svglib.svglib import svg2rlg
from reportlab.graphics.renderPM import drawToPIL
from flask_apscheduler import APScheduler
from io import BytesIO
from werkzeug.middleware.proxy_fix import ProxyFix


load_dotenv()


class Config:
    SCHEDULER_API_ENABLED = True
    PREFERRED_URL_SCHEME = "https"


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
openweather_token = os.getenv("openweather_token")

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


app = Flask(__name__)
# App is behind one proxy that sets the -For and -Host headers.
app.config.from_object(Config())
app.secret_key = "supersekrit"
blueprint = make_spotify_blueprint(
    client_id=os.getenv("SPOTIPY_CLIENT_ID"),
    client_secret=(os.getenv("SPOTIPY_CLIENT_SECRET")),
)
app.register_blueprint(blueprint, url_prefix="/login")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

scheduler = APScheduler()
scheduler.init_app(app)
dailyMinTemp = 8
dailyMaxTemp = 97


@scheduler.task("cron", id="buildWeatherScale", hour="8", misfire_grace_time=900)
def buildWeatherScale():
    print("Setting Weather Scale")
    URL = (
        "https://api.openweathermap.org/data/2.5/forecast/daily?units=imperial"
        + "&lat={}".format(lat)
        + "&lon={}".format(lon)
        + "&appid={}".format(openweather_token)
    )
    resp = requests.get(URL)
    forecast = resp.json()
    # dailyMinTemp = forecast["list"][0]["main"]["temp_min"]
    # dailyMaxTemp = forecast["list"][0]["main"]["temp_max"]
    # for time in forecast["list"]:
    #     print(time["main"])
    #     if time["main"]["temp_min"] < dailyMinTemp:
    #         dailyMinTemp = time["main"]["temp_min"]
    #     if time["main"]["temp_max"] > dailyMaxTemp:
    #         dailyMaxTemp = time["main"]["temp_max"]
    weatherScale = generateScale(
        int(dailyMinTemp),
        int(dailyMaxTemp),
        "°F",
        "Weather",
        weather_icon_path,
    )

    r = requests.post(
        f"http://localhost:5340/api/v0/dial/{weather_dial_UID}/image/set",
        params={"key": VU_Key, "imgfile": "my_awesome_image.png"},
        files={"imgfile": weatherScale},
    )
    return [dailyMinTemp, dailyMaxTemp]


# @app.route("/updateWeather")
@scheduler.task("interval", id="updateWeather", hours=12, misfire_grace_time=900)
def updateWeather():
    print("Updating Weather")
    URL = (
        "https://api.openweathermap.org/data/2.5/weather?units=imperial"
        + "&lat={}".format(lat)
        + "&lon={}".format(lon)
        + "&appid={}".format(openweather_token)
    )
    resp = requests.get(URL)
    temp = resp.json()
    print(temp)
    print(dailyMinTemp)
    print(dailyMaxTemp)

    value = (
        (temp["main"]["temp"] - dailyMinTemp)
        / (temp["main"]["temp"] - dailyMaxTemp)
        * 100
    )
    r = requests.get(
        f"http://localhost:5340/api/v0/dial/{weather_dial_UID}/set",
        params={"key": VU_Key, "value": value},
    )
    return r.json()


@app.route("/spotify")
def spotifyAuth():
    if not spotify.authorized:
        return redirect(url_for("spotify.login"))
    resp = spotify.get("/user")
    assert resp.ok
    return "You are @{login} on spotify".format(login=resp.json()["login"])


# @app.route("/callbackSpotify")
# def callbackSpotify():


# @app.route("/updateEvents")
@scheduler.task("interval", id="updateEvent", seconds=60, misfire_grace_time=900)
def updateEvent():
    print("Updating Events")
    calIDs = [
        "primary",
        "shy@hackny.org",
        "t8c5iip0pbh62sfvjm8nhn2mko5sgn2o@import.calendar.google.com",
    ]
    if os.path.exists("google_auth/token.json"):
        creds = Credentials.from_authorized_user_file("google_auth/token.json", SCOPES)
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


@app.route("/")
def manualUpdate():
    updateEvent()
    updateWeather()
    return "Manual Run Triggered."


[dailyMinTemp, dailyMaxTemp] = buildWeatherScale()
scheduler.start()

if __name__ == "__main__":
    app.run(ssl_context="adhoc")
