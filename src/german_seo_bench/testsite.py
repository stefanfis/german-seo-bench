"""Die Prüflandschaft von A01 lokal starten — zum Ansehen, nicht zum Messen.

    uv run python -m german_seo_bench.testsite            # http://www.example.net/
    uv run python -m german_seo_bench.testsite --port 8099
    uv run python -m german_seo_bench.testsite --hosts    # nur die /etc/hosts-Zeile zeigen

Wozu. Die Aufgabe A01 prüft eine kleine Website mit einunddreißig eingebauten
Fehlern. Wer verstehen will, was ein Modell dort eigentlich sehen soll — oder
wer sie mit einem echten Werkzeug crawlen will (Screaming Frog, Sitebulb,
curl) —, braucht sie unter einer Adresse, die sein Rechner auflöst.

**Es ist derselbe Server wie im Messlauf**, und das ist der ganze Zweck dieser
Datei. Sechs der sechsundzwanzig Adressen haben keine eigene Datei: Was dort
ausgeliefert wird, entscheidet `testsite.nginx.conf`. Ein schnell
hingeschriebener Python-Server zeigte dort sechs 404er, und fünf Befunde
fehlten — eine andere Website als die, über die später Zahlen berichtet werden.

**Die Adresse ist `www.example.net`.** Reserviert nach RFC 2606, löst nirgends
auf einen echten Host auf. Damit sie auf diesen Rechner zeigt, braucht es einen
Eintrag in `/etc/hosts` — den druckt `--hosts`.
"""

import argparse
import os
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

from german_seo_bench.dataset import data_path

COMPOSE = "a01_browser_audit/local.compose.yaml"

# Ein EIGENER Compose-Projektname, und der ist keine Kosmetik. Alle drei
# Compose-Dateien von A01 liegen im selben Verzeichnis, und Compose leitet den
# Projektnamen aus dem Verzeichnis ab — ohne diese Angabe teilten sich der
# lokale Server, der Messlauf und die Referenzerzeugung einen Namensraum.
# Zwei Dienste hießen dann beide `testsite` und wären dieselbe Identität: Ein
# `up` hier könnte den Container eines laufenden Messlaufs neu erzeugen.
PROJECT = "german-seo-bench-testsite"

# Dieselbe Adresse wie im Messlauf. Sie steht hier nicht noch einmal als
# Zeichenkette, sondern kommt aus der Aufgabe — zwei Fassungen liefen
# auseinander, und dann zeigte diese Datei auf etwas anderes als der Prompt.
from german_seo_bench.tasks.a01_browser_audit import SITE_URL

HOST = SITE_URL.removeprefix("http://").rstrip("/")
DEFAULT_PORT = 80

# Wie lange nach dem Start auf die erste Antwort gewartet wird. nginx ist in
# unter einer Sekunde da; die Spanne deckt das Laden des Images ab.
PROBE_TIMEOUT = 60.0


def hosts_line() -> str:
    return f"127.0.0.1\t{HOST}"


# Die zwei Gründe, aus denen ein Port ausfällt, und sie brauchen verschiedene
# Antworten. Deshalb zwei Werte statt eines Wahrheitswerts.
PORT_BELEGT = "belegt"
PORT_VERBOTEN = "nicht erlaubt"


def port_pruefen(port: int) -> str | None:
    """`None`, wenn der Port benutzbar ist — sonst der Grund.

    Die Prüfung läuft VOR dem Start, weil ihr Ausbleiben teuer ist: Scheitert
    die Veröffentlichung des Ports, läuft `docker compose up` trotzdem weiter,
    die Fehlermeldung steht irgendwo in der Ausgabe, und im Browser steht
    danach nur „nicht erreichbar". Drei Symptome, eine Ursache, und keines der
    drei nennt sie.

    Zwei Prüfungen, weil es zwei Ursachen gibt:

    **belegt** — da hört schon etwas. Dann hilft ein anderer Port.

    **nicht erlaubt** — Ports unter 1024 sind privilegiert, und ein rootless
    Docker (Fedora-Vorgabe) veröffentlicht sie als normaler Benutzer. Der
    Bindeversuch hier läuft unter demselben Benutzer und scheitert deshalb an
    derselben Stelle wie der des Containers. Gemessen: `docker compose up`
    meldet das zwar, läuft aber weiter — der Port bleibt stumm.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        if s.connect_ex(("127.0.0.1", port)) == 0:
            return PORT_BELEGT
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except PermissionError:
            return PORT_VERBOTEN
        except OSError:
            return PORT_BELEGT
    return None


def erreichbar(port: int, timeout: float = PROBE_TIMEOUT) -> str | None:
    """Wartet, bis die Site antwortet — und gibt sonst den Grund zurück.

    Gefragt wird über 127.0.0.1 und nicht über den Hostnamen: Scheitert es
    hier, liegt es am Server; klappt es hier und im Browser nicht, liegt es an
    der Namensauflösung oder am Browser. Das ist die Unterscheidung, die man
    beim Suchen zuerst braucht.
    """
    import time

    ende = time.monotonic() + timeout
    letzter = "keine Antwort"
    while time.monotonic() < ende:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/home.html", timeout=2
            ) as antwort:
                if antwort.status == 200:
                    return None
                letzter = f"HTTP {antwort.status}"
        except urllib.error.HTTPError as fehler:
            letzter = f"HTTP {fehler.code}"
        except Exception as fehler:  # noqa: BLE001 — jeder Grund ist einer
            letzter = type(fehler).__name__
        time.sleep(0.5)
    return letzter


def _melde_bereit(port: int) -> None:
    grund = erreichbar(port)
    adresse = f"http://{HOST}" + ("" if port == 80 else f":{port}")
    print()
    if grund is None:
        print(f"  ✓ Die Prüflandschaft antwortet: {adresse}/home.html")
        print("    Im Browser die Adresse MIT http:// eingeben — ohne das")
        print("    versucht Chrome zuerst https und meldet, die Seite sei")
        print("    nicht erreichbar.")
    else:
        print(f"  ✗ Der Server antwortet auf 127.0.0.1:{port} nicht ({grund}).")
        print("    Dann liegt es nicht am Hostnamen und nicht am Browser.")
        print("    Oben in der Compose-Ausgabe steht, woran es lag.")
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m german_seo_bench.testsite",
        description="Die Prüflandschaft von A01 lokal ausliefern (braucht Docker).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("BENCH_SITE_PORT") or DEFAULT_PORT),
        help=f"Host-Port (Vorgabe {DEFAULT_PORT} — nur damit stimmen die "
        "absoluten Adressen in sitemap.xml und Canonicals).",
    )
    parser.add_argument(
        "--hosts",
        action="store_true",
        help="Nur die benötigte /etc/hosts-Zeile ausgeben und beenden.",
    )
    args = parser.parse_args(argv)

    if args.hosts:
        print(hosts_line())
        return 0

    grund = port_pruefen(args.port)
    if grund == PORT_BELEGT:
        print(
            f"Auf Port {args.port} hört bereits etwas. Entweder läuft die "
            f"Prüflandschaft schon, oder der Port gehört einem anderen Dienst.\n"
            f"  nachsehen:     ss -ltnp 'sport = :{args.port}'\n"
            f"  anderer Port:  uv run python -m german_seo_bench.testsite --port 8099",
            file=sys.stderr,
        )
        return 1
    if grund == PORT_VERBOTEN:
        print(
            f"Port {args.port} darf dieser Benutzer nicht binden — Ports unter "
            "1024 sind privilegiert, und ein rootless Docker veröffentlicht "
            "sie unter deinem Konto.\n"
            "\nZwei Wege:\n"
            f"  1. Ports ab {args.port} freigeben (bis zum Neustart):\n"
            f"       sudo sysctl net.ipv4.ip_unprivileged_port_start={args.port}\n"
            "     dauerhaft:\n"
            f"       echo net.ipv4.ip_unprivileged_port_start={args.port} \\\n"
            "         | sudo tee /etc/sysctl.d/99-unprivileged-ports.conf\n"
            "\n  2. Einen hohen Port nehmen:\n"
            "       uv run python -m german_seo_bench.testsite --port 8099\n"
            f"     Dann stimmen die absoluten Adressen in sitemap.xml und den\n"
            f"     Canonicals nicht mehr — sie nennen {HOST} ohne Port. Zum\n"
            "     Durchklicken reicht es, für einen Crawl über die Sitemap nicht.",
            file=sys.stderr,
        )
        return 1

    compose = Path(data_path(COMPOSE))
    adresse = f"http://{HOST}" + ("" if args.port == 80 else f":{args.port}")
    print(f"Prüflandschaft:  {adresse}/")
    print(f"ersatzweise:     http://127.0.0.1:{args.port}/")
    print()
    print("Damit der Name auflöst, muss diese Zeile in /etc/hosts stehen:")
    print(f"    {hosts_line()}")
    if args.port != 80:
        # Nicht verschweigen: Die Sitemap nennt Adressen ohne Port. Ein Crawler,
        # der sie liest, läuft dann auf Port 80 und findet nichts — das sieht
        # aus wie eine kaputte Sitemap und ist eine falsche Portwahl.
        print()
        print(f"ACHTUNG: sitemap.xml und Canonicals nennen {HOST} ohne Port.")
        print("Ein Crawler, der der Sitemap folgt, landet auf Port 80.")
    print()
    print("Beenden mit Strg-C.")

    # Die Rückmeldung läuft nebenher, weil `compose up` im Vordergrund bleibt
    # (nur so beendet Strg-C sauber). Ohne sie steht am Ende eine Wand aus
    # Compose-Ausgabe und die Frage, ob das jetzt läuft.
    threading.Thread(target=_melde_bereit, args=(args.port,), daemon=True).start()

    befehl = ["docker", "compose", "-p", PROJECT, "-f", str(compose), "up"]
    try:
        return subprocess.run(
            befehl,
            env={**os.environ, "BENCH_SITE_PORT": str(args.port)},
            cwd=compose.parent,
        ).returncode
    except FileNotFoundError:
        print(
            "Kein `docker` gefunden. Die Prüflandschaft braucht ihn, weil ihre "
            "Auslieferungsregeln in einer nginx-Konfiguration stehen und nicht "
            "im Seiteninhalt.",
            file=sys.stderr,
        )
        return 127
    except KeyboardInterrupt:
        return 0
    finally:
        # Aufräumen darf nicht die eigentliche Meldung verdecken: Ohne `docker`
        # scheitert auch dieser Aufruf, und die FileNotFoundError aus dem
        # `finally` überschrieb die saubere Fehlermeldung von oben.
        #
        # KEIN `--remove-orphans`: Unter einem geteilten Projektnamen hätte das
        # die Container eines laufenden Messlaufs mitgenommen. Der eigene
        # Projektname oben verhindert das zwar — aber der Schalter wäre dann
        # auch ohne Nutzen.
        try:
            subprocess.run(
                ["docker", "compose", "-p", PROJECT, "-f", str(compose), "down"],
                cwd=compose.parent,
                capture_output=True,
            )
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
