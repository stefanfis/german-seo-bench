# Der Container, in dem der Agent den Browser fährt.
#
# Alles, was zur Laufzeit gebraucht wird, steckt im Image: Chromium und
# chrome-devtools-mcp in einer **festgenagelten** Version. Kein `npx -y
# paket@latest` beim Lauf — sonst entscheidet der Tag, was gemessen wurde, und
# zwei Läufe im Abstand von einer Woche sind nicht mehr vergleichbar.
FROM node:22-bookworm-slim

# chromium statt Google Chrome: Chrome for Testing gibt es nicht für arm64, und
# der Mac (M-Serie) ist die Entwicklungsmaschine. chrome-devtools-mcp nennt
# Chromium offiziell "may work, not guaranteed" — was der Lauf tatsächlich
# benutzt hat, steht deshalb über `--executablePath` in der Kommandozeile und
# über die Version unten im Image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium ca-certificates fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Global installiert, damit `npx chrome-devtools-mcp` es ohne Netz findet.
ARG CHROME_DEVTOOLS_MCP_VERSION=1.10.1
RUN npm install -g "chrome-devtools-mcp@${CHROME_DEVTOOLS_MCP_VERSION}" \
    && npm cache clean --force

# Das Agent-CLI für die zweite Messachse (`-T agent=goose`). Ebenfalls
# festgenagelt, und aus demselben Grund: Ein Framework, das sich zwischen zwei
# Läufen ändert, ist keine Messachse, sondern eine Fehlerquelle.
#
# Installiert wird über das Skript AUS DEM RELEASE (nicht über den
# `stable`-Zeiger): Damit sind Skript und Binärdatei dieselbe Version. Das
# Skript entpackt ein tar.bz2, deshalb `bzip2`; `CONFIGURE=false` unterdrückt
# den interaktiven Einrichtungsdialog, den es im Image-Bau nicht geben kann.
#
# Das Image trägt goose auch dann, wenn über `react` gemessen wird. Die
# Alternative — zwei Images — hieße, dass der Frameworkvergleich zwei
# verschiedene Browser und zwei verschiedene Node-Stände umfasst; dann misst er
# nicht mehr das Framework.
ARG GOOSE_VERSION=1.52.0
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl bzip2 \
    && curl -fsSL "https://github.com/block/goose/releases/download/v${GOOSE_VERSION}/download_cli.sh" \
        | GOOSE_VERSION="${GOOSE_VERSION}" GOOSE_BIN_DIR=/usr/local/bin CONFIGURE=false bash \
    && rm -rf /var/lib/apt/lists/* \
    && goose --version

# pi (`-T agent=pi`), festgenagelt wie goose und als eigene Schicht danach: Ein
# neuer Build mit pi lässt Chromium und goose aus dem Cache. `--ignore-scripts`
# empfiehlt pi selbst für die Installation über npm. pi braucht Node 22.19 oder
# neuer; welche Version das Basisimage hat, steht in `/versions.json`.
ARG PI_VERSION=1.5.0
RUN npm install -g --ignore-scripts "@earendil-works/pi-coding-agent@${PI_VERSION}" \
    && npm cache clean --force \
    && pi --version

# Die Versionen zum Nachlesen. Ein Lauf, dessen Zahlen man später erklären
# muss, braucht die Angabe, mit welchem Browser gerendert wurde.
RUN printf '{"chromium":"%s","chrome_devtools_mcp":"%s","node":"%s","goose":"%s","pi":"%s"}\n' \
        "$(chromium --version)" \
        "$(npm ls -g --depth 0 chrome-devtools-mcp | sed -n 's/.*chrome-devtools-mcp@//p')" \
        "$(node --version)" \
        "$(goose --version | tr -d ' \n')" \
        "$(pi --version | tr -d ' \n')" > /versions.json

ENV PUPPETEER_SKIP_DOWNLOAD=1 \
    CHROME_PATH=/usr/bin/chromium

# KEIN `USER`-Wechsel — das Image läuft als root, und das ist eine Bedingung von
# Inspect, keine Bequemlichkeit:
#
# Inspect injiziert seine Sandbox-Werkzeuge nach /var/tmp/.<hash> und legt das
# Verzeichnis, sobald es im Container als root ausführen kann (bei Docker immer),
# root-eigen mit Modus 0700 an (`inspect_ai/util/_sandbox/_cli.py`). Die
# MCP-Aufrufe laufen danach als **Vorgabebenutzer** des Images
# (`_json_rpc_transport.py`, `user=None`). Ein Image mit `USER node` kommt damit
# nicht in das 0700-Verzeichnis hinein: Der Start des Werkzeugservers scheitert
# mit Exit 126, `Permission denied`.
#
# Sicherheitlich kostet das hier nichts: Unter rootless Docker ist Container-root
# der eigene Host-Benutzer, der Container hängt an einem `internal`-Netz und
# bekommt die Prüflandschaft read-only. Chromium braucht als root ohnehin
# `--no-sandbox`, und der Schalter steht fest in `MCP_ARGS`.
WORKDIR /root
