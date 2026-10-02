FROM python:3.13-alpine

WORKDIR /app
COPY server/server.py /app/server/server.py
COPY index.html og-image.jpg /app/public/
COPY server/admin.html /app/public/admin.html

RUN adduser -D -H -u 1000 memomoji && mkdir /data && chown memomoji /data
USER memomoji

ENV PORT=8080 DB_PATH=/data/memomoji.db STATIC_DIR=/app/public PYTHONUNBUFFERED=1
EXPOSE 8080
VOLUME /data

HEALTHCHECK --interval=30s --timeout=3s \
  CMD wget -qO- http://127.0.0.1:8080/api/health || exit 1

CMD ["python", "/app/server/server.py"]
