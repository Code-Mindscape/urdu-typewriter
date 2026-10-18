# Start from an official, lightweight Python 3.12 image
FROM python:3.12-slim

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Configure headless Qt environment variables inside the container
ENV QT_QPA_PLATFORM=offscreen
ENV PYTHONUTF8=1

# Install system GUI, OpenGL, and core GLib event tracking dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libegl1 \
    libxkbcommon0 \
    libdbus-1-3 \
    fonts-dejavu-core \
    ghostscript \
    && rm -rf /var/lib/apt/lists/*

# Set the working directory inside the container
WORKDIR /app

# Copy dependency mappings and install them along with the formatting tool
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt black

# Copy the rest of your application code into the container workspace
COPY . .

# Clean default execution array (overridden safely by the CI workflow parameters)
CMD ["black", "--check", "."]

