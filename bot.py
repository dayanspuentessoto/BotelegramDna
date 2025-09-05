import logging
from playwright.async_api import async_playwright  # Usamos la versión asíncrona de Playwright
from bs4 import BeautifulSoup
import datetime
import os
from telegram import Update, ChatPermissions
from telegram.ext import Application, ContextTypes, CommandHandler, MessageHandler, filters, ChatMemberHandler

# --- CONFIGURACIÓN ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
ADMIN_IDS = [5032964793]
GENERAL_CHAT_ID = "-2421748184"      # Grupo principal D.N.A TV
GROUP_ID = "-2421748184"             # Igual que GENERAL_CHAT_ID
CANAL_EVENTOS_ID = "-1002421748184"  # Canal EVENTOS DEPORTIVOS

CARTELERA_URL = "https://www.emol.com/movil/deportes/carteleradirecttv/index.aspx"
TZ = datetime.timezone.utc

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# --- Scraping Cartelera ---
async def scrape_cartelera():
    async with async_playwright() as p:
        # Lanzar el navegador en modo "headless" (sin interfaz gráfica)
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(CARTELERA_URL)

        try:
            # Esperar hasta que se carguen los eventos (si existen), con un mayor tiempo de espera
            await page.wait_for_selector("div.cartelera_fecha", timeout=60000)  # Aumento el tiempo de espera a 60 segundos
        except TimeoutError:
            print("No se pudo cargar el selector en el tiempo esperado.")
            await browser.close()  # Cerrar el navegador al terminar
            return []

        # Agregar un pequeño retraso explícito por si la página aún está cargando dinámicamente
        await page.wait_for_timeout(5000)

        # Obtener el contenido de la página después de cargar JavaScript
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        eventos = []
        tomorrow = datetime.datetime.now(TZ).date() + datetime.timedelta(days=1)  # Día siguiente

        for bloque_fecha in soup.find_all("div", class_="cartelera_fecha"):
            fecha = bloque_fecha.get_text(strip=True)
            fecha_evento = datetime.datetime.strptime(fecha, "%d/%m").date().replace(year=tomorrow.year)  # Convertimos la fecha a formato de fecha

            # Filtramos solo eventos del día siguiente
            if fecha_evento != tomorrow:
                continue

            bloque_eventos = bloque_fecha.find_next_sibling("div", class_="cartelera_eventos")
            if not bloque_eventos:
                continue
            for evento in bloque_eventos.find_all("div", class_="cartelera_evento"):
                hora = evento.find("div", class_="cartelera_hora")
                nombre = evento.find("div", class_
