from telegram import Update, ChatPermissions
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import os
from datetime import datetime, time, timedelta
import asyncio

TOKEN = os.getenv("TELEGRAM_TOKEN")

GRUPO_GENERAL = "GENERAL"

REGLAS = "- Ser siempre amables con todos los integrantes.\n- Pedir las cosas con respeto."

HORA_INICIO_NOCHE = 23
HORA_FIN_NOCHE = 8

modo_noche_avisado = False
modo_dia_avisado = False

# Bandera para controlar la ejecución de la tarea de aviso de fin de modo noche
tarea_en_ejecucion = False

# ============================
# Bienvenida
# ============================
async def bienvenida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for usuario in update.message.new_chat_members:
        chat = update.message.chat
        if chat.title == GRUPO_GENERAL:
            mensaje = f"👋🎉 {usuario.first_name} BIENVENIDO(A) A NUESTRO SELECTO GRUPO, MANTENTE SIEMPRE AL DIA Y ACTUALIZADO 😎🤖\n\nReglas del grupo:\n{REGLAS}"
            await update.message.reply_text(mensaje)

# ============================
# Despedida
# ============================
async def despedida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuario = update.message.left_chat_member
    chat = update.message.chat
    if chat.title == GRUPO_GENERAL:
        mensaje = f"👋 CHAO {usuario.first_name}, DESPUÉS NO PIDAS AYUDA 🤷🏻‍♂️"
        await update.message.reply_text(mensaje)

# ============================
# Modo noche
# ============================
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

# ============================
# Aviso fin de modo noche
# ============================
async def aviso_fin_modo_noche(app: Application):
    global modo_dia_avisado
    global tarea_en_ejecucion

    if tarea_en_ejecucion:
        return  # Si la tarea ya está en ejecución, no la ejecutamos nuevamente

    tarea_en_ejecucion = True  # Marcamos la tarea como en ejecución
    try:
        while True:
            now = datetime.now()
            if now.hour == HORA_FIN_NOCHE and not modo_dia_avisado:
                for chat_data in await app.bot.get_updates():
                    if hasattr(chat_data, 'message'):
                        chat = chat_data.message.chat
                        if chat.title == GRUPO_GENERAL:
                            await app.bot.send_message(chat_id=chat.id, text="☀️ El modo noche ha terminado. ¡Ya puedes enviar mensajes! 😎")
                            modo_dia_avisado = True
            elif now.hour != HORA_FIN_NOCHE:
                modo_dia_avisado = False
            await asyncio.sleep(60)  # Revisa cada minuto
    finally:
        tarea_en_ejecucion = False  # Marcamos la tarea como no en ejecución cuando termina

# ============================
# Respuesta en privado
# ============================
async def respuesta_privada(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuario = update.message.from_user
    now = datetime.now()

    # Saludo con buenos días o buenas tardes
    if now.hour < 12:
        saludo = "¡Buenos días!"
    else:
        saludo = "¡Buenas tardes!"

    # Respuesta indicando que es un bot
    mensaje = f"{saludo} Soy un bot y no tengo todas las respuestas. Si necesitas ayuda, por favor contacta con el administrador."
    await update.message.reply_text(mensaje)

# ============================
# Respuesta en grupo
# ============================
async def respuesta_grupo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuario = update.message.from_user
    mensaje = update.message.text.lower()

    # Saludo con buenos días o buenas tardes en función de la hora
    if datetime.now().hour < 12:
        saludo = "¡Buenos días!"
    else:
        saludo = "¡Buenas tardes!"

    # Si no es "ayuda", responde pidiendo que escriban "ayuda"
    if mensaje != "ayuda":
        await update.message.reply_text(f"{saludo} Si necesitas ayuda, escribe la palabra 'ayuda'.")
    else:
        # Si el mensaje es "ayuda", indica que el administrador atenderá pronto
        await update.message.reply_text("En un momento te atenderá el administrador, mientras tanto verifica el tema ACTUALIZACIONES DE APPS GRATUITAS, puede que encuentres lo que buscas")

# ============================
# Inicialización del bot
# ============================
def main():
    app = Application.builder().token(TOKEN).build()

    # Handlers para GENERAL
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, bienvenida))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, despedida))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.StatusUpdate.ALL, modo_noche))

    # Respuesta en privado
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.PRIVATE, respuesta_privada))

    # Respuesta en grupo
    app.add_handler(MessageHandler(filters.TEXT & filters.ChatType.GROUP, respuesta_grupo))

    # Tarea programada para avisar fin de modo noche
    app.job_queue.run_repeating(lambda ctx: asyncio.create_task(aviso_fin_modo_noche(app)), interval=60, first=0)

    print("🤖 Bot en ejecución...")
    app.run_polling()

if __name__ == "__main__":
    main()
