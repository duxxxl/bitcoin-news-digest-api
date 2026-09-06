# Kleines, offizielles Python-Image als Basis.
FROM python:3.11-slim

# Arbeitsverzeichnis im Container.
WORKDIR /app

# Logs sofort ausgeben, keine .pyc-Dateien anlegen.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Erst nur die Abhaengigkeiten kopieren und installieren.
# So wird dieser (langsame) Schritt beim Neubauen aus dem Cache genutzt,
# solange sich requirements.txt nicht aendert.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Dann den restlichen Code.
COPY . .

# Doku: die App lauscht auf diesem Port.
EXPOSE 8000

# Start. Hoster wie Render/Fly setzen $PORT selbst; lokal faellt es auf 8000 zurueck.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
