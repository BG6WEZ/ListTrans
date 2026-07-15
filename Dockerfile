# Use a lightweight python base image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

# Set working directory
WORKDIR /app

# Install system dependencies if any are needed (e.g. git/gcc for some libraries)
# For openpyxl/pandas, standard python packages are pre-compiled wheels, so no compiler is needed.

# Copy requirements file first to leverage Docker layer caching
COPY requirements.txt /app/

# Install python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application folders and templates
COPY app/ /app/app/
COPY web/ /app/web/
COPY doc/ /app/doc/

# Expose the API port
EXPOSE 8000

# Run FastAPI using uvicorn binding to 0.0.0.0 for LAN access
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
