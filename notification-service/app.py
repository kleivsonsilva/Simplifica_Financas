"""
NOTIFICATION SERVICE - Microsserviço de Notificações
Responsabilidade: Consome eventos do RabbitMQ e envia alertas (logs/webhook)
Mensageria: RabbitMQ (consumidor de eventos)
Cache: Redis (armazena notificações do usuário)
Porta: 5005
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime
from functools import wraps
import redis
import pika
import jwt
import json
import os
import threading
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [NOTIFICATION] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

RABBITMQ_URL = os.getenv('RABBITMQ_URL')
REDIS_URL    = os.getenv('REDIS_URL')
JWT_SECRET   = os.getenv('JWT_SECRET')
PORT         = int(os.getenv('SERVICE_PORT', 5005))

# ─── VALIDAÇÃO DE VARIÁVEIS CRÍTICAS ─────────────────────────
_missing = [v for v, val in [('REDIS_URL', REDIS_URL), ('JWT_SECRET', JWT_SECRET)] if not val]
if _missing:
    raise EnvironmentError(f"Variáveis de ambiente obrigatórias não definidas: {_missing}")

def get_redis():
    return redis.from_url(REDIS_URL, decode_responses=True)

# ─── AUTENTICAÇÃO COM BLACKLIST ────────────────────────────────
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            return jsonify({'error': 'Token não fornecido'}), 401
        try:
            r = get_redis()
            if r.get(f"blacklist:{token}"):
                return jsonify({'error': 'Token revogado'}), 401
            request.user = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expirado'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Token inválido'}), 401
        return f(*args, **kwargs)
    return decorated

def salvar_notificacao(user_id: str, mensagem: str, tipo: str = 'info'):
    r    = get_redis()
    key  = f"notif:{user_id}"
    notif = json.dumps({
        'mensagem': mensagem,
        'tipo':     tipo,
        'data':     datetime.utcnow().isoformat(),
        'lida':     False
    })
    r.lpush(key, notif)
    r.ltrim(key, 0, 49)

def processar_transacao_criada(evento: dict):
    user_id   = evento.get('usuario_id')
    tipo      = evento.get('tipo')
    valor     = evento.get('valor', 0)
    categoria = evento.get('categoria', '')

    if tipo == 'despesa' and valor > 1000:
        salvar_notificacao(user_id, f'⚠️ Despesa alta registrada: R$ {valor:.2f} em {categoria}', 'alerta')
    elif tipo == 'receita':
        salvar_notificacao(user_id, f'✅ Receita registrada: R$ {valor:.2f}', 'sucesso')
    else:
        salvar_notificacao(user_id, f'📝 Despesa registrada: R$ {valor:.2f} em {categoria}', 'info')

def consumir_eventos():
    if not RABBITMQ_URL:
        logger.warning("⚠️  RABBITMQ_URL não definida — consumidor desativado")
        return
    try:
        params = pika.URLParameters(RABBITMQ_URL)
        conn   = pika.BlockingConnection(params)
        ch     = conn.channel()
        ch.queue_declare(queue='transacao_criada',  durable=True)
        ch.queue_declare(queue='transacao_excluida', durable=True)

        def on_transacao_criada(ch, method, properties, body):
            try:
                evento = json.loads(body)
                processar_transacao_criada(evento)
                logger.info(f"📨 Notificação processada para user {evento.get('usuario_id')}")
                ch.basic_ack(delivery_tag=method.delivery_tag)
            except Exception as e:
                logger.error(f"❌ Erro: {e}")

        def on_transacao_excluida(ch, method, properties, body):
            try:
                evento  = json.loads(body)
                user_id = evento.get('usuario_id')
                salvar_notificacao(user_id, '🗑️ Transação excluída do sistema', 'info')
                ch.basic_ack(delivery_tag=method.delivery_tag)
            except Exception as e:
                logger.error(f"❌ Erro: {e}")

        ch.basic_consume('transacao_criada',   on_message_callback=on_transacao_criada)
        ch.basic_consume('transacao_excluida', on_message_callback=on_transacao_excluida)
        logger.info("🔄 Consumidor de notificações iniciado")
        ch.start_consuming()
    except Exception as e:
        logger.warning(f"⚠️  RabbitMQ não disponível: {e}")

# ─── ROTAS ────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'notification-service'})

@app.route('/notificacoes', methods=['GET'])
@token_required
def listar_notificacoes():
    user_id = request.user['sub']
    try:
        r            = get_redis()
        raw          = r.lrange(f"notif:{user_id}", 0, 19)
        notificacoes = [json.loads(n) for n in raw]
        return jsonify({'notificacoes': notificacoes, 'total': len(notificacoes)})
    except Exception as e:
        logger.error(f"❌ Erro: {e}")
        return jsonify({'error': 'Erro interno'}), 500

@app.route('/notificacoes/limpar', methods=['DELETE'])
@token_required
def limpar_notificacoes():
    user_id = request.user['sub']
    get_redis().delete(f"notif:{user_id}")
    return jsonify({'message': 'Notificações limpas'})

if __name__ == '__main__':
    t = threading.Thread(target=consumir_eventos, daemon=True)
    t.start()
    app.run(host='0.0.0.0', port=PORT, debug=False)
