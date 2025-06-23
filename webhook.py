"""
Веб-хук обработчик для ЮKassa
Этот файл нужно разместить на веб-сервере для получения уведомлений о платежах
"""

from flask import Flask, request, jsonify
import json
import logging
import asyncio
from database import DatabaseManager
from payment import PaymentManager

app = Flask(__name__)

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация менеджеров
db_manager = DatabaseManager()
payment_manager = PaymentManager(db_manager)

@app.route('/yookassa/webhook', methods=['POST'])
def yookassa_webhook():
    """Обработчик веб-хука от ЮKassa"""
    try:
        # Получаем данные из запроса
        notification_data = request.get_json()
        
        if not notification_data:
            logging.error("Пустые данные в веб-хуке")
            return jsonify({"error": "No data"}), 400
        
        logging.info(f"Получено уведомление от ЮKassa: {notification_data}")
        
        # Обрабатываем уведомление асинхронно
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        result = loop.run_until_complete(
            payment_manager.handle_payment_notification(notification_data)
        )
        
        if result:
            return jsonify({"status": "success"}), 200
        else:
            return jsonify({"status": "error"}), 400
            
    except Exception as e:
        logging.error(f"Ошибка в веб-хуке ЮKassa: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Проверка здоровья сервиса"""
    return jsonify({"status": "OK"}), 200

if __name__ == '__main__':
    # Инициализируем БД
    loop = asyncio.get_event_loop()
    loop.run_until_complete(db_manager.init_database())
    
    # Запускаем Flask сервер
    app.run(host='0.0.0.0', port=5000, debug=False)