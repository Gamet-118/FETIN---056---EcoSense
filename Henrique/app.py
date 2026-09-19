import sqlite3
from datetime import datetime

import requests

from werkzeug.security import generate_password_hash, check_password_hash
from flask import Flask, jsonify, request, send_from_directory


# =====================================================
# CONFIGURAÇÕES
# =====================================================

# Banco REAL que já está sendo alimentado pelo
# programa Python da Raspberry
DB_PATH = "/home/pedro/ProjetoEnergia/banco/energia.db"

TARIFA_PADRAO = 0.85

tarifa_global_kwh = TARIFA_PADRAO


# =====================================================
# TARIFAS POR ESTADO
# =====================================================

TARIFAS_ESTADO = {
    "MG": 0.89,
    "SP": 0.84,
    "RJ": 1.05,
    "PR": 0.81,
    "SC": 0.73,
    "RS": 0.88,
    "BA": 0.94,
}


# =====================================================
# FLASK
# =====================================================

app = Flask(
    __name__,
    static_folder="static",
    static_url_path=""
)


# =====================================================
# CONEXÃO COM O BANCO
# =====================================================

def get_db():

    conn = sqlite3.connect(DB_PATH)

    conn.row_factory = sqlite3.Row

    return conn


# =====================================================
# INICIALIZAR BANCO
# =====================================================

def init_db():

    conn = get_db()

    # A tabela LEITURAS já existe no banco
    # criado pelo coletor da Raspberry.
    #
    # Portanto, NÃO criamos ela novamente aqui.

    # Criamos apenas a tabela de usuários,
    # que pertence ao backend.

    conn.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            senha TEXT NOT NULL,
            cep TEXT NOT NULL,
            tarifa REAL NOT NULL
        )
    """)

    conn.commit()

    conn.close()


# =====================================================
# CADASTRO
# =====================================================

@app.route("/api/cadastrar", methods=["POST"])
def api_cadastrar():

    dados = request.get_json() or {}

    email = dados.get("email", "").strip().lower()

    senha = dados.get("senha", "").strip()

    cep = dados.get("cep", "").replace("-", "").strip()


    # ---------------------------------------------
    # VALIDAR DADOS
    # ---------------------------------------------

    if not email or not senha or len(cep) != 8:

        return jsonify({
            "erro": "Preencha todos os campos corretamente."
        }), 400


    # ---------------------------------------------
    # BUSCAR TARIFA PELO CEP
    # ---------------------------------------------

    tarifa = TARIFA_PADRAO

    try:

        resposta = requests.get(
            f"https://viacep.com.br/ws/{cep}/json/",
            timeout=4
        ).json()

        if "uf" in resposta:

            tarifa = TARIFAS_ESTADO.get(
                resposta["uf"],
                TARIFA_PADRAO
            )

    except Exception:

        pass


    # ---------------------------------------------
    # CRIPTOGRAFAR SENHA
    # ---------------------------------------------

    senha_hash = generate_password_hash(senha)


    # ---------------------------------------------
    # SALVAR USUÁRIO
    # ---------------------------------------------

    try:

        conn = get_db()

        conn.execute(
            """
            INSERT INTO usuarios
            (
                email,
                senha,
                cep,
                tarifa
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                email,
                senha_hash,
                cep,
                tarifa
            )
        )

        conn.commit()

        conn.close()

        return jsonify({
            "mensagem": "Usuário cadastrado com sucesso!",
            "tarifa": tarifa
        })


    except sqlite3.IntegrityError:

        return jsonify({
            "erro": "Este e-mail já está cadastrado."
        }), 409


# =====================================================
# LOGIN
# =====================================================

@app.route("/api/login", methods=["POST"])
def api_login():

    global tarifa_global_kwh

    dados = request.get_json() or {}

    email = dados.get(
        "email",
        ""
    ).strip().lower()

    senha = dados.get(
        "senha",
        ""
    ).strip()


    conn = get_db()

    usuario = conn.execute(
        """
        SELECT *
        FROM usuarios
        WHERE email = ?
        """,
        (email,)
    ).fetchone()

    conn.close()


    # ---------------------------------------------
    # VERIFICAR LOGIN
    # ---------------------------------------------

    if usuario and check_password_hash(
        usuario["senha"],
        senha
    ):

        tarifa_global_kwh = usuario["tarifa"]

        return jsonify({
            "sucesso": True,
            "email": usuario["email"],
            "cep": usuario["cep"],
            "tarifa": usuario["tarifa"]
        })


    return jsonify({
        "erro": "E-mail ou senha incorretos."
    }), 401


# =====================================================
# TARIFA PELO CEP
# =====================================================

@app.route("/api/tarifa-cep", methods=["GET"])
def obter_tarifa_por_cep():

    global tarifa_global_kwh

    cep = request.args.get(
        "cep",
        ""
    ).replace("-", "").strip()


    # ---------------------------------------------
    # VALIDAR CEP
    # ---------------------------------------------

    if len(cep) != 8:

        return jsonify({
            "erro": "CEP inválido"
        }), 400


    try:

        resposta = requests.get(
            f"https://viacep.com.br/ws/{cep}/json/",
            timeout=4
        ).json()


        if "erro" in resposta:

            return jsonify({
                "erro": "CEP não encontrado"
            }), 404


        uf = resposta.get(
            "uf",
            "SP"
        )

        cidade = resposta.get(
            "localidade",
            ""
        )


        tarifa_global_kwh = TARIFAS_ESTADO.get(
            uf,
            TARIFA_PADRAO
        )


        return jsonify({

            "cidade": cidade,

            "uf": uf,

            "tarifa_kwh": tarifa_global_kwh,

            "bandeira": "Bandeira Verde"

        })


    except Exception as e:

        return jsonify({

            "erro": str(e),

            "tarifa_kwh": tarifa_global_kwh

        }), 500


# =====================================================
# LEITURAS
# =====================================================

@app.route("/api/leituras", methods=["GET"])
def api_leituras():

    conn = get_db()


    linhas = conn.execute(
        """
        SELECT
            id,
            corrente,
            tensao,
            potencia,
            energia,
            dispositivo,
            data_hora

        FROM leituras

        ORDER BY id DESC

        LIMIT 50
        """
    ).fetchall()


    conn.close()


    # ---------------------------------------------
    # Converter SQLite Row para dicionário
    # ---------------------------------------------

    dados = [
        dict(linha)
        for linha in reversed(linhas)
    ]


    return jsonify(dados)


# =====================================================
# ESTATÍSTICAS
# =====================================================

@app.route("/api/stats", methods=["GET"])
def api_stats():

    conn = get_db()


    linhas = conn.execute(
        """
        SELECT
            corrente,
            tensao,
            potencia,
            energia

        FROM leituras

        ORDER BY id DESC

        LIMIT 200
        """
    ).fetchall()


    conn.close()


    # ---------------------------------------------
    # VERIFICAR SE EXISTEM LEITURAS
    # ---------------------------------------------

    if not linhas:

        return jsonify({
            "erro": "Ainda não há leituras salvas."
        }), 404


    # ---------------------------------------------
    # SEPARAR VALORES
    # ---------------------------------------------

    potencias = [
        linha["potencia"]
        for linha in linhas
    ]


    correntes = [
        linha["corrente"]
        for linha in linhas
    ]


    tensoes = [
        linha["tensao"]
        for linha in linhas
    ]


    # ---------------------------------------------
    # ÚLTIMA LEITURA
    # ---------------------------------------------

    ultima = linhas[0]


    # ---------------------------------------------
    # MÉDIAS
    # ---------------------------------------------

    media_potencia = (
        sum(potencias) / len(potencias)
    )


    media_corrente = (
        sum(correntes) / len(correntes)
    )


    media_tensao = (
        sum(tensoes) / len(tensoes)
    )


    # ---------------------------------------------
    # RETORNO
    # ---------------------------------------------

    return jsonify({

        "ultima_potencia":
            round(
                ultima["potencia"],
                2
            ),

        "ultima_corrente":
            round(
                ultima["corrente"],
                2
            ),

        "ultima_tensao":
            round(
                ultima["tensao"],
                2
            ),


        "media_potencia":
            round(
                media_potencia,
                2
            ),

        "media_corrente":
            round(
                media_corrente,
                2
            ),

        "media_tensao":
            round(
                media_tensao,
                2
            ),


        "maximo":
            round(
                max(potencias),
                2
            ),

        "minimo":
            round(
                min(potencias),
                2
            )

    })


# =====================================================
# PÁGINA PRINCIPAL
# =====================================================

@app.route("/")
def home():

    return send_from_directory(
        "static",
        "index.html"
    )


# =====================================================
# INICIAR SERVIDOR
# =====================================================

if __name__ == "__main__":

    # Cria somente a tabela de usuários.
    # Não altera a tabela de leituras.
    init_db()


    # Inicia o Flask
    #
    # O Flask NÃO abre a porta serial.
    # O outro programa Python da Raspberry
    # já é responsável por isso.

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
