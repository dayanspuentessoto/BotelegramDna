import logging
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
import datetime
import pytz
import os
from telegram import Update, ChatPermissions
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters, ChatMemberHandler

# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GENERAL_CHAT_ID = "-2421748184"
CANAL_EVENTOS_ID = "-1002421748184"
CARTELERA_URL = "https://www.futbolenvivochile.com/"
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
        # Scroll para carga dinámica
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

async def send_long_message(bot, chat_id, text, parse_mode=None):
    for i in range(0, len(text), 4000):
        await bot.send_message(chat_id=chat_id, text=text[i:i+4000], parse_mode=parse_mode)

# --- COMANDO /cartelera: Muestra partidos en dos mensajes, día actual y siguiente ---
async def cartelera(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        # Detecta si el mensaje proviene de chat privado
        if update.effective_chat.type == "private":
            destino = update.effective_chat.id
        else:
            # Siempre responde en CANAL_EVENTOS_ID si no es privado
            destino = CANAL_EVENTOS_ID

        hoy, manana = dias_a_mostrar()
        partidos = await scrape_cartelera_table()
        partidos_hoy = filtra_partidos_por_fecha(partidos, hoy)
        partidos_manana = filtra_partidos_por_fecha(partidos, manana)

        if partidos_hoy:
            agrupados_hoy = agrupa_partidos_por_campeonato(partidos_hoy)
            mensaje_hoy = formato_mensaje_partidos(agrupados_hoy, hoy)
            await send_long_message(context.bot, destino, mensaje_hoy, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=destino, text="No hay partidos para hoy.")

        if partidos_manana:
            agrupados_manana = agrupa_partidos_por_campeonato(partidos_manana)
            mensaje_manana = formato_mensaje_partidos(agrupados_manana, manana)
            await send_long_message(context.bot, destino, mensaje_manana, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=destino, text="No hay partidos para mañana.")

        # Si el comando se ejecutó en un grupo/canal distinto, avisa al usuario que la cartelera fue enviada a EVENTOS DEPORTIVOS
        if update.effective_chat.type != "private" and update.effective_chat.id != int(CANAL_EVENTOS_ID):
            await update.message.reply_text("La cartelera fue enviada al canal EVENTOS DEPORTIVOS.")

    except Exception as e:
        await context.bot.send_message(chat_id=CANAL_EVENTOS_ID, text=f"Error: {str(e)}")
        logging.error(f"Error en /cartelera: {e}")

# --- ENVÍO AUTOMÁTICO DIARIO AL CANAL DE EVENTOS DEPORTIVOS ---
async def enviar_eventos_diarios(context: ContextTypes.DEFAULT_TYPE):
    try:
        hoy, manana = dias_a_mostrar()
        partidos = await scrape_cartelera_table()
        partidos_hoy = filtra_partidos_por_fecha(partidos, hoy)
        partidos_manana = filtra_partidos_por_fecha(partidos, manana)

        if partidos_hoy:
            agrupados_hoy = agrupa_partidos_por_campeonato(partidos_hoy)
            mensaje_hoy = formato_mensaje_partidos(agrupados_hoy, hoy)
            await send_long_message(context.bot, CANAL_EVENTOS_ID, mensaje_hoy, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=CANAL_EVENTOS_ID, text="No hay partidos para hoy.")

        if partidos_manana:
            agrupados_manana = agrupa_partidos_por_campeonato(partidos_manana)
            mensaje_manana = formato_mensaje_partidos(agrupados_manana, manana)
            await send_long_message(context.bot, CANAL_EVENTOS_ID, mensaje_manana, parse_mode="Markdown")
        else:
            await context.bot.send_message(chat_id=CANAL_EVENTOS_ID, text="No hay partidos para mañana.")

    except Exception as e:
        await context.bot.send_message(chat_id=CANAL_EVENTOS_ID, text=f"Error al obtener cartelera: {str(e)}")
        logging.error(f"Error en envío diario: {e}")

# --- COMANDO /htmlcartelera: Envía HTML de la cartelera completa ---
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

# --- COMANDO /textocartelera: Envía el texto plano del body ---
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

# --- COMANDO /hora: Muestra la hora local en Chile ---
async def hora_chile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ahora = datetime.datetime.now(TZ)
    await update.message.reply_text(
        f"La hora en Chile es: {ahora.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"(Zona horaria detectada: {TZ.zone})"
    )

# --- MODO NOCHE MANUAL ---
async def modo_noche_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_admins = await context.bot.get_chat_administrators(GENERAL_CHAT_ID)
    admin_ids = [admin.user.id for admin in chat_admins]
    if user_id not in admin_ids:
        await update.message.reply_text("Solo el administrador puede activar el modo noche manualmente.")
        return
    await activar_modo_noche(context, update.effective_chat.id)
    await update.message.reply_text("Modo noche activado manualmente hasta las 08:00.")

# --- MODO NOCHE AUTOMÁTICO ---
async def activar_modo_noche(context: ContextTypes.DEFAULT_TYPE, chat_id):
    permisos = ChatPermissions(
        can_send_messages=False,
        can_send_media_messages=False,
        can_send_polls=False,
        can_send_other_messages=False,
        can_add_web_page_previews=False,
        can_change_info=False,
        can_invite_users=True,
        can_pin_messages=False,
    )
    await context.bot.set_chat_permissions(chat_id, permissions=permisos)
    await context.bot.send_message(chat_id=chat_id, text="🌙 Modo noche activado. El canal queda restringido hasta las 08:00.")

async def desactivar_modo_noche(context: ContextTypes.DEFAULT_TYPE):
    permisos = ChatPermissions(
        can_send_messages=True,
        can_send_media_messages=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
        can_change_info=False,
        can_invite_users=True,
        can_pin_messages=False,
    )
    await context.bot.set_chat_permissions(GENERAL_CHAT_ID, permissions=permisos)
    await context.bot.send_message(GENERAL_CHAT_ID, text="☀️ ¡Fin del modo noche! Ya pueden enviar mensajes.")

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
    chat_member = update.chat_member
    if chat_member.new_chat_members:
        for member in chat_member.new_chat_members:
            nombre = member.first_name if member.first_name else ""
            apellidos = member.last_name if member.last_name else ""
            nombre_completo = f"{nombre} {apellidos}".strip()
            if not nombre_completo:
                nombre_completo = member.username if member.username else "Usuario"
            await context.bot.send_message(
                GENERAL_CHAT_ID,
                text=f"{nombre_completo} BIENVENIDO(A) A NUESTRO SELECTO GRUPO D.N.A. TV, MANTENTE SIEMPRE AL DIA Y ACTUALIZADO, SI TIENES ALGUNA DUDA ESCRIBE EL COMANDO AYUDA PARA MAS INFO 😎🤖",
            )

# --- MENSAJE DESPEDIDA ---
async def despedida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_member = update.chat_member
    if chat_member.old_chat_member.status not in ['left', 'kicked']:
        return
    user = chat_member.old_chat_member.user
    nombre = user.first_name if user.first_name else ""
    apellidos = user.last_name if user.last_name else ""
    nombre_completo = f"{nombre} {apellidos}".strip()
    if not nombre_completo:
        nombre_completo = user.username if user.username else "Usuario"
    await context.bot.send_message(
        GENERAL_CHAT_ID,
        text=f"{nombre_completo} ADIOS, DESPUES NO RECLAMES NI PREGUNTES🤷🏻‍♂"
    )

# --- FILTRO DE MENSAJES MODO NOCHE ---
async def restringir_mensajes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hora = datetime.datetime.now(TZ).hour
    if 23 <= hora or hora < 8:
        user_id = update.effective_user.id
        chat_admins = await context.bot.get_chat_administrators(GENERAL_CHAT_ID)
        admin_ids = [admin.user.id for admin in chat_admins]
        if user_id in admin_ids:
            return
        await update.message.delete()

# --- RESPUESTA GENERAL EN GRUPO ---
async def respuesta_general(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_admins = await context.bot.get_chat_administrators(GENERAL_CHAT_ID)
    admin_ids = [admin.user.id for admin in chat_admins]
    if user_id in admin_ids:
        return
    hora = datetime.datetime.now(TZ).hour
    if 8 <= hora < 23:
        saludo = obtener_saludo()
        await update.message.reply_text(
            f"{saludo} 👋 Si necesitas ayuda, escribe el comando /ayuda para recibir información clara sobre cómo contactar al administrador y resolver tus dudas."
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
    await update.message.reply_text(texto)

def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Comandos
    application.add_handler(CommandHandler("cartelera", cartelera))
    application.add_handler(CommandHandler("htmlcartelera", enviar_html))
    application.add_handler(CommandHandler("textocartelera", enviar_texto_body))
    application.add_handler(CommandHandler("hora", hora_chile))
    application.add_handler(CommandHandler("noche", modo_noche_manual))
    application.add_handler(CommandHandler("ayuda", ayuda))

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

    application.add_handler(ChatMemberHandler(bienvenida, ChatMemberHandler.CHAT_MEMBER))
    application.add_handler(ChatMemberHandler(despedida, ChatMemberHandler.CHAT_MEMBER))

    application.add_handler(MessageHandler(filters.ALL & filters.Chat(GENERAL_CHAT_ID), restringir_mensajes))
    application.add_handler(
        MessageHandler(
            filters.TEXT & filters.Chat(GENERAL_CHAT_ID) & ~filters.COMMAND & ~filters.Regex(r"^/ayuda"),
            respuesta_general
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
