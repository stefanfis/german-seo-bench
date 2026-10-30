# Der Container, der die Referenz rendert. NICHT der Agent-Container.
#
# Warum ein eigenes Image und nicht das des Agenten: Die Referenz soll vom
# Stack, den sie bewertet, unabhängig sein. Ein Fehler in chrome-devtools-mcp
# darf sich nicht in der Referenz wiederholen — sonst hebt er beide Seiten um
# denselben Betrag und wird unsichtbar. Gerendert wird deshalb mit Puppeteer
# direkt.
#
# Gleiche Basis, gleiche Distribution, gleicher Browser wie im Agent-Image: Die
# Referenz entsteht damit unter derselben Rendering-Grundlage, die gemessen
# wird.
FROM node:22-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium ca-certificates fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# `puppeteer-core` statt `puppeteer`: kein eigener Browser-Download. Chrome for
# Testing gibt es nicht für linux/arm64, und ein Rückfall auf den gebündelten
# Browser hinge die Referenz an die Architektur des Rechners, der sie rechnet.
ARG PUPPETEER_CORE_VERSION=25.12.0
RUN npm install -g "puppeteer-core@${PUPPETEER_CORE_VERSION}" && npm cache clean --force

# Global installiert, deshalb muss `require` dort suchen dürfen.
ENV NODE_PATH=/usr/local/lib/node_modules \
    PUPPETEER_SKIP_DOWNLOAD=1 \
    BENCH_CHROME_PATH=/usr/bin/chromium

COPY render.js /render.js
WORKDIR /
CMD ["node", "/render.js"]
