FROM python:3.12-slim

# ffmpeg lagbe video+audio merge korar jonno
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py index.html ./

# Hosting platform e bind koro ei porte
ENV HOST=0.0.0.0
ENV PORT=7860

CMD ["python", "app.py"]
