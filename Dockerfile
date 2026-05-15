FROM python:3.11-slim

WORKDIR /app

# Install Node.js for frontend dependencies
RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    curl -fsSL https://deb.nodesource.com/setup_18.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Node.js dependencies
COPY package.json .
RUN npm install --omit=dev

# Copy application code
COPY . .

EXPOSE ${PORT:-8000}

CMD ["gunicorn", "server:app", "--bind", "0.0.0.0:8000"]
