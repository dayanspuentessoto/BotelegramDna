import logging
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import datetime
import pytz
import os
import aiofiles
from telegram import Update, ChatPermissions
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters, ChatMemberHandler

# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GENERAL_CHAT_ID = -1002421748184  # ID del grupo D.N.A. TV (supergrupo)
GENERAL_THREAD_ID = 1             # ID del tema "General"
EVENTOS_DEPORTIVOS_THREAD_ID = 1396  # ID del tema "EVENTOS DEPORTIVOS"
CARTELERA_URL = "https://www.futbolenvivochile.com/"
MGS_THREAD_ID = 1437  # Tema "Actualización de contenido APP MGS"
MGS_GROUP_ID = GENERAL_CHAT_ID
URL_MGS = "https://magistv.lynkbe.com/new/"
LAST_MGS_DATE_FILE = "last_mgs_date.txt"
TZ = pytz.timezone("America/Santiago")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

def dias_a_mostrar():
    hoy = datetime.datetime.now(TZ).date()
    manana = hoy + datetime.timedelta(days=1)
    return hoy, manana

def fecha_en_partido(fecha_str):
    import re
    fecha_str = (fecha_str or "").lower()
    hoy = datetime.datetime.now(TZ).date()
    manana = hoy + datetime.timedelta(days=1)
    match = re.search(r"(\d{2}-\d{2}-\d{4})", fecha_str)
    if match:
        d, m, y = map(int, match.group(1).split('-'))
        fecha = datetime.date(y, m, d)
        if "hoy" in fecha_str and fecha == hoy:
            return hoy
        if ("mañana" in fecha_str or "manana" in fecha_str) and fecha == manana:
            return manana
        return fecha
    if "hoy" in fecha_str:
        return hoy
    if "mañana" in fecha_str or "manana" in fecha_str:
        return manana
    return None

def agrupa_partidos_por_campeonato(partidos):
    agrupados = {}
    for partido in partidos:
        fecha_obj = fecha_en_partido(partido["fecha"])
        campeonato = partido["campeonato"] or "Sin campeonato"
        key = (fecha_obj, campeonato)
        if key not in agrupados:
            agrupados[key] = []
        agrupados[key].append(partido)
    return agrupados

def parse_cartelera(html):
    soup = BeautifulSoup(html, "html.parser")
    partidos = []
    fecha = None
    campeonato = None
    for tr in soup.find_all("tr"):
        if "cabeceraTabla" in tr.get("class", []):
            fecha = tr.get_text(strip=True)
            continue
        if "cabeceraCompericion" in tr.get("class", []):
            campeonato = tr.get_text(strip=True)
            continue
        tds = tr.find_all("td")
        if len(tds) >= 5 and "hora" in tds[0].get("class", []):
            hora = tds[0].get_text(strip=True)
            local = (
                tds[2].find("span").get("title", "")
                if tds[2].find("span") else tds[2].get_text(strip=True)
            )
            visitante = (
                tds[3].find("span").get("title", "")
                if tds[3].find("span") else tds[3].get_text(strip=True)
            )
            canales = []
            ul_canales = tds[4].find("ul", class_="listaCanales")
            if ul_canales:
                canales = [li.get_text(strip=True) for li in ul_canales.find_all("li")]
            partidos.append({
                "fecha": fecha,
                "campeonato": campeonato,
                "hora": hora,
                "local": local,
                "visitante": visitante,
                "canales": canales
            })
    return partidos

async def scrape_cartelera_table():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(CARTELERA_URL, timeout=120000)
        for _ in range(10):
            await page.evaluate("window.scrollBy(0, window.innerHeight);")
            await page.wait_for_timeout(800)
        html = await page.inner_html("body")
        await browser.close()
        return parse_cartelera(html)

def filtra_partidos_por_fecha(partidos, fecha_filtrar):
    partidos_filtrados = []
    for partido in partidos:
        fecha_obj = fecha_en_partido(partido.get("fecha"))
        if fecha_obj and fecha_obj == fecha_filtrar:
            partidos_filtrados.append(partido)
    return partidos_filtrados

def formato_mensaje_partidos(agrupados, fecha):
    mensaje = f"⚽ *Cartelera de Partidos Televisados - {fecha.strftime('%d-%m-%Y')}*\n"
    campeonatos = sorted({c for (f, c) in agrupados.keys() if f == fecha})
    for campeonato in campeonatos:
        mensaje += f"\n🏆 *{campeonato}*\n"
        partidos = agrupados.get((fecha, campeonato), [])
        for partido in partidos:
            canales_str = ", ".join(partido['canales']) if partido['canales'] else "Sin canal"
            mensaje += (
                f"• {partido['hora']} | {partido['local']} vs {partido['visitante']} | {canales_str}\n"
            )
    return mensaje

async def send_long_message(bot, chat_id, text, parse_mode=None, thread_id=None):
    for i in range(0, len(text), 4000):
        params = {"chat_id": chat_id, "text": text[i:i+4000]}
        if parse_mode:
            params["parse_mode"] = parse_mode
        if thread_id and str(chat_id).startswith("-100") and thread_id > 0:
            try:
                params["message_thread_id"] = thread_id
                await bot.send_message(**params)
            except Exception as e:
                logging.error(f"Error enviando mensaje en thread {thread_id}: {e}")
                params.pop("message_thread_id", None)
                await bot.send_message(**params)
        else:
            await bot.send_message(**params)

# --- SCRAPER MGS MEJORADO ---
async def scrape_mgs_content():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(URL_MGS, timeout=120000)
        prev_height = 0
        for _ in range(60):
            await page.evaluate("window.scrollBy(0, window.innerHeight);")
            await page.wait_for_timeout(700)
            curr_height = await page.evaluate("document.body.scrollHeight")
            if curr_height == prev_height:
                break
            prev_height = curr_height
        html = await page.content()
        await browser.close()
        # Guardar HTML para depuración
        try:
            with open("debug_mgs.html", "w", encoding="utf-8") as f:
                f.write(html)
        except Exception as e:
            logging.error(f"No se pudo guardar debug_mgs.html: {e}")
        soup = BeautifulSoup(html, "html.parser")

        # Obtener fecha
        fecha_actualizacion = None
        for tag in soup.find_all(["h1","h2","span"]):
            txt = tag.get_text(strip=True)
            if txt.lower().startswith("actualización de contenido"):
                fecha_actualizacion = txt
                break
            if "hasta el" in txt.lower() and "actualización" in txt.lower():
                fecha_actualizacion = txt
                break

        # Buscar secciones por <span> rojo y nombre
        categorias = {}
        for span in soup.find_all("span"):
            style = span.get("style", "")
            texto = span.get_text(strip=True)
            if "color: red" in style or texto.lower() in ["películas", "series", "anime", "cartoon", "animado"]:
                items = []
                curr = span
                while True:
                    curr = curr.find_next_sibling()
                    if not curr or (curr.name in ["span", "h2", "h1"] and curr.get_text(strip=True) != ""): break
                    if curr.name == "li":
                        items.append(curr.get_text(strip=True))
                if items and texto:
                    categorias[texto.capitalize()] = items

        for titulo in ["Películas", "Series", "Anime", "Cartoon", "Animado"]:
            if titulo not in categorias:
                for tag in soup.find_all(["span", "strong"]):
                    if tag.get_text(strip=True).lower() == titulo.lower():
                        items = []
                        curr = tag
                        while True:
                            curr = curr.find_next_sibling()
                            if not curr or curr.name in ["span", "h2", "h1"]: break
                            if curr.name == "li":
                                items.append(curr.get_text(strip=True))
                        if items:
                            categorias[titulo] = items

        if not categorias:
            logging.error("No se encontraron categorías en el scraping de MGS.")

        return {
            "fecha": fecha_actualizacion,
            "categorias": categorias,
            "html": html  # Devuelve el HTML para enviar por Telegram
        }

def formato_mgs_msgs(data):
    msgs = []
    if data['fecha']:
        msgs.append(f"*{data['fecha']}*")
    for nombre, items in data.get("categorias", {}).items():
        if items:
            msg = f"🎬 *{nombre}:*\n" if nombre.lower().startswith("película") else \
                  f"📺 *{nombre}:*\n" if nombre.lower().startswith("serie") else \
                  f"🧑‍🎤 *{nombre}:*\n" if nombre.lower().startswith("anime") else \
                  f"🦸 *{nombre}:*\n" if nombre.lower().startswith("cartoon") or nombre.lower().startswith("animado") else \
                  f"*{nombre}:*\n"
            msg += "\n".join(f"• {item}" for item in items)
            msgs.append(msg)
    if not msgs:
        logging.error("formato_mgs_msgs retornó []")
    return msgs

async def obtener_ultima_fecha_mgs():
    try:
        async with aiofiles.open(LAST_MGS_DATE_FILE, mode="r") as f:
            return (await f.read()).strip()
    except Exception:
        return ""

async def guardar_ultima_fecha_mgs(fecha):
    async with aiofiles.open(LAST_MGS_DATE_FILE, mode="w") as f:
        await f.write(fecha)

async def enviar_actualizacion_mgs(context: ContextTypes.DEFAULT_TYPE):
    try:
        data = await scrape_mgs_content()
        if not data or not data["fecha"]:
            logging.error("No se encontró fecha de actualización MGS.")
            return
        ultima_fecha = await obtener_ultima_fecha_mgs()
        if data["fecha"] != ultima_fecha:
            msgs = formato_mgs_msgs(data)
            for msg in msgs:
                await send_long_message(
                    context.bot,
                    MGS_GROUP_ID,
                    msg,
                    parse_mode="Markdown",
                    thread_id=MGS_THREAD_ID
                )
            await guardar_ultima_fecha_mgs(data["fecha"])
    except Exception as e:
        logging.error(f"Error en actualización MGS: {e}")

async def pelis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await send_long_message(context.bot, update.effective_chat.id, "Procesando, por favor espere...", parse_mode="Markdown")
        # --- scraping y obtención HTML ---
        data = await scrape_mgs_content()

        # Envía el HTML por Telegram si estás en privado
        if update.effective_chat.type == "private":
            html_file_path = "debug_mgs.html"
            async with aiofiles.open(html_file_path, "w", encoding="utf-8") as f:
                await f.write(data.get("html", ""))
            await context.bot.send_document(
                chat_id=update.effective_chat.id,
                document=open(html_file_path, "rb"),
                caption="Archivo HTML del contenido MGS para inspección.",
            )

        if not data:
            await send_long_message(context.bot, update.effective_chat.id, "No se pudo obtener datos de la web.", parse_mode="Markdown")
            logging.error("scrape_mgs_content retornó None")
            return
        msgs = formato_mgs_msgs(data)
        if not msgs:
            await send_long_message(context.bot, update.effective_chat.id, "No hay contenido disponible.", parse_mode="Markdown")
            return
        if update.effective_chat.type == "private":
            for msg in msgs:
                await send_long_message(context.bot, update.effective_chat.id, msg, parse_mode="Markdown")
        else:
            for msg in msgs:
                await send_long_message(context.bot, MGS_GROUP_ID, msg, parse_mode="Markdown", thread_id=MGS_THREAD_ID)
            thread_actual = getattr(getattr(update, "message", None), "message_thread_id", None)
            if thread_actual != MGS_THREAD_ID:
                await send_long_message(context.bot, MGS_GROUP_ID, "El listado fue enviado al tema Actualización de contenido APP MGS.", thread_id=MGS_THREAD_ID)
    except Exception as e:
        await send_long_message(context.bot, update.effective_chat.id, f"Error: {e}", parse_mode="Markdown")
        logging.error(f"Error en /pelis: {e}")

# --- RESTO DE FUNCIONES (sin cambios, igual que tu versión anterior) ---

async def cartelera(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... igual que antes ...

async def enviar_eventos_diarios(context: ContextTypes.DEFAULT_TYPE):
    # ... igual que antes ...

async def enviar_html(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... igual que antes ...

async def enviar_texto_body(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... igual que antes ...

async def hora_chile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... igual que antes ...

async def modo_noche_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... igual que antes ...

async def activar_modo_noche(context: ContextTypes.DEFAULT_TYPE, chat_id):
    # ... igual que antes ...

async def desactivar_modo_noche(context: ContextTypes.DEFAULT_TYPE):
    # ... igual que antes ...

def obtener_saludo():
    # ... igual que antes ...

async def bienvenida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... igual que antes ...

async def despedida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... igual que antes ...

async def restringir_mensajes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... igual que antes ...

async def respuesta_general(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... igual que antes ...

async def respuesta_privada(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... igual que antes ...

async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... igual que antes ...

def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("cartelera", cartelera))
    application.add_handler(CommandHandler("htmlcartelera", enviar_html))
    application.add_handler(CommandHandler("textocartelera", enviar_texto_body))
    application.add_handler(CommandHandler("hora", hora_chile))
    application.add_handler(CommandHandler("noche", modo_noche_manual))
    application.add_handler(CommandHandler("ayuda", ayuda))
    application.add_handler(CommandHandler("pelis", pelis))

    application.job_queue.run_daily(
        lambda context: activar_modo_noche(context, GENERAL_CHAT_ID),
        time=datetime.time(hour=23, minute=0, tzinfo=TZ),
        name="activar_modo_noche"
    )
    application.job_queue.run_daily(
        desactivar_modo_noche,
        time=datetime.time(hour=8, minute=0, tzinfo=TZ),
        name="desactivar_modo_noche"
    )
    application.job_queue.run_daily(
        enviar_eventos_diarios,
        time=datetime.time(hour=10, minute=0, tzinfo=TZ),
        name="cartelera_diaria"
    )
    application.job_queue.run_daily(
        enviar_actualizacion_mgs,
        time=datetime.time(hour=8, minute=30, tzinfo=TZ),
        name="mgs_actualizacion_diaria"
    )

    application.add_handler(ChatMemberHandler(bienvenida, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(ChatMemberHandler(despedida, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(MessageHandler(
        filters.StatusUpdate.NEW_CHAT_MEMBERS & filters.Chat(GENERAL_CHAT_ID),
        bienvenida
    ))

    application.add_handler(
        MessageHandler(
            filters.ALL & filters.Chat(GENERAL_CHAT_ID) & ~filters.COMMAND,
            respuesta_general
        )
    )
    application.add_handler(
        MessageHandler(
            filters.ALL & filters.Chat(GENERAL_CHAT_ID),
            restringir_mensajes
        )
    )
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.ChatType.PRIVATE & ~filters.COMMAND,
            respuesta_privada
        )
    )

    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        webhook_url=os.environ.get("WEBHOOK_URL")
    )

if __name__ == "__main__":
    main()
