# Dockerfile for the NHL Setup Web UI

# Stage 1: Builder
# This stage installs all the Python dependencies into a virtual environment.
FROM python:3.13-slim-trixie AS builder

# Set the working directory
WORKDIR /app

# Create a virtual environment
RUN python -m venv /opt/venv

# Activate the virtual environment
ENV PATH="/opt/venv/bin:$PATH"

# Copy the requirements file
COPY requirements.txt .

# Install dependencies into the virtual environment
RUN pip install --no-cache-dir -r requirements.txt


# Stage 2: Runtime
# This stage creates the final, smaller image.
FROM python:3.13-slim-trixie

# Create a non-root user and group
RUN addgroup --system --gid 1001 appgroup && \
    adduser --system --uid 1001 --gid 1001 appuser

# Set the working directory
WORKDIR /app

# Copy the virtual environment from the builder stage
COPY --chown=appuser:appgroup --from=builder /opt/venv /opt/venv

# Copy the application code as the non-root user
COPY --chown=appuser:appgroup . .

# Use the non-root user to run the application
USER appuser

# Expose the port the Flask app runs on
EXPOSE 8000

# Set the path to include the virtual environment
ENV PATH="/opt/venv/bin:$PATH"

# Use an entrypoint to allow for command-line arguments
ENTRYPOINT ["python", "config_server.py"]