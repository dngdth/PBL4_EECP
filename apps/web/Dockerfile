FROM node:24-alpine AS dependencies
WORKDIR /web
COPY apps/web/package.json apps/web/package-lock.json ./
RUN npm ci

FROM dependencies AS builder
COPY apps/web ./
RUN npm run build

FROM nginx:1.27-alpine AS runner
COPY apps/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /web/dist /usr/share/nginx/html
EXPOSE 8080

