FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ISS_TRACK_DATA_DIR=/data

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server ./server
COPY web ./web
# The engine, its timings and the frontend assets, from the pinned submodule.
# Run "git submodule update --init" before building.
COPY vendor/iss_track/custom_components/iss_track/engine ./vendor/iss_track/custom_components/iss_track/engine
COPY vendor/iss_track/custom_components/iss_track/const.py ./vendor/iss_track/custom_components/iss_track/const.py
COPY vendor/iss_track/custom_components/iss_track/www ./vendor/iss_track/custom_components/iss_track/www

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=5s --start-period=30s \
  CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["python", "-m", "uvicorn", "server.main:app", "--host", "0.0.0.0", "--port", "8000"]
