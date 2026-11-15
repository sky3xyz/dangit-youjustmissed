# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "flask-cors>=6.0.5",
# ]
# ///
from flask import Flask, Response, request, jsonify, stream_with_context
from flask_cors import CORS

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from app.gtfs.service import build_at
from app.missed import missed_trips_at, next_departure_at
from app.google import legs_for, extract_legs


app = Flask(__name__)
CORS(app)

@app.route("/", methods=["GET"])
def landing():
    return "Missed It"


@app.route("/api/trip", methods=["GET"])
def stream_trip():
    def event_stream():

        def wrap(data):
            # SSE format requires 'data: ' prefix followed by two newlines
            return f"data: {jsonify({"data", data})}\n\n"

        args = request.args;#Start and enpoint info

        #Note placeholder values
        route = legs_for(api_key, referer, args.start, args.end, time.time())

        first_stop = extract_legs(route)[0]["from"]

        #Send 3 past departures
        yield wrap(missed_trips_at(feed, first_stop, 3, build_at(datetime.now(), datetime.now().astimezone())))

        while True:
            #Find time until next departure
            delay = next_departure_at(feed, first_stop, build_at(datetime.now(), datetime.now().astimezone())) - time.time()

            #Wait until they've missed it
            time.sleep(delay/1000)

            #Update them
            yield wrap({"notification" : "You just missed another departure"})

    return Response(event_stream(), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(host="localhost", threaded=True, port=8080)
