FROM ghcr.io/amacneil/dbmate:2 AS dbmate
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*
COPY --from=dbmate /usr/local/bin/dbmate /usr/local/bin/dbmate
WORKDIR /srv
COPY api/requirements.txt api/requirements.txt
RUN pip install --no-cache-dir -r api/requirements.txt
COPY db db
COPY api api
COPY viewer viewer
COPY client client
ENV PYTHONUNBUFFERED=1 DBMATE_MIGRATIONS_DIR=/srv/db/migrations DBMATE_NO_DUMP_SCHEMA=true
WORKDIR /srv/api
RUN chmod +x entrypoint.sh
EXPOSE 8000
CMD ["./entrypoint.sh"]
