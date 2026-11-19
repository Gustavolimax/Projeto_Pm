
from werkzeug.security import generate_password_hash, check_password_hash


def gerar_hash(senha):
    return generate_password_hash(senha)


def verificar_senha(senha, senha_hash):
    return check_password_hash(senha_hash, senha)
