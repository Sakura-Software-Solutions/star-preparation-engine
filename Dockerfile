FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd --gid 10001 star && useradd --uid 10001 --gid star --no-create-home star \
    && mkdir -p /var/lib/star && chown star:star /var/lib/star
COPY --chown=star:star src ./src
COPY --chown=star:star profiles ./profiles
COPY --chown=star:star star-prep ./star-prep
USER star
EXPOSE 8080
CMD ["./star-prep", "serve", "--host", "0.0.0.0", "--shared", "--secure-cookies", "--data-dir", "/var/lib/star"]
