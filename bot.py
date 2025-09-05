import os
import logging
from datetime import datetime, timedelta
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
API_FOOTBALL_KEY = "0e6b079461156a444f01f84256feb6cb"  # Tu clave aquí

def get_chile_fixtures():
    url = "https://v3.football.api-sports.io/fixtures"
    headers = {
        "x-apisports-key": API_FOOTBALL_KEY,
        "Accept": "application/json"
    }
    # Liga chilena principal: 226 (Primera División), puedes agregar más ligas si lo deseas
    liga_chile = "226"
    hoy = datetime.utcnow()
    manana = hoy + timedelta(days=1)
    fechas = [
        hoy.strftime("%Y-%m-%d"),
        manana.strftime("%Y-%m-%d")
    ]
    eventos = []
    for fecha in fechas:
        params = {
            "league": liga_chile,
            "season": "2024",  # Cambia por el año actual si corresponde
            "date": fecha
        }
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            data = resp.json()
            for match in data.get("response", []):
                fixture = match["fixture"]
                teams = match["teams"]
                eventos.append({
                    "fecha": fixture["date"][:10],
                    "hora": fixture["date"][11:16],
                    "local": teams["home"]["name"],
                    "visitante": teams["away"]["name"],
                    "estado": fixture["status"]["long"]
                })
        except Exception as e:
            eventos.append({
                "fecha": fecha, "hora": "", "local": "", "visitante": f"Error: {e}", "estado": ""
            })
    return eventos

async def cartelera(update: Update, context: ContextTypes.DEFAULT_TYPE):
    eventos = get_chile_fixtures()
    if not eventos or (len(eventos) == 1 and "Error" in eventos[0]["visitante"]):
        await update.message.reply_text("No se pudo obtener la cartelera de fútbol o ocurrió un error.")
        if eventos:
            await update.message.reply_text(eventos[0]["visitante"])
        return
    await update.message.reply_text(f"Partidos de fútbol transmitidos en Chile hoy y mañana: {len(eventos)} encontrados.")
    for evento in eventos:
        texto = (
            f"📅 {evento['fecha']} {evento['hora']}\n"
            f"⚽ {evento['local']} vs {evento['visitante']}\n"
            f"🔔 Estado: {evento['estado']}"
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
