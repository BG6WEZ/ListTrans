# Use official lightweight Python image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV HF_ENDPOINT=https://hf-mirror.com

# Set work directory
WORKDIR /app

# Install python dependencies
COPY requirements.txt /app/
RUN pip install --no-cache-dir --upgrade pip --default-timeout=120 -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu --default-timeout=120 && \
    pip install --no-cache-dir -r requirements.txt --default-timeout=120 -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com

# Copy project files
COPY . /app/

# Expose port 18080
EXPOSE 18080

# Command to run the application binding to all interfaces
# NOTE: System uses in-memory SQLite connection caches and dynamic rule engine states.
# Must maintain single worker (--workers 1 / default 1 worker) to prevent SQLite lock contention and cache divergence.
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "18080", "--workers", "1"]
