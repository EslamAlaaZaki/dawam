# The `edge` service: nginx as DAWAM's single entry point and its one trusted hop.
# It sends /api/, /healthz and /readyz to `app` and everything else to `web`, and it
# overwrites the forwarded headers, so neither service ever sees a client's own.
FROM nginxinc/nginx-unprivileged:stable-alpine

# Read by the image's entrypoint: NGINX_LOCAL_RESOLVERS becomes Docker's DNS server, so
# `app` and `web` are looked up again when their containers are recreated.
ENV NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1 \
    DAWAM_EDGE_APP=app:8000 \
    DAWAM_EDGE_WEB=web:3000 \
    DAWAM_TRUSTED_PROXY_CIDRS=

RUN rm /etc/nginx/conf.d/default.conf
COPY templates/ /etc/nginx/templates/
COPY --chmod=755 15-dawam-trusted-proxies.sh /docker-entrypoint.d/
EXPOSE 8080
