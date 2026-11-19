
from functools import wraps
from flask import session, redirect, url_for, flash


def login_obrigatorio(funcao):
    @wraps(funcao)
    def verificar(*args, **kwargs):

        if "usuario_id" not in session:
            flash("Faça login para continuar.", "erro")
            return redirect(url_for("login"))

        return funcao(*args, **kwargs)

    return verificar


def exigir_perfil(perfil):
    def decorador(funcao):

        @wraps(funcao)
        @login_obrigatorio
        def verificar(*args, **kwargs):

            if session.get("perfil") != perfil:
                flash("Você não tem permissão para acessar esta página.", "erro")
                return redirect(url_for("index"))

            return funcao(*args, **kwargs)

        return verificar

    return decorador
