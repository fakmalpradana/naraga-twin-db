FROM postgres:16-alpine
RUN apk add --no-cache aws-cli
COPY deploy/backup/backup.sh /backup.sh
RUN chmod +x /backup.sh
CMD ["/backup.sh"]
