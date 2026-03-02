FROM python:3.10-slim

WORKDIR /app

# Install system dependencies if needed (e.g. for some pillow/torch operations)
# RUN apt-get update && apt-get install -y --no-install-recommends ...

COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Expose port (default FastAPI)
EXPOSE 8000

# Command to run the application
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
