FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATA_DIR=/data
RUN useradd --uid 10001 --create-home app && mkdir /data && chown app:app /data
WORKDIR /srv
COPY --chown=app:app app/ /srv/
USER app
VOLUME /data
EXPOSE 7000 7001
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7000/health', timeout=4).status==200 else 1)"
CMD ["python", "server.py"]
