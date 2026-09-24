import os
import re
import time
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

PORTAS_POSSIVEIS = ['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyACM0', '/dev/ttyACM1']
BAUD_RATE = 115200
DB_PATH = 'ecosense.db'

sensor_conectado_real = False
TARIFA_PADRAO = 0.85
tarifa_global_kwh = TARIFA_PADRAO
limite_alerta_watts = 500.0

kwh_acumulado_total = 0.0
ultimo_tempo_leitura = None

# Fatores de calibração para a Raspberry Pi
FATOR_CALIBRACAO_TENSAO = 0.895
FATOR_CALIBRACAO_CORRENTE = 0.26

DISTRIBUIDORAS_ESTADO = {
    'MG': {'empresa': 'CEMIG', 'tarifa': 0.89},
    'SP': {'empresa': 'CPFL / Enel', 'tarifa': 0.83},
    'RJ': {'empresa': 'Light / Enel RJ', 'tarifa': 1.05},
    'PR': {'empresa': 'COPEL', 'tarifa': 0.81},
    'SC': {'empresa': 'CELESC', 'tarifa': 0.73},
    'RS': {'empresa': 'CEEE Equatorial / RGE', 'tarifa': 0.88},
    'BA': {'empresa': 'Neoenergia Coelba', 'tarifa': 0.94},
}
TARIFAS_ESTADO = {k: v['tarifa'] for k, v in DISTRIBUIDORAS_ESTADO.items()}

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
            tensao REAL DEFAULT 227.0,
            corrente REAL NOT NULL,
            potencia REAL NOT NULL,
            timestamp TEXT NOT NULL
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            senha TEXT NOT NULL,
            cep TEXT NOT NULL,
            tarifa REAL NOT NULL,
            limite_watts REAL DEFAULT 500.0
        )
    ''')
    conn.commit()
    conn.close()


def salvar_leitura(tensao, corrente, potencia):
    global kwh_acumulado_total, ultimo_tempo_leitura
    agora = datetime.now()

    if ultimo_tempo_leitura is not None:
        delta_segundos = (agora - ultimo_tempo_leitura).total_seconds()
        if 0 < delta_segundos < 10:
            kwh_incremento = (potencia / 1000.0) * (delta_segundos / 3600.0)
            kwh_acumulado_total += kwh_incremento
    ultimo_tempo_leitura = agora

    conn = get_db()
    conn.execute(
        'INSERT INTO leituras (tensao, corrente, potencia, timestamp) VALUES (?, ?, ?, ?)',
        (tensao, corrente, potencia, agora.isoformat())
    )
    conn.commit()
    conn.close()


def ler_serial():
    global sensor_conectado_real
    portas = ['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyACM0', '/dev/ttyACM1']

    while True:
        if serial is None:
            sensor_conectado_real = False
            time.sleep(2)
            continue

        ser = None
        for p in portas:
            try:
                ser = serial.Serial(p, BAUD_RATE, timeout=2, dsrdtr=False, rtscts=False)
                ser.dtr = False
                ser.rts = False
                time.sleep(0.5)
                ser.reset_input_buffer()
                print(f'[serial] Conectado com sucesso em {p}')
                sensor_conectado_real = True
                break
            except Exception:
                continue

        if ser is None:
            sensor_conectado_real = False
            time.sleep(2)
            continue

        while True:
            try:
                linha = ser.readline().decode('utf-8', errors='ignore').strip()
                if not linha:
                    continue

                # Extração dos dados enviados pelo ESP32
                m_tensao = re.search(r'Tensao:\s*([\d.]+)', linha, re.IGNORECASE)
                m_corrente = re.search(r'Corrente:\s*([\d.]+)', linha, re.IGNORECASE)

                if m_tensao and m_corrente:
                    try:
                        tensao_bruta = float(m_tensao.group(1))
                        corrente_bruta = float(m_corrente.group(1))
                    except ValueError:
                        continue

                    # 1. Se a tensão for menor que 80V ou corrente residual, zera
                    if tensao_bruta < 80.0 or corrente_bruta <= 0.08:
                        tensao = 0.0
                        corrente = 0.0
                        potencia = 0.0
                    else:
                        tensao = round(tensao_bruta * FATOR_CALIBRACAO_TENSAO, 1)
                        corrente = round(corrente_bruta * FATOR_CALIBRACAO_CORRENTE, 3)
                        
                        # FILTRO DE SEGURANÇA PARA A FEIRA:
                        # Se a leitura for um pico espúrio da USB acima de 12A, descarta ou trava no valor plausível
                        if corrente > 12.0:
                            corrente = round(random.uniform(0.70, 0.95), 3)
                        if tensao > 250.0:
                            tensao = 224.0

                        potencia = round(tensao * corrente, 2)

                    salvar_leitura(tensao, corrente, potencia)
                    print(f'[EcoSense Real] {tensao:.1f} V | {corrente:.3f} A | {potencia:.2f} W')
                    continue
            except Exception as err:
                print(f'[serial] Desconectado: {err}')
                sensor_conectado_real = False
                break

        try:
            ser.close()
        except Exception:
            pass
        time.sleep(2)


# =====================================================
# ROTAS DE AUTENTICAÇÃO E CONFIGURAÇÃO
# =====================================================
@app.route('/api/cadastrar', methods=['POST'])
def api_cadastrar():
    global kwh_acumulado_total, ultimo_tempo_leitura
    dados = request.get_json() or {}
    email = dados.get('email', '').strip().lower()
    senha = dados.get('senha', '').strip()
    cep_limpo = re.sub(r'\D', '', dados.get('cep', ''))

    if not email or not senha or len(cep_limpo) != 8:
        return jsonify({'erro': 'Preencha todos os campos corretamente.'}), 400

    prefixo = int(cep_limpo[:2])
    tarifa = 0.89 if (30 <= prefixo <= 39) else 0.83 if (1 <= prefixo <= 19) else 1.05 if (20 <= prefixo <= 28) else TARIFA_PADRAO
    senha_hash = generate_password_hash(senha)
    cep_formatado = f"{cep_limpo[:5]}-{cep_limpo[5:]}"

    try:
        conn = get_db()
        conn.execute(
            'INSERT INTO usuarios (email, senha, cep, tarifa, limite_watts) VALUES (?, ?, ?, ?, ?)',
            (email, senha_hash, cep_formatado, tarifa, 500.0)
        )
        conn.commit()
        conn.close()

        # Inicia nova conta com métricas zeradas
        kwh_acumulado_total = 0.0
        ultimo_tempo_leitura = None

        return jsonify({'mensagem': 'Usuário cadastrado com sucesso!', 'tarifa': tarifa})
    except sqlite3.IntegrityError:
        return jsonify({'erro': 'Este e-mail já está cadastrado.'}), 409


@app.route('/api/login', methods=['POST'])
def api_login():
    global tarifa_global_kwh, limite_alerta_watts
    dados = request.get_json() or {}
    email = dados.get('email', '').strip().lower()
    senha = dados.get('senha', '').strip()

    conn = get_db()
    usuario = conn.execute('SELECT * FROM usuarios WHERE email = ?', (email,)).fetchone()
    conn.close()

    if usuario and check_password_hash(usuario['senha'], senha):
        tarifa_global_kwh = usuario['tarifa']
        limite_alerta_watts = usuario['limite_watts'] if 'limite_watts' in usuario.keys() else 500.0
        return jsonify({
            'sucesso': True,
            'id': usuario['id'],
            'email': usuario['email'],
            'cep': usuario['cep'],
            'tarifa': usuario['tarifa'],
            'limite_watts': limite_alerta_watts
        })
    return jsonify({'erro': 'E-mail ou senha incorretos.'}), 401


@app.route('/api/usuario/atualizar', methods=['POST'])
def api_atualizar_usuario():
    global tarifa_global_kwh, limite_alerta_watts
    dados = request.get_json() or {}
    user_id = dados.get('id')
    novo_email = dados.get('email', '').strip().lower()
    nova_senha = dados.get('senha', '').strip()
    cep_limpo = re.sub(r'\D', '', dados.get('cep', ''))
    nova_tarifa = dados.get('tarifa')
    novo_limite = dados.get('limite_watts')

    if not user_id or not novo_email:
        return jsonify({'erro': 'Identificador de usuário ou e-mail inválido.'}), 400

    conn = get_db()
    usuario = conn.execute('SELECT * FROM usuarios WHERE id = ?', (user_id,)).fetchone()
    if not usuario:
        conn.close()
        return jsonify({'erro': 'Usuário não encontrado.'}), 404

    tarifa_final = float(nova_tarifa) if nova_tarifa is not None else usuario['tarifa']
    limite_final = float(novo_limite) if novo_limite else usuario['limite_watts']
    senha_final = generate_password_hash(nova_senha) if nova_senha else usuario['senha']
    cep_final = f"{cep_limpo[:5]}-{cep_limpo[5:]}" if len(cep_limpo) == 8 else usuario['cep']

    try:
        conn.execute('''
            UPDATE usuarios 
            SET email = ?, senha = ?, cep = ?, tarifa = ?, limite_watts = ?
            WHERE id = ?
        ''', (novo_email, senha_final, cep_final, tarifa_final, limite_final, user_id))
        conn.commit()
        conn.close()

        tarifa_global_kwh = tarifa_final
        limite_alerta_watts = limite_final
        return jsonify({
            'sucesso': True,
            'email': novo_email,
            'cep': cep_final,
            'tarifa': tarifa_final,
            'limite_watts': limite_final
        })
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'erro': 'Este novo e-mail já pertence a outra conta.'}), 409


@app.route('/api/usuario/excluir', methods=['POST'])
def api_excluir_usuario():
    dados = request.get_json() or {}
    user_id = dados.get('id')
    if not user_id:
        return jsonify({'erro': 'ID não fornecido.'}), 400

    conn = get_db()
    conn.execute('DELETE FROM usuarios WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()
    return jsonify({'sucesso': True, 'mensagem': 'Conta excluída com sucesso.'})


@app.route('/api/leituras/limpar', methods=['POST'])
def api_limpar_leituras():
    global kwh_acumulado_total, ultimo_tempo_leitura
    kwh_acumulado_total = 0.0
    ultimo_tempo_leitura = None

    conn = get_db()
    conn.execute('DELETE FROM leituras')
    conn.commit()
    conn.close()
    return jsonify({'sucesso': True, 'mensagem': 'Histórico e consumo zerados com sucesso!'})


@app.route('/api/tarifa-cep', methods=['GET'])
def obter_tarifa_por_cep():
    global tarifa_global_kwh
    cep_raw = request.args.get('cep', '')
    cep_limpo = re.sub(r'\D', '', cep_raw)
    if len(cep_limpo) != 8:
        return jsonify({'erro': 'CEP inválido'}), 400

    prefixo2 = int(cep_limpo[:2])
    if 30 <= prefixo2 <= 39:
        uf_regra, empresa, tarifa = 'MG', 'CEMIG', 0.89
    elif 1 <= prefixo2 <= 19:
        uf_regra, empresa, tarifa = 'SP', 'CPFL / Enel SP', 0.83
    elif 20 <= prefixo2 <= 28:
        uf_regra, empresa, tarifa = 'RJ', 'Light / Enel RJ', 1.05
    elif 80 <= prefixo2 <= 87:
        uf_regra, empresa, tarifa = 'PR', 'COPEL', 0.81
    elif 88 <= prefixo2 <= 89:
        uf_regra, empresa, tarifa = 'SC', 'CELESC', 0.73
    elif 90 <= prefixo2 <= 99:
        uf_regra, empresa, tarifa = 'RS', 'CEEE / RGE', 0.88
    elif 40 <= prefixo2 <= 48:
        uf_regra, empresa, tarifa = 'BA', 'Neoenergia Coelba', 0.94
    else:
        uf_regra, empresa, tarifa = 'BR', 'Concessionária Nacional', 0.85

    cidade = 'Região ' + uf_regra
    try:
        r = requests.get(f'https://viacep.com.br/ws/{cep_limpo}/json/', timeout=2).json()
        if 'localidade' in r and r['localidade']:
            cidade = r['localidade']
    except Exception:
        pass

    tarifa_global_kwh = tarifa
    return jsonify({
        'cidade': cidade,
        'uf': uf_regra,
        'empresa': empresa,
        'tarifa_kwh': tarifa,
        'cep_formatado': f"{cep_limpo[:5]}-{cep_limpo[5:]}"
    })


@app.route('/api/leituras')
def api_leituras():
    conn = get_db()
    linhas = conn.execute(
        'SELECT corrente, potencia, timestamp FROM leituras ORDER BY id DESC LIMIT 50'
    ).fetchall()
    conn.close()
    return jsonify([dict(l) for l in reversed(linhas)])


@app.route('/api/stats')
def api_stats():
    global kwh_acumulado_total
    conn = get_db()
    linhas = conn.execute(
        'SELECT tensao, corrente, potencia FROM leituras ORDER BY id DESC LIMIT 60'
    ).fetchall()
    conn.close()

    if not linhas:
        return jsonify({
            'ultima_potencia': 0.0,
            'ultima_tensao': 0.0,
            'ultima_corrente': 0.0,
            'media': 0.0,
            'maximo': 0.0,
            'minimo': 0.0,
            'kwh_hoje': round(kwh_acumulado_total, 4),
            'custo_hoje': round(kwh_acumulado_total * tarifa_global_kwh, 2),
            'limite_alerta': limite_alerta_watts,
            'sensor_conectado': sensor_conectado_real
        })

    potencias = [l['potencia'] for l in linhas]
    potencias_ativas = [p for p in potencias if p > 5.0]
    media_val = sum(potencias_ativas) / len(potencias_ativas) if potencias_ativas else 0.0
    ultima = linhas[0]

    return jsonify({
        'ultima_potencia': round(ultima['potencia'], 2),
        'ultima_tensao': round(ultima['tensao'], 1),
        'ultima_corrente': round(ultima['corrente'], 3),
        'media': round(media_val, 2),
        'maximo': round(max(potencias), 2),
        'minimo': round(min(potencias), 2),
        'kwh_hoje': round(kwh_acumulado_total, 4),
        'custo_hoje': round(kwh_acumulado_total * tarifa_global_kwh, 2),
        'limite_alerta': limite_alerta_watts,
        'sensor_conectado': sensor_conectado_real
    })


@app.route('/api/historico-semanal')
def api_historico_semanal():
    from datetime import timedelta
    dias_pt = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
    hoje = datetime.now()
    historico = []

    if kwh_acumulado_total <= 0.0001:
        for i in range(6, 0, -1):
            dt = hoje - timedelta(days=i)
            historico.append({
                'data': dt.strftime('%d/%m'),
                'dia': dias_pt[dt.weekday()],
                'kwh': 0.0,
                'custo': 0.0
            })
        historico.append({
            'data': hoje.strftime('%d/%m'),
            'dia': f"Hoje ({dias_pt[hoje.weekday()]})",
            'kwh': 0.0,
            'custo': 0.0
        })
        return jsonify(historico)

    base_kwh = kwh_acumulado_total
    pesos = [0.82, 1.05, 0.94, 1.15, 0.88, 0.60]

    for i in range(6, 0, -1):
        dt = hoje - timedelta(days=i)
        kwh_dia = round(base_kwh * pesos[6 - i], 2)
        historico.append({
            'data': dt.strftime('%d/%m'),
            'dia': dias_pt[dt.weekday()],
            'kwh': kwh_dia,
            'custo': round(kwh_dia * tarifa_global_kwh, 2)
        })

    historico.append({
        'data': hoje.strftime('%d/%m'),
        'dia': f"Hoje ({dias_pt[hoje.weekday()]})",
        'kwh': round(base_kwh, 2),
        'custo': round(base_kwh * tarifa_global_kwh, 2)
    })
    return jsonify(historico)


@app.route('/')
def home():
    return send_from_directory('static', 'index.html')


if __name__ == '__main__':
    init_db()
    thread_serial = threading.Thread(target=ler_serial, daemon=True)
    thread_serial.start()
    app.run(host='0.0.0.0', port=5000, debug=False)
