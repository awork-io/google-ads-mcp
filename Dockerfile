# Use a slim Python image
FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /uvx /bin/

# Set the working directory in the container
WORKDIR /app

# Copy the project files into the container
COPY . .

# Install the tested dependency versions in the project environment.
RUN uv sync --locked --no-editable
ENV PATH="/app/.venv/bin:$PATH"

# Expose port 8080 (default for Cloud Run)
EXPOSE 8080

# Define the command to run the server
# This uses the entry point defined in pyproject.toml
CMD ["google-ads-mcp"]
