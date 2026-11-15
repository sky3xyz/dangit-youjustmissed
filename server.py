from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/", methods=["GET"])
def landing():
    return "Missed It"

@app.route("/trip", methods=["GET"])
def get_trip():
    args = request.args;

    if not ("start" in args and "end" in args):
        return jsonify({"error" : "invalid request"})

    return jsonify({"start":args["start"], "end":args["end"]})


if __name__ == "__main__":
    app.run(host="localhost", port=8080)
