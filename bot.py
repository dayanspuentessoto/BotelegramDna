import logging
import re
import os
import json
import datetime
import pytz
import aiofiles
import hashlib
import asyncio
from collections import defaultdict, OrderedDict

from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

from telegram import Update, ChatPermissions
from telegram.ext import (
    Application, ContextTypes, CommandHandler, MessageHandler,
    filters, ChatMemberHandler
)
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

AYUDA_RATE_LIMIT_SECONDS = 240
ayuda_last_sent = {}
ADMIN_ID = 5032964793

# --------- CONFIGURACIÓN DE HORARIOS MODO NOCHE/DÍA ---------
# Variables globales para los horarios (por defecto 23:00 y 08:00)
MODO_NOCHE_HORA = 23
MODO_NOCHE_MINUTO = 0
MODO_DIA_HORA = 8
MODO_DIA_MINUTO = 0
HORARIO_FILE = "horario_noche_dia.json"

def cargar_horarios():
    global MODO_NOCHE_HORA, MODO_NOCHE_MINUTO, MODO_DIA_HORA, MODO_DIA_MINUTO
    try:
        with open(HORARIO_FILE, "r") as f:
            data = json.load(f)
            MODO_NOCHE_HORA = data.get("noche_hora", 23)
            MODO_NOCHE_MINUTO = data.get("noche_minuto", 0)
            MODO_DIA_HORA = data.get("dia_hora", 8)
            MODO_DIA_MINUTO = data.get("dia_minuto", 0)
    except Exception:
        pass

def guardar_horarios():
    data = {
        "noche_hora": MODO_NOCHE_HORA,
        "noche_minuto": MODO_NOCHE_MINUTO,
        "dia_hora": MODO_DIA_HORA,
        "dia_minuto": MODO_DIA_MINUTO
    }
    with open(HORARIO_FILE, "w") as f:
        json.dump(data, f)

# --------- UTILIDADES Y ESTADO ---------
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

# --------- CARTELERA DEPORTIVA ---------
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

# --------- MGS (MAGISTV) SCRAPING Y FORMATO ---------
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

# --------- ENVÍO LARGO ---------
async def send_long_message(bot, chat_id, text, parse_mode=None, thread_id=None):
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
                await asyncio.sleep(1)
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

# --------- FORMATO CARTELERA DISNEY/ESPN PERSONALIZADO ---------
def formatear_cartelera_telegram(texto, fecha_formato=None):
    lineas = texto.strip().split("\n")
    primer_linea = lineas[0]
    dia_titulo = ""
    m_dia = re.match(r"^(Lunes|Martes|Miércoles|Jueves|Viernes|Sábado|Domingo)\s+(\d+)", primer_linea, re.IGNORECASE)
    if m_dia:
        dia_titulo = primer_linea
        lineas = lineas[1:]
        if not fecha_formato:
            n_dia = int(m_dia.group(2))
            hoy = datetime.datetime.now(TZ)
            mes = hoy.month
            anio = hoy.year
            if n_dia < hoy.day:
                mes += 1
                if mes > 12:
                    mes = 1
                    anio += 1
            fecha_formato = f"{n_dia:02d}-{mes:02d}-{anio}"
    if not fecha_formato:
        fecha_formato = datetime.datetime.now(TZ).strftime("%d-%m-%Y")

    grupos = OrderedDict()

    for linea in lineas:
        linea = linea.strip()
        if not linea:
            continue
        linea = re.sub(r"^Plan Premium Disney\+ ?/ ?", "", linea)
        m = re.match(r"^(\d{1,2}:\d{2}) ?(.*)", linea)
        if not m:
            continue
        hora = m.group(1)
        resto = m.group(2)
        partes = [x.strip() for x in resto.split("–")]
        if len(partes) >= 3:
            canal, competencia, descripcion = partes[0], partes[1], " – ".join(partes[2:])
        elif len(partes) == 2:
            canal, descripcion = partes[0], partes[1]
            competencia = canal
        else:
            competencia = "Otros"
            canal = ""
            descripcion = partes[0] if partes else resto

        canal = canal.replace("/", "").strip()
        if "Eliminatorias UEFA" in competencia:
            key = "Eliminatorias UEFA"
        else:
            key = competencia

        if key not in grupos:
            grupos[key] = []
        canal_final = canal if canal else ""
        grupos[key].append((hora, descripcion, canal_final))

    mensaje = f"⚽ Cartelera de Partidos Televisados - {fecha_formato}\n"
    for competencia, eventos in grupos.items():
        mensaje += f"\n🏆 {competencia}\n"
        for hora, descripcion, canal in eventos:
            canal_str = f" | {canal}" if canal else ""
            mensaje += f"• {hora} | {descripcion}{canal_str}\n"

    return mensaje.strip()

# --------- DISNEY/ESPN AGENDA Y FORMATO DEPORTIVO ---------
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

def extraer_ultima_fecha_agenda(mensajes_por_dia):
    fecha_re = re.compile(r"(Lunes|Martes|Miércoles|Jueves|Viernes|Sábado|Domingo)\s+(\d{1,2})", re.IGNORECASE)
    dias = []
    for msg in mensajes_por_dia:
        for linea in msg.splitlines():
            m = fecha_re.match(linea.strip())
            if m:
                dias.append(int(m.group(2)))
    if dias:
        return max(dias)
    return None

async def enviar_recordatorio_disney(context: ContextTypes.DEFAULT_TYPE):
    user_id = context.job.data
    await context.bot.send_message(
        chat_id=user_id,
        text="¡Hola! Hoy es el último día de la agenda Disney/ESPN que cargaste.\n\nEnvíame el link nuevo con el comando /disney <URL> para obtener la agenda actualizada de más días. 😊"
    )

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
                mensajes_por_dia.append(f"{current_day}\n" + "\n".join(current_events))
            current_day = text
            current_events = []
        else:
            if re.match(r"^\d{1,2}:\d{2}", text) or "Plan Premium Disney+" in text or "ESPN" in text:
                if not es_footer_espn(text):
                    current_events.append(text)
    if current_day and current_events:
        mensajes_por_dia.append(f"{current_day}\n" + "\n".join(current_events))
    if not mensajes_por_dia:
        posible_lines = texto_body.splitlines()
        for line in posible_lines:
            text = line.strip()
            if not text or es_footer_espn(text):
                continue
            if regex_dia.match(text):
                if current_day and current_events:
                    mensajes_por_dia.append(f"{current_day}\n" + "\n".join(current_events))
                current_day = text
                current_events = []
            else:
                if re.match(r"^\d{1,2}:\d{2}", text) or "Plan Premium Disney+" in text or "ESPN" in text:
                    if not es_footer_espn(text):
                        current_events.append(text)
        if current_day and current_events:
            mensajes_por_dia.append(f"{current_day}\n" + "\n".join(current_events))
    return mensajes_por_dia

# --------- COMANDO /DISNEY CON FORMATO CARTELERA DEPORTIVA MULTI-DÍA ---------
async def disney(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ultima_agenda_disney
    if update.effective_chat.type != "private":
        await update.message.reply_text("Por favor, usa este comando en privado.")
        return
    if not context.args:
        await update.message.reply_text("⚠️ Usa el comando así:\n/disney <URL_de_ESPN> o pega el texto de la programación")
        return
    arg = " ".join(context.args)
    if arg.startswith("http"):
        url = arg
        try:
            mensajes_por_dia = await get_programacion_espn_playwright(url)
            if mensajes_por_dia:
                formateados = []
                for txt in mensajes_por_dia:
                    formateados.append(formatear_cartelera_telegram(txt))
                ultima_agenda_disney = formateados
                for cartelera_formateada in formateados:
                    for i in range(0, len(cartelera_formateada), 3900):
                        enviado = False
                        while not enviado:
                            try:
                                await update.message.reply_text(cartelera_formateada[i:i+3900], parse_mode="Markdown")
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
                dia_max = extraer_ultima_fecha_agenda(mensajes_por_dia)
                if dia_max:
                    hoy = datetime.datetime.now(TZ)
                    mes = hoy.month
                    anio = hoy.year
                    if dia_max < hoy.day:
                        mes += 1
                        if mes > 12:
                            mes = 1
                            anio += 1
                    fecha_recordatorio = datetime.datetime(anio, mes, dia_max, 13, 0, tzinfo=TZ)
                    if fecha_recordatorio > hoy:
                        context.job_queue.run_once(
                            enviar_recordatorio_disney,
                            when=(fecha_recordatorio - hoy).total_seconds(),
                            data=update.effective_user.id,
                            name=f"recordatorio_disney_{fecha_recordatorio.strftime('%Y%m%d')}"
                        )
            else:
                await update.message.reply_text("⚠️ No encontré programación en la página.")
        except Exception as e:
            await update.message.reply_text(f"❌ Error al procesar la página: {e}")
    else:
        texto = arg
        cartelera_formateada = formatear_cartelera_telegram(texto)
        ultima_agenda_disney = [cartelera_formateada]
        for i in range(0, len(cartelera_formateada), 3900):
            enviado = False
            while not enviado:
                try:
                    await update.message.reply_text(cartelera_formateada[i:i+3900], parse_mode="Markdown")
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

# --------- COMANDO /PELIS Y ENVÍO/MGS AUTOMÁTICO ---------
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

# --------- ENVÍO EVENTOS DIARIOS ---------
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

# --------- FUNCIONES DE GRUPO: BIENVENIDA, DESPEDIDA, MODO NOCHE, RESTRICCIONES ---------
def obtener_saludo():
    hora = datetime.datetime.now(TZ).hour
    if 6 <= hora < 12:
        return "¡Buenos días!"
    elif 12 <= hora < 19:
        return "¡Buenas tardes!"
    else:
        return "¡Buenas noches!"

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

async def respuesta_general(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def restringir_mensajes(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

# --------- AYUDA, AVISO PRIVADO Y MAIN ---------
async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ayuda_last_sent
    texto = (
        "👋 ¡Hola! Tu mensaje ha sido recibido.\n"
        "El administrador se comunicará contigo pronto.\n\n"
        "Mientras esperas, revisa la sección SOPORTE DECOS que está dentro de este grupo D.N.A. TV.\n"
        "Si tienes otra pregunta, escríbela aquí. ¡Gracias!"
    )
    user_id = update.effective_user.id
    now = datetime.datetime.now().timestamp()
    last_time = ayuda_last_sent.get(user_id, 0)
    if now - last_time < AYUDA_RATE_LIMIT_SECONDS:
        return
    ayuda_last_sent[user_id] = now
    if update.effective_chat.type == "private":
        await update.message.reply_text(texto)
    else:
        await send_long_message(context.bot, GENERAL_CHAT_ID, texto, thread_id=GENERAL_THREAD_ID)

async def respuesta_privada(update: Update, context: ContextTypes.DEFAULT_TYPE):
    saludo = obtener_saludo()
    user = update.effective_user
    texto_usuario = update.message.text if update.message else ""
    nombre = f"{user.first_name or ''} {user.last_name or ''}".strip() or user.username or str(user.id)
    aviso = (
        f"📩 *Nuevo mensaje en privado al bot:*\n"
        f"👤 Usuario: {nombre} (ID: {user.id})\n"
        f"💬 Mensaje: {texto_usuario}"
    )
    try:
        await send_long_message(context.bot, ADMIN_ID, aviso, parse_mode="Markdown")
    except Exception as e:
        logging.warning(f"No se pudo avisar mensaje privado: {e}")
    await update.message.reply_text(
        f"{saludo} 👋 Soy un bot automático.\n"
        "Si tienes preguntas o necesitas soporte, por favor contacta directamente al administrador (@Daayaanss).\n"
        "También puedes escribir /ayuda para ver información y recursos útiles."
    )

# --------- COMANDO HORANOCHE ---------
async def horanoche(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Este comando solo puede usarse por privado.")
        return
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("Solo el administrador puede cambiar el horario de modo noche.")
        return
    if not context.args or len(context.args) == 0:
        await update.message.reply_text("Usa el comando así: /horanoche HH:MM\nEjemplo: /horanoche 22:30")
        return
    arg = context.args[0]
    match = re.match(r"^(\d{1,2}):(\d{2})$", arg)
    if not match:
        await update.message.reply_text("Formato incorrecto, usa HH:MM (ejemplo: 22:30)")
        return
    global MODO_NOCHE_HORA, MODO_NOCHE_MINUTO
    MODO_NOCHE_HORA = int(match.group(1))
    MODO_NOCHE_MINUTO = int(match.group(2))
    guardar_horarios()
    await update.message.reply_text(f"✅ Horario de activación automática de modo noche actualizado a las {MODO_NOCHE_HORA:02d}:{MODO_NOCHE_MINUTO:02d}.")

    if context.job_queue:
        context.job_queue.run_daily(
            lambda ctx: activar_modo_noche(ctx, GENERAL_CHAT_ID),
            time=datetime.time(hour=MODO_NOCHE_HORA, minute=MODO_NOCHE_MINUTO, tzinfo=TZ),
            name="activar_modo_noche",
            replace=True
        )

# --------- COMANDO HORADIA ---------
async def horadia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Este comando solo puede usarse por privado.")
        return
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("Solo el administrador puede cambiar el horario de fin de modo noche.")
        return
    if not context.args or len(context.args) == 0:
        await update.message.reply_text("Usa el comando así: /horadia HH:MM\nEjemplo: /horadia 07:45")
        return
    arg = context.args[0]
    match = re.match(r"^(\d{1,2}):(\d{2})$", arg)
    if not match:
        await update.message.reply_text("Formato incorrecto, usa HH:MM (ejemplo: 07:45)")
        return
    global MODO_DIA_HORA, MODO_DIA_MINUTO
    MODO_DIA_HORA = int(match.group(1))
    MODO_DIA_MINUTO = int(match.group(2))
    guardar_horarios()
    await update.message.reply_text(f"✅ Horario de desactivación automática de modo noche actualizado a las {MODO_DIA_HORA:02d}:{MODO_DIA_MINUTO:02d}.")

    if context.job_queue:
        context.job_queue.run_daily(
            desactivar_modo_noche,
            time=datetime.time(hour=MODO_DIA_HORA, minute=MODO_DIA_MINUTO, tzinfo=TZ),
            name="desactivar_modo_noche",
            replace=True
        )

# --------- COMANDO COMANDOS ---------
async def comandos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        return
    lista = [
        "/cartelera - Cartelera deportiva",
        "/htmlcartelera - HTML cartelera",
        "/textocartelera - Texto cartelera",
        "/hora - Hora Chile",
        "/noche - Activar modo noche manual (solo admin)",
        "/ayuda - Ayuda y contacto",
        "/pelis - Últimos estrenos MGS",
        "/disney <URL o texto> - Agenda Disney/ESPN",
        "/enviardisney - Enviar agenda Disney al grupo",
        "/horanoche HH:MM - Cambiar horario de activación automática modo noche (solo admin, privado)",
        "/horadia HH:MM - Cambiar horario de desactivación automática modo noche (solo admin, privado)",
        "/comandos - Mostrar esta lista de comandos"
    ]
    await update.message.reply_text(
        "*Comandos disponibles:*\n\n" + "\n".join(lista),
        parse_mode="Markdown"
    )

# --------- MAIN ---------
def main():
    cargar_horarios()
    logging.basicConfig(
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
    )
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
    application.add_handler(CommandHandler("horanoche", horanoche))
    application.add_handler(CommandHandler("horadia", horadia))
    application.add_handler(CommandHandler("comandos", comandos))
    application.job_queue.run_daily(
        lambda context: activar_modo_noche(context, GENERAL_CHAT_ID),
        time=datetime.time(hour=MODO_NOCHE_HORA, minute=MODO_NOCHE_MINUTO, tzinfo=TZ),
        name="activar_modo_noche"
    )
    application.job_queue.run_daily(
        desactivar_modo_noche,
        time=datetime.time(hour=MODO_DIA_HORA, minute=MODO_DIA_MINUTO, tzinfo=TZ),
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
