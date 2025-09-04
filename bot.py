from telegram import Update, ChatPermissions
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CallbackContext
import os
from datetime import datetime, time, timedelta
import asyncio

TOKEN = os.getenv("TELEGRAM_TOKEN")  # El token está configurado en Railway

# Nombre del grupo y el tema
GRUPO_GENERAL = "D.N.A. TV"

REGLAS = "- Ser siempre amables con todos los integrantes.\n- Pedir las cosas con respeto."

# Horas del modo noche
HORA_INICIO_NOCHE = 23
HORA_FIN_NOCHE = 8

modo_noche_avisado = False
modo_dia_avisado = False

# ===========================
# Bienvenida
# ===========================
async def bienvenida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for usuario in update.message.new_chat_members:
        chat = update.message.chat
        if chat.title == GRUPO_GENERAL:
            mensaje = f"👋🎉 {usuario.first_name} BIENVENIDO(A) A NUESTRO SELECTO GRUPO, MANTENTE SIEMPRE AL DIA Y ACTUALIZADO 😎🤖\n\nReglas del grupo:\n{REGLAS}"
            await update.message.reply_text(mensaje)

# ===========================
# Despedida
# ===========================
async def despedida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuario = update.message.left_chat_member
    chat = update.message.chat
    if chat.title == GRUPO_GENERAL:
        mensaje = f"👋 CHAO {usuario.first_name}, DESPUÉS NO PIDAS AYUDA 🤷🏻‍♂️"
        await update.message.reply_text(mensaje)

# ===========================
# Modo noche
# ===========================
async def modo_noche(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global modo_noche_avisado
    now = datetime.now()
    chat = update.message.chat

    if chat.title != GRUPO_GENERAL:
        return

    if now.hour >= HORA_INICIO_NOCHE or now.hour < HORA_FIN_NOCHE:
        if not modo_noche_avisado:
            await update.message.reply_text("🌙 El grupo ha entrado en MODO NOCHE: no se podrán enviar mensajes hasta las 08:00")
            modo_noche_avisado = True

        until_time = datetime.combine(now.date(), time(HORA_FIN_NOCHE)) + timedelta(days=1 if now.hour >= HORA_INICIO_NOCHE else 0)
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat.id,
                user_id=update.message.from_user.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until_time
            )
            await update.message.reply_text(f"⛔ {update.message.from_user.first_name}, no se permite enviar mensajes hasta las 08:00")
        except:
            pass
    else:
        modo_noche_avisado = False

# ===========================
# Aviso fin de modo noche
# ===========================
async def aviso_fin_modo_noche(app: Application):
    global modo_dia_avisado
    while True:
        now = datetime.now()
        if now.hour == HORA_FIN_NOCHE and not modo_dia_avisado:
            # Buscar chat GENERAL en los updates recientes
            for chat_data in await app.bot.get_updates():
                if hasattr(chat_data, 'message'):
                    chat = chat_data.message.chat
                    if chat.title == GRUPO_GENERAL:
                        await app.bot.send_message(chat_id=chat.id, text="☀️ El modo noche ha terminado. ¡Ya puedes enviar mensajes! 😎")
                        modo_dia_avisado = True
        elif now.hour != HORA_FIN_NOCHE:
            modo_dia_avisado = False
        await asyncio.sleep(60)  # Revisa cada minuto

# ===========================
# Responder al bot directamente en el grupo
# ===========================
async def responder_al_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.message.chat

    if chat.title == GRUPO_GENERAL:
        if update.message.text:
            user_message = update.message.text.lower()
            if "hola" in user_message:
                now = datetime.now()
                saludo = "buenos días" if now.hour < 12 else "buenas tardes"
                await update.message.reply_text(f"¡{saludo} {update.message.from_user.first_name}! ¿Cómo puedo ayudarte? 😎")
            elif "ayuda" in user_message:
                await update.message.reply_text(f"En un momento te atenderá el administrador, mientras tanto verifica el tema ACTUALIZACIONES DE APPS GRATUITAS, puede que encuentres lo que buscas.")
            else:
                await update.message.reply_text(f"Hola {update.message.from_user.first_name}, si necesitas ayuda, escribe la palabra 'ayuda'. 😎")

# ===========================
# Responder al bot en privado
# ===========================
async def responder_en_privado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.message.chat

    if chat.type == "private":  # Asegúrate de que el mensaje es en privado
        if update.message.text:
            user_message = update.message.text.lower()
            now = datetime.now()
            saludo = "buenos días" if now.hour < 12 else "buenas tardes"

            if "hola" in user_message:
                await update.message.reply_text(f"¡Hola {update.message.from_user.first_name}! Soy un bot, no tengo todas las respuestas, pero puedo ayudarte en lo que pueda. Si necesitas asistencia, por favor contacta con el administrador. 😎")
            elif "ayuda" in user_message:
                await update.message.reply_text(f"En un momento te atenderá el administrador, mientras tanto verifica el tema ACTUALIZACIONES DE APPS GRATUITAS, puede que encuentres lo que buscas.")
            else:
                await update.message.reply_text(f"¡Hola {update.message.from_user.first_name}! Soy un bot, no tengo todas las respuestas, pero puedo ayudarte en lo que pueda. Si necesitas asistencia, por favor contacta con el administrador. 😎")

# ===========================
# Inicialización del bot
# ===========================
def main():
    app = Application.builder().token(TOKEN).build()

    # Handlers para GENERAL
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, bienvenida))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, despedida))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.StatusUpdate.ALL, modo_noche))

    # Responder al bot directamente en el grupo
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUP, responder_al_bot))

    # Responder al bot en privado
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, responder_en_privado))

    # Tarea programada para avisar fin de modo noche
    app.job_queue.run_repeating(lambda ctx: asyncio.create_task(aviso_fin_modo_noche(app)), interval=60, first=0)

    print("🤖 Bot en ejecución...")
    app.run_polling()

if __name__ == "__main__":
    main()
