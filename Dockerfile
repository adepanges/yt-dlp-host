FROM python:3.11

WORKDIR /app

COPY requirements.txt .
RUN apt update && \
    apt install ffmpeg -y && \
    pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    python -c "import curl_cffi; print('curl_cffi OK', curl_cffi.__version__)"

COPY . .

CMD ["flask", "run"]
