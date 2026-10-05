# Gymcast Flask Walkthrough

This document explains how Gymcast's Flask backend connects the forecasting pipeline to a frontend website.

The main goal is to understand the request flow at a relatively low level:

```text
forecasting pipeline
        ↓
predictions.json
        ↓
Flask API
        ↓
HTTP
        ↓
browser / frontend
```

Flask does not generate the forecast itself. It exposes forecast data produced by the rest of the Gymcast pipeline.

---

## 1. Where Flask Fits in Gymcast

The broader Gymcast architecture is:

```text
GoBoard
   ↓
collector.py
   ↓
features.py
   ↓
trained model + predict.py
   ↓
predictions.json
   ↓
Flask
   ↓
frontend / browser
```

The forecasting pipeline determines what the forecast should be.

Flask provides a way for another program, such as a website, to access that forecast.

The current server provides two main operations:

```text
GET /api/predictions
→ return the current saved predictions

POST /api/refresh
→ regenerate features and predictions
```

This creates an important separation:

```text
predict.py
→ produces information

serve.py
→ exposes that information
```

For example, a browser does not need direct access to:

```text
outputs/predictions.json
```

Instead, it asks Flask:

```text
GET /api/predictions
```

Flask reads the file and returns the data over HTTP.

### Key idea

Flask is the communication boundary between Gymcast's Python forecasting pipeline and external clients such as a website.

---

## 2. What a Web Server Is

When:

```python
app.run(port=5000)
```

executes, `serve.py` becomes a long-running process.

A normal script often behaves like:

```text
start
  ↓
do work
  ↓
finish
  ↓
exit
```

A server instead behaves more like:

```text
start
  ↓
listen for requests
  ↓
request arrives
  ↓
handle request
  ↓
send response
  ↓
continue listening
```

The server remains alive waiting for requests such as:

```text
GET /api/predictions
```

or:

```text
POST /api/refresh
```

### Ports

A port helps the operating system determine which program should receive network traffic.

Gymcast currently uses:

```python
app.run(port=5000)
```

so the Flask server listens on port `5000`.

A computer can have different programs listening on different ports:

```text
port 5000 → Gymcast Flask backend
port 3000 → possible frontend development server
port 22   → SSH
```

### localhost

During local development:

```text
localhost
```

means the same computer making the request.

Therefore:

```text
http://localhost:5000
```

means:

```text
use HTTP
      ↓
contact this computer
      ↓
contact the program on port 5000
```

`localhost` commonly maps to:

```text
127.0.0.1
```

so these normally refer to the same machine:

```text
http://localhost:5000
http://127.0.0.1:5000
```

A full Gymcast API address can be broken down as:

```text
http://localhost:5000/api/predictions

http://
→ protocol

localhost
→ machine

5000
→ port / service

/api/predictions
→ route inside the Flask app
```

### Key idea

A Flask server is a long-running Python process that listens on a network port, handles incoming HTTP requests, returns responses, and continues waiting for more requests.

---

## 3. Creating the Flask Application

The server begins with:

```python
app = Flask(__name__)
```

`Flask` is a class provided by the Flask library.

Therefore:

```python
app = Flask(...)
```

creates an instance of a Flask application.

The `app` object becomes the central object representing the web application.

It keeps track of things such as:

```text
routes
configuration
request handling
extensions
server behavior
```

The same object is later used for:

```python
CORS(app)
```

```python
@app.route(...)
```

and:

```python
app.run(...)
```

The lifecycle is roughly:

```text
app = Flask(__name__)
        ↓
create application
        ↓
configure application
        ↓
register routes
        ↓
start serving application
```

### `__name__`

Python gives every module a special variable:

```python
__name__
```

If the file is executed directly:

```bash
python serve.py
```

then:

```python
__name__ == "__main__"
```

If the module is imported instead, `__name__` normally contains the module name.

Passing `__name__` to Flask helps Flask identify where the application was created and locate resources relative to that module.

It does not start the server.

These two operations are different:

```python
app = Flask(__name__)
```

creates the application.

```python
app.run(port=5000)
```

starts serving it.

### Key idea

`app = Flask(__name__)` creates the Flask application object. Routes and other behavior are then registered on that object before the server begins listening.

---

## 4. Routes and HTTP Methods

A Flask route connects an incoming HTTP request to a Python function.

Gymcast has:

```python
@app.route("/api/predictions", methods=["GET"])
def get_predictions():
    ...
```

Conceptually, Flask registers:

```text
GET /api/predictions
→ get_predictions()
```

The decorator:

```python
@app.route(...)
```

associates the function directly below it with a request pattern.

The function is not executed when the server starts.

Instead:

```text
server starts
→ route is registered

request arrives
→ route is matched
→ function executes
```

### Route paths

This:

```text
/api/predictions
```

is a URL path, not a filesystem path.

The complete local URL is:

```text
http://localhost:5000/api/predictions
```

### HTTP methods

The HTTP method describes the kind of operation being requested.

Gymcast currently uses:

```text
GET
→ retrieve existing information

POST
→ request an operation that changes state
```

Therefore:

```text
GET /api/predictions
→ retrieve saved predictions
```

while:

```text
POST /api/refresh
→ regenerate prediction data
```

A route match requires both the correct path and method:

```text
route match
=
path match
+
HTTP method match
```

For example:

```text
POST /api/predictions
```

does not match a route that only permits:

```python
methods=["GET"]
```

### `/api/`

The `/api/` prefix is a useful convention indicating that the route is intended for programmatic data access rather than directly representing a webpage.

For example:

```text
/
→ possible website page

/api/predictions
→ machine-readable prediction data
```

### Key idea

A Flask route maps an HTTP method and URL path to a Python function.

---

## 5. `GET /api/predictions`

The current route is:

```python
@app.route("/api/predictions", methods=["GET"])
def get_predictions():
    with open(PREDICTIONS_FILE) as f:
        return jsonify(json.load(f))
```

The complete transformation is:

```text
predictions.json
      ↓
json.load()
      ↓
Python object
      ↓
jsonify()
      ↓
HTTP JSON response
      ↓
browser
```

### Opening the file

```python
with open(PREDICTIONS_FILE) as f:
```

opens the saved predictions file.

The variable `f` is a file object that Python can read.

The `with` block ensures that the file is closed when the block finishes.

### `json.load()`

```python
json.load(f)
```

reads JSON from the file and converts it into Python objects.

For example:

```json
{
  "locations": {
    "Marino": [
      {
        "predicted_count": 80.2
      }
    ]
  }
}
```

becomes a Python structure containing dictionaries, lists, strings, and numbers.

Therefore:

```text
JSON text
→ json.load()
→ Python data
```

`json.load()` reads from a file object.

By contrast:

```python
json.loads(...)
```

parses JSON from a string already stored in memory.

### `jsonify()`

Flask then needs to send the Python object back over HTTP.

```python
jsonify(data)
```

creates an HTTP response containing JSON.

The original route can therefore be thought of as:

```python
with open(PREDICTIONS_FILE) as f:
    data = json.load(f)

response = jsonify(data)
return response
```

The browser never needs to know where `predictions.json` exists on the backend filesystem.

It only knows the API endpoint:

```text
GET /api/predictions
```

This means the backend could later replace the JSON file with another storage mechanism while keeping the same API contract.

### Key idea

`GET /api/predictions` reads saved JSON into Python, converts it into an HTTP JSON response, and returns it to the client.

---

## 6. Browser `fetch()` and the Response

A frontend can request prediction data using JavaScript:

```javascript
const response = await fetch(
    "http://localhost:5000/api/predictions"
);

const data = await response.json();
```

### `fetch()`

`fetch()` tells the browser to make an HTTP request.

Without another method being specified, it uses GET.

Therefore:

```javascript
fetch("http://localhost:5000/api/predictions")
```

results in:

```text
GET /api/predictions
```

being sent to Flask.

### The `Response` object

The value returned by `fetch()` is not immediately the prediction data.

```javascript
const response = await fetch(...);
```

stores an HTTP response object.

Conceptually, it includes:

```text
status
headers
response body
```

The body still contains JSON.

### `response.json()`

```javascript
const data = await response.json();
```

parses the JSON body into JavaScript objects.

Therefore:

```text
HTTP response containing JSON
          ↓
response.json()
          ↓
JavaScript object
```

This mirrors the Python side:

```text
JSON file
   ↓
json.load()
   ↓
Python object
```

JSON becomes the common data format between Python and JavaScript.

### Why `await`?

Network requests take time.

The browser must:

```text
send request
↓
wait for Flask
↓
receive response
```

`await` allows the asynchronous operation to finish before the result is used.

A typical function might therefore look like:

```javascript
async function loadPredictions() {
    const response = await fetch(
        "http://localhost:5000/api/predictions"
    );

    const data = await response.json();

    console.log(data);
}
```

The frontend can then use that JavaScript data to render cards, graphs, occupancy percentages, or other UI elements.

### Full request cycle

```text
JavaScript
   ↓
fetch()
   ↓
HTTP GET
   ↓
Flask
   ↓
get_predictions()
   ↓
predictions.json
   ↓
json.load()
   ↓
jsonify()
   ↓
HTTP JSON response
   ↓
response.json()
   ↓
JavaScript object
   ↓
frontend rendering
```

### Key idea

`fetch()` sends a browser request to Flask. Flask returns JSON, and `response.json()` converts that response into JavaScript data that the frontend can display.

---

## 7. `POST /api/refresh`

Gymcast's second route performs work rather than simply reading existing data.

```python
@app.route("/api/refresh", methods=["POST"])
def refresh():
    for step in ("features.py", "predict.py"):
        subprocess.run(
            [sys.executable, os.path.join(BASE_DIR, step)],
            check=True,
            cwd=BASE_DIR
        )
    return jsonify({"status": "refreshed"})
```

The flow is:

```text
POST /api/refresh
      ↓
refresh()
      ↓
features.py
      ↓
predict.py
      ↓
updated predictions.json
      ↓
{"status": "refreshed"}
```

### Why POST?

Refreshing has side effects because it regenerates data.

Therefore:

```text
GET /api/predictions
→ read current state

POST /api/refresh
→ cause state to change
```

### Script order

The loop:

```python
for step in ("features.py", "predict.py"):
```

runs:

```text
1. features.py
2. predict.py
```

The order matters because prediction depends on the latest feature data.

### `subprocess.run()`

`subprocess.run()` launches another process from the Flask process.

Conceptually:

```text
serve.py process
      ↓
launch features.py
      ↓
wait for completion
      ↓
launch predict.py
      ↓
wait for completion
```

This is similar to running:

```bash
python features.py
python predict.py
```

from a shell.

Flask is not directly calling internal functions from those modules.

### `sys.executable`

```python
sys.executable
```

refers to the Python interpreter currently running Flask.

Using it for the subprocesses helps ensure that:

```text
serve.py
features.py
predict.py
```

all use the same Python environment and installed dependencies.

### `cwd=BASE_DIR`

`cwd` means current working directory.

```python
cwd=BASE_DIR
```

runs each subprocess as though the source directory were its working directory.

This makes relative path behavior more predictable.

The location of a script and its working directory are not necessarily the same thing.

### `check=True`

```python
check=True
```

causes `subprocess.run()` to raise an exception if the subprocess exits unsuccessfully.

This prevents a failed feature-generation step from being silently treated as a successful refresh.

Conceptually:

```text
features.py fails
      ↓
subprocess raises error
      ↓
refresh does not return false success
```

### What refresh does not do

The route does not run:

```text
collector.py
train.py
```

Instead:

```text
collector.py
→ continually gathers observations

train.py
→ learns model parameters

refresh endpoint
→ rebuilds current features and runs inference
```

So:

```text
refresh
≠ retraining
```

### Retrieving updated data

The response:

```json
{
  "status": "refreshed"
}
```

confirms that the refresh finished.

It does not contain the forecast itself.

A frontend can therefore use:

```text
POST /api/refresh
      ↓
refresh completes
      ↓
GET /api/predictions
      ↓
receive updated predictions
```

### Key idea

`POST /api/refresh` launches `features.py` and `predict.py` as subprocesses using the same Python environment. Successful completion produces an updated `predictions.json`.

---

## 8. CORS and Browser Origins

Gymcast currently enables CORS with:

```python
CORS(app)
```

CORS stands for:

```text
Cross-Origin Resource Sharing
```

An origin is approximately:

```text
protocol + hostname + port
```

For example:

```text
http://localhost:3000
```

and:

```text
http://localhost:5000
```

are different origins because their ports differ.

A possible development setup is:

```text
frontend
http://localhost:3000

backend
http://localhost:5000
```

The browser's same-origin policy normally restricts JavaScript from freely reading responses from other origins.

Therefore, when the frontend makes:

```javascript
fetch("http://localhost:5000/api/predictions")
```

the browser needs the backend to indicate that cross-origin access is permitted.

`CORS(app)` configures Flask to send the relevant CORS headers.

Conceptually:

```text
browser
   ↓
cross-origin request
   ↓
Flask
   ↓
response + CORS permission
   ↓
browser allows JavaScript to use response
```

A CORS failure does not necessarily mean the Flask route itself failed.

It is possible for:

```text
request to reach Flask
↓
Flask to return response
↓
browser to block frontend JavaScript from accessing it
```

CORS is primarily a browser security mechanism.

Tools such as `curl` are not governed by the browser's same-origin policy in the same way.

### Production

Broad CORS access is convenient during development.

Once Gymcast has a known frontend domain, the backend can later restrict which origins are allowed.

CORS should also not be confused with authentication.

```text
CORS
≠ proving who a user is
```

### Key idea

CORS allows browser JavaScript from a different origin to access responses from the Flask API.

---

## 9. Local Development vs. Deployment

During local development:

```text
browser
   ↓
localhost:5000
   ↓
Flask on the same computer
```

works because `localhost` refers to the computer making the request.

Once Gymcast is deployed publicly, the Flask backend will run on a remote machine.

Then:

```text
user's browser
      ↓
internet
      ↓
remote Gymcast backend
```

If deployed frontend code still contained:

```javascript
fetch("http://localhost:5000/api/predictions")
```

the user's browser would interpret `localhost` as the user's own machine, not the Gymcast server.

A deployed frontend therefore needs to reach a real backend address.

Conceptually:

```text
https://gymcast.example/api/predictions
```

or:

```text
https://api.gymcast.example/api/predictions
```

### Development server vs. production server

This:

```python
app.run(port=5000)
```

is useful for local development.

In production, the Flask application is usually run through production server infrastructure rather than exposing Flask's built-in development server directly.

Conceptually:

```text
internet
   ↓
production server infrastructure
   ↓
Flask application
```

The Flask route logic itself can remain mostly unchanged.

Whether a request originates locally or remotely, Flask can still receive:

```text
GET /api/predictions
```

and run:

```python
get_predictions()
```

### Backend filesystem

Locally:

```text
outputs/predictions.json
```

exists on the local machine.

When deployed, the backend must have access to the corresponding predictions and model data on the remote environment.

The browser still does not need to know where those files exist.

It only interacts with the API.

### Relation to the Gymcast droplet

The existing Gymcast droplet already provides a useful analogy.

It is a remote computer capable of continuously running Python processes.

A deployed Flask backend follows the same general idea:

```text
remote machine
   ↓
Python environment
   ↓
Gymcast backend
   ↓
long-running server process
```

The difference is that the Flask backend must also be reachable through HTTP.

### Key idea

`localhost` is appropriate for local development, but a deployed frontend must communicate with a remotely reachable backend. Flask's application logic can stay largely the same while the surrounding deployment environment changes.

---

## 10. End-to-End Mental Model

The Flask layer can now be understood through two main request flows.

### Reading predictions

```text
Browser JavaScript
      ↓
fetch("/api/predictions")
      ↓
HTTP GET request
      ↓
Flask server
      ↓
route matching
      ↓
get_predictions()
      ↓
open predictions.json
      ↓
json.load()
      ↓
Python data
      ↓
jsonify()
      ↓
HTTP JSON response
      ↓
browser
      ↓
response.json()
      ↓
JavaScript data
      ↓
frontend renders forecast
```

### Refreshing predictions

```text
Browser
   ↓
POST /api/refresh
   ↓
Flask
   ↓
refresh()
   ↓
features.py
   ↓
predict.py
   ↓
predictions.json updated
   ↓
success response
   ↓
Browser
   ↓
GET /api/predictions
   ↓
Flask
   ↓
updated prediction data
   ↓
frontend
```

---

## Gymcast's Three Layers

The project can be viewed as three major layers.

### 1. Forecasting Layer

```text
collector.py
features.py
train.py
evaluate.py
predict.py
```

Responsibilities include:

```text
collecting data
engineering features
training models
evaluating forecasts
generating predictions
```

### 2. API Layer

```text
serve.py
Flask
HTTP
JSON
routes
```

Responsibilities include:

```text
receiving requests
mapping requests to Python functions
exposing predictions
triggering refreshes
returning responses
```

### 3. Frontend Layer

```text
browser
JavaScript
fetch()
HTML / CSS
charts / cards / UI
```

Responsibilities include:

```text
requesting API data
turning JSON into JavaScript objects
presenting forecasts to users
handling user interaction
```

The clean architecture is therefore:

```text
forecast
   ↓
expose
   ↓
display
```

or:

```text
ML pipeline
     ↓
Flask API
     ↓
frontend
```

Flask does not need to understand how LightGBM works or how leakage-safe lag features are built.

The frontend does not need to know where the model pickle or prediction file lives.

Each layer interacts with the next through a clear boundary.

### Final Mental Model

A normal prediction request is:

```text
user opens Gymcast
      ↓
frontend requests predictions
      ↓
Flask receives HTTP request
      ↓
matching route executes
      ↓
saved forecast is loaded
      ↓
Flask returns JSON
      ↓
frontend parses response
      ↓
occupancy forecast is displayed
```

Flask is therefore the interface between Gymcast's internal Python forecasting system and the software that presents its forecasts to users.