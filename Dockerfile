FROM python:3.13-slim

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r agent && useradd -r -g agent -d /app -s /sbin/nologin agent

# Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY . .

# Create data and logs directories with proper ownership
RUN mkdir -p /app/data /app/logs && chown -R agent:agent /app/data /app/logs

EXPOSE 8000

ENV PYTHONUNBUFFERED=1

USER agent

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

