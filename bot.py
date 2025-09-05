import os
import re
from datetime import datetime, time, timedelta
from telegram import Update, ChatPermissions
from telegram.ext import Application, MessageHandler, filters, ContextTypes

TOKEN = os.getenv("TELEGRAM_TOKEN")
PORT = int(os.environ.get('PORT', '8080'))
WEBHOOK_PATH = f"/webhook/{TOKEN[:10]}"  # El path único del webhook

GRUPO_NOMBRE = "D.N.A. TV"
CANAL_GENERAL = "General"
HORA_INICIO_NOCHE = 23
HORA_FIN_NOCHE = 8

modo_noche_avisado = False
modo_dia_avisado = False

def es_general(update: Update):
    chat = update.message.chat
    return getattr(chat, "title", None) == CANAL_GENERAL and chat.type in ["supergroup", "group"]

async def bienvenida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("Handler: bienvenida")
    if not es_general(update):
        return
    for usuario in update.message.new_chat_members:
        mensaje = (
            f"👋🎉 {usuario.first_name} BIENVENIDO(A) A NUESTRO SELECTO GRUPO, MANTENTE SIEMPRE AL DIA Y ACTUALIZADO 😎🤖\n\n"
            "Reglas del grupo:\n- Ser siempre amables con todos los integrantes.\n- Pedir las cosas con respeto."
        )
        await update.message.reply_text(mensaje)

async def despedida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("Handler: despedida")
    if not es_general(update):
        return
    usuario = update.message.left_chat_member
    mensaje = f"👋 CHAO {usuario.first_name}, DESPUÉS NO PIDAS AYUDA 🤷🏻‍♂️"
    await update.message.reply_text(mensaje)

async def modo_noche(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global modo_noche_avisado
    print("Handler: modo_noche")
    if not es_general(update):
        return
    now = datetime.now()
    if now.hour >= HORA_INICIO_NOCHE or now.hour < HORA_FIN_NOCHE:
        if not modo_noche_avisado:
            await update.message.reply_text("🌙 El canal General ha entrado en MODO NOCHE: no se podrán enviar mensajes hasta las 08:00")
            modo_noche_avisado = True
        until_time = datetime.combine(now.date(), time(HORA_FIN_NOCHE))
        if now.hour >= HORA_INICIO_NOCHE:
            until_time += timedelta(days=1)
        try:
            await context.bot.restrict_chat_member(
                chat_id=update.message.chat.id,
                user_id=update.message.from_user.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until_time
            )
            await update.message.reply_text(f"⛔ {update.message.from_user.first_name}, no se permite enviar mensajes hasta las 08:00")
        except Exception as e:
            print(f"Error en modo_noche: {e}")
    else:
        modo_noche_avisado = False

async def aviso_fin_modo_noche(context: ContextTypes.DEFAULT_TYPE):
    global modo_dia_avisado
    print("Job: aviso_fin_modo_noche")
    app = context.application
    now = datetime.now()
    if now.hour == HORA_FIN_NOCHE and not modo_dia_avisado:
        for update in await app.bot.get_updates():
            if hasattr(update, 'message'):
                chat = update.message.chat
                if getattr(chat, "title", None) == CANAL_GENERAL and chat.type in ["supergroup", "group"]:
                    await app.bot.send_message(chat_id=chat.id,
                        text="☀️ El modo noche ha terminado. ¡Ya puedes enviar mensajes! 😎")
                    modo_dia_avisado = True
    elif now.hour != HORA_FIN_NOCHE:
        modo_dia_avisado = False

async def saludo_general(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("Handler: saludo_general")
    if not es_general(update):
        return
    now = datetime.now()
    saludo = "¡Buenos días!" if now.hour < 12 else "¡Buenas tardes!"
    await update.message.reply_text(
        f"{saludo} Soy el bot del canal General de D.N.A. TV. Si necesitas recomendación, escribe 'ayuda'."
    )

async def ayuda_general(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("Handler: ayuda_general")
    if not es_general(update):
        return
    await update.message.reply_text(
        "En un momento te atenderá el administrador, mientras tanto verifica el tema ACTUALIZACIONES DE APPS GRATUITAS, puede que encuentres lo que buscas."
    )

async def saludo_privado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("Handler: saludo_privado (mensaje privado recibido)", update.message)
    now = datetime.now()
    saludo = "¡Buenos días!" if now.hour < 12 else "¡Buenas tardes!"
    await update.message.reply_text(
        f"{saludo} Soy un bot, no tengo todas las respuestas. Si necesitas ayuda, por favor contáctate con el administrador."
    )

def main():
    app = Application.builder().token(TOKEN).build()
    regex_ayuda = re.compile(r'^ayuda$', re.IGNORECASE)

    app.add_handler(MessageHandler(filters.ChatType.PRIVATE, saludo_privado))
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, bienvenida))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, despedida))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.StatusUpdate.ALL, modo_noche))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(regex_ayuda), ayuda_general))
    app.add_handler(MessageHandler(filters.TEXT, saludo_general))
    app.job_queue.run_repeating(aviso_fin_modo_noche, interval=60, first=0)

    url_base = os.environ.get('WEBHOOK_BASE', '')
    webhook_url = f"{url_base}{WEBHOOK_PATH}"
    print(f"Usando webhook URL: {webhook_url}")

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        webhook_url=webhook_url,
        webhook_path=WEBHOOK_PATH  # <-- Esto es muy importante para Telegram
    )

if __name__ == "__main__":
    main()
