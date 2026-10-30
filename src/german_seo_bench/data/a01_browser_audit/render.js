// Referenz-Render: erzeugt EINMAL das eingefrorene Capture, aus dem der
// Python-Auditor (`german_seo_bench/reference/audit.py`) die Referenz rechnet.
//
// Läuft im referenzgen-Container (`reference.compose.yaml`), nie während einer
// Messung, und rendert gegen DIESELBE testsite, die auch der Agent sieht —
// mitsamt ihrer nginx-Regeln. Ein eigener Server hier wäre bequemer und
// lieferte eine Referenz für eine Auslieferung, die niemand misst.
//
// Puppeteer DIREKT, entkoppelt vom Stack des Agenten (Agent-Framework +
// chrome-devtools-mcp): Ein Fehler dort kann sich so nicht in der Referenz
// wiederholen und dadurch unsichtbar werden.
//
// Der Browser kommt aus der Distribution (BENCH_CHROME_PATH) und NICHT aus
// Puppeteers eigenem Download: Chrome for Testing gibt es nicht für
// linux/arm64. Welche Version tatsächlich gerendert hat, steht im Capture
// (`browser`) — eine Angabe, keine Annahme.
//
// Die Felder neben dem gerenderten DOM, und warum — jedes trägt einen Befund:
//
//   redirect_chain   die SERVERseitigen Sprünge vor der Antwort. Ohne sie ist
//                    eine 301 nicht von einer JS-Weiterleitung zu
//                    unterscheiden, und jede richtige Weiterleitung bekäme
//                    den Befund `client_side_redirect`.
//   bot_html         die Antwort auf dieselbe Adresse mit Crawler-Kennung.
//                    Der einzige Weg zu `cloaked_content` — und der Grund,
//                    warum hier `fetch` mit gesetztem Header steht und nicht
//                    ein zweiter Seitenaufruf im Browser.
//   links            die internen Links im gerenderten DOM, für die verwaiste
//                    Seite (Link-Graph) und die Paginierung.
//   link_status      was diese Links antworten. Ein Link ist kein Request;
//                    der Browser holt ihn nie von selbst.
//   sitemap          die angemeldeten Adressen.
//   second_visit_html  dieselbe Adresse ein zweites Mal, diesmal mit den
//                      Cookies der ersten Antwort. Der einzige Weg zu
//                      `noindex_on_second_visit` — und einer, den ein
//                      Seitenaudit ohne zweiten Abruf nicht hat.
//   text_blocks      jeder Textblock des gerenderten Dokuments mit seinem
//                    BERECHNETEN Stil und seiner Geometrie. Der einzige Weg zu
//                    `hidden_by_css`: Ob ein Block sichtbar ist, entscheidet
//                    die Kaskade über mehrere Stylesheets und eine Media
//                    Query hinweg — im Markup steht davon nichts, und wer die
//                    Regeln einzeln liest, bekommt für dieselbe Klasse auf
//                    zwei Seiten dasselbe Ergebnis. `getComputedStyle` sagt
//                    es je Element.
//
// Aufruf:  uv run python -m german_seo_bench.reference

const fs = require("fs");
const path = require("path");
const puppeteer = require("puppeteer-core");

const CAPTURE_VERSION = 1;

const CHROME = process.env.BENCH_CHROME_PATH || "/usr/bin/chromium";
const BASE = (process.env.BENCH_TESTSITE_URL || "http://www.example.net/").replace(/\/*$/, "/");
const SITE = process.env.BENCH_SITE || "/site";
const OUT = process.env.BENCH_CAPTURE || "/out/capture.json";

const VIEWPORT = { width: 1280, height: 900 };

// Nachlaufzeit nach networkidle0. Muss über der größten absichtlichen
// Verzögerung der Prüflandschaft liegen (galerie.html und sale.html schieben
// ihre Einblendung nach 250 ms ein) — sonst fände der Auditor die Verschiebung
// nicht, und die Referenz wäre still um einen Befund ärmer.
const SETTLE_MS = 1500;

// Die Kennung, mit der die zweite Anfrage gestellt wird. Sie muss zu der Regel
// in `testsite.nginx.conf` passen; stimmt sie nicht überein, entsteht ein
// Capture ohne Cloaking-Befund, und das sähe aus wie eine saubere Seite.
const BOT_UA =
  "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)";

// Ab welcher Textlänge ein Element als Textblock ins Capture kommt. Bewusst
// niedriger als die Schwelle der Regel in `reference/audit.py` (60): Das
// Capture soll die Entscheidung nicht vorwegnehmen, sondern sie ermöglichen —
// wer die Regel später lockert, braucht sonst ein neues Capture.
const TEXT_BLOCK_MIN_CHARS = 40;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const bare = (u) => (u || "").split("#", 1)[0].replace(/\/+$/, "");

// Welche Adressen geprüft werden: die Dateien in `site/` plus die, die erst
// der Server beantwortet. Die zweite Liste kommt aus der Aufgabe und wird
// übergeben, damit hier keine zweite Wahrheit entsteht.
const PAGES = JSON.parse(process.env.BENCH_PAGES || "[]");

async function fetchRaw(url, userAgent, cookies) {
  // `redirect: "manual"` ist der Kern: Wir wollen die Antwort DIESER Adresse,
  // nicht die des Ziels. Die Kette bauen wir selbst, damit jeder Sprung mit
  // Status und Ziel im Capture steht.
  const chain = [];
  let current = url;
  for (let hop = 0; hop < 10; hop++) {
    const headers = {};
    if (userAgent) headers["user-agent"] = userAgent;
    if (cookies) headers["cookie"] = cookies;
    const response = await fetch(current, { redirect: "manual", headers });
    if (response.status >= 300 && response.status < 400 && response.headers.get("location")) {
      const location = new URL(response.headers.get("location"), current).toString();
      chain.push({ url: current, status: response.status, location });
      current = location;
      continue;
    }
    // Die gesetzten Cookies mitgeben: Der zweite Abruf braucht sie, sonst ist
    // er kein Zweitbesuch, sondern nur derselbe Erstbesuch noch einmal.
    const gesetzt = (response.headers.getSetCookie ? response.headers.getSetCookie() : [])
      .map((c) => c.split(";", 1)[0])
      .join("; ");
    return {
      chain,
      status: response.status,
      body: await response.text(),
      final: current,
      cookies: gesetzt,
    };
  }
  return { chain, status: 508, body: "", final: current };
}

async function linkStatus(urls) {
  const status = {};
  for (const url of urls) {
    try {
      const response = await fetch(url, { method: "GET", redirect: "manual" });
      status[url.replace(BASE, "")] = response.status;
    } catch (e) {
      status[url.replace(BASE, "")] = 0;
    }
  }
  return status;
}

async function main() {
  if (!fs.existsSync(CHROME)) {
    console.error(`Kein Browser unter ${CHROME} — Image neu bauen?`);
    process.exit(1);
  }
  if (!PAGES.length) {
    console.error("BENCH_PAGES ist leer — ohne Seitenliste kein Capture.");
    process.exit(1);
  }

  const browser = await puppeteer.launch({
    headless: true,
    executablePath: CHROME,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  const browserVersion = await browser.version();
  console.log(`Browser: ${browserVersion} (${CHROME})`);

  const pages = {};
  for (const name of PAGES) {
    const url = BASE + name;
    const visitor = await fetchRaw(url, null, null);
    const bot = await fetchRaw(url, BOT_UA, null);
    // Der Zweitbesuch mit den Cookies des ersten. Ohne sie sähe der Server
    // wieder einen neuen Besucher, und der Befund verschwände lautlos.
    const second = await fetchRaw(url, null, visitor.cookies || null);

    const page = await browser.newPage();
    await page.setViewport(VIEWPORT);

    // Der Beobachter muss VOR dem ersten Skript der Seite stehen, sonst zählt
    // er die Verschiebung nicht mit, die er messen soll.
    await page.evaluateOnNewDocument(() => {
      window.__cls = 0;
      try {
        new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) {
            if (!entry.hadRecentInput) window.__cls += entry.value;
          }
        }).observe({ type: "layout-shift", buffered: true });
      } catch (e) {
        /* Browser ohne layout-shift: cls bleibt 0 */
      }
    });

    const requests = [];
    page.on("response", (response) => {
      const request = response.request();
      let initiator = null;
      try {
        initiator = (request.initiator() || {}).type || null;
      } catch (e) {
        /* optional */
      }
      let type = null;
      try {
        type = request.resourceType();
      } catch (e) {
        /* optional */
      }
      requests.push({ url: response.url(), status: response.status(), type, initiator });
    });

    try {
      await page.goto(url, { waitUntil: "networkidle0", timeout: 30000 });
    } catch (e) {
      // networkidle kann bei clientseitiger Weiterleitung „abbrechen" — nicht fatal.
    }
    await sleep(SETTLE_MS);

    const rendered = await page.content();
    const finalUrl = page.url();
    const moved = bare(finalUrl) !== bare(visitor.final);

    // Bei einer Weiterleitung gehört alles Weitere schon zur ZIELSEITE.
    // Geometrie, CLS, Scroll und Klick davon unter dem Namen der
    // Ausgangsseite abzulegen, wäre eine Messung an der falschen Seite.
    let images = null, cls = null, scrolled = null, interaction = null, links = [];
    let textBlocks = null;
    if (!moved && !visitor.chain.length) {
      images = await page.evaluate(() =>
        Array.from(document.images).map((img) => {
          const r = img.getBoundingClientRect();
          return {
            src: img.getAttribute("src") || "",
            loading: (img.getAttribute("loading") || "").toLowerCase(),
            rect: {
              top: Math.round(r.top + window.scrollY),
              left: Math.round(r.left + window.scrollX),
              width: Math.round(r.width),
              height: Math.round(r.height),
            },
          };
        })
      );
      cls = await page.evaluate(() => (typeof window.__cls === "number" ? window.__cls : null));
      // Die Textblöcke mit ihrem BERECHNETEN Stil. Erhoben wird vor dem
      // Scrollen und vor dem Klick: Beides verändert das Dokument, gemeint ist
      // aber der Zustand, den ein Besucher beim Laden vorfindet.
      //
      // Gezählt wird der EIGENE Text eines Elements (nur direkte Textknoten).
      // Sonst trüge jeder Vorfahr den Text seiner Kinder noch einmal, und ein
      // sichtbarer Absatz in einem Container ohne eigene Maße ergäbe einen
      // Block mit Text und Höhe 0 — ein Befund, den es nicht gibt.
      textBlocks = await page.evaluate((MIN) => {
        const ohne = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE"]);
        const out = [];
        for (const el of document.body ? document.body.querySelectorAll("*") : []) {
          if (ohne.has(el.tagName)) continue;
          let own = "";
          for (const node of el.childNodes) {
            if (node.nodeType === 3) own += node.nodeValue;
          }
          own = own.replace(/\s+/g, " ").trim();
          if (own.length < MIN) continue;
          const cs = getComputedStyle(el);
          const r = el.getBoundingClientRect();
          out.push({
            tag: el.tagName.toLowerCase(),
            text: own.slice(0, 80),
            text_len: own.length,
            computed_style: {
              display: cs.display,
              visibility: cs.visibility,
              opacity: cs.opacity,
              font_size_px: parseFloat(cs.fontSize) || 0,
              text_indent: cs.textIndent,
              clip_path: cs.clipPath,
            },
            rect: {
              top: Math.round(r.top + window.scrollY),
              left: Math.round(r.left + window.scrollX),
              width: Math.round(r.width),
              height: Math.round(r.height),
            },
          });
        }
        return out;
      }, TEXT_BLOCK_MIN_CHARS);

      links = await page.evaluate(() =>
        Array.from(document.querySelectorAll("a[href], link[href]"))
          .map((el) => el.href)
          .filter((href) => href && href.endsWith(".html"))
      );

      // Scrollen VOR dem Klicken: Ein Klick kann das DOM verändern, und der
      // Scroll-Vergleich soll nur zeigen, was das Scrollen selbst nachlädt.
      await page.evaluate(async () => {
        for (let i = 0; i < 12; i++) {
          window.scrollTo(0, document.body.scrollHeight);
          await new Promise((r) => setTimeout(r, 250));
        }
      });
      await sleep(500);
      scrolled = await page.content();

      // Der erste Button der Seite. Bewusst kein benchmark-eigenes Attribut:
      // Ein `data-bench-klick` im Fixture wäre ein Wegweiser, den ein echter
      // Auftritt nicht hat. Gemessen wird, wie lange der Handler den
      // Hauptthread synchron hält.
      interaction = await page.evaluate(() => {
        const button = document.querySelector("button");
        if (!button) return null;
        const t0 = performance.now();
        button.click();
        return {
          selector: "button",
          text: (button.textContent || "").trim().slice(0, 60),
          duration_ms: Math.round(performance.now() - t0),
        };
      });
    }
    await page.close();

    pages[name] = {
      requested_url: url,
      final_url: finalUrl,
      redirect_chain: visitor.chain,
      status: visitor.status,
      raw_html: visitor.body,
      bot_html: bot.body,
      second_visit_html: second.body,
      rendered_html: rendered,
      rendered_html_scrolled: scrolled,
      requests,
      viewport: VIEWPORT,
      images,
      text_blocks: textBlocks,
      cls,
      interaction,
      links: links.map((href) => href.replace(BASE, "")),
      link_status: await linkStatus([...new Set(links)]),
    };
    console.log(
      `  ${name}  hops=${visitor.chain.length}  cls=${cls === null ? "-" : cls.toFixed(3)}` +
        `  klick=${interaction ? interaction.duration_ms + "ms" : "-"}` +
        `  cloaked=${bot.body !== visitor.body ? "ja" : "nein"}` +
        `  zweitbesuch=${second.body !== visitor.body ? "anders" : "gleich"}`
    );
  }
  await browser.close();

  // Die angemeldeten Adressen. Aus der ausgelieferten Datei, nicht aus dem
  // Verzeichnis: Was nicht ausgeliefert wird, meldet auch niemand an.
  let sitemap = [];
  try {
    const xml = await (await fetch(`${BASE}sitemap.xml`)).text();
    sitemap = [...xml.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => m[1]);
  } catch (e) {
    console.error("Keine sitemap.xml — dann fehlt der Befund zur verwaisten Seite.");
  }

  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(
    OUT,
    JSON.stringify(
      {
        capture_version: CAPTURE_VERSION,
        viewport: VIEWPORT,
        browser: { version: browserVersion, executable: CHROME },
        site: SITE,
        sitemap,
        pages,
      },
      null,
      2
    )
  );
  console.log(`\nCapture geschrieben (v${CAPTURE_VERSION}): ${OUT}`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
