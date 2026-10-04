# The `web` service: the Next.js frontend as a standalone Node.js server
# (`output: "standalone"`), forwarding the API to the `app` service.

FROM node:22-slim AS build
ENV NEXT_TELEMETRY_DISABLED=1
WORKDIR /frontend
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY . .
# Where `web` forwards /api, /healthz and /readyz. Next.js bakes it into the build, so
# changing it means rebuilding the image.
ARG DAWAM_API_URL=http://app:8000
RUN DAWAM_API_URL="$DAWAM_API_URL" npm run build

FROM node:22-slim
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=3000
WORKDIR /web
# Owned by root and only readable by the server's user.
COPY --from=build /frontend/.next/standalone ./
COPY --from=build /frontend/.next/static ./.next/static
USER node
EXPOSE 3000
CMD ["node", "server.js"]
