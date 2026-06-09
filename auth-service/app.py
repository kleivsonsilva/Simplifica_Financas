"""
AUTH SERVICE - Microsserviço de Autenticação e Autorização
Responsabilidade: Registro, Login, JWT, Gerenciamento de Usuários
Banco: PostgreSQL (dados relacionais de usuários)
Cache: Redis (sessões e blacklist de tokens)
Porta: 5001
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from functools import wraps
import psycopg2
from psycopg2.extras import RealDictCursor
import redis
import jwt
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [AUTH] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['JSON_ENSURE_ASCII'] = False  # ← FIX: garante UTF-8 correto no JSON
CORS(app)

DATABASE_URL = os.getenv('DATABASE_URL')
REDIS_URL    = os.getenv('REDIS_URL')
JWT_SECRET   = os.getenv('JWT_SECRET')
PORT         = int(os.getenv('SERVICE_PORT', 5001))

# ─── VALIDAÇÃO DE VARIÁVEIS CRÍTICAS ─────────────────────────
_missing = [v for v, val in [('DATABASE_URL', DATABASE_URL), ('JWT_SECRET', JWT_SECRET)] if not val]
if _missing:
    raise EnvironmentError(f"Variáveis de ambiente obrigatórias não definidas: {_missing}")

# ─── CONEXÕES ─────────────────────────────────────────────────
def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def get_redis():
    if not REDIS_URL:
        return None
    return redis.from_url(REDIS_URL, decode_responses=True)

# ─── INICIALIZAÇÃO DO BANCO ────────────────────────────────────
def init_db():
    conn = get_db()
    cur  = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            nome VARCHAR(100) NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL,
            senha_hash VARCHAR(255) NOT NULL,
            modo_interface VARCHAR(20) DEFAULT 'simples',
            ativo BOOLEAN DEFAULT TRUE,
            data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ultimo_login TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_email ON usuarios(email);
    """)
    conn.commit()
    cur.close()
    conn.close()
    logger.info("✅ Banco PostgreSQL inicializado")

# ─── HELPERS JWT ───────────────────────────────────────────────
def gerar_token(user_id: str, email: str, nome: str) -> str:
    payload = {
        'sub':   user_id,
        'email': email,
        'nome':  nome,
        'iat':   datetime.utcnow(),
        'exp':   datetime.utcnow() + timedelta(hours=24)
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm='HS256')
    # garante que sempre retorna string (compatibilidade PyJWT < 2.0)
    return token if isinstance(token, str) else token.decode('utf-8')

def verificar_token(token: str) -> dict:
    try:
        r = get_redis()
        if r and r.get(f"blacklist:{token}"):
            return None
        return jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            return jsonify({'error': 'Token não fornecido'}), 401
        payload = verificar_token(token)
        if not payload:
            return jsonify({'error': 'Token inválido ou expirado'}), 401
        request.user = payload
        return f(*args, **kwargs)
    return decorated

# ─── ROTAS ────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'auth-service', 'timestamp': datetime.utcnow().isoformat()})

@app.route('/auth/registro', methods=['POST'])
def registro():
    data  = request.get_json()
    nome  = data.get('nome', '').strip()
    email = data.get('email', '').strip().lower()
    senha = data.get('senha', '')
    modo  = data.get('modo_interface', 'simples')

    if not all([nome, email, senha]):
        return jsonify({'error': 'Nome, email e senha são obrigatórios'}), 400
    if len(senha) < 6:
        return jsonify({'error': 'Senha deve ter pelo menos 6 caracteres'}), 400

    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("SELECT id FROM usuarios WHERE email = %s", (email,))
        if cur.fetchone():
            cur.close()
            conn.close()
            return jsonify({'error': 'Email já cadastrado'}), 409

        senha_hash = generate_password_hash(senha)
        cur.execute(
            "INSERT INTO usuarios (nome, email, senha_hash, modo_interface) VALUES (%s,%s,%s,%s) RETURNING id, nome, email, modo_interface, data_criacao",
            (nome, email, senha_hash, modo)
        )
        usuario = dict(cur.fetchone())
        usuario['id'] = str(usuario['id'])
        if usuario.get('data_criacao'):
            usuario['data_criacao'] = usuario['data_criacao'].isoformat()
        conn.commit()
        cur.close()
        conn.close()

        token = gerar_token(usuario['id'], usuario['email'], usuario['nome'])
        logger.info(f"✅ Novo usuário registrado: {email}")
        return jsonify({'message': 'Usuário criado com sucesso', 'token': token, 'usuario': usuario}), 201

    except Exception as e:
        logger.error(f"❌ Erro no registro: {e}")
        return jsonify({'error': 'Erro interno'}), 500

@app.route('/auth/login', methods=['POST'])
def login():
    data  = request.get_json()
    email = data.get('email', '').strip().lower()
    senha = data.get('senha', '')

    if not all([email, senha]):
        return jsonify({'error': 'Email e senha obrigatórios'}), 400

    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("SELECT * FROM usuarios WHERE email = %s AND ativo = TRUE", (email,))
        usuario = cur.fetchone()

        if not usuario or not check_password_hash(usuario['senha_hash'], senha):
            cur.close()
            conn.close()
            return jsonify({'error': 'Credenciais inválidas'}), 401

        cur.execute("UPDATE usuarios SET ultimo_login = %s WHERE id = %s", (datetime.utcnow(), usuario['id']))
        conn.commit()
        cur.close()
        conn.close()

        user_id = str(usuario['id'])
        token   = gerar_token(user_id, usuario['email'], usuario['nome'])

        r = get_redis()
        if r:
            r.setex(f"session:{user_id}", 86400, token)

        logger.info(f"✅ Login realizado: {email}")
        return jsonify({
            'token': token,
            'usuario': {
                'id':             user_id,
                'nome':           usuario['nome'],
                'email':          usuario['email'],
                'modo_interface': usuario['modo_interface']
            }
        })

    except Exception as e:
        logger.error(f"❌ Erro no login: {e}")
        return jsonify({'error': 'Erro interno'}), 500

@app.route('/auth/logout', methods=['POST'])
@token_required
def logout():
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    r     = get_redis()
    if r:
        r.setex(f"blacklist:{token}", 86400, '1')
        r.delete(f"session:{request.user['sub']}")
    logger.info(f"✅ Logout: {request.user['email']}")
    return jsonify({'message': 'Logout realizado com sucesso'})

@app.route('/auth/verificar', methods=['GET'])
@token_required
def verificar():
    return jsonify({'valid': True, 'user': request.user})

@app.route('/auth/perfil', methods=['GET'])
@token_required
def perfil():
    try:
        conn = get_db()
        cur  = conn.cursor()
        cur.execute("SELECT id, nome, email, modo_interface, data_criacao, ultimo_login FROM usuarios WHERE id = %s", (request.user['sub'],))
        usuario = cur.fetchone()
        cur.close()
        conn.close()

        if not usuario:
            return jsonify({'error': 'Usuário não encontrado'}), 404

        usuario = dict(usuario)
        usuario['id'] = str(usuario['id'])
        for campo in ['data_criacao', 'ultimo_login']:
            if usuario.get(campo):
                usuario[campo] = usuario[campo].isoformat()
        return jsonify(usuario)

    except Exception as e:
        logger.error(f"❌ Erro ao buscar perfil: {e}")
        return jsonify({'error': 'Erro interno'}), 500

@app.route('/auth/perfil', methods=['PUT'])
@token_required
def atualizar_perfil():
    data = request.get_json()
    nome = data.get('nome', '').strip()
    modo = data.get('modo_interface', '')

    try:
        conn = get_db()
        cur  = conn.cursor()
        if nome:
            cur.execute("UPDATE usuarios SET nome = %s WHERE id = %s", (nome, request.user['sub']))
        if modo in ['simples', 'avancado']:
            cur.execute("UPDATE usuarios SET modo_interface = %s WHERE id = %s", (modo, request.user['sub']))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({'message': 'Perfil atualizado com sucesso'})
    except Exception as e:
        logger.error(f"❌ Erro ao atualizar perfil: {e}")
        return jsonify({'error': 'Erro interno'}), 500

# ─── INICIALIZAÇÃO ────────────────────────────────────────────
try:
    init_db()
except Exception as e:
    logger.warning(f"⚠️  Banco ainda não disponível: {e}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=False)
