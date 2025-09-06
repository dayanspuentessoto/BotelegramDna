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
        if thread_id and str(chat_id).startswith("-100"):
            params["message_thread_id"] = thread_id
        await bot.send_message(**params)

# --- SCRAPER MGS ---
async def scrape_mgs_content():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(URL_MGS, timeout=120000)
        html = await page.content()
        await browser.close()
        soup = BeautifulSoup(html, "html.parser")

        fecha_actualizacion = None
        peliculas, series, anime, cartoon = [], [], [], []

        # Buscar fecha de actualización
        fecha_tag = soup.find("h6", string=lambda x: x and "Actualización de contenido" in x)
        if fecha_tag:
            fecha_actualizacion = fecha_tag.get_text(strip=True)

        # Buscar listados
        secciones = soup.find_all("div", class_="accordion-body")
        for section in secciones:
            title_tag = section.find_previous("button")
            title = title_tag.get_text(strip=True) if title_tag else ""
            items = [li.get_text(strip=True) for li in section.find_all("li")]
            if "Películas" in title:
                peliculas = items
            elif "Series" in title:
                series = items
            elif "Anime" in title:
                anime = items
            elif "Cartoon" in title or "Animado" in title:
                cartoon = items

        return {
            "fecha": fecha_actualizacion,
            "peliculas": peliculas,
            "series": series,
            "anime": anime,
            "cartoon": cartoon
        }

def formato_mgs_msg(data):
    msg = f"*{data['fecha']}*\n\n"
    if data["peliculas"]:
        msg += "🎬 *Películas:*\n" + "\n".join(f"• {p}" for p in data["peliculas"]) + "\n\n"
    if data["series"]:
        msg += "📺 *Series:*\n" + "\n".join(f"• {s}" for s in data["series"]) + "\n\n"
    if data["anime"]:
        msg += "🧑‍🎤 *Anime:*\n" + "\n".join(f"• {a}" for a in data["anime"]) + "\n\n"
    if data["cartoon"]:
        msg += "🦸 *Cartoon/Animado:*\n" + "\n".join(f"• {c}" for c in data["cartoon"]) + "\n\n"
    return msg.strip()

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
        if not data["fecha"]:
            return
        ultima_fecha = await obtener_ultima_fecha_mgs()
        if data["fecha"] != ultima_fecha:
            await send_long_message(
                context.bot,
                MGS_GROUP_ID,
                formato_mgs_msg(data),
                parse_mode="Markdown",
                thread_id=MGS_THREAD_ID
            )
            await guardar_ultima_fecha_mgs(data["fecha"])
    except Exception as e:
        logging.error(f"Error en actualización MGS: {e}")

async def pelis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        data = await scrape_mgs_content()
        msg = formato_mgs_msg(data)
        if update.effective_chat.type == "private":
            await send_long_message(context.bot, update.effective_chat.id, msg, parse_mode="Markdown")
        else:
            await send_long_message(context.bot, MGS_GROUP_ID, msg, parse_mode="Markdown", thread_id=MGS_THREAD_ID)
            thread_actual = getattr(getattr(update, "message", None), "message_thread_id", None)
            if thread_actual != MGS_THREAD_ID:
                await send_long_message(context.bot, MGS_GROUP_ID, "El listado fue enviado al tema Actualización de contenido APP MGS.", thread_id=MGS_THREAD_ID)
    except Exception as e:
        await send_long_message(context.bot, MGS_GROUP_ID, f"Error: {e}", thread_id=MGS_THREAD_ID)

# --- COMANDO /cartelera ---
async def cartelera(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if update.effective_chat.type == "private":
            destino = update.effective_chat.id
            thread_id = None
        else:
            destino = GENERAL_CHAT_ID
            thread_id = EVENTOS_DEPORTIVOS_THREAD_ID

        hoy, manana = dias_a_mostrar()
        partidos = await scrape_cartelera_table()
        partidos_hoy = filtra_partidos_por_fecha(partidos, hoy)
        partidos_manana = filtra_partidos_por_fecha(partidos, manana)

        if partidos_hoy:
            agrupados_hoy = agrupa_partidos_por_campeonato(partidos_hoy)
            mensaje_hoy = formato_mensaje_partidos(agrupados_hoy, hoy)
            await send_long_message(context.bot, destino, mensaje_hoy, parse_mode="Markdown", thread_id=thread_id)
        else:
            await send_long_message(context.bot, destino, "No hay partidos para hoy.", thread_id=thread_id)

        if partidos_manana:
            agrupados_manana = agrupa_partidos_por_campeonato(partidos_manana)
            mensaje_manana = formato_mensaje_partidos(agrupados_manana, manana)
            await send_long_message(context.bot, destino, mensaje_manana, parse_mode="Markdown", thread_id=thread_id)
        else:
            await send_long_message(context.bot, destino, "No hay partidos para mañana.", thread_id=thread_id)

        if update.effective_chat.type != "private":
            thread_actual = None
            if update.message and hasattr(update.message, "message_thread_id"):
                thread_actual = update.message.message_thread_id
            if thread_actual != EVENTOS_DEPORTIVOS_THREAD_ID:
                await send_long_message(context.bot, GENERAL_CHAT_ID, "La cartelera fue enviada al tema EVENTOS DEPORTIVOS.", thread_id=GENERAL_THREAD_ID)

    except Exception as e:
        await send_long_message(context.bot, GENERAL_CHAT_ID, f"Error: {str(e)}", thread_id=EVENTOS_DEPORTIVOS_THREAD_ID)
        logging.error(f"Error en /cartelera: {e}")

# --- ENVÍO AUTOMÁTICO DIARIO CARTELERA ---
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

# --- COMANDO /htmlcartelera ---
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

# --- COMANDO /textocartelera ---
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

# --- COMANDO /hora ---
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

# --- MODO NOCHE MANUAL ---
async def modo_noche_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_admins = await context.bot.get_chat_administrators(GENERAL_CHAT_ID)
    admin_ids = [admin.user.id for admin in chat_admins]
    if user_id not in admin_ids:
        await update.message.reply_text("Solo el administrador puede activar el modo noche manualmente.")
        return
    await activar_modo_noche(context, GENERAL_CHAT_ID)
    await send_long_message(context.bot, GENERAL_CHAT_ID, "Modo noche activado manualmente hasta las 08:00.", thread_id=GENERAL_THREAD_ID)

# --- MODO NOCHE AUTOMÁTICO (CORREGIDO) ---
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

# --- SALUDO SEGÚN HORA ---
def obtener_saludo():
    hora = datetime.datetime.now(TZ).hour
    if 6 <= hora < 12:
        return "¡Buenos días!"
    elif 12 <= hora < 19:
        return "¡Buenas tardes!"
    else:
        return "¡Buenas noches!"

# --- MENSAJE BIENVENIDA ---
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

# --- MENSAJE DESPEDIDA ---
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

# --- FILTRO DE MENSAJES MODO NOCHE ---
async def restringir_mensajes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if hasattr(update.message, "message_thread_id"):
        if update.message.message_thread_id != GENERAL_THREAD_ID:
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

# --- RESPUESTA GENERAL EN GRUPO (tema General, cualquier mensaje excepto admins) ---
async def respuesta_general(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if hasattr(update.message, "message_thread_id"):
        if update.message.message_thread_id != GENERAL_THREAD_ID:
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

# --- RESPUESTA PRIVADA ---
async def respuesta_privada(update: Update, context: ContextTypes.DEFAULT_TYPE):
    saludo = obtener_saludo()
    await update.message.reply_text(
        f"{saludo} 👋 Soy un bot automático.\n"
        "Si tienes preguntas o necesitas soporte, por favor contacta directamente al administrador (@Daayaanss).\n"
        "También puedes escribir /ayuda para ver información y recursos útiles."
    )

# --- COMANDO /ayuda ---
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
