from telegram import Update, ChatPermissions
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import asyncio
from datetime import datetime, time, timedelta
import os

TOKEN = os.getenv("TELEGRAM_TOKEN")

GRUPO_GENERAL = "GENERAL"
HORA_INICIO_NOCHE = 23
HORA_FIN_NOCHE = 8

modo_noche_avisado = False
modo_dia_avisado = False

# Variable de control para evitar la ejecución simultánea de las tareas programadas
job_running = False


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
    global modo_dia_avisado, job_running
    if job_running:
        return  # Evita que se ejecute si el job ya está corriendo
    job_running = True  # Marcar que el job está en ejecución

    try:
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
    finally:
        job_running = False  # Liberar el control para permitir la siguiente ejecución

    await asyncio.sleep(60)  # Revisa cada minuto


# ===========================
# Bienvenida
# ===========================
async def bienvenida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for usuario in update.message.new_chat_members:
        chat = update.message.chat
        if chat.title == GRUPO_GENERAL:
            mensaje = f"👋🎉 {usuario.first_name} BIENVENIDO(A) A NUESTRO SELECTO GRUPO, MANTENTE SIEMPRE AL DIA Y ACTUALIZADO 😎🤖\n\nReglas del grupo:\n- Ser siempre amables con todos los integrantes.\n- Pedir las cosas con respeto."
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
# Responder en privado
# ===========================
async def responder_privado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuario = update.message.from_user
    mensaje = update.message.text.lower()

    # Saludar según la hora
    now = datetime.now()
    if now.hour < 12:
        saludo = "¡Buenos días!"
    else:
        saludo = "¡Buenas tardes!"

    # Si alguien escribe algo en privado
    if mensaje != "ayuda":
        await update.message.reply_text(f"{saludo} Soy un bot, no tengo todas las respuestas. Si necesitas ayuda, por favor contáctate con el administrador.")

    if mensaje == "ayuda":
        await update.message.reply_text("En un momento te atenderá el administrador, mientras tanto verifica el tema ACTUALIZACIONES DE APPS GRATUITAS, puede que encuentres lo que buscas.")


# ===========================
# Inicialización del bot
# ===========================
def main():
    app = Application.builder().token(TOKEN).build()

    # Handlers para GENERAL
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, bienvenida))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, despedida))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.StatusUpdate.ALL, modo_noche))
    
    # Respuesta en privado
    app.add_handler(MessageHandler(filters.TEXT, responder_privado))

    # Tarea programada para avisar fin de modo noche
    app.job_queue.run_repeating(lambda ctx: asyncio.create_task(aviso_fin_modo_noche(app)), interval=60, first=0)

    print("🤖 Bot en ejecución...")
    app.run_polling()

if __name__ == "__main__":
    main()
