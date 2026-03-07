FROM python:3.11-slim

WORKDIR /app

# Install only CPU PyTorch first (saves ~4GB vs full CUDA build)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Install remaining dependencies
COPY hostfi-bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY hostfi-bot/ .

EXPOSE 8000

CMD ["python", "main.py"]
