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
RUN --mount=type=bind,from=wheelhouse,target=/offline,ro     python -m pip --disable-pip-version-check --no-cache-dir install         --no-index --find-links=/offline --require-hashes --only-binary=:all: -r /offline/requirements.lock &&     python -m pip --disable-pip-version-check --no-cache-dir install --no-index --no-deps -r /app/requirements.txt &&     python -m pip check

# Allow-list copy ONLY clean application source, web assets, and baseline configs
# (sensitive files, .env variants, DBs/WAL/SHM, personnel lists, and runtime state must NEVER enter any image layer)
COPY app/*.py /app/app/
COPY app/eval_assets/golden_runtime_v1.json app/eval_assets/report_template_v1.json app/eval_assets/smoke_materials_v1.json /app/app/eval_assets/
COPY app/calibration_assets/v019_m2_policy.json app/calibration_assets/v019_m2_equivalences.json /app/app/calibration_assets/
COPY web/ /app/web/
COPY config/ /app/config/

# Ensure runtime mount/writable directories exist empty in the image
RUN mkdir -p /app/app/uploads /app/app/vector_cache /app/logs /app/model_offline

# Expose port 18080
EXPOSE 18080

# Command to run the application binding to all interfaces
# NOTE: System uses in-memory SQLite connection caches and dynamic rule engine states.
# Must maintain single worker (--workers 1 / default 1 worker) to prevent SQLite lock contention and cache divergence.
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "18080", "--workers", "1"]
