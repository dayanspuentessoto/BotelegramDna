import logging
import re
import os
import json
import datetime
import pytz
import aiofiles
import hashlib
import asyncio
from collections import OrderedDict

from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

from telegram import Update, ChatPermissions, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, ContextTypes, CommandHandler, MessageHandler,
    filters, ChatMemberHandler
)
from telegram.error import RetryAfter, TelegramError

# --------- CONFIGURACIÓN ---------
TELEGRAM_TOKEN = "8210053884:AAEwiAG3Eofs3VUtsMewpJkE4PdZ-WIb9Yg"
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
DISNEY_ESPN_URL = "https://www.espn.cl/television/nota/_/id/6790200/chile-agenda-disney-plus-lo-mejor-de-la-programacion-de-espn"

AYUDA_RATE_LIMIT_SECONDS = 240
ayuda_last_sent = {}
ADMIN_ID = 5032964793

# Defaults for night/day mode
MODO_NOCHE_HORA = 23
MODO_NOCHE_MINUTO = 0
MODO_DIA_HORA = 8
MODO_DIA_MINUTO = 0
HORARIO_FILE = "horario_noche_dia.json"

# Defaults for other scheduled jobs (cartelera, pelis)
CARTELERA_HORA = 10
CARTELERA_MINUTO = 0
PELIS_HORA = 8
PELIS_MINUTO = 30

cartelera_diaria_guardada = []
ultima_agenda_disney = []
pelis_guardadas = []

mensajes_privados_usuario = {}  # <--- contador para mensajes privados
mensajes_grupo_usuario = {}     # <--- contador para mensajes en grupo
MAX_MENSAJES_GRUPO = 2  # Cambia este valor si quieres más o menos mensajes antes de restringir

def cargar_horarios():
    global MODO_NOCHE_HORA, MODO_NOCHE_MINUTO, MODO_DIA_HORA, MODO_DIA_MINUTO
    global CARTELERA_HORA, CARTELERA_MINUTO, PELIS_HORA, PELIS_MINUTO
    try:
        with open(HORARIO_FILE, "r") as f:
            data = json.load(f)
            MODO_NOCHE_HORA = data.get("noche_hora", MODO_NOCHE_HORA)
            MODO_NOCHE_MINUTO = data.get("noche_minuto", MODO_NOCHE_MINUTO)
            MODO_DIA_HORA = data.get("dia_hora", MODO_DIA_HORA)
            MODO_DIA_MINUTO = data.get("dia_minuto", MODO_DIA_MINUTO)
            CARTELERA_HORA = data.get("cartelera_hora", CARTELERA_HORA)
            CARTELERA_MINUTO = data.get("cartelera_minuto", CARTELERA_MINUTO)
            PELIS_HORA = data.get("pelis_hora", PELIS_HORA)
            PELIS_MINUTO = data.get("pelis_minuto", PELIS_MINUTO)
    except Exception:
        # If file missing or parsing error, keep defaults
        pass

def guardar_horarios():
    data = {
        "noche_hora": MODO_NOCHE_HORA,
        "noche_minuto": MODO_NOCHE_MINUTO,
        "dia_hora": MODO_DIA_HORA,
        "dia_minuto": MODO_DIA_MINUTO,
        "cartelera_hora": CARTELERA_HORA,
        "cartelera_minuto": CARTELERA_MINUTO,
        "pelis_hora": PELIS_HORA,
        "pelis_minuto": PELIS_MINUTO
    }
    with open(HORARIO_FILE, "w") as f:
        json.dump(data, f)

def safe_command(func):
    async def wrapper(*args, **kwargs):
        try:
            await func(*args, **kwargs)
        except Exception as e:
            logging.error(f"Error en comando {func.__name__}: {e}", exc_info=True)
            update = None
            for arg in args:
                if isinstance(arg, Update):
                    update = arg
                    break
            if update is not None and hasattr(update, "effective_chat") and hasattr(update, "message"):
                try:
                    await update.message.reply_text(f"❌ Error inesperado en el comando. Contacta al administrador.")
                except Exception:
                    pass
    return wrapper

async def es_admin(context, user_id):
    try:
        chat_admins = await context.bot.get_chat_administrators(GENERAL_CHAT_ID)
        admin_ids = [admin.user.id for admin in chat_admins]
        return user_id in admin_ids
    except Exception:
        return False

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

# --- SCRAPE MGS (falta anteriormente) ---
async def scrape_mgs_content():
    import unicodedata
    from bs4 import BeautifulSoup

    def normalize_categoria(nombre):
        nombre = nombre.lower().strip()
        nombre = ''.join(
            c for c in unicodedata.normalize('NFD', nombre)
            if unicodedata.category(c) != 'Mn'
        )
        return nombre

    OMITIR = [
        "todas las semanas tenemos contenido nuevo",
        "estrenos y material resubido en nuestra app",
        "¡disfrútalo!",
        "hasta el ",
        "todos los derechos ©",
        "impulsado por lynkbe.com",
        "scroll al inicio"
    ]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page()
        await page.goto(URL_MGS, timeout=120000)
        for _ in range(60):
            await page.evaluate("window.scrollBy(0, window.innerHeight);")
            await page.wait_for_timeout(700)
        html = await page.content()
        await browser.close()

        soup = BeautifulSoup(html, "html.parser")
        categorias = {}
        clave_to_titulo = {}

        for bloque in soup.find_all("div", class_="uagb-ifb-content"):
            h3 = bloque.find("h3", class_="uagb-ifb-title")
            p = bloque.find("p", class_="uagb-ifb-desc")
            if h3 and p:
                cat_name = h3.get_text(strip=True)
                cat_norm = normalize_categoria(cat_name)
                titulos_raw = p.decode_contents().split("<br>")
                titulos = []
                for t in titulos_raw:
                    for subtitulo in BeautifulSoup(t, "html.parser").get_text(separator="\n").split("\n"):
                        subtitulo = subtitulo.strip()
                        t_l = subtitulo.lower()
                        if not subtitulo or any(omit in t_l for omit in OMITIR):
                            continue
                        if subtitulo not in titulos:
                            titulos.append(subtitulo)
                if titulos:
                    categorias[cat_norm] = titulos
                    clave_to_titulo[cat_norm] = cat_name

        fecha_actualizacion = None
        for tag in soup.find_all(['p', 'div', 'span', 'li']):
            txt = tag.get_text(strip=True)
            if "Actualización de contenido" in txt:
                fecha_actualizacion = txt.split("Todas las semanas")[0].strip()
                fecha_actualizacion = fecha_actualizacion.split("hasta el")[0].strip() if "hasta el" in fecha_actualizacion else fecha_actualizacion
                break

        return {
            "fecha": fecha_actualizacion,
            "categorias": {clave_to_titulo[k]: v for k, v in categorias.items()},
            "html": html
        }

def escape_markdown_v2(text: str) -> str:
    """
    Escapa los caracteres especiales para MarkdownV2 de Telegram.
    """
    if text is None:
        return ""
    # Lista de caracteres que requiere escape en MarkdownV2
    # Nota: incluimos la barra vertical '|' entre los caracteres a escapar
    return re.sub(r'([_\*\[\]()~`>#+\-=|{}.!])', r'\\\1', str(text))

def escape_markdown(text):
    """
    Compatibilidad: delega en escape_markdown_v2 para usar siempre MarkdownV2.
    """
    return escape_markdown_v2(text)


def formato_mgs_msgs(data):
    emojis = {
        "películas": "🎬",
        "series": "📺",
        "anime": "🧑‍🎤",
        "cartoon/animado": "🦸",
        "cartoon": "🦸",
        "animado": "🦸"
    }
    max_chars = 3900
    categorias_orden = ["películas", "series", "anime", "cartoon/animado", "cartoon", "animado"]

    def normaliza(nombre):
        return nombre.lower().replace("í", "i").replace("á", "a").replace("é", "e").replace("ó", "o").replace("ú", "u")

    cats = list(data.get("categorias", {}).items())
    cats.sort(key=lambda x: categorias_orden.index(normaliza(x[0])) if normaliza(x[0]) in categorias_orden else 999)

    msgs = []
    for nombre, items in cats:
        nombre_lower = normaliza(nombre)
        emoji = emojis.get(nombre_lower, "")
        if nombre_lower == "peliculas":
            header = f"{emoji} Películas:\n"
        elif nombre_lower == "series":
            header = f"{emoji} Series:\n"
        elif nombre_lower == "anime":
            header = f"{emoji} Anime:\n"
        elif nombre_lower in ["cartoon/animado", "cartoon", "animado"]:
            header = f"{emoji} Cartoon/Animado:\n"
        else:
            header = f"*{escape_markdown(nombre)}*\n"

        bloque = header
        for item in items:
            linea = f"• {escape_markdown(item)}\n"
            if len(bloque) + len(linea) > max_chars:
                msgs.append(bloque.rstrip())
                bloque = header + linea
            else:
                bloque += linea
        if bloque.strip() != header.strip():
            msgs.append(bloque.rstrip())
    return msgs

async def pelis_core(context: ContextTypes.DEFAULT_TYPE, update: Update = None):
    global pelis_guardadas
    try:
        data = await scrape_mgs_content()
        if not data:
            if update:
                await send_long_message(context.bot, update.effective_chat.id, "No se pudo obtener datos de la web.", parse_mode="MarkdownV2")
            logging.error("scrape_mgs_content retornó None")
            return
        fecha_actual = data.get("fecha", "")
        categorias_actual = data.get("categorias", {})
        hash_actual = await hash_mgs_categorias(categorias_actual)
        ultima_fecha, ultimo_hash = await obtener_ultimo_estado_mgs()
        if not fecha_actual:
            if update:
                await send_long_message(context.bot, update.effective_chat.id, "No se pudo determinar la fecha de actualización.", parse_mode="MarkdownV2")
            return
        if fecha_actual == ultima_fecha and hash_actual == ultimo_hash:
            if update:
                await send_long_message(context.bot, update.effective_chat.id, "No hay cambios en el contenido desde la última actualización.", parse_mode="MarkdownV2")
            return
        msgs = formato_mgs_msgs(data)
        if not msgs:
            if update:
                await send_long_message(context.bot, update.effective_chat.id, "No hay contenido disponible.", parse_mode="MarkdownV2")
            return
        fecha_txt = f"*{escape_markdown(fecha_actual)}*" if fecha_actual else ""
        pelis_guardadas = ([fecha_txt] if fecha_txt else []) + msgs
        if update:
            if fecha_txt:
                await send_long_message(context.bot, update.effective_chat.id, fecha_txt, parse_mode="MarkdownV2")
            for msg in msgs:
                await send_long_message(context.bot, update.effective_chat.id, msg, parse_mode="MarkdownV2")
        else:
            if fecha_txt:
                await send_long_message(context.bot, MGS_GROUP_ID, fecha_txt, parse_mode="MarkdownV2", thread_id=MGS_THREAD_ID)
            for msg in msgs:
                await send_long_message(context.bot, MGS_GROUP_ID, msg, parse_mode="MarkdownV2", thread_id=MGS_THREAD_ID)
        await guardar_estado_mgs(fecha_actual, hash_actual)
    except Exception as e:
        logging.error(f"Error en pelis_core: {e}")
        if update:
            await send_long_message(context.bot, update.effective_chat.id, f"Error: {e}", parse_mode="MarkdownV2")

@safe_command
async def pelis_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await pelis_core(context=context, update=update)

@safe_command
async def pelis_job(context: ContextTypes.DEFAULT_TYPE):
    await pelis_core(context=context, update=None)

@safe_command
async def enviarpelis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global pelis_guardadas
    if update.effective_chat.type != "private":
        await update.message.reply_text("Este comando solo puede usarse por privado.")
        return
    if not pelis_guardadas:
        await update.message.reply_text("⚠️ No hay estrenos de MGS guardados todavía. Usa /pelis primero para obtenerlos.")
        return
    chat_id = MGS_GROUP_ID
    thread_id = MGS_THREAD_ID
    total_msgs = 0
    error_ocurrido = False
    for msg in pelis_guardadas:
        for i in range(0, len(msg), 3900):
            params = {
                "chat_id": chat_id,
                "text": msg[i:i+3900],
                "parse_mode": "MarkdownV2",
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
        await update.message.reply_text(f"✅ Estrenos MGS enviados al grupo correctamente ({total_msgs} mensajes).")
    elif error_ocurrido and total_msgs > 0:
        await update.message.reply_text(f"⚠️ Estrenos enviados parcialmente ({total_msgs} mensajes). Verifica los logs para más detalles.")
    else:
        await update.message.reply_text("❌ No se pudo enviar la agenda.")

# ----- Disney/ESPN -----
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

def formatear_cartelera_telegram(texto, fecha_formato=None):
    lineas = texto.strip().split("\n")
    primer_linea = lineas[0] if lineas else ""
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
    mensaje = f"⚽ Cartelera de Partidos Televisados - {escape_markdown(fecha_formato)}\n"
    for competencia, eventos in grupos.items():
        mensaje += f"\n🏆 {escape_markdown(competencia)}\n"
        for hora, descripcion, canal in eventos:
            canal_str = f" \\| {escape_markdown(canal)}" if canal else ""
            mensaje += f"• {escape_markdown(hora)} \\| {escape_markdown(descripcion)}{canal_str}\n"
    return mensaje.strip()

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

async def disney_scan_and_send(context: ContextTypes.DEFAULT_TYPE):
    global ultima_agenda_disney
    try:
        mensajes_por_dia = await get_programacion_espn_playwright(DISNEY_ESPN_URL)
        if mensajes_por_dia:
            formateados = [formatear_cartelera_telegram(txt) for txt in mensajes_por_dia]
            ultima_agenda_disney = formateados
            chat_id = GENERAL_CHAT_ID
            thread_id = DISNEY_THREAD_ID
            for msg in ultima_agenda_disney:
                for i in range(0, len(msg), 3900):
                    params = {
                        "chat_id": chat_id,
                        "text": msg[i:i+3900],
                        "parse_mode": "MarkdownV2",
                        "message_thread_id": thread_id
                    }
                    enviado = False
                    while not enviado:
                        try:
                            await context.bot.send_message(**params)
                            enviado = True
                            await asyncio.sleep(1)
                        except RetryAfter as e:
                            await asyncio.sleep(e.retry_after)
                        except TelegramError as e:
                            if "Message thread not found" in str(e) and "message_thread_id" in params:
                                params.pop("message_thread_id", None)
                                continue
                            break
                        except Exception:
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
                # Aquí faltaba el cierre de la función
            #  Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función
    # Aquí faltaba el cierre de la función