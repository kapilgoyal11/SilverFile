# SilverFile: one image that runs the Kushim review console (dashboard),
# the console runner (run.py) and the test suite.
#
#   docker build -t silverfile .
#   docker run --rm -p 127.0.0.1:8000:8000 silverfile   # then open http://localhost:8000
#
# Full instructions: HOW_TO_RUN.md

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so code changes don't reinstall them on every build.
COPY requirements.txt ./requirements.txt
COPY dashboard/requirements.txt ./dashboard/requirements.txt
RUN pip install -r requirements.txt -r dashboard/requirements.txt httpx

COPY . .

# Run as an ordinary user. Sessions live in /data (mount a volume to keep them);
# run.py writes final states to traces/.
RUN useradd --create-home silverfile \
    && mkdir -p /data \
    && chown -R silverfile:silverfile /data /app
USER silverfile

ENV HOST=0.0.0.0 \
    PORT=8000 \
    SILVERFILE_DB=/data/silverfile_sessions.sqlite \
    SILVERFILE_DEMO=1

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/meta', timeout=4)"

CMD ["python", "-m", "dashboard"]
