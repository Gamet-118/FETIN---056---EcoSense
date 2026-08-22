import re
import time
import random
import sqlite3
import threading
from datetime import datetime

from flask import Flask, jsonify, send_from_directory

try:
    import serial
except ImportError:
    serial = None


PORTA_SERIAL = 'COM3'
BAUD_RATE = 9600
DB_PATH = 'ecosense.db'

app = Flask(__name__, static_folder='static', static_url_path='')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS leituras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            corrente REAL NOT NULL,
            potencia REAL NOT NULL,
            timestamp TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()


def salvar_leitura(corrente, potencia):
    conn = get_db()
    conn.execute(
        'INSERT INTO leituras (corrente, potencia, timestamp) VALUES (?, ?, ?)',
        (corrente, potencia, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def ler_serial():
    if serial is None:
        modo_demonstracao()
        return

    try:
        ser = serial.Serial(PORTA_SERIAL, BAUD_RATE, timeout=2)
        print(f'[serial] conectado em {PORTA_SERIAL}')
    except Exception as e:
        print(f'[serial] não consegui abrir {PORTA_SERIAL} ({e})')
        modo_demonstracao()
        return

    corrente_atual = None
    while True:
        try:
            linha = ser.readline().decode('utf-8', errors='ignore').strip()
            if not linha:
                continue

            m_corrente = re.search(r'Corrente\s*=\s*([\d.]+)\s*A', linha)
            m_potencia = re.search(r'Potencia\s*=\s*([\d.]+)\s*W', linha)

            if m_corrente:
                corrente_atual = float(m_corrente.group(1))
            elif m_potencia and corrente_atual is not None:
                potencia_atual = float(m_potencia.group(1))
                salvar_leitura(corrente_atual, potencia_atual)
                print(f'[leitura] {corrente_atual} A / {potencia_atual} W')
                corrente_atual = None
        except Exception as e:
            print(f'[serial] erro lendo linha: {e}')
            time.sleep(1)


def modo_demonstracao():
    base_potencia = 181.0
    while True:
        potencia = round(base_potencia + random.uniform(-4, 4), 2)
        corrente = round(potencia / 127, 4)
        salvar_leitura(corrente, potencia)
        time.sleep(3)


@app.route('/api/leituras')
def api_leituras():
    conn = get_db()
    linhas = conn.execute(
        'SELECT corrente, potencia, timestamp FROM leituras ORDER BY id DESC LIMIT 50'
    ).fetchall()
    conn.close()
    dados = [dict(l) for l in reversed(linhas)]
    return jsonify(dados)


@app.route('/api/stats')
def api_stats():
    conn = get_db()
    linhas = conn.execute(
        'SELECT corrente, potencia FROM leituras ORDER BY id DESC LIMIT 200'
    ).fetchall()
    conn.close()

    if not linhas:
        return jsonify({'erro': 'ainda não há leituras salvas'}), 404

    potencias = [l['potencia'] for l in linhas]
    ultima = linhas[0]

    media = sum(potencias) / len(potencias)
    HORAS_USO_ESTIMADAS = 8
    TARIFA_KWH = 0.75
    kwh_hoje = (media * HORAS_USO_ESTIMADAS) / 1000
    custo_hoje = kwh_hoje * TARIFA_KWH

    return jsonify({
        'ultima_potencia': ultima['potencia'],
        'ultima_corrente': ultima['corrente'],
        'media': round(media, 2),
        'maximo': round(max(potencias), 2),
        'minimo': round(min(potencias), 2),
        'kwh_hoje': round(kwh_hoje, 3),
        'custo_hoje': round(custo_hoje, 2),
    })


@app.route('/')
def home():
    return send_from_directory('static', 'index.html')


if __name__ == '__main__':
    init_db()
    thread_serial = threading.Thread(target=ler_serial, daemon=True)
    thread_serial.start()
    app.run(host='0.0.0.0', port=5000, debug=False)
