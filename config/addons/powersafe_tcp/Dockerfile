FROM python:3.11-alpine

# Install dependencies for Python packages that require compilation
RUN apk add --no-cache gcc musl-dev libffi-dev openssl-dev python3-dev make

# Set working directory
WORKDIR /app

# Copy all files into the container
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose the callback HTTP server port
EXPOSE 9999

# Start the Python script
CMD ["python", "main.py"]
