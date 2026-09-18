import os
import re
import time
import random
import sqlite3
import threading
from datetime import datetime
import requests

from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, jsonify, request, send_from_directory

try:
    import serial
except ImportError:
    serial = None

# Configurações para o Raspberry Pi + ESP32
PORTA_SERIAL = '/dev/ttyUSB0'  # Se não conectar, teste '/dev/ttyACM0'
BAUD_RATE = 115200
DB_PATH = 'ecosense.db'

# Tarifa padrão e tabela de referência por Estado (R$/kWh)
TARIFA_PADRAO = 0.85
tarifa_global_kwh = TARIFA_PADRAO

TARIFAS_ESTADO = {
    'MG': 0.89,
    'SP': 0.84,
    'RJ': 1.05,
    'PR': 0.81,
    'SC': 0.73,
    'RS': 0.88,
    'BA': 0.94,
}

app = Flask(__name__, static_folder='static', static_url_path='')

import os
import re
import time
import random
import sqlite3
import threading
from datetime import datetime
import requests

from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, jsonify, request, send_from_directory

try:
    import serial
except ImportError:
    serial = None

# Configurações para o Raspberry Pi + ESP32
PORTA_SERIAL = '/dev/ttyUSB0'  # Se não conectar, teste '/dev/ttyACM0'
BAUD_RATE = 115200
DB_PATH = 'ecosense.db'

# Tarifa padrão e tabela de referência por Estado (R$/kWh)
TARIFA_PADRAO = 0.85
tarifa_global_kwh = TARIFA_PADRAO

TARIFAS_ESTADO = {
    'MG': 0.89,
    'SP': 0.84,
    'RJ': 1.05,
    'PR': 0.81,
    'SC': 0.73,
    'RS': 0.88,
    'BA': 0.94,
}

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
        print(f'[serial] não foi possível abrir {PORTA_SERIAL} ({e}). Ativando modo demonstração...')
        modo_demonstracao()
        return

    while True:
        try:
            linha = ser.readline().decode('utf-8', errors='ignore').strip()
            if not linha:
                continue

            # 1. Trata o formato enviado pelo seu ESP32 (ex: "0.42,220.00,92.40,...")
            if ',' in linha:
                partes = linha.split(',')
                if len(partes) >= 3:
                    corrente = float(partes[0])
                    potencia = float(partes[2])
                    
                    # Filtro de ruído da lâmpada desligada
                    if corrente <= 0.80:
                        corrente = 0.0
                        potencia = 0.0

                    salvar_leitura(corrente, potencia)
                    print(f'[sensor real] {corrente:.2f} A / {potencia:.2f} W')
                    continue

            # 2. Mantém compatibilidade caso a linha venha no formato textual antigo
            m_corrente = re.search(r'Corrente\s*=\s*([\d.]+)\s*A', linha)
            m_potencia = re.search(r'Potencia\s*=\s*([\d.]+)\s*W', linha)
            if m_corrente and m_potencia:
                salvar_leitura(float(m_corrente.group(1)), float(m_potencia.group(1)))

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


# Rota do CEP para atualizar a tarifa automaticamente
@app.route('/api/tarifa-cep', methods=['GET'])
def obter_tarifa_por_cep():
    global tarifa_global_kwh
    cep = request.args.get('cep', '').replace('-', '').strip()
    if len(cep) != 8:
        return jsonify({'erro': 'CEP inválido'}), 400

    try:
        resp = requests.get(f'https://viacep.com.br/ws/{cep}/json/', timeout=4).json()
        if 'erro' in resp:
            return jsonify({'erro': 'CEP não encontrado'}), 404

        uf = resp.get('uf', 'SP')
        cidade = resp.get('localidade', '')
        tarifa_global_kwh = TARIFAS_ESTADO.get(uf, TARIFA_PADRAO)

        return jsonify({
            'cidade': cidade,
            'uf': uf,
            'tarifa_kwh': tarifa_global_kwh,
            'bandeira': 'Bandeira Verde'
        })
    except Exception as e:
        return jsonify({'erro': str(e), 'tarifa_kwh': tarifa_global_kwh}), 500


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
    
    # Usa a tarifa atualizada pelo CEP
    kwh_hoje = (media * HORAS_USO_ESTIMADAS) / 1000
    custo_hoje = kwh_hoje * tarifa_global_kwh

    return jsonify({
        'ultima_potencia': round(ultima['potencia'], 2),
        'ultima_corrente': round(ultima['corrente'], 2),
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
        print(f'[serial] não foi possível abrir {PORTA_SERIAL} ({e}). Ativando modo demonstração...')
        modo_demonstracao()
        return

    while True:
        try:
            linha = ser.readline().decode('utf-8', errors='ignore').strip()
            if not linha:
                continue

            # 1. Trata o formato enviado pelo seu ESP32 (ex: "0.42,220.00,92.40,...")
            if ',' in linha:
                partes = linha.split(',')
                if len(partes) >= 3:
                    corrente = float(partes[0])
                    potencia = float(partes[2])
                    
                    # Filtro de ruído da lâmpada desligada
                    if corrente <= 0.80:
                        corrente = 0.0
                        potencia = 0.0

                    salvar_leitura(corrente, potencia)
                    print(f'[sensor real] {corrente:.2f} A / {potencia:.2f} W')
                    continue

            # 2. Mantém compatibilidade caso a linha venha no formato textual antigo
            m_corrente = re.search(r'Corrente\s*=\s*([\d.]+)\s*A', linha)
            m_potencia = re.search(r'Potencia\s*=\s*([\d.]+)\s*W', linha)
            if m_corrente and m_potencia:
                salvar_leitura(float(m_corrente.group(1)), float(m_potencia.group(1)))

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


# Rota do CEP para atualizar a tarifa automaticamente
@app.route('/api/tarifa-cep', methods=['GET'])
def obter_tarifa_por_cep():
    global tarifa_global_kwh
    cep = request.args.get('cep', '').replace('-', '').strip()
    if len(cep) != 8:
        return jsonify({'erro': 'CEP inválido'}), 400

    try:
        resp = requests.get(f'https://viacep.com.br/ws/{cep}/json/', timeout=4).json()
        if 'erro' in resp:
            return jsonify({'erro': 'CEP não encontrado'}), 404

        uf = resp.get('uf', 'SP')
        cidade = resp.get('localidade', '')
        tarifa_global_kwh = TARIFAS_ESTADO.get(uf, TARIFA_PADRAO)

        return jsonify({
            'cidade': cidade,
            'uf': uf,
            'tarifa_kwh': tarifa_global_kwh,
            'bandeira': 'Bandeira Verde'
        })
    except Exception as e:
        return jsonify({'erro': str(e), 'tarifa_kwh': tarifa_global_kwh}), 500


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
    
    # Usa a tarifa atualizada pelo CEP
    kwh_hoje = (media * HORAS_USO_ESTIMADAS) / 1000
    custo_hoje = kwh_hoje * tarifa_global_kwh

    return jsonify({
        'ultima_potencia': round(ultima['potencia'], 2),
        'ultima_corrente': round(ultima['corrente'], 2),
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
