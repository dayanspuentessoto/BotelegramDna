import os
import logging
import requests
from bs4 import BeautifulSoup
from telegram import Update
from telegram.ext import (
    Application, ContextTypes, CommandHandler
)

# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")

# Fuente alternativa: TV Sports Guide Chile
CARTELERA_URL = "https://www.tvsportsguide.com/chile/"
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

def scrape_tvsportsguide():
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
        # Cada evento está en <li class="event">
        for li in soup.find_all("li", class_="event"):
            hora = li.find("span", class_="time")
            nombre = li.find("span", class_="name")
            canal = li.find("span", class_="channel")
            deporte = li.find("span", class_="sport")
            # Extrae la info, si falta algo lo deja vacío
            eventos.append({
                "hora": hora.get_text(strip=True) if hora else "",
                "nombre": nombre.get_text(strip=True) if nombre else "",
                "canal": canal.get_text(strip=True) if canal else "",
                "deporte": deporte.get_text(strip=True) if deporte else ""
            })
        return eventos
    except Exception as e:
        return [{"hora": "", "nombre": f"Error: {e}", "canal": "", "deporte": ""}]

async def cartelera(update: Update, context: ContextTypes.DEFAULT_TYPE):
    eventos = scrape_tvsportsguide()
    if not eventos or (len(eventos) == 1 and eventos[0]["nombre"].startswith("Error")):
        await update.message.reply_text("No se pudo obtener la cartelera deportiva o ocurrió un error.")
        if eventos:
            await update.message.reply_text(eventos[0]["nombre"])
        return
    await update.message.reply_text(f"Eventos deportivos hoy en Chile: {len(eventos)} encontrados.")
    # Muestra hasta 12 eventos (puedes cambiar el número)
    for evento in eventos[:12]:
        texto = (
            f"🕒 {evento['hora']}\n"
            f"🏟️ {evento['nombre']}\n"
            f"📺 Canal: {evento['canal']}\n"
            f"⚽ Deporte: {evento['deporte']}"
        )
        await update.message.reply_text(texto)

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
