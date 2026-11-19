
import sys
from pathlib import Path
from datetime import date, datetime

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash
)

# ============================================================
# CONFIGURAÇÃO DOS CAMINHOS
# ============================================================

RAIZ = Path(__file__).resolve().parent.parent

if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


# ============================================================
# IMPORTAÇÃO DO BANCO DE DADOS
# ============================================================

from web.banco import (
    criar_tabelas,
    listar_hidrometros,
    obter_hidrometro,
    cadastrar_hidrometro,
    registrar_leitura,
    listar_meses,
    obter_leituras,
    resumo_mensal,
    fechar_mes,
    resumo_historico,
    obter_comparacao,
)


# ============================================================
# CONFIGURAÇÃO DO FLASK
# ============================================================

app = Flask(__name__)

app.secret_key = "agua-em-alerta-chave-local"

# Cria as tabelas quando a aplicação é iniciada.
criar_tabelas()


@app.context_processor
def disponibilizar_menu():
    """Disponibiliza os links principais para todos os templates."""
    return {
        "menu_principal": [
            {"nome": "Início", "endpoint": "index", "icone": "⌂"},
            {"nome": "Registrar leitura", "endpoint": "registrar", "icone": "＋"},
            {"nome": "Histórico", "endpoint": "historico", "icone": "▤"},
            {"nome": "Alertas", "endpoint": "alertas", "icone": "⚠"},
        ]
    }


# ============================================================
# SELEÇÃO DO HIDRÔMETRO
# ============================================================

def selecionar_hidrometro():

    hidrometros = listar_hidrometros()

    if not hidrometros:
        return None, hidrometros

    # Prioriza o ID enviado pela URL ou formulário.
    hidrometro_id = request.values.get("hidrometro_id", "").strip()

    try:
        escolhido = int(hidrometro_id) if hidrometro_id else hidrometros[0]["id"]
    except (TypeError, ValueError):
        escolhido = hidrometros[0]["id"]

    hidro = obter_hidrometro(escolhido)

    # Se o ID não existir ou estiver inativo, usa o primeiro disponível.
    if hidro is None:
        hidro = hidrometros[0]

    return hidro, hidrometros


# ============================================================
# DASHBOARD PRINCIPAL
# ============================================================

@app.route("/inicio")
def inicio():
    return redirect(url_for("index"))


@app.route("/")
def index():

    hidro, hidrometros = selecionar_hidrometro()

    mes_atual = date.today().strftime("%Y-%m")

    # Busca os dados do hidrômetro selecionado.
    if hidro:

        resumo = resumo_mensal(
            hidro["id"],
            mes_atual
        )

        analise = resumo_historico(
            hidro["id"]
        )

        ultimas = obter_leituras(
            hidro["id"]
        )[:5]

    else:

        resumo = {
            "total": 0,
            "quantidade_leituras": 0
        }

        analise = {
            "total": 0,
            "media": 0,
            "urgentes": 0,
            "alertas": 0,
            "atencao": 0
        }

        ultimas = []

    # --------------------------------------------------------
    # CLASSIFICAÇÃO DOS ALERTAS
    # Prioridade: URGENTE > ALERTA > ATENÇÃO > SEM BASE > NORMAL
    # --------------------------------------------------------

    if analise["urgentes"]:

        status = "URGENTE"

        mensagem = (
            f"Existem {analise['urgentes']} registro(s) "
            "com consumo extremamente elevado. "
            "Verifique imediatamente as instalações."
        )

    elif analise["alertas"]:

        status = "ALERTA"

        mensagem = (
            f"Existem {analise['alertas']} registro(s) "
            "com consumo acima do padrão."
        )

    elif analise["atencao"]:

        status = "ATENCAO"

        mensagem = (
            f"Existem {analise['atencao']} registro(s) "
            "que precisam de acompanhamento."
        )

    elif analise["total"] < 2:

        status = "SEM-BASE"

        mensagem = (
            "Registre mais leituras para formar "
            "um histórico de comparação."
        )

    else:

        status = "NORMAL"

        mensagem = (
            "Nenhum alerta identificado no histórico "
            "deste hidrômetro."
        )

    # Adiciona o status e a mensagem à análise.
    analise["status"] = status
    analise["mensagem"] = mensagem

    # --------------------------------------------------------
    # ENVIO DAS INFORMAÇÕES PARA O HTML
    # --------------------------------------------------------

    return render_template(
        "index.html",

        hidrometros=hidrometros,

        hidrometro_id=(
            hidro["id"] if hidro else None
        ),

        hidrometro=hidro,

        mes_atual=mes_atual,

        consumo_mes=resumo["total"],

        total_mes=resumo["total"],

        qtd_leituras=resumo["quantidade_leituras"],

        analise=analise,

        recentes=ultimas,

        leituras=ultimas
    )


# ============================================================
# REGISTRAR LEITURA E CADASTRAR HIDRÔMETRO
# ============================================================

@app.route("/registrar", methods=["GET", "POST"])
def registrar():

    if request.method == "POST":

        acao = request.form.get(
            "acao",
            "nova_leitura"
        )

        try:

            # ------------------------------------------------
            # CADASTRAR NOVO HIDRÔMETRO
            # ------------------------------------------------

            if acao == "novo_hidrometro":

                novo_id = cadastrar_hidrometro(
                    request.form.get("setor", ""),
                    request.form.get("nome", "")
                )

                flash(
                    "Hidrômetro cadastrado com sucesso!",
                    "sucesso"
                )

                return redirect(
                    url_for(
                        "registrar",
                        hidrometro_id=novo_id
                    )
                )

            # ------------------------------------------------
            # REGISTRAR NOVA LEITURA
            # ------------------------------------------------

            hidrometro_id = request.form.get(
                "hidrometro_id",
                ""
            ).strip()

            # Verifica se o hidrômetro foi selecionado.
            if not hidrometro_id:

                raise ValueError(
                    "Selecione um hidrômetro antes de registrar a leitura."
                )

            # Verifica se o ID é numérico.
            if not hidrometro_id.isdigit():

                raise ValueError(
                    "O hidrômetro selecionado é inválido."
                )

            # Verifica se os campos obrigatórios estão preenchidos.
            data_leitura = request.form.get(
                "data_leitura",
                ""
            ).strip()

            horario_leitura = request.form.get(
                "horario_leitura",
                ""
            ).strip()

            valor_acumulado = request.form.get(
                "valor_acumulado",
                ""
            ).strip()

            if not data_leitura:

                raise ValueError(
                    "Informe a data da leitura."
                )

            if not horario_leitura:

                raise ValueError(
                    "Informe o horário da leitura."
                )

            if not valor_acumulado:

                raise ValueError(
                    "Informe o valor acumulado do hidrômetro."
                )

            # Registra a leitura no banco de dados.
            registrar_leitura(
                int(hidrometro_id),

                data_leitura,

                horario_leitura,

                valor_acumulado
            )

            flash(
                "Leitura registrada com sucesso! "
                "A análise de consumo foi atualizada.",
                "sucesso"
            )

            return redirect(
                url_for(
                    "historico",
                    hidrometro_id=hidrometro_id
                )
            )

        except (ValueError, TypeError) as erro:

            flash(
                str(erro),
                "erro"
            )

        except Exception:

            app.logger.exception(
                "Erro ao registrar dados."
            )

            flash(
                "Não foi possível salvar. "
                "Confira os dados e tente novamente.",
                "erro"
            )

    # Busca o hidrômetro selecionado.
    hidro, hidrometros = selecionar_hidrometro()

    return render_template(
        "registrar.html",

        hidrometros=hidrometros,

        hidrometro_id=(
            hidro["id"] if hidro else None
        ),

        hoje=date.today().isoformat(),

        horario=datetime.now().strftime("%H:%M")
    )


# ============================================================
# HISTÓRICO MENSAL
# ============================================================

@app.route("/historico")
def historico():

    hidro, hidrometros = selecionar_hidrometro()

    # Verifica se existe algum hidrômetro cadastrado.
    if not hidro:

        return render_template(
            "historico.html",

            hidrometros=[],

            hidrometro=None,

            hidrometro_id=None,

            registros=[],

            meses=[],

            mes_selecionado=None,

            resumo=None,

            comparacao=None
        )

    # Busca os meses que possuem registros.
    meses = listar_meses(
        hidro["id"]
    )

    mes = request.args.get("mes")

    # Seleciona o mês solicitado ou o mais recente.
    if not mes or mes not in meses:

        mes = (
            meses[0]
            if meses
            else date.today().strftime("%Y-%m")
        )

    # Calcula o resumo mensal.
    resumo = resumo_mensal(
        hidro["id"],
        mes
    )

    return render_template(
        "historico.html",

        hidrometros=hidrometros,

        hidrometro=hidro,

        hidrometro_id=hidro["id"],

        registros=resumo["leituras"],

        meses=meses,

        mes_selecionado=mes,

        resumo=resumo,

        comparacao=obter_comparacao(
            hidro["id"],
            mes
        )
    )


# ============================================================
# FECHAMENTO MENSAL
# ============================================================

@app.route("/fechar-mes", methods=["POST"])
def fechar_mes_rota():

    try:

        hidrometro_id = request.form.get(
            "hidrometro_id",
            ""
        ).strip()

        mes = request.form.get(
            "mes",
            ""
        ).strip()

        # Verifica se o hidrômetro foi informado.
        if not hidrometro_id:

            raise ValueError(
                "Selecione um hidrômetro."
            )

        if not hidrometro_id.isdigit():

            raise ValueError(
                "O hidrômetro selecionado é inválido."
            )

        # Verifica se o mês foi informado.
        if not mes:

            raise ValueError(
                "Selecione o mês que deseja fechar."
            )

        fechar_mes(
            int(hidrometro_id),
            mes,
            "manual"
        )

        flash(
            "Mês fechado. O histórico foi preservado.",
            "sucesso"
        )

    except (ValueError, TypeError) as erro:

        flash(
            str(erro),
            "erro"
        )

    except Exception:

        app.logger.exception(
            "Erro ao fechar mês."
        )

        flash(
            "Não foi possível fechar esse mês.",
            "erro"
        )

    return redirect(
        url_for(
            "historico",

            hidrometro_id=request.form.get(
                "hidrometro_id"
            ),

            mes=request.form.get("mes")
        )
    )


# ============================================================
# CENTRAL DE ALERTAS
# ============================================================

@app.route("/alertas")
def alertas():

    hidro, hidrometros = selecionar_hidrometro()

    # Verifica se existe hidrômetro cadastrado.
    if not hidro:

        return render_template(
            "alertas.html",

            hidrometros=[],

            hidrometro=None,

            hidrometro_id=None,

            urgentes=[],

            alertas=[],

            atencoes=[]
        )

    # Busca as leituras do hidrômetro selecionado.
    registros = obter_leituras(
        hidro["id"]
    )

    # Separa os registros por nível de gravidade.
    urgentes = [
        r for r in registros
        if r["situacao"] == "URGENTE"
    ]

    alertas = [
        r for r in registros
        if r["situacao"] == "ALERTA"
    ]

    atencoes = [
        r for r in registros
        if r["situacao"] == "ATENCAO"
    ]

    return render_template(
        "alertas.html",

        hidrometros=hidrometros,

        hidrometro=hidro,

        hidrometro_id=hidro["id"],

        urgentes=urgentes,

        alertas=alertas,

        atencoes=atencoes
    )


# ============================================================
# EXECUÇÃO DA APLICAÇÃO
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=True
    )
