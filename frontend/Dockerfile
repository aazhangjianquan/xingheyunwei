FROM node:22-alpine AS build
WORKDIR /app
ARG NPM_REGISTRY=https://registry.npmjs.org
RUN npm install --global pnpm@11.25.0 --registry="${NPM_REGISTRY}"
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile --registry="${NPM_REGISTRY}"
COPY index.html vite.config.js ./
COPY src ./src
RUN pnpm run build

FROM nginx:1.29.0
ENV API_UPSTREAM=api:8000 DNS_RESOLVER=127.0.0.11
COPY nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
RUN chmod -R a+rX /usr/share/nginx/html /etc/nginx/templates

