# ARGUS — production container.
# Works on Render, Railway, Koyeb, Hugging Face Spaces (sdk: docker), Fly.io.
# Deploys KEYLESS on purpose: judges get the full Zero-Touch experience and
# no credits can be burned by the public. Set ANAKIN_API_KEY as a platform
# secret env var if you ever want the cloud browser on the deployed instance.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ARGUS_ARTIFACTS_DIR=/tmp/argus-artifacts

WORKDIR /app

# Dependencies first (cached layer), then the browser runtime.
COPY requirements.txt requirements-full.txt ./
RUN pip install -r requirements-full.txt \
 && playwright install --with-deps chromium

# Application code.
COPY argus/ argus/
COPY server/ server/
COPY run.py ./
COPY LICENSE README.md ./

# Render/Railway inject $PORT; Spaces/Fly default to 7860/8080.
EXPOSE 7860
CMD ["sh", "-c", "python run.py --host 0.0.0.0 --port ${PORT:-7860} --no-browser"]