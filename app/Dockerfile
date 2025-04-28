# Use lightweight Python image
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy files
COPY app.py app.py
COPY config.yaml config.yaml
COPY requirements.txt requirements.txt
COPY alert_emailer.py alert_emailer.py

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Command to run the app
CMD ["python", "app.py"]

