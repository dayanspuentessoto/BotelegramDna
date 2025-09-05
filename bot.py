import os
import logging
import datetime
from zoneinfo import ZoneInfo
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import (
    Application, ContextTypes, CommandHandler
)

# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CARTELERA_URL = "https://www.emol.com/movil/deportes/carteleradirecttv/index.aspx"
TZ = ZoneInfo("America/Santiago")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# --- Scraping Cartelera con depuración HTML ---
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
        # Si no hay fechas, devuelve el HTML en el evento 'nombre'
        if not fechas_encontradas:
            return [{"fecha": "", "hora": "", "nombre": resp.text[:4000], "logo": ""}]
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
        return [{"fecha": "", "hora": "", "nombre": f"Error scrape_cartelera: {e}", "logo": ""}]

# --- Comando /cartelera manual con depuración ---
async def cartelera(update: Update, context: ContextTypes.DEFAULT_TYPE):
    eventos = scrape_cartelera()
    await update.message.reply_text(f"Eventos encontrados: {len(eventos)}")
    # Si el primero es solo HTML, mándalo como texto preformateado
    if len(eventos) == 1 and eventos[0]["fecha"] == "":
        await update.message.reply_text(f"HTML recibido:\n{eventos[0]['nombre']}")
        return
    if not eventos:
        await update.message.reply_text("No hay eventos deportivos programados para hoy y mañana.")
        return
    for evento in eventos[:10]:  # limita a 10 eventos por prueba
        texto = f"📅 *{evento['fecha']}*\n*{evento['hora']}*\n_{evento['nombre']}_"
        if evento['logo']:
            await context.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=evento['logo'],
                caption=texto,
                parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(texto, parse_mode="Markdown")

# --- MAIN SOLO CON /cartelera ---
def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("cartelera", cartelera))
    application.run_webhook(
        listen="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        webhook_url=os.environ.get("WEBHOOK_URL")
    )

if __name__ == "__main__":
    main()
