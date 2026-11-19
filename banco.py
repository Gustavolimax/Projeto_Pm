
"""Banco de dados do Água em Alerta PM.

Gerencia hidrômetros, leituras acumuladas, consumo por intervalo,
histórico mensal, fechamento e análise de possíveis anormalidades.
"""

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
import calendar
import sqlite3

from algoritmos.deteccao_vazamento import detectar_vazamento


RAIZ = Path(__file__).resolve().parent.parent
PASTA_DADOS = RAIZ / "dados"
CAMINHO_BANCO = PASTA_DADOS / "agua_em_alerta.db"

FORMATO = "%Y-%m-%d %H:%M"


# ============================================================
# CONEXÃO COM O BANCO
# ============================================================

@contextmanager
def conectar():
    PASTA_DADOS.mkdir(parents=True, exist_ok=True)

    conexao = sqlite3.connect(CAMINHO_BANCO)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")

    try:
        yield conexao
        conexao.commit()

    except Exception:
        conexao.rollback()
        raise

    finally:
        conexao.close()


# ============================================================
# CRIAÇÃO DAS TABELAS
# ============================================================

def criar_tabelas():

    with conectar() as banco:

        banco.execute("""
            CREATE TABLE IF NOT EXISTS hidrometros (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                setor TEXT NOT NULL,
                nome TEXT NOT NULL,
                ativo INTEGER NOT NULL DEFAULT 1,
                criado_em TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(setor, nome)
            )
        """)

        banco.execute("""
            CREATE TABLE IF NOT EXISTS leituras_hidrometro (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hidrometro_id INTEGER NOT NULL,
                data_hora TEXT NOT NULL,
                valor_acumulado REAL NOT NULL
                    CHECK(valor_acumulado >= 0),
                leitura_anterior REAL,
                consumo_intervalo REAL,
                situacao TEXT NOT NULL DEFAULT 'SEM BASE',

                FOREIGN KEY(hidrometro_id)
                    REFERENCES hidrometros(id),

                UNIQUE(hidrometro_id, data_hora)
            )
        """)

        banco.execute("""
            CREATE TABLE IF NOT EXISTS fechamento_hidrometro (
                hidrometro_id INTEGER NOT NULL,
                mes TEXT NOT NULL,
                fechado INTEGER NOT NULL DEFAULT 0,
                tipo TEXT,
                fechado_em TEXT,

                PRIMARY KEY(hidrometro_id, mes),

                FOREIGN KEY(hidrometro_id)
                    REFERENCES hidrometros(id)
            )
        """)

        banco.execute("""
            CREATE INDEX IF NOT EXISTS idx_leituras_hidro_data
            ON leituras_hidrometro(hidrometro_id, data_hora)
        """)

        # Cria o hidrômetro principal se ainda não existir.
        existe = banco.execute(
            "SELECT id FROM hidrometros LIMIT 1"
        ).fetchone()

        if not existe:
            banco.execute("""
                INSERT INTO hidrometros (setor, nome)
                VALUES (?, ?)
            """, ("Geral", "Hidrômetro principal"))

        # Preserva e importa leituras do banco antigo, se existirem.
        tabelas = banco.execute("""
            SELECT name
            FROM sqlite_master
            WHERE type='table'
        """).fetchall()

        nomes_tabelas = {
            linha["name"] for linha in tabelas
        }

        principal = banco.execute("""
            SELECT id
            FROM hidrometros
            ORDER BY id
            LIMIT 1
        """).fetchone()

        if principal and "leituras" in nomes_tabelas:

            colunas = {
                linha["name"]
                for linha in banco.execute(
                    "PRAGMA table_info(leituras)"
                ).fetchall()
            }

            if {"data_hora", "valor_hidrometro"}.issubset(colunas):

                antigas = banco.execute("""
                    SELECT data_hora, valor_hidrometro
                    FROM leituras
                """).fetchall()

                for leitura in antigas:

                    banco.execute("""
                        INSERT OR IGNORE INTO leituras_hidrometro
                        (
                            hidrometro_id,
                            data_hora,
                            valor_acumulado
                        )
                        VALUES (?, ?, ?)
                    """, (
                        principal["id"],
                        leitura["data_hora"],
                        leitura["valor_hidrometro"]
                    ))

                _recalcular_leituras(
                    banco,
                    principal["id"]
                )

        # Importa fechamentos antigos, sem apagar os existentes.
        if principal and "fechamento_mensal" in nomes_tabelas:

            colunas_fechamento = {
                linha["name"]
                for linha in banco.execute(
                    "PRAGMA table_info(fechamento_mensal)"
                ).fetchall()
            }

            colunas_necessarias = {
                "mes", "fechado", "tipo", "fechado_em"
            }

            if colunas_necessarias.issubset(colunas_fechamento):

                fechamentos = banco.execute("""
                    SELECT mes, fechado, tipo, fechado_em
                    FROM fechamento_mensal
                """).fetchall()

                for fechamento in fechamentos:

                    banco.execute("""
                        INSERT OR IGNORE INTO fechamento_hidrometro
                        (
                            hidrometro_id,
                            mes,
                            fechado,
                            tipo,
                            fechado_em
                        )
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        principal["id"],
                        fechamento["mes"],
                        fechamento["fechado"],
                        fechamento["tipo"],
                        fechamento["fechado_em"]
                    ))


# ============================================================
# HIDRÔMETROS
# ============================================================

def listar_hidrometros():

    with conectar() as banco:

        return banco.execute("""
            SELECT *
            FROM hidrometros
            WHERE ativo=1
            ORDER BY setor, nome
        """).fetchall()


def obter_hidrometro(hidrometro_id):

    with conectar() as banco:

        return banco.execute("""
            SELECT *
            FROM hidrometros
            WHERE id=? AND ativo=1
        """, (hidrometro_id,)).fetchone()


def cadastrar_hidrometro(setor, nome):

    setor = setor.strip()
    nome = nome.strip()

    if not setor or not nome:
        raise ValueError(
            "Informe o setor e a identificação do hidrômetro."
        )

    with conectar() as banco:

        try:

            cursor = banco.execute("""
                INSERT INTO hidrometros (setor, nome)
                VALUES (?, ?)
            """, (setor, nome))

        except sqlite3.IntegrityError as erro:

            raise ValueError(
                "Já existe um hidrômetro com esse setor e identificação."
            ) from erro

        return cursor.lastrowid


# ============================================================
# RECÁLCULO E DETECÇÃO DE ANORMALIDADES
# ============================================================

def _recalcular_leituras(banco, hidrometro_id):

    linhas = banco.execute("""
        SELECT id, data_hora, valor_acumulado
        FROM leituras_hidrometro
        WHERE hidrometro_id=?
        ORDER BY data_hora ASC
    """, (hidrometro_id,)).fetchall()

    leitura_anterior = None
    data_anterior = None

    # Armazena taxas diárias anteriores para formar a média.
    consumos_diarios_anteriores = []

    for linha in linhas:

        valor_atual = float(linha["valor_acumulado"])

        data_atual = datetime.strptime(
            linha["data_hora"],
            FORMATO
        )

        consumo = None
        situacao = "SEM BASE"

        if leitura_anterior is not None:

            consumo = valor_atual - leitura_anterior

            diferenca_tempo = data_atual - data_anterior

            horas_intervalo = (
                diferenca_tempo.total_seconds() / 3600
            )

            if horas_intervalo <= 0:
                raise ValueError(
                    "O intervalo entre as leituras precisa ser positivo."
                )

            # Converte o histórico para litros por dia.
            if consumos_diarios_anteriores:

                media_diaria = (
                    sum(consumos_diarios_anteriores)
                    / len(consumos_diarios_anteriores)
                )

            else:
                media_diaria = None

            # Executa a análise considerando o tempo decorrido.
            situacao, mensagem = detectar_vazamento(
                consumo,
                media_diaria,
                horas_intervalo
            )

            consumo_diario = (
                consumo / horas_intervalo
            ) * 24

            # Leituras urgentes e alertas extremos não entram
            # na média histórica, para não distorcer o padrão.
            if situacao not in ("ALERTA", "URGENTE"):

                consumos_diarios_anteriores.append(
                    consumo_diario
                )

        banco.execute("""
            UPDATE leituras_hidrometro
            SET
                leitura_anterior=?,
                consumo_intervalo=?,
                situacao=?
            WHERE id=?
        """, (
            leitura_anterior,
            consumo,
            situacao,
            linha["id"]
        ))

        leitura_anterior = valor_atual
        data_anterior = data_atual


# ============================================================
# REGISTRO DE LEITURAS
# ============================================================

def registrar_leitura(
    hidrometro_id,
    data,
    horario,
    valor
):

    try:

        valor = float(
            str(valor).replace(",", ".")
        )

    except (TypeError, ValueError) as erro:

        raise ValueError(
            "Informe um número válido para a leitura do hidrômetro."
        ) from erro

    if valor < 0:
        raise ValueError(
            "A leitura acumulada não pode ser negativa."
        )

    try:

        data_hora = datetime.strptime(
            f"{data} {horario}",
            "%Y-%m-%d %H:%M"
        ).strftime(FORMATO)

    except ValueError as erro:

        raise ValueError(
            "Informe uma data e um horário válidos."
        ) from erro

    mes = data_hora[:7]

    with conectar() as banco:

        hidro = banco.execute("""
            SELECT id
            FROM hidrometros
            WHERE id=? AND ativo=1
        """, (hidrometro_id,)).fetchone()

        if not hidro:
            raise ValueError("Hidrômetro não encontrado.")

        fechamento = banco.execute("""
            SELECT fechado
            FROM fechamento_hidrometro
            WHERE hidrometro_id=? AND mes=?
        """, (hidrometro_id, mes)).fetchone()

        if fechamento and fechamento["fechado"]:
            raise ValueError(
                "Este mês está fechado para esse hidrômetro."
            )

        anterior = banco.execute("""
            SELECT *
            FROM leituras_hidrometro
            WHERE hidrometro_id=?
              AND data_hora < ?
            ORDER BY data_hora DESC
            LIMIT 1
        """, (hidrometro_id, data_hora)).fetchone()

        posterior = banco.execute("""
            SELECT *
            FROM leituras_hidrometro
            WHERE hidrometro_id=?
              AND data_hora > ?
            ORDER BY data_hora ASC
            LIMIT 1
        """, (hidrometro_id, data_hora)).fetchone()

        if anterior and valor < anterior["valor_acumulado"]:
            raise ValueError(
                "A leitura não pode ser menor que a leitura anterior."
            )

        if posterior and valor > posterior["valor_acumulado"]:
            raise ValueError(
                "A leitura não pode ser maior que a leitura seguinte."
            )

        if posterior:

            mes_posterior = posterior["data_hora"][:7]

            fechamento_posterior = banco.execute("""
                SELECT fechado
                FROM fechamento_hidrometro
                WHERE hidrometro_id=? AND mes=?
            """, (
                hidrometro_id,
                mes_posterior
            )).fetchone()

            if fechamento_posterior and fechamento_posterior["fechado"]:
                raise ValueError(
                    "A leitura seguinte pertence a um mês fechado."
                )

        try:

            banco.execute("""
                INSERT INTO leituras_hidrometro
                (
                    hidrometro_id,
                    data_hora,
                    valor_acumulado
                )
                VALUES (?, ?, ?)
            """, (
                hidrometro_id,
                data_hora,
                valor
            ))

        except sqlite3.IntegrityError as erro:

            raise ValueError(
                "Já existe uma leitura nesse dia e horário."
            ) from erro

        # Recalcula toda a sequência para atualizar os alertas.
        _recalcular_leituras(
            banco,
            hidrometro_id
        )

        # Fecha automaticamente os meses anteriores.
        meses_anteriores = banco.execute("""
            SELECT DISTINCT substr(data_hora, 1, 7) AS mes
            FROM leituras_hidrometro
            WHERE hidrometro_id=?
              AND substr(data_hora, 1, 7) < ?
        """, (
            hidrometro_id,
            mes
        )).fetchall()

        agora = datetime.now().strftime(FORMATO)

        for item in meses_anteriores:

            banco.execute("""
                INSERT INTO fechamento_hidrometro
                (
                    hidrometro_id,
                    mes,
                    fechado,
                    tipo,
                    fechado_em
                )
                VALUES (?, ?, 1, 'automatico', ?)

                ON CONFLICT(hidrometro_id, mes)
                DO NOTHING
            """, (
                hidrometro_id,
                item["mes"],
                agora
            ))


# ============================================================
# HISTÓRICO
# ============================================================

def listar_meses(hidrometro_id):

    with conectar() as banco:

        leituras = banco.execute("""
            SELECT DISTINCT substr(data_hora, 1, 7) AS mes
            FROM leituras_hidrometro
            WHERE hidrometro_id=?
        """, (hidrometro_id,)).fetchall()

        fechamentos = banco.execute("""
            SELECT mes
            FROM fechamento_hidrometro
            WHERE hidrometro_id=?
        """, (hidrometro_id,)).fetchall()

    meses_leituras = {
        linha["mes"] for linha in leituras
    }

    meses_fechamentos = {
        linha["mes"] for linha in fechamentos
    }

    return sorted(
        meses_leituras | meses_fechamentos,
        reverse=True
    )


def obter_leituras(hidrometro_id, mes=None):

    with conectar() as banco:

        if mes:

            return banco.execute("""
                SELECT *
                FROM leituras_hidrometro
                WHERE hidrometro_id=?
                  AND substr(data_hora, 1, 7)=?
                ORDER BY data_hora DESC
            """, (
                hidrometro_id,
                mes
            )).fetchall()

        return banco.execute("""
            SELECT *
            FROM leituras_hidrometro
            WHERE hidrometro_id=?
            ORDER BY data_hora DESC
        """, (hidrometro_id,)).fetchall()


# ============================================================
# RESUMO MENSAL
# ============================================================

def resumo_mensal(hidrometro_id, mes):

    leituras = obter_leituras(
        hidrometro_id,
        mes
    )

    consumos = [
        r["consumo_intervalo"]
        for r in leituras
        if r["consumo_intervalo"] is not None
    ]

    ano, numero = map(int, mes.split("-"))

    dias_mes = calendar.monthrange(
        ano,
        numero
    )[1]

    with conectar() as banco:

        fechamento = banco.execute("""
            SELECT *
            FROM fechamento_hidrometro
            WHERE hidrometro_id=? AND mes=?
        """, (
            hidrometro_id,
            mes
        )).fetchone()

    total = sum(consumos)

    return {
        "mes": mes,
        "total": total,
        "media_diaria": total / dias_mes,
        "dias_com_registro": len({
            r["data_hora"][:10]
            for r in leituras
        }),
        "quantidade_leituras": len(leituras),
        "fechado": (
            bool(fechamento["fechado"])
            if fechamento else False
        ),
        "tipo_fechamento": (
            fechamento["tipo"]
            if fechamento else None
        ),
        "leituras": leituras,
    }


# ============================================================
# FECHAMENTO MENSAL
# ============================================================

def fechar_mes(hidrometro_id, mes, tipo="manual"):

    if tipo not in ("manual", "automatico"):
        raise ValueError("Tipo de fechamento inválido.")

    with conectar() as banco:

        banco.execute("""
            INSERT INTO fechamento_hidrometro
            (
                hidrometro_id,
                mes,
                fechado,
                tipo,
                fechado_em
            )
            VALUES (?, ?, 1, ?, ?)

            ON CONFLICT(hidrometro_id, mes)
            DO UPDATE SET
                fechado=1,
                tipo=excluded.tipo,
                fechado_em=excluded.fechado_em
        """, (
            hidrometro_id,
            mes,
            tipo,
            datetime.now().strftime(FORMATO)
        ))


# ============================================================
# RESUMO DOS ALERTAS
# ============================================================

def resumo_historico(hidrometro_id):

    with conectar() as banco:

        total = banco.execute("""
            SELECT COUNT(*)
            FROM leituras_hidrometro
            WHERE hidrometro_id=?
        """, (hidrometro_id,)).fetchone()[0]

        urgentes = banco.execute("""
            SELECT COUNT(*)
            FROM leituras_hidrometro
            WHERE hidrometro_id=?
              AND situacao='URGENTE'
        """, (hidrometro_id,)).fetchone()[0]

        alertas = banco.execute("""
            SELECT COUNT(*)
            FROM leituras_hidrometro
            WHERE hidrometro_id=?
              AND situacao='ALERTA'
        """, (hidrometro_id,)).fetchone()[0]

        atencoes = banco.execute("""
            SELECT COUNT(*)
            FROM leituras_hidrometro
            WHERE hidrometro_id=?
              AND situacao='ATENCAO'
        """, (hidrometro_id,)).fetchone()[0]

        media = banco.execute("""
            SELECT AVG(consumo_intervalo)
            FROM leituras_hidrometro
            WHERE hidrometro_id=?
              AND consumo_intervalo IS NOT NULL
        """, (hidrometro_id,)).fetchone()[0]

    return {
        "total": total,
        "urgentes": urgentes,
        "alertas": alertas,
        "atencao": atencoes,
        "media": media or 0
    }


# ============================================================
# COMPARAÇÃO ENTRE MESES
# ============================================================

def obter_comparacao(hidrometro_id, mes):

    meses = [
        m for m in listar_meses(hidrometro_id)
        if m < mes
    ]

    if not meses:
        return None

    atual = resumo_mensal(
        hidrometro_id,
        mes
    )["total"]

    mes_anterior = meses[0]

    anterior = resumo_mensal(
        hidrometro_id,
        mes_anterior
    )["total"]

    diferenca = atual - anterior

    percentual = (
        (diferenca / anterior * 100)
        if anterior else None
    )

    return {
        "mes_anterior": mes_anterior,
        "total_anterior": anterior,
        "total_atual": atual,
        "diferenca": diferenca,
        "percentual": percentual
    }
