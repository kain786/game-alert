"""Fast, cache-only web presentation; external collection runs in refresh.py."""
import hashlib
from configuration import public_base_url
from datetime import datetime
from urllib.parse import urlsplit
from flask import Flask, Response, jsonify, redirect, render_template, request
import schedule
import storage

app = Flask(__name__)
app.config.update(MAX_CONTENT_LENGTH=4096, SEND_FILE_MAX_AGE_DEFAULT=86400)
RELEASE = '1.0.0'


@app.after_request
def response_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'"
    if not request.path.startswith('/static/'):
        response.headers.setdefault('Cache-Control', 'private, no-cache')
    return response


@app.get('/')
def index():
    return render_template('schedule.html', **schedule.payload(request.args.get('game', '')), game_names=schedule.GAMES, release=RELEASE)


@app.get('/api/schedule')
def api_schedule():
    # Legacy ?refresh=1 is deliberately a read-only GET.
    response = jsonify(schedule.payload(request.args.get('game', '')))
    response.set_etag(hashlib.sha256(response.get_data()).hexdigest())
    return response.make_conditional(request)


@app.post('/api/refresh')
def request_refresh():
    # The recipient's Caddy authentication protects this endpoint.
    # Only the configured origin and custom header can request collection.
    origin = urlsplit(request.headers.get('Origin', ''))
    expected = urlsplit(public_base_url())
    if (origin.scheme != expected.scheme or origin.netloc != expected.netloc
            or origin.path or origin.query or origin.fragment
            or request.headers.get('X-Gevent-Request') != 'refresh'):
        return jsonify(error='같은 사이트에서 갱신을 요청해 주세요.'), 403
    try:
        with storage.lock('.refresh-request.lock') as acquired:
            if not acquired:
                return jsonify(state='queued', message='갱신 요청을 처리하고 있습니다.'), 202
            status = storage.job_status()
            if status.get('state') == 'running':
                return jsonify(state='running', message='이미 갱신하고 있습니다.'), 202
            try:
                previous = storage.read_json(storage.CACHE_DIR / 'refresh-request.json')
                age = (storage.utcnow() - datetime.fromisoformat(previous['requested_at'])).total_seconds()
                if age < 300:
                    response = jsonify(state='cooldown', message='최근에 갱신을 요청했습니다. 잠시 후 다시 시도해 주세요.')
                    response.status_code = 429
                    response.headers['Retry-After'] = str(max(1, int(300 - age)))
                    return response
            except (OSError, ValueError, KeyError, TypeError):
                pass
            storage.atomic_json(storage.CACHE_DIR / 'refresh-request.json', {'requested_at': storage.utcnow().isoformat()})
            return jsonify(state='queued', message='갱신을 요청했습니다. 저장된 일정은 계속 볼 수 있습니다.'), 202
    except OSError:
        return jsonify(error='갱신 요청을 저장하지 못했습니다. 잠시 후 다시 시도해 주세요.'), 503


@app.get('/api/refresh/status')
def refresh_status():
    return jsonify(storage.job_status())


@app.get('/calendar.ics')
def calendar():
    response = Response(schedule.calendar(request.args.get('game', '')), mimetype='text/calendar')
    response.headers['Content-Disposition'] = 'attachment; filename="gevent.ics"'
    response.set_etag(hashlib.sha256(response.get_data()).hexdigest())
    return response.make_conditional(request)


@app.get('/health')
def health():
    return 'ok', 200


@app.get('/api/ping')
def ping():
    return jsonify(ok=True)


@app.get('/diag')
def diag():
    # Keep the diagnostics location, without artificial sleep or echoed headers.
    return jsonify(health='ok', sources=schedule.payload()['sources'], refresh=storage.job_status())


@app.get('/favicon.ico')
def favicon():
    return redirect('/static/icons/starrail.webp', code=302)
