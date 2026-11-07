FROM python:3.12-slim
RUN pip install --no-cache-dir aiohttp==3.10.11
COPY server.py index.html /app/
EXPOSE 8770
CMD ["python", "/app/server.py"]
