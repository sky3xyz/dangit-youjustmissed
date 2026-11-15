from flask import Flask, Response, request, jsonify, stream_with_context
import time
from flask_cors import CORS

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
            return f"data: {str(data)}\n\n"

        args = request.args;#Start and enpoint info

        #Msg 1, 3 past departures
        yield wrap("hello")

        while True:
            #Find time until next departure
            delay = 1

            #Wait until they've missed it
            time.sleep(delay + 1)

            #Update them
            yield wrap("Hello")

    return Response(event_stream(), mimetype="text/event-stream")


if __name__ == "__main__":
    app.run(host="localhost", threaded=True, port=8080)
