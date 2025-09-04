import asyncio
from telegram import Update, ChatPermissions
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import os
from datetime import datetime, time, timedelta
import logging

TOKEN = os.getenv("TELEGRAM_TOKEN")
GRUPO_GENERAL = "D.N.A. TV"
REGLAS = "- Ser siempre amables con todos los integrantes.\n- Pedir las cosas con respeto."
HORA_INICIO_NOCHE = 23
HORA_FIN_NOCHE = 8

modo_noche_avisado = False
modo_dia_avisado = False
is_job_running = False  # Flag to check if job is running

async def bienvenida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for usuario in update.message.new_chat_members:
        chat = update.message.chat
        if chat.title == GRUPO_GENERAL:
            mensaje = f"👋🎉 {usuario.first_name} BIENVENIDO(A) A NUESTRO SELECTO GRUPO, MANTENTE SIEMPRE AL DIA Y ACTUALIZADO 😎🤖\n\nReglas del grupo:\n{REGLAS}"
            await update.message.reply_text(mensaje)

async def despedida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    usuario = update.message.left_chat_member
    chat = update.message.chat
    if chat.title == GRUPO_GENERAL:
        mensaje = f"👋 CHAO {usuario.first_name}, DESPUÉS NO PIDAS AYUDA 🤷🏻‍♂️"
        await update.message.reply_text(mensaje)

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

# Método para enviar aviso de fin de modo noche
async def aviso_fin_modo_noche(app: Application):
    global modo_dia_avisado, is_job_running
    if is_job_running:
        return  # No permite que la tarea se ejecute si ya está corriendo

    is_job_running = True  # Marca la tarea como en ejecución
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
        await asyncio.sleep(60)  # Revisa cada minuto
    finally:
        is_job_running = False  # Marca la tarea como no en ejecución

def main():
    app = Application.builder().token(TOKEN).build()

    # Handlers para GENERAL
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, bienvenida))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, despedida))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.StatusUpdate.ALL, modo_noche))

    # Tarea programada para avisar fin de modo noche
    app.job_queue.run_repeating(lambda ctx: asyncio.create_task(aviso_fin_modo_noche(app)), interval=60, first=0)

    print("🤖 Bot en ejecución...")
    app.run_polling()

if __name__ == "__main__":
    main()
