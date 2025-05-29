# Dockerfile

# --- Base Image ---
FROM python:3.11-slim

# --- Environment Setup ---
WORKDIR /app

# --- Copy App Files ---
COPY . .

# --- Install Requirements ---
RUN pip install --no-cache-dir -r requirements.txt

# --- Default Command ---
CMD ["python", "main.py"]

