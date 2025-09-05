import logging
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from bs4 import BeautifulSoup
import datetime
import pytz
import os
from telegram import Update, ChatPermissions, InputFile
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters, ChatMemberHandler

# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_IDS = [5032964793]
GENERAL_CHAT_ID = "-2421748184"
CANAL_EVENTOS_ID = "-1002421748184"

CARTELERA_URL = "https://www.futbolenvivochile.com/"
TZ = pytz.timezone("America/Santiago")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# --- Scraping Cartelera de Futbol en Vivo Chile ---
async def scrape_cartelera():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        try:
            await page.goto(CARTELERA_URL, timeout=120000)
            # Espera 10 segundos para asegurar que el contenido cargado por JS aparezca
            await page.wait_for_timeout(10000)
        except PlaywrightTimeoutError:
            logging.error("Timeout: No se pudo cargar la página en el tiempo esperado.")
            await browser.close()
            return {}

        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")
        
        tomorrow = datetime.datetime.now(TZ).date() + datetime.timedelta(days=1)
        tomorrow_str = tomorrow.strftime("%d-%m-%Y")

        fecha_header = None
        for tag in soup.find_all(text=True):
            if "mañana" in tag.lower() and tomorrow_str in tag:
                fecha_header = tag.parent
                break

        if not fecha_header:
            logging.info("No se encontró el bloque de la fecha buscada")
            return {}

        campeonatos = {}
        current = fecha_header.find_next_sibling()
        campeonato_actual = None

        while current:
            txt = current.get_text(strip=True)
            if any(day in txt.lower() for day in [
                "hoy", "mañana", "domingo", "lunes", "martes", "miércoles", "jueves", "viernes"
            ]) and tomorrow_str not in txt:
                break

            if current.name == "div" and (
                "background" in current.get("style", "") or current.get("class")
            ):
                campeonato_actual = txt
                campeonatos[campeonato_actual] = []
            if current.name == "tr":
                cols = current.find_all("td")
                if len(cols) >= 3:
                    hora = cols[0].get_text(strip=True)
                    equipos = cols[1].get_text(separator=" vs ", strip=True)
                    canales = cols[2].get_text(separator=", ", strip=True)
                    if campeonato_actual:
                        campeonatos[campeonato_actual].append({
                            "fecha": tomorrow_str,
                            "hora": hora,
                            "nombre": equipos,
                            "canales": canales
                        })
            current = current.find_next_sibling()
        logging.info(f"Eventos obtenidos: {campeonatos}")
        return campeonatos

# --- Comando para enviar el HTML como texto ---
async def enviar_html(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.goto(CARTELERA_URL, timeout=120000)
            # Espera 10 segundos para asegurar que el contenido cargado por JS aparezca
            await page.wait_for_selector("table", timeout=20000)
            html = await page.content()
            await browser.close()
            await update.message.reply_text(html[:4000])
            # Si quieres ver más, puedes dividir el HTML y enviarlo en partes:
            # for i in range(4000, len(html), 4000):
            #     await update.message.reply_text(html[i:i+4000])
    except Exception as e:
        await update.message.reply_text(f"Error al obtener HTML: {e}")

# --- Envío de eventos del día siguiente al canal EVENTOS DEPORTIVOS ---
async def enviar_eventos_diarios(context: ContextTypes.DEFAULT_TYPE):
    campeonatos = await scrape_cartelera()
    if not campeonatos:
        await context.bot.send_message(chat_id=CANAL_EVENTOS_ID, text="No hay eventos deportivos programados para mañana.")
        return
    for camp, eventos in campeonatos.items():
        if not eventos:
            continue
        mensaje = f"🏆 *{camp}*\n"
        for evento in eventos:
            mensaje += f"📅 *{evento['fecha']}* - *{evento['hora']}*\n_{evento['nombre']}_\nCanales: {evento['canales']}\n\n"
        await context.bot.send_message(chat_id=CANAL_EVENTOS_ID, text=mensaje, parse_mode="Markdown")

# --- Comando /cartelera manual ---
async def cartelera(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        campeonatos = await scrape_cartelera()
        if not campeonatos:
            await update.message.reply_text("No hay eventos deportivos programados para mañana.")
            return
        mensajes = []
        for camp, eventos in campeonatos.items():
            if not eventos:
                continue
            mensaje = f"🏆 *{camp}*\n"
            for evento in eventos:
                mensaje += f"📅 *{evento['fecha']}* - *{evento['hora']}*\n_{evento['nombre']}_\nCanales: {evento['canales']}\n\n"
            mensajes.append(mensaje)
        for texto in mensajes:
            await update.message.reply_text(texto, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"Error: {str(e)}")
        logging.error(f"Error en /cartelera: {e}")

# --- Modo noche manual ---
async def modo_noche_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("Solo el administrador puede activar el modo noche manualmente.")
        return
    await activar_modo_noche(context, update.effective_chat.id)
    await update.message.reply_text("Modo noche activado manualmente hasta las 08:00.")

# --- Modo noche automático ---
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

# --- Saludo según hora (zona horaria Chile) ---
def obtener_saludo():
    hora = datetime.datetime.now(TZ).hour
    if 6 <= hora < 12:
        return "¡Buenos días!"
    elif 12 <= hora < 19:
        return "¡Buenas tardes!"
    else:
        return "¡Buenas noches!"

# --- Mensaje bienvenida ---
async def bienvenida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for member in update.chat_member.new_chat_members:
        nombre = member.first_name if member.first_name else ""
        apellidos = member.last_name if member.last_name else ""
        nombre_completo = f"{nombre} {apellidos}".strip()
        if not nombre_completo:
            nombre_completo = member.username if member.username else "Usuario"
        await context.bot.send_message(
            GENERAL_CHAT_ID,
            text=f"{nombre_completo} BIENVENIDO(A) A NUESTRO SELECTO GRUPO D.N.A. TV, MANTENTE SIEMPRE AL DIA Y ACTUALIZADO, SI TIENES ALGUNA DUDA ESCRIBE EL COMANDO AYUDA PARA MAS INFO 😎🤖",
        )

# --- Mensaje despedida ---
async def despedida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.chat_member.old_chat_member.status not in ['left', 'kicked']:
        return
    user = update.chat_member.old_chat_member.user
    nombre = user.first_name if user.first_name else ""
    apellidos = user.last_name if user.last_name else ""
    nombre_completo = f"{nombre} {apellidos}".strip()
    if not nombre_completo:
        nombre_completo = user.username if user.username else "Usuario"
    await context.bot.send_message(
        GENERAL_CHAT_ID,
        text=f"{nombre_completo} ADIOS, DESPUES NO RECLAMES NI PREGUNTES🤷🏻‍♂"
    )

# --- Filtro de mensajes modo noche ---
async def restringir_mensajes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hora = datetime.datetime.now(TZ).hour
    if 23 <= hora or hora < 8:
        user_id = update.effective_user.id
        if user_id in ADMIN_IDS:
            return
        await update.message.delete()

# --- RESPUESTA automática fuera de modo noche (grupo) ---
async def respuesta_general(update: Update, context: ContextTypes.DEFAULT_TYPE):
    hora = datetime.datetime.now(TZ).hour
    if 8 <= hora < 23:
        saludo = obtener_saludo()
        await update.message.reply_text(
            f"{saludo} 👋 Si necesitas ayuda, escribe el comando /ayuda para recibir información clara sobre cómo contactar al administrador y resolver tus dudas."
        )

# --- RESPUESTA automática por privado ---
async def respuesta_privada(update: Update, context: ContextTypes.DEFAULT_TYPE):
    saludo = obtener_saludo()
    await update.message.reply_text(
        f"{saludo} 👋 Soy un bot automático.\n"
        "Si tienes preguntas o necesitas soporte, por favor contacta directamente al administrador (@Daayaanss).\n"
        "También puedes escribir /ayuda para ver información y recursos útiles."
    )

# --- Comando /ayuda ---
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
    application.add_handler(CommandHandler("noche", modo_noche_manual))
    application.add_handler(CommandHandler("ayuda", ayuda))

    # Jobs automáticos
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
        name="eventos_diarios"
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

    # --- WEBHOOK ---
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        webhook_url=os.environ.get("WEBHOOK_URL")
    )

if __name__ == "__main__":
    main()
