"""
GOAL SERVICE - Microsserviço de Metas Financeiras
Responsabilidade: CRUD de metas, progresso, gamificação
Banco: MongoDB
Mensageria: RabbitMQ (consome eventos de transações para atualizar metas)
Porta: 5003
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime
from functools import wraps
from pymongo import MongoClient, DESCENDING
from bson import ObjectId
import pika
import redis
import jwt
import json
import os
import threading
import time
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [GOAL] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

MONGO_URL    = os.getenv('MONGO_URL')
RABBITMQ_URL = os.getenv('RABBITMQ_URL')
REDIS_URL    = os.getenv('REDIS_URL', '')
JWT_SECRET   = os.getenv('JWT_SECRET')
PORT         = int(os.getenv('SERVICE_PORT', 5003))

# ─── VALIDAÇÃO DE VARIÁVEIS CRÍTICAS ─────────────────────────
_missing = [v for v, val in [('MONGO_URL', MONGO_URL), ('JWT_SECRET', JWT_SECRET)] if not val]
if _missing:
    raise EnvironmentError(f"Variáveis de ambiente obrigatórias não definidas: {_missing}")

def get_mongo():
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    return client['simplifica_financas']

def get_redis():
    if not REDIS_URL:
        return None
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
            if r and r.get(f"blacklist:{token}"):
                return jsonify({'error': 'Token revogado'}), 401
            request.user = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expirado'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Token inválido'}), 401
        return f(*args, **kwargs)
    return decorated

# ─── LÓGICA DE NEGÓCIO: PROCESSAR RECEITA NAS METAS ──────────
def processar_receita_nas_metas(usuario_id: str, valor: float, categoria: str):
    """
    Quando uma receita é registrada, distribui automaticamente o valor
    entre as metas ativas do usuário, priorizando a com menor percentual.

    Regras:
    - Apenas eventos do tipo 'receita' atualizam metas automaticamente
    - A meta com MENOR percentual de conclusão recebe o depósito completo
    - Se o valor levar a meta a 100%, ela é marcada como 'concluida'
    - Se não há metas ativas, o evento é ignorado silenciosamente
    """
    try:
        db    = get_mongo()
        metas = list(db.metas.find({'usuario_id': usuario_id, 'status': 'ativa'}))

        if not metas:
            logger.info(f"ℹ️  Nenhuma meta ativa para user {usuario_id} — evento ignorado")
            return

        # Ordena por percentual de conclusão (menor primeiro = prioridade)
        def percentual(m):
            alvo = m.get('valor_alvo', 0)
            return (m.get('valor_atual', 0) / alvo * 100) if alvo > 0 else 0

        metas_ordenadas = sorted(metas, key=percentual)
        meta_alvo       = metas_ordenadas[0]
        meta_id         = meta_alvo['_id']

        novo_valor = meta_alvo.get('valor_atual', 0.0) + valor
        update     = {'$set': {'valor_atual': round(novo_valor, 2)}}

        if novo_valor >= meta_alvo.get('valor_alvo', 0):
            update['$set']['status']         = 'concluida'
            update['$set']['data_conclusao'] = datetime.utcnow().isoformat()
            logger.info(
                f"🏆 Meta concluída automaticamente via evento: "
                f"'{meta_alvo.get('titulo')}' (user {usuario_id})"
            )
        else:
            pct_antes  = round(percentual(meta_alvo), 1)
            pct_depois = round(min(100, novo_valor / meta_alvo['valor_alvo'] * 100), 1)
            logger.info(
                f"📈 Meta '{meta_alvo.get('titulo')}' atualizada via evento: "
                f"R${meta_alvo.get('valor_atual', 0):.2f} → R${novo_valor:.2f} "
                f"({pct_antes}% → {pct_depois}%)"
            )

        db.metas.update_one({'_id': meta_id}, update)

    except Exception as e:
        logger.error(f"❌ Erro ao processar receita nas metas: {e}")
        raise  # re-raise para o caller decidir nack/ack

# ─── CONSUMIDOR RABBITMQ COM RECONEXÃO AUTOMÁTICA ────────────
def consumir_eventos():
    if not RABBITMQ_URL:
        logger.warning("⚠️  RABBITMQ_URL não definida — consumidor desativado")
        return

    RETRY_DELAY = 5  # segundos entre tentativas de reconexão

    while True:
        try:
            params = pika.URLParameters(RABBITMQ_URL)
            conn   = pika.BlockingConnection(params)
            ch     = conn.channel()
            ch.queue_declare(queue='transacao_criada', durable=True)
            ch.basic_qos(prefetch_count=1)  # processa 1 mensagem por vez

            def callback(ch, method, properties, body):
                try:
                    evento    = json.loads(body)
                    tipo      = evento.get('tipo', '')
                    valor     = float(evento.get('valor', 0))
                    usuario   = evento.get('usuario_id', '')
                    categoria = evento.get('categoria', '')

                    logger.info(
                        f"📨 Evento recebido: tipo={tipo} valor=R${valor:.2f} "
                        f"categoria={categoria} user={usuario}"
                    )

                    # Só receitas alimentam metas automaticamente
                    if tipo == 'receita' and valor > 0 and usuario:
                        processar_receita_nas_metas(usuario, valor, categoria)
                    else:
                        logger.info(f"ℹ️  Evento do tipo '{tipo}' — sem ação nas metas")

                    ch.basic_ack(delivery_tag=method.delivery_tag)

                except Exception as e:
                    logger.error(f"❌ Erro no callback do evento: {e}")
                    # nack sem requeue para não travar a fila com mensagem inválida
                    ch.basic_nack(delivery_tag=method.delivery_tag, requeue=False)

            ch.basic_consume(queue='transacao_criada', on_message_callback=callback)
            logger.info("🔄 Consumidor RabbitMQ iniciado — aguardando eventos...")
            ch.start_consuming()

        except pika.exceptions.AMQPConnectionError as e:
            logger.warning(f"⚠️  Conexão RabbitMQ perdida: {e}. Reconectando em {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)
        except Exception as e:
            logger.error(f"❌ Erro inesperado no consumidor: {e}. Reconectando em {RETRY_DELAY}s...")
            time.sleep(RETRY_DELAY)

# ─── ROTAS ────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'goal-service'})

@app.route('/metas', methods=['GET'])
@token_required
def listar_metas():
    user_id = request.user['sub']
    status  = request.args.get('status', 'ativa')
    try:
        db     = get_mongo()
        filtro = {'usuario_id': user_id}
        if status != 'todas':
            filtro['status'] = status
        metas = list(db.metas.find(filtro).sort('data_criacao', DESCENDING))
        for m in metas:
            m['id'] = str(m.pop('_id'))
            if m.get('valor_alvo', 0) > 0:
                m['percentual'] = min(100, round((m.get('valor_atual', 0) / m['valor_alvo']) * 100, 1))
            else:
                m['percentual'] = 0
        return jsonify(metas)
    except Exception as e:
        logger.error(f"❌ Erro ao listar metas: {e}")
        return jsonify({'error': 'Erro interno'}), 500

@app.route('/metas', methods=['POST'])
@token_required
def criar_meta():
    user_id     = request.user['sub']
    data        = request.get_json()
    titulo      = data.get('titulo', '').strip()
    valor_alvo  = data.get('valor_alvo', 0)
    data_inicio = data.get('data_inicio', datetime.utcnow().date().isoformat())
    data_limite = data.get('data_limite')
    categoria   = data.get('categoria', 'Outros')
    cor         = data.get('cor', '#6366F1')
    descricao   = data.get('descricao', '')

    if not titulo:
        return jsonify({'error': 'Título obrigatório'}), 400
    if float(valor_alvo) <= 0:
        return jsonify({'error': 'Valor alvo deve ser positivo'}), 400

    doc = {
        'usuario_id':     user_id,
        'titulo':         titulo,
        'descricao':      descricao,
        'valor_alvo':     float(valor_alvo),
        'valor_atual':    0.0,
        'categoria':      categoria,
        'data_inicio':    data_inicio,
        'data_limite':    data_limite,
        'cor':            cor,
        'status':         'ativa',
        'data_criacao':   datetime.utcnow().isoformat(),
        'data_conclusao': None
    }

    try:
        db     = get_mongo()
        result = db.metas.insert_one(doc)
        logger.info(f"✅ Meta criada: {titulo} para user {user_id}")
        return jsonify({'message': 'Meta criada com sucesso', 'id': str(result.inserted_id)}), 201
    except Exception as e:
        logger.error(f"❌ Erro ao criar meta: {e}")
        return jsonify({'error': 'Erro interno'}), 500

@app.route('/metas/<meta_id>/deposito', methods=['POST'])
@token_required
def depositar_meta(meta_id):
    user_id = request.user['sub']
    data    = request.get_json()
    valor   = float(data.get('valor', 0))

    if valor <= 0:
        return jsonify({'error': 'Valor deve ser positivo'}), 400

    try:
        db   = get_mongo()
        meta = db.metas.find_one({'_id': ObjectId(meta_id), 'usuario_id': user_id, 'status': 'ativa'})
        if not meta:
            return jsonify({'error': 'Meta não encontrada'}), 404

        novo_valor = meta['valor_atual'] + valor
        update     = {'$set': {'valor_atual': round(novo_valor, 2)}}

        if novo_valor >= meta['valor_alvo']:
            update['$set']['status']         = 'concluida'
            update['$set']['data_conclusao'] = datetime.utcnow().isoformat()
            logger.info(f"🏆 Meta concluída: {meta['titulo']}")

        db.metas.update_one({'_id': ObjectId(meta_id)}, update)
        return jsonify({'message': 'Depósito realizado', 'novo_valor': round(novo_valor, 2)})
    except Exception as e:
        logger.error(f"❌ Erro no depósito: {e}")
        return jsonify({'error': 'Erro interno'}), 500

@app.route('/metas/<meta_id>', methods=['PUT'])
@token_required
def editar_meta(meta_id):
    user_id = request.user['sub']
    data    = request.get_json()
    try:
        db     = get_mongo()
        campos = {campo: data[campo] for campo in ['titulo', 'descricao', 'valor_alvo', 'data_limite', 'categoria', 'cor'] if campo in data}
        if not campos:
            return jsonify({'error': 'Nenhum campo para atualizar'}), 400
        db.metas.update_one({'_id': ObjectId(meta_id), 'usuario_id': user_id}, {'$set': campos})
        return jsonify({'message': 'Meta atualizada com sucesso'})
    except Exception as e:
        logger.error(f"❌ Erro ao editar meta: {e}")
        return jsonify({'error': 'Erro interno'}), 500

@app.route('/metas/<meta_id>/cancelar', methods=['POST'])
@token_required
def cancelar_meta(meta_id):
    user_id = request.user['sub']
    try:
        db = get_mongo()
        db.metas.update_one({'_id': ObjectId(meta_id), 'usuario_id': user_id}, {'$set': {'status': 'cancelada'}})
        return jsonify({'message': 'Meta cancelada'})
    except Exception as e:
        return jsonify({'error': 'Erro interno'}), 500

@app.route('/metas/<meta_id>', methods=['DELETE'])
@token_required
def excluir_meta(meta_id):
    user_id = request.user['sub']
    try:
        db = get_mongo()
        db.metas.delete_one({'_id': ObjectId(meta_id), 'usuario_id': user_id})
        return jsonify({'message': 'Meta excluída com sucesso'})
    except Exception as e:
        return jsonify({'error': 'Erro interno'}), 500

if __name__ == '__main__':
    t = threading.Thread(target=consumir_eventos, daemon=True)
    t.start()
    app.run(host='0.0.0.0', port=PORT, debug=False)