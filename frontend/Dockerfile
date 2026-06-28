# Frontend image: vendor Monaco + AG Grid locally (no CDN at runtime), then serve the static
# SPA via nginx (which also reverse-proxies the API and WebSocket to the backend).

# --- stage 1: vendor third-party libraries ---
FROM node:20-alpine AS vendor
WORKDIR /build
RUN npm init -y >/dev/null 2>&1 \
    && npm install --no-audit --no-fund --silent \
       monaco-editor@0.52.2 ag-grid-community@31.3.4 sweetalert2@11 tom-select@2.3.1
RUN mkdir -p /out/monaco /out/ag-grid /out/sweetalert2/dist /out/tom-select/dist \
    && cp -r node_modules/monaco-editor/min /out/monaco/min \
    && cp -r node_modules/ag-grid-community/. /out/ag-grid/ \
    && cp node_modules/sweetalert2/dist/sweetalert2.min.js /out/sweetalert2/dist/ \
    && cp node_modules/sweetalert2/dist/sweetalert2.min.css /out/sweetalert2/dist/ \
    && cp -r node_modules/tom-select/dist/. /out/tom-select/dist/

# --- stage 2: nginx static server ---
FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY . /usr/share/nginx/html
# Production runtime config points the lib bases at the vendored copies.
COPY config.docker.js /usr/share/nginx/html/config.js
COPY --from=vendor /out /usr/share/nginx/html/vendor
# Drop dev-only files from the served root.
RUN rm -rf /usr/share/nginx/html/tests \
           /usr/share/nginx/html/Dockerfile \
           /usr/share/nginx/html/.dockerignore \
           /usr/share/nginx/html/nginx.conf \
           /usr/share/nginx/html/config.docker.js \
           /usr/share/nginx/html/package.json \
           /usr/share/nginx/html/package-lock.json
EXPOSE 80
