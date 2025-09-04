import os
from telegram import Update, ChatPermissions
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv
from datetime import datetime, time, timedelta
import asyncio

# Cargar las variables de entorno desde el archivo .env (asegúrate de instalar python-dotenv)
load_dotenv()

# Obtener el token de la variable de entorno
TOKEN = os.getenv("TELEGRAM_TOKEN")

# Nombre del grupo
GRUPO_GENERAL = "D.N.A. TV"

# Nombres de los temas
TEMAS_CERRADOS = ["ACTUALIZACIONES DE APPS GRATUITAS", "Información aplicación de pago"]

REGLAS = "- Ser siempre amables con todos los integrantes.\n- Pedir las cosas con respeto."

# Horas del modo noche
HORA_INICIO_NOCHE = 23
HORA_FIN_NOCHE = 8

# Diccionario para gestionar el estado de los avisos de modo noche/día
estado_modo_noche = {}

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
    chat = update.message.chat
    if chat.title != GRUPO_GENERAL:
        return

    now = datetime.now()
    user_id = update.message.from_user.id

    # Inicializa el estado del chat si no existe
    if chat.id not in estado_modo_noche:
        estado_modo_noche[chat.id] = {"modo_noche_avisado": False, "modo_dia_avisado": False}

    if now.hour >= HORA_INICIO_NOCHE or now.hour < HORA_FIN_NOCHE:
        if not estado_modo_noche[chat.id]["modo_noche_avisado"]:
            await update.message.reply_text("🌙 El grupo ha entrado en MODO NOCHE: no se podrán enviar mensajes hasta las 08:00")
            estado_modo_noche[chat.id]["modo_noche_avisado"] = True

        until_time = datetime.combine(now.date(), time(HORA_FIN_NOCHE)) + timedelta(days=1 if now.hour >= HORA_INICIO_NOCHE else 0)
        try:
            await context.bot.restrict_chat_member(
                chat_id=chat.id,
                user_id=user_id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until_time
            )
            await update.message.reply_text(f"⛔ {update.message.from_user.first_name}, no se permite enviar mensajes hasta las 08:00")
        except Exception as e:
            print(f"Error al restringir al usuario {user_id}: {e}")
    else:
        estado_modo_noche[chat.id]["modo_noche_avisado"] = False

# ===========================
# Filtrar mensajes por tema
# ===========================
async def filtrar_mensajes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.message.chat
    if chat.title == GRUPO_GENERAL:  # Solo permite mensajes en el grupo "D.N.A. TV"
        if update.message.chat.title in TEMAS_CERRADOS:
            await update.message.delete()  # Elimina el mensaje en los temas cerrados
        else:
            # El bot puede interactuar en el tema 'General' u otros temas abiertos
            pass

# ===========================
# Aviso fin de modo noche
# ===========================
async def aviso_fin_modo_noche(app: Application):
    while True:
        now = datetime.now()
        if now.hour == HORA_FIN_NOCHE:
            for chat_id, estado in estado_modo_noche.items():
                if not estado["modo_dia_avisado"]:
                    try:
                        await app.bot.send_message(chat_id=chat_id, text="☀️ El modo noche ha terminado. ¡Ya puedes enviar mensajes! 😎")
                        estado_modo_noche[chat_id]["modo_dia_avisado"] = True
                    except Exception as e:
                        print(f"Error al enviar mensaje de fin de modo noche al chat {chat_id}: {e}")
        elif now.hour != HORA_FIN_NOCHE:
            for estado in estado_modo_noche.values():
                estado["modo_dia_avisado"] = False
        await asyncio.sleep(60)  # Revisa cada minuto

# ===========================
# Inicialización del bot
# ===========================
def main():
    app = Application.builder().token(TOKEN).build()

    # Handlers para GENERAL
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, bienvenida))
    app.add_handler(MessageHandler(filters.StatusUpdate.LEFT_CHAT_MEMBER, despedida))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.StatusUpdate.ALL, filtrar_mensajes))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.StatusUpdate.ALL, modo_noche))

    # Tarea programada para avisar fin de modo noche
    app.job_queue.run_repeating(lambda ctx: asyncio.create_task(aviso_fin_modo_noche(app)), interval=60, first=0)

    print("🤖 Bot en ejecución...")
    app.run_polling()

if __name__ == "__main__":
    main()
