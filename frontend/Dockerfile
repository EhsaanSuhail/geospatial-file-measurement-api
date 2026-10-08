# Demo interface only. Builds the static site and serves it with `vite preview`, which also
# forwards /api, /health and /docs to the backend (API_PROXY_TARGET).
FROM node:22-slim

ENV NPM_CONFIG_UPDATE_NOTIFIER=false
WORKDIR /app
# Vite writes a compiled copy of vite.config.ts into node_modules when it starts, so the app
# directory belongs to the unprivileged "node" user that runs the server.
RUN chown node:node /app
USER node

COPY --chown=node:node package.json package-lock.json ./
RUN npm ci
COPY --chown=node:node . .
RUN npm run build

EXPOSE 5173
CMD ["npx", "vite", "preview", "--host", "0.0.0.0", "--port", "5173"]
