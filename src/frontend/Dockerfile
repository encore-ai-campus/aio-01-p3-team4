FROM node:22-alpine AS build
WORKDIR /repo/src/frontend
COPY src/frontend/package*.json ./
RUN npm ci
COPY contracts/ /repo/contracts/
COPY src/frontend/ .
RUN npm run build
FROM nginx:stable-alpine
ENV NGINX_ENVSUBST_FILTER=^ADMIN_
RUN apk add --no-cache openssl
COPY src/frontend/nginx/default.conf.template /etc/nginx/templates/default.conf.template
COPY src/frontend/nginx/40-admin-gate.sh /docker-entrypoint.d/40-admin-gate.sh
RUN chmod +x /docker-entrypoint.d/40-admin-gate.sh
COPY --from=build /repo/src/frontend/dist /usr/share/nginx/html
HEALTHCHECK --interval=5s --timeout=3s --retries=10 CMD wget -q -O /dev/null http://127.0.0.1/health || exit 1
