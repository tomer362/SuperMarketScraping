FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy project root (scrapers, utils, config)
COPY scrapers/ /app/scrapers/
COPY utils.py /app/utils.py
COPY config.py /app/config.py

# Copy backend
COPY webapp/backend/ /app/webapp/backend/

# Install scraper deps first
RUN pip install --no-cache-dir aiohttp certifi

# Install backend deps
RUN pip install --no-cache-dir -r /app/webapp/backend/requirements.txt

WORKDIR /app/webapp/backend

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
