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
GENERAL_CHAT_ID = "-2421748184"      # Grupo principal D.N.A TV
GROUP_ID = "-2421748184"             # Igual que GENERAL_CHAT_ID
CANAL_EVENTOS_ID = "-1002421748184"  # Canal EVENTOS DEPORTIVOS

CARTELERA_URL = "https://www.emol.com/movil/deportes/carteleradirecttv/index.aspx"
TZ = ZoneInfo("America/Santiago")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# --- Scraping Cartelera ---
def scrape_cartelera():
    url = CARTELERA_URL
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/115.0.0.0 Safari/537.36",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        eventos = []
        fechas_encontradas = soup.find_all("div", class_="cartelera_fecha")
        # Depuración: muestra cantidad de bloques de fecha encontrados
        print(f"Fechas encontradas: {len(fechas_encontradas)}")
        for bloque_fecha in fechas_encontradas:
            fecha = bloque_fecha.get_text(strip=True)
            bloque_eventos = bloque_fecha.find_next_sibling("div", class_="cartelera_eventos")
            if not bloque_eventos:
                continue
            eventos_evento = bloque_eventos.find_all("div", class_="cartelera_evento")
            for evento in eventos_evento:
                hora = evento.find("div", class_="cartelera_hora")
                nombre = evento.find("div", class_="cartelera_nombre")
                logo_img = evento.find("img")
                eventos.append({
                    "fecha": fecha,
                    "hora": hora.get_text(strip=True) if hora else "",
                    "nombre": nombre.get_text(strip=True) if nombre else "",
                    "logo": logo_img['src'] if logo_img and logo_img.has_attr('src') else ""
                })
        return eventos
    except Exception as e:
        print(f"Error scrape_cartelera: {e}")
        return []

# --- Comando /cartelera manual con depuración ---
async def cartelera(update: Update, context: ContextTypes.DEFAULT_TYPE):
    eventos = scrape_cartelera()
    # Muestra la cantidad de eventos encontrados como primer mensaje
    await update.message.reply_text(f"Eventos encontrados: {len(eventos)}")
    if not eventos:
        await update.message.reply_text("No hay eventos deportivos programados para hoy y mañana. ¿Puedes probar más tarde?")
        return
    for evento in eventos[:10]:  # limita a 10 eventos por prueba
        texto = f"📅 *{evento['fecha']}*\n*{evento['hora']}*\n_{evento['nombre']}_"
        if evento['logo']:
            await context.bot.send_photo(chat_id=update.effective_chat.id, photo=evento['logo'], caption=texto, parse_mode="Markdown")
        else:
            await update.message.reply_text(texto, parse_mode="Markdown")

# --- MAIN SOLO CON /cartelera ---
def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    # Comando cartelera con depuración
    application.add_handler(CommandHandler("cartelera", cartelera))

    # --- WEBHOOK ---
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        webhook_url=os.environ.get("WEBHOOK_URL")
    )

if __name__ == "__main__":
    main()
