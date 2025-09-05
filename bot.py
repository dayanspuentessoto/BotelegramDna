import os
import logging
import datetime
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from telegram import Update, ChatPermissions
from telegram.ext import (
    Application, ContextTypes, CommandHandler, MessageHandler, filters, ChatMemberHandler
)

# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_IDS = [5032964793]
GENERAL_CHAT_ID = "-2421748184"
GROUP_ID = "-2421748184"

CARTELERA_URL = "https://www.emol.com/movil/deportes/carteleradirecttv/index.aspx"
TZ = ZoneInfo("America/Santiago")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# --- Scraping Cartelera ---
async def scrape_cartelera(context: ContextTypes.DEFAULT_TYPE, chat_id):
    try:
        resp = requests.get(CARTELERA_URL, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        eventos_encontrados = False

        for bloque_fecha in soup.find_all("div", class_="cartelera_fecha"):
            fecha = bloque_fecha.get_text(strip=True)
            await context.bot.send_message(chat_id=chat_id, text=f"📅 {fecha}")

            bloque_eventos = bloque_fecha.find_next_sibling("div", class_="cartelera_eventos")
            if not bloque_eventos:
                continue

            for evento in bloque_eventos.find_all("div", class_="cartelera_evento"):
                hora = evento.find("div", class_="cartelera_hora")
                hora_txt = hora.get_text(strip=True) if hora else ""

                nombre = evento.find("div", class_="cartelera_nombre")
                nombre_txt = nombre.get_text(strip=True) if nombre else ""

                logo_img = evento.find("img")
                logo_url = logo_img['src'] if logo_img and logo_img.has_attr('src') else ""

                mensaje = f"🕒 {hora_txt}\n🏟️ {nombre_txt}"
                await context.bot.send_message(chat_id=chat_id, text=mensaje)
                if logo_url:
                    await context.bot.send_photo(chat_id=chat_id, photo=logo_url)
                eventos_encontrados = True

        if not eventos_encontrados:
            await context.bot.send_message(chat_id=chat_id, text="No hay eventos deportivos programados para hoy y mañana.")
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"Error obteniendo cartelera: {e}")

# --- Comando /cartelera ---
async def cartelera(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await scrape_cartelera(context, update.effective_chat.id)

# --- Comando /noche manual ---
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

# --- Saludo según hora ---
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

# --- MAIN ---
def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Comandos
    application.add_handler(CommandHandler("cartelera", cartelera))
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
        port=int(os.environ.get("PORT", 8443)),
        webhook_url=os.environ.get("WEBHOOK_URL")
    )

if __name__ == "__main__":
    main()
