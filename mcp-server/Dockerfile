FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py guide.md ./
COPY tests ./tests
EXPOSE 8765 8766
ENTRYPOINT ["python", "server.py"]
