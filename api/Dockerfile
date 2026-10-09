FROM ghcr.io/amacneil/dbmate:2 AS dbmate
# citydb-tool is pure Java: take its files (any arch) and a JRE that matches the build platform
FROM --platform=linux/amd64 3dcitydb/citydb-tool:1.4.0 AS citydb
FROM eclipse-temurin:25-jre AS jre
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client && rm -rf /var/lib/apt/lists/*
COPY --from=dbmate /usr/local/bin/dbmate /usr/local/bin/dbmate
COPY --from=jre /opt/java/openjdk /opt/java/openjdk
COPY --from=citydb /opt/citydb-tool /opt/citydb-tool
ENV JAVA_HOME=/opt/java/openjdk PATH=/opt/java/openjdk/bin:$PATH
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
