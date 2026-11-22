# Static build served by nginx, which also proxies /api to the backend (same origin: no CORS).
FROM node:22-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
# Docker's embedded DNS; on AWS ECS set DNS_RESOLVER to the VPC resolver (VPC CIDR base + 2).
ENV API_UPSTREAM=http://api:8000 DNS_RESOLVER=127.0.0.11
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 80
