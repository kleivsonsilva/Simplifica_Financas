"""
API GATEWAY - Ponto único de entrada do sistema distribuído
Responsabilidade: Roteamento, autenticação centralizada, rate limiting, logs
Cache: Redis (rate limiting)
Porta: 8000
"""

from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import redis
import requests
import jwt
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [GATEWAY] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, origins='*')

# ─── CONFIG ───────────────────────────────────────────────────
AUTH_URL         = os.getenv('AUTH_SERVICE_URL',         'http://auth-service:5001')
TRANSACTION_URL  = os.getenv('TRANSACTION_SERVICE_URL',  'http://transaction-service:5002')
GOAL_URL         = os.getenv('GOAL_SERVICE_URL',         'http://goal-service:5003')
REPORT_URL       = os.getenv('REPORT_SERVICE_URL',       'http://report-service:5004')
NOTIFICATION_URL = os.getenv('NOTIFICATION_SERVICE_URL', 'http://notification-service:5005')
REDIS_URL        = os.getenv('REDIS_URL')
JWT_SECRET       = os.getenv('JWT_SECRET')
PORT             = int(os.getenv('GATEWAY_PORT', 8000))

# ─── VALIDAÇÃO DE VARIÁVEIS CRÍTICAS ─────────────────────────
_missing = [v for v, val in [('JWT_SECRET', JWT_SECRET)] if not val]
if _missing:
    raise EnvironmentError(f"Variáveis de ambiente obrigatórias não definidas: {_missing}")

TIMEOUT = (15, 60)

# ─── HTTP SESSION COM CONNECTION POOL ─────────────────────────
def make_session():
    s       = requests.Session()
    retry   = Retry(total=2, backoff_factor=0.2, status_forcelist=[502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=50)
    s.mount('http://', adapter)
    return s

http = make_session()

# ─── REDIS ────────────────────────────────────────────────────
_redis_client = None
def get_redis():
    global _redis_client
    if _redis_client is None and REDIS_URL:
        _redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    return _redis_client

# ─── RATE LIMITING ────────────────────────────────────────────
def rate_limit(max_req=60, window=60):
    def decorator(f):
        from functools import wraps
        @wraps(f)
        def wrapper(*args, **kwargs):
            ip  = request.remote_addr
            key = f"rl:{ip}"
            try:
                r   = get_redis()
                if r:
                    cnt = r.incr(key)
                    if cnt == 1:
                        r.expire(key, window)
                    if cnt > max_req:
                        return jsonify({'error': 'Rate limit excedido. Tente novamente em breve.'}), 429
            except Exception:
                pass
            return f(*args, **kwargs)
        return wrapper
    return decorator

# ─── PROXY ────────────────────────────────────────────────────
def proxy(base_url: str, path: str) -> Response:
    url     = f"{base_url}{path}"
    headers = {k: v for k, v in request.headers if k != 'Host'}
    try:
        json_data = request.get_json(silent=True)
        form_data = request.form or None
        resp = http.request(
            method          = request.method,
            url             = url,
            headers         = headers,
            params          = request.args,
            json            = json_data if json_data is not None else None,
            data            = form_data if json_data is None else None,
            timeout         = TIMEOUT,
            allow_redirects = False
        )
        logger.info(f"→ {request.method} {url} [{resp.status_code}]")
        excluded    = ['content-encoding', 'transfer-encoding', 'connection', 'content-length']
        headers_out = {k: v for k, v in resp.headers.items() if k.lower() not in excluded}
        return Response(resp.content, status=resp.status_code, headers=headers_out)

    except requests.exceptions.ConnectionError:
        logger.error(f"❌ Serviço indisponível: {base_url}")
        return jsonify({'error': 'Serviço temporariamente indisponível'}), 503
    except requests.exceptions.Timeout:
        logger.error(f"⏱ Timeout: {url}")
        return jsonify({'error': 'Timeout ao processar requisição'}), 504

# ─── AUTH ─────────────────────────────────────────────────────
def verificar_token_gateway() -> bool:
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if not token:
        return False
    try:
        jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        return True
    except Exception:
        return False

def requer_autenticacao(f):
    from functools import wraps
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not verificar_token_gateway():
            return jsonify({'error': 'Autenticação necessária'}), 401
        return f(*args, **kwargs)
    return wrapper

# ─── LOGGING ──────────────────────────────────────────────────
@app.before_request
def log_request():
    logger.info(f"📥 {request.method} {request.path} | IP: {request.remote_addr}")

# ─── ROTAS PÚBLICAS ───────────────────────────────────────────
@app.route('/health', methods=['GET'])
def health():
    services = {}
    for nome, url in [
        ('auth',         AUTH_URL),
        ('transaction',  TRANSACTION_URL),
        ('goal',         GOAL_URL),
        ('report',       REPORT_URL),
        ('notification', NOTIFICATION_URL)
    ]:
        try:
            r = http.get(f"{url}/health", timeout=(2, 5))
            services[nome] = 'ok' if r.status_code == 200 else 'degraded'
        except Exception:
            services[nome] = 'down'
    status = 'ok' if all(v == 'ok' for v in services.values()) else 'degraded'
    return jsonify({'gateway': 'ok', 'services': services, 'status': status})

@app.route('/api/auth/registro', methods=['POST'])
@rate_limit(max_req=10, window=60)
def registro():
    return proxy(AUTH_URL, '/auth/registro')

@app.route('/api/auth/login', methods=['POST'])
@rate_limit(max_req=20, window=60)
def login():
    return proxy(AUTH_URL, '/auth/login')

# ─── ROTAS PROTEGIDAS ─────────────────────────────────────────
@app.route('/api/auth/logout', methods=['POST'])
@requer_autenticacao
def logout():
    return proxy(AUTH_URL, '/auth/logout')

@app.route('/api/auth/perfil', methods=['GET', 'PUT'])
@requer_autenticacao
def perfil():
    return proxy(AUTH_URL, '/auth/perfil')

@app.route('/api/transacoes', methods=['GET', 'POST'])
@requer_autenticacao
@rate_limit(max_req=120, window=60)
def transacoes():
    return proxy(TRANSACTION_URL, '/transacoes')

@app.route('/api/transacoes/saldo', methods=['GET'])
@requer_autenticacao
def saldo():
    return proxy(TRANSACTION_URL, '/transacoes/saldo')

@app.route('/api/transacoes/resumo-mensal', methods=['GET'])
@requer_autenticacao
def resumo_mensal():
    return proxy(TRANSACTION_URL, '/transacoes/resumo-mensal')

@app.route('/api/transacoes/por-categoria', methods=['GET'])
@requer_autenticacao
def por_categoria():
    return proxy(TRANSACTION_URL, '/transacoes/por-categoria')

@app.route('/api/transacoes/categorias', methods=['GET'])
@requer_autenticacao
def categorias():
    return proxy(TRANSACTION_URL, '/transacoes/categorias')

@app.route('/api/transacoes/<transacao_id>', methods=['DELETE'])
@requer_autenticacao
def excluir_transacao(transacao_id):
    return proxy(TRANSACTION_URL, f'/transacoes/{transacao_id}')

@app.route('/api/metas', methods=['GET', 'POST'])
@requer_autenticacao
def metas():
    return proxy(GOAL_URL, '/metas')

@app.route('/api/metas/<meta_id>', methods=['PUT', 'DELETE'])
@requer_autenticacao
def meta_detalhe(meta_id):
    return proxy(GOAL_URL, f'/metas/{meta_id}')

@app.route('/api/metas/<meta_id>/deposito', methods=['POST'])
@requer_autenticacao
def deposito_meta(meta_id):
    return proxy(GOAL_URL, f'/metas/{meta_id}/deposito')

@app.route('/api/metas/<meta_id>/cancelar', methods=['POST'])
@requer_autenticacao
def cancelar_meta(meta_id):
    return proxy(GOAL_URL, f'/metas/{meta_id}/cancelar')

@app.route('/api/relatorios/resumo', methods=['GET'])
@requer_autenticacao
def relatorio_resumo():
    return proxy(REPORT_URL, '/relatorios/resumo')

@app.route('/api/relatorios/exportar/excel', methods=['GET'])
@requer_autenticacao
def exportar_excel():
    return proxy(REPORT_URL, '/relatorios/exportar/excel')

@app.route('/api/relatorios/exportar/pdf', methods=['GET'])
@requer_autenticacao
def exportar_pdf():
    return proxy(REPORT_URL, '/relatorios/exportar/pdf')

@app.route('/api/notificacoes', methods=['GET'])
@requer_autenticacao
def notificacoes():
    return proxy(NOTIFICATION_URL, '/notificacoes')

@app.route('/api/notificacoes/limpar', methods=['DELETE'])
@requer_autenticacao
def limpar_notificacoes():
    return proxy(NOTIFICATION_URL, '/notificacoes/limpar')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=False)
