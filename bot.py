import logging
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup
import datetime
import pytz
import os
import aiofiles
import hashlib
import json
import re
import asyncio  # Necesario para sleep
from telegram import Update, ChatPermissions
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters, ChatMemberHandler
from telegram.error import RetryAfter, TelegramError

# --------- CONFIGURACIÓN ---------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GENERAL_CHAT_ID = -1002421748184
GENERAL_THREAD_ID = 1
EVENTOS_DEPORTIVOS_THREAD_ID = 1396
CARTELERA_URL = "https://www.futbolenvivochile.com/"
MGS_THREAD_ID = 1437
MGS_GROUP_ID = GENERAL_CHAT_ID
URL_MGS = "https://magistv.lynkbe.com/new/"
LAST_MGS_STATE_FILE = "last_mgs_state.json"
TZ = pytz.timezone("America/Santiago")
DISNEY_THREAD_ID = 1469

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# --------- FUNCIONES DE ESTADO/UTILIDAD PARA MGS ---------
async def hash_mgs_categorias(categorias):
    contenido = json.dumps(categorias, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()

async def obtener_ultimo_estado_mgs():
    try:
        async with aiofiles.open(LAST_MGS_STATE_FILE, mode="r") as f:
            data = json.loads(await f.read())
            return data.get("fecha", ""), data.get("hash", "")
    except Exception:
        return "", ""

async def guardar_estado_mgs(fecha, hash_):
    data = {"fecha": fecha, "hash": hash_}
    async with aiofiles.open(LAST_MGS_STATE_FILE, mode="w") as f:
        await f.write(json.dumps(data))

# --------- SCRAPING Y PARSING ---------
def dias_a_mostrar():
    hoy = datetime.datetime.now(TZ).date()
    manana = hoy + datetime.timedelta(days=1)
    return hoy, manana

def fecha_en_partido(fecha_str):
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
    """
    Envía mensajes largos de forma segura, cortando en 4000 caracteres y esperando 1 segundo entre mensajes.
    Maneja errores de flood y otros, para asegurar que se envíen todos los fragmentos posibles.
    """
    for i in range(0, len(text), 4000):
        params = {"chat_id": chat_id, "text": text[i:i+4000]}
        if parse_mode:
            params["parse_mode"] = parse_mode
        if thread_id and str(chat_id).startswith("-100") and thread_id > 0:
            params["message_thread_id"] = thread_id
        enviado = False
        while not enviado:
            try:
                await bot.send_message(**params)
                enviado = True
                await asyncio.sleep(1)  # 1 segundo entre mensajes
            except RetryAfter as e:
                logging.warning(f"Flood control: esperando {e.retry_after} segundos")
                await asyncio.sleep(e.retry_after)
            except TelegramError as e:
                logging.error(f"Telegram error: {e}")
                if "Message thread not found" in str(e) and "message_thread_id" in params:
                    params.pop("message_thread_id", None)
                    continue
                break
            except Exception as e:
                logging.error(f"Error inesperado enviando mensaje: {e}")
                break

# --------- MGS ---------
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
        soup = BeautifulSoup(html, "html.parser")

        texto = soup.get_text("\n", strip=True)
        lineas = texto.splitlines()

        categorias = {}
        categoria_actual = None
        fecha_actualizacion = None

        categorias_claves = ["Películas", "Series", "Anime", "Cartoon/Animado", "Cartoon", "Animado"]

        for idx, linea in enumerate(lineas):
            linea = linea.strip()
            if not linea:
                continue
            if not fecha_actualizacion and "Actualización de contenido" in linea:
                fecha_actualizacion = linea
            if any(linea.lower().startswith(cat.lower()) for cat in categorias_claves):
                categoria_actual = linea.split(":")[0].strip()
                if categoria_actual.lower().startswith("cartoon/animado"):
                    categoria_actual = "Cartoon/Animado"
                categorias[categoria_actual] = []
                continue
            if categoria_actual and not any(linea.lower().startswith(cat.lower()) for cat in categorias_claves):
                if linea and not linea.lower().startswith("todas las semanas tenemos contenido nuevo"):
                    categorias[categoria_actual].append(linea)

        if not categorias:
            logging.error("No se encontraron categorías en el scraping de MGS.")

        return {
            "fecha": fecha_actualizacion,
            "categorias": categorias,
            "html": html
        }

def formato_mgs_msgs(data):
    msgs = []
    FILTROS = [
        "Todos los derechos",
        "Impulsado por Lynkbe.com",
        "Scroll al inicio"
    ]
    for nombre, items in data.get("categorias", {}).items():
        items_filtrados = [
            item for item in items
            if not any(filtro.lower() in item.lower() for filtro in FILTROS)
            and item.strip() != ""
        ]
        if items_filtrados:
            msg = f"🎬 *{nombre}:*\n" if nombre.lower().startswith("película") else \
                  f"📺 *{nombre}:*\n" if nombre.lower().startswith("serie") else \
                  f"🧑‍🎤 *{nombre}:*\n" if nombre.lower().startswith("anime") else \
                  f"🦸 *{nombre}:*\n" if nombre.lower().startswith("cartoon") or nombre.lower().startswith("animado") else \
                  f"*{nombre}:*\n"
            msg += "\n".join(f"• {item}" for item in items_filtrados)
            msgs.append(msg)
    if not msgs:
        logging.error("formato_mgs_msgs retornó []")
    return msgs

# --------- DISNEY/ESPN - AGENDA TV ---------
ultima_agenda_disney = []

ESPN_FOOTER_FILTER = [
    "Terms of Use", "Privacy Policy", "Your US State Privacy Rights",
    "Children's Online Privacy Policy", "Interest-Based Ads",
    "About Nielsen Measurement", "Do Not Sell or Share My Personal Information",
    "Contact Us", "Disney Ad Sales Site", "Work for ESPN", "Corrections",
    "ESPN BET Sportsbook", "PENN Entertainment", "Must be 21+ to wager",
    "1-800-GAMBLER", "Copyright:",
    "ESPN Enterprises, Inc. All rights reserved"
]

def es_footer_espn(linea: str) -> bool:
    for palabra in ESPN_FOOTER_FILTER:
        if palabra.lower() in linea.lower():
            return True
    return False

async def get_programacion_espn_playwright(url: str) -> list:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(url, timeout=60000)
        await page.wait_for_timeout(2500)
        content = await page.content()
        texto_body = await page.inner_text("body")
        await browser.close()
    soup = BeautifulSoup(content, "html.parser")
    nodes = soup.find_all(['h2', 'strong', 'b', 'div', 'span', 'p', 'li'])
    regex_dia = re.compile(r"^(Lunes|Martes|Miércoles|Jueves|Viernes|Sábado|Domingo)\s*\d*", re.IGNORECASE)
    mensajes_por_dia = []
    current_day = ""
    current_events = []
    for tag in nodes:
        text = tag.get_text(strip=True)
        if not text or len(text) < 4:
            continue
        if es_footer_espn(text):
            continue
        if regex_dia.match(text):
            if current_day and current_events:
                mensajes_por_dia.append(f"*{current_day}*\n" + "\n".join(current_events))
            current_day = text
            current_events = []
        else:
            if re.match(r"^\d{1,2}:\d{2}", text) or "Plan Premium Disney+" in text or "ESPN" in text:
                if not es_footer_espn(text):
                    current_events.append(text)
    if current_day and current_events:
        mensajes_por_dia.append(f"*{current_day}*\n" + "\n".join(current_events))
    # Fallback si no encuentra nada
    if not mensajes_por_dia:
        posible_lines = texto_body.splitlines()
        for line in posible_lines:
            text = line.strip()
            if not text or es_footer_espn(text):
                continue
            if regex_dia.match(text):
                if current_day and current_events:
                    mensajes_por_dia.append(f"*{current_day}*\n" + "\n".join(current_events))
                current_day = text
                current_events = []
            else:
                if re.match(r"^\d{1,2}:\d{2}", text) or "Plan Premium Disney+" in text or "ESPN" in text:
                    if not es_footer_espn(text):
                        current_events.append(text)
        if current_day and current_events:
            mensajes_por_dia.append(f"*{current_day}*\n" + "\n".join(current_events))
    return mensajes_por_dia

async def disney(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ultima_agenda_disney
    if update.effective_chat.type != "private":
        await update.message.reply_text("Por favor, usa este comando en privado.")
        return
    if not context.args:
        await update.message.reply_text("⚠️ Usa el comando así:\n/disney <URL_de_ESPN>")
        return
    url = context.args[0]
    try:
        mensajes_por_dia = await get_programacion_espn_playwright(url)
        if mensajes_por_dia:
            ultima_agenda_disney = mensajes_por_dia
            for msg in mensajes_por_dia:
                for i in range(0, len(msg), 3900):
                    enviado = False
                    while not enviado:
                        try:
                            await update.message.reply_text(msg[i:i+3900], parse_mode="Markdown")
                            enviado = True
                            await asyncio.sleep(1)
                        except RetryAfter as e:
                            logging.warning(f"Flood control: esperando {e.retry_after} segundos")
                            await asyncio.sleep(e.retry_after)
                        except TelegramError as e:
                            logging.error(f"Telegram error: {e}")
                            break
                        except Exception as e:
                            logging.error(f"Error inesperado enviando mensaje: {e}")
                            break
        else:
            await update.message.reply_text("⚠️ No encontré programación en la página.")
    except Exception as e:
        await update.message.reply_text(f"❌ Error al procesar la página: {e}")

async def enviardisney(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ultima_agenda_disney
    if update.effective_chat.type != "private":
        await update.message.reply_text("Por favor, usa este comando en privado.")
        return
    if not ultima_agenda_disney:
        await update.message.reply_text("⚠️ Aún no has cargado ninguna agenda con /disney.")
        return
    chat_id = GENERAL_CHAT_ID
    thread_id = DISNEY_THREAD_ID
    total_msgs = 0
    error_ocurrido = False
    for msg in ultima_agenda_disney:
        for i in range(0, len(msg), 3900):
            params = {
                "chat_id": chat_id,
                "text": msg[i:i+3900],
                "parse_mode": "Markdown",
                "message_thread_id": thread_id
            }
            enviado = False
            while not enviado:
                try:
                    await context.bot.send_message(**params)
                    enviado = True
                    total_msgs += 1
                    await asyncio.sleep(1)
                except RetryAfter as e:
                    logging.warning(f"Flood control: esperando {e.retry_after} segundos")
                    await asyncio.sleep(e.retry_after)
                except TelegramError as e:
                    logging.error(f"Telegram error: {e}")
                    error_ocurrido = True
                    if "Message thread not found" in str(e) and "message_thread_id" in params:
                        params.pop("message_thread_id", None)
                        continue
                    break
                except Exception as e:
                    logging.error(f"Error inesperado enviando mensaje: {e}")
                    error_ocurrido = True
                    break
    if not error_ocurrido and total_msgs > 0:
        await update.message.reply_text(f"✅ Agenda enviada al grupo correctamente ({total_msgs} mensajes).")
    elif error_ocurrido and total_msgs > 0:
        await update.message.reply_text(f"⚠️ Agenda enviada parcialmente ({total_msgs} mensajes). Verifica los logs para más detalles.")
    else:
        await update.message.reply_text("❌ No se pudo enviar la agenda.")

# --------- FUNCIONES AUXILIARES Y DE GRUPO (bienvenida, despedida, modo noche, ayuda, respuesta_general, etc) ---------
async def bienvenida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_member = getattr(update, "chat_member", None)
    if chat_member and getattr(chat_member, "new_chat_members", None):
        for member in chat_member.new_chat_members:
            nombre = member.first_name if member.first_name else ""
            apellidos = member.last_name if member.last_name else ""
            nombre_completo = f"{nombre} {apellidos}".strip()
            if not nombre_completo:
                nombre_completo = member.username if member.username else "Usuario"
            await send_long_message(
                context.bot,
                GENERAL_CHAT_ID,
                f"{nombre_completo} BIENVENIDO(A) A NUESTRO SELECTO GRUPO D.N.A. TV, MANTENTE SIEMPRE AL DIA Y ACTUALIZADO, SI TIENES ALGUNA DUDA ESCRIBE EL COMANDO AYUDA PARA MAS INFO 😎🤖",
                thread_id=GENERAL_THREAD_ID
            )
    elif hasattr(update, "message") and getattr(update.message, "new_chat_members", None):
        for member in update.message.new_chat_members:
            nombre = member.first_name if member.first_name else ""
            apellidos = member.last_name if member.last_name else ""
            nombre_completo = f"{nombre} {apellidos}".strip()
            if not nombre_completo:
                nombre_completo = member.username if member.username else "Usuario"
            await send_long_message(
                context.bot,
                GENERAL_CHAT_ID,
                f"{nombre_completo} BIENVENIDO(A) A NUESTRO SELECTO GRUPO D.N.A. TV, MANTENTE SIEMPRE AL DIA Y ACTUALIZADO, SI TIENES ALGUNA DUDA ESCRIBE EL COMANDO AYUDA PARA MAS INFO 😎🤖",
                thread_id=GENERAL_THREAD_ID
            )

async def despedida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_member = getattr(update, "chat_member", None)
    if not chat_member or chat_member.old_chat_member.status not in ['left', 'kicked']:
        return
    user = chat_member.old_chat_member.user
    nombre = user.first_name if user.first_name else ""
    apellidos = user.last_name if user.last_name else ""
    nombre_completo = f"{nombre} {apellidos}".strip()
    if not nombre_completo:
        nombre_completo = user.username if user.username else "Usuario"
    await send_long_message(
        context.bot,
        GENERAL_CHAT_ID,
        f"{nombre_completo} ADIOS, DESPUES NO RECLAMES NI PREGUNTES🤷🏻‍♂",
        thread_id=GENERAL_THREAD_ID
    )

def obtener_saludo():
    hora = datetime.datetime.now(TZ).hour
    if 6 <= hora < 12:
        return "¡Buenos días!"
    elif 12 <= hora < 19:
        return "¡Buenas tardes!"
    else:
        return "¡Buenas noches!"

async def respuesta_general(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if hasattr(update.message, "message_thread_id") and update.message.message_thread_id != GENERAL_THREAD_ID:
        return
    user_id = update.effective_user.id
    chat_admins = await context.bot.get_chat_administrators(GENERAL_CHAT_ID)
    admin_ids = [admin.user.id for admin in chat_admins]
    if user_id in admin_ids:
        return
    saludo = obtener_saludo()
    await send_long_message(
        context.bot,
        GENERAL_CHAT_ID,
        f"{saludo} 👋 Si necesitas ayuda, escribe el comando /ayuda para recibir información clara sobre cómo contactar al administrador y resolver tus dudas.",
        thread_id=GENERAL_THREAD_ID
    )

async def respuesta_privada(update: Update, context: ContextTypes.DEFAULT_TYPE):
    saludo = obtener_saludo()
    await update.message.reply_text(
        f"{saludo} 👋 Soy un bot automático.\n"
        "Si tienes preguntas o necesitas soporte, por favor contacta directamente al administrador (@Daayaanss).\n"
        "También puedes escribir /ayuda para ver información y recursos útiles."
    )

async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = (
        "👋 ¡Hola! Tu mensaje ha sido recibido.\n"
        "El administrador se comunicará contigo pronto.\n\n"
        "Mientras esperas, revisa la sección ACTUALIZACIONES DE APPS GRATUITAS que está dentro de este grupo D.N.A. TV.\n"
        "Si tienes otra pregunta, escríbela aquí. ¡Gracias!"
    )
    if update.effective_chat.type == "private":
        await update.message.reply_text(texto)
    else:
        await send_long_message(context.bot, GENERAL_CHAT_ID, texto, thread_id=GENERAL_THREAD_ID)

async def restringir_mensajes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if hasattr(update.message, "message_thread_id") and update.message.message_thread_id != GENERAL_THREAD_ID:
        return
    hora = datetime.datetime.now(TZ).hour
    if 23 <= hora or hora < 8:
        user_id = update.effective_user.id
        chat_admins = await context.bot.get_chat_administrators(GENERAL_CHAT_ID)
        admin_ids = [admin.user.id for admin in chat_admins]
        if user_id in admin_ids:
            return
        try:
            await update.message.delete()
        except Exception as e:
            logging.warning(f"No se pudo borrar el mensaje de usuario {user_id} por modo noche: {e}")

async def modo_noche_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_admins = await context.bot.get_chat_administrators(GENERAL_CHAT_ID)
    admin_ids = [admin.user.id for admin in chat_admins]
    if user_id not in admin_ids:
        await update.message.reply_text("Solo el administrador puede activar el modo noche manualmente.")
        return
    try:
        await activar_modo_noche(context, GENERAL_CHAT_ID)
        await send_long_message(context.bot, GENERAL_CHAT_ID, "Modo noche activado manualmente hasta las 08:00.", thread_id=GENERAL_THREAD_ID)
        if update.effective_chat.id != GENERAL_CHAT_ID:
            await update.message.reply_text("Modo noche activado en el grupo D.N.A. TV.")
    except Exception as e:
        await update.message.reply_text(f"Error al activar modo noche: {e}")

async def activar_modo_noche(context: ContextTypes.DEFAULT_TYPE, chat_id):
    permisos = ChatPermissions(
        can_send_messages=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
        can_change_info=False,
        can_invite_users=True,
        can_pin_messages=False,
    )
    await context.bot.set_chat_permissions(chat_id, permissions=permisos)
    await send_long_message(context.bot, chat_id, "🌙 Modo noche activado. El canal queda restringido hasta las 08:00.", thread_id=GENERAL_THREAD_ID)

async def desactivar_modo_noche(context: ContextTypes.DEFAULT_TYPE):
    permisos = ChatPermissions(
        can_send_messages=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_change_info=False,
        can_invite_users=True,
        can_pin_messages=False,
    )
    await context.bot.set_chat_permissions(GENERAL_CHAT_ID, permissions=permisos)
    await send_long_message(context.bot, GENERAL_CHAT_ID, "☀️ ¡Fin del modo noche! Ya pueden enviar mensajes.", thread_id=GENERAL_THREAD_ID)

async def hora_chile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ahora = datetime.datetime.now(TZ)
    if update.effective_chat.type == "private":
        await update.message.reply_text(
            f"La hora en Chile es: {ahora.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"(Zona horaria detectada: {TZ.zone})"
        )
    else:
        await send_long_message(
            context.bot,
            GENERAL_CHAT_ID,
            f"La hora en Chile es: {ahora.strftime('%Y-%m-%d %H:%M:%S')}\n(Zona horaria detectada: {TZ.zone})",
            thread_id=GENERAL_THREAD_ID
        )

async def enviar_html(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(CARTELERA_URL, timeout=120000)
            for _ in range(10):
                await page.evaluate("window.scrollBy(0, window.innerHeight);")
                await page.wait_for_timeout(800)
            html = await page.inner_html("body")
            await browser.close()
            await send_long_message(context.bot, update.effective_chat.id, html[:4000])
    except Exception as e:
        await update.message.reply_text(f"Error al obtener HTML: {e}")

async def enviar_texto_body(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(CARTELERA_URL, timeout=120000)
            for _ in range(10):
                await page.evaluate("window.scrollBy(0, window.innerHeight);")
                await page.wait_for_timeout(800)
            texto = await page.inner_text("body")
            await browser.close()
            await send_long_message(context.bot, update.effective_chat.id, texto[:4000])
    except Exception as e:
        await update.message.reply_text(f"Error al obtener texto: {e}")

async def enviar_eventos_diarios(context: ContextTypes.DEFAULT_TYPE):
    try:
        hoy, manana = dias_a_mostrar()
        partidos = await scrape_cartelera_table()
        partidos_hoy = filtra_partidos_por_fecha(partidos, hoy)
        partidos_manana = filtra_partidos_por_fecha(partidos, manana)
        thread_id = EVENTOS_DEPORTIVOS_THREAD_ID
        chat_id = GENERAL_CHAT_ID
        if partidos_hoy:
            agrupados_hoy = agrupa_partidos_por_campeonato(partidos_hoy)
            mensaje_hoy = formato_mensaje_partidos(agrupados_hoy, hoy)
            await send_long_message(context.bot, chat_id, mensaje_hoy, parse_mode="Markdown", thread_id=thread_id)
        else:
            await send_long_message(context.bot, chat_id, "No hay partidos para hoy.", thread_id=thread_id)
        if partidos_manana:
            agrupados_manana = agrupa_partidos_por_campeonato(partidos_manana)
            mensaje_manana = formato_mensaje_partidos(agrupados_manana, manana)
            await send_long_message(context.bot, chat_id, mensaje_manana, parse_mode="Markdown", thread_id=thread_id)
        else:
            await send_long_message(context.bot, chat_id, "No hay partidos para mañana.", thread_id=thread_id)
    except Exception as e:
        await send_long_message(context.bot, GENERAL_CHAT_ID, f"Error al obtener cartelera: {str(e)}", thread_id=EVENTOS_DEPORTIVOS_THREAD_ID)
        logging.error(f"Error en envío diario: {e}")

async def pelis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        data = await scrape_mgs_content()
        if not data:
            await send_long_message(context.bot, update.effective_chat.id, "No se pudo obtener datos de la web.", parse_mode="Markdown")
            logging.error("scrape_mgs_content retornó None")
            return

        fecha_actual = data.get("fecha", "")
        categorias_actual = data.get("categorias", {})
        hash_actual = await hash_mgs_categorias(categorias_actual)
        ultima_fecha, ultimo_hash = await obtener_ultimo_estado_mgs()

        if not fecha_actual:
            await send_long_message(context.bot, update.effective_chat.id, "No se pudo determinar la fecha de actualización.", parse_mode="Markdown")
            return

        if fecha_actual == ultima_fecha and hash_actual == ultimo_hash:
            await send_long_message(context.bot, update.effective_chat.id, "No hay cambios en el contenido desde la última actualización.", parse_mode="Markdown")
            return

        msgs = formato_mgs_msgs(data)
        if not msgs:
            await send_long_message(context.bot, update.effective_chat.id, "No hay contenido disponible.", parse_mode="Markdown")
            return

        fecha_txt = f"*{fecha_actual}*"
        await send_long_message(context.bot, update.effective_chat.id, fecha_txt, parse_mode="Markdown")
        for msg in msgs:
            await send_long_message(context.bot, update.effective_chat.id, msg, parse_mode="Markdown")
        await guardar_estado_mgs(fecha_actual, hash_actual)
    except Exception as e:
        await send_long_message(context.bot, update.effective_chat.id, f"Error: {e}", parse_mode="Markdown")
        logging.error(f"Error en /pelis: {e}")

async def enviar_actualizacion_mgs(context: ContextTypes.DEFAULT_TYPE):
    try:
        data = await scrape_mgs_content()
        if not data or not data["fecha"]:
            logging.error("No se encontró fecha de actualización MGS.")
            return

        fecha_actual = data.get("fecha", "")
        categorias_actual = data.get("categorias", {})
        hash_actual = await hash_mgs_categorias(categorias_actual)
        ultima_fecha, ultimo_hash = await obtener_ultimo_estado_mgs()

        if fecha_actual == ultima_fecha and hash_actual == ultimo_hash:
            logging.info("No hay cambios automáticos en MGS.")
            return

        msgs = formato_mgs_msgs(data)
        fecha_txt = f"*{fecha_actual}*"
        await send_long_message(context.bot, MGS_GROUP_ID, fecha_txt, parse_mode="Markdown", thread_id=MGS_THREAD_ID)
        for msg in msgs:
            await send_long_message(context.bot, MGS_GROUP_ID, msg, parse_mode="Markdown", thread_id=MGS_THREAD_ID)
        await guardar_estado_mgs(fecha_actual, hash_actual)
    except Exception as e:
        logging.error(f"Error en actualización MGS: {e}")

def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("cartelera", enviar_eventos_diarios))
    application.add_handler(CommandHandler("htmlcartelera", enviar_html))
    application.add_handler(CommandHandler("textocartelera", enviar_texto_body))
    application.add_handler(CommandHandler("hora", hora_chile))
    application.add_handler(CommandHandler("noche", modo_noche_manual))
    application.add_handler(CommandHandler("ayuda", ayuda))
    application.add_handler(CommandHandler("pelis", pelis))
    application.add_handler(CommandHandler("disney", disney))
    application.add_handler(CommandHandler("enviardisney", enviardisney))
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
