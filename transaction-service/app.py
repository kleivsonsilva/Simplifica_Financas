"""
TRANSACTION SERVICE - Microsserviço de Transações Financeiras
Responsabilidade: CRUD de receitas/despesas, categorização, saldo
Banco: MongoDB (persistência distribuída de transações)
Cache: Redis (saldo em cache por usuário)
Mensageria: RabbitMQ (eventos para notification-service e report-service)
Porta: 5002
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
from datetime import datetime, date
from functools import wraps
from pymongo import MongoClient, DESCENDING
import redis
import pika
import jwt
import json
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [TRANSACTION] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

MONGO_URL    = os.getenv('MONGO_URL')
REDIS_URL    = os.getenv('REDIS_URL')
RABBITMQ_URL = os.getenv('RABBITMQ_URL')
JWT_SECRET   = os.getenv('JWT_SECRET')
PORT         = int(os.getenv('SERVICE_PORT', 5002))

# ─── VALIDAÇÃO DE VARIÁVEIS CRÍTICAS ─────────────────────────
_missing = [v for v, val in [('MONGO_URL', MONGO_URL), ('REDIS_URL', REDIS_URL), ('JWT_SECRET', JWT_SECRET)] if not val]
if _missing:
    raise EnvironmentError(f"Variáveis de ambiente obrigatórias não definidas: {_missing}")

CATEGORIAS_DESPESA = ['Alimentação','Moradia','Transporte','Saúde','Educação','Lazer','Vestuário','Serviços','Estoque','Outros']
CATEGORIAS_RECEITA = ['Salário','Vendas','Freelance','Investimentos','Aposentadoria','Aluguel','Outros']

# ─── CONEXÕES ─────────────────────────────────────────────────
def get_mongo():
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
    return client['simplifica_financas']

def get_redis():
    return redis.from_url(REDIS_URL, decode_responses=True)

def publicar_evento(fila: str, dados: dict):
    """Publica evento assíncrono no RabbitMQ"""
    if not RABBITMQ_URL:
        return
    try:
        params = pika.URLParameters(RABBITMQ_URL)
        conn   = pika.BlockingConnection(params)
        ch     = conn.channel()
        ch.queue_declare(queue=fila, durable=True)
        ch.basic_publish(
            exchange='',
            routing_key=fila,
            body=json.dumps(dados, default=str),
            properties=pika.BasicProperties(delivery_mode=2)
        )
        conn.close()
        logger.info(f"📨 Evento publicado na fila '{fila}'")
    except Exception as e:
        logger.warning(f"⚠️  Falha ao publicar evento: {e}")

# ─── AUTENTICAÇÃO COM BLACKLIST ────────────────────────────────
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            return jsonify({'error': 'Token não fornecido'}), 401
        try:
            # Verifica blacklist no Redis (tokens revogados por logout)
            r = get_redis()
            if r.get(f"blacklist:{token}"):
                return jsonify({'error': 'Token revogado'}), 401
            payload = jwt.decode(token, JWT_SECRET, algorithms=['HS256'])
            request.user = payload
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expirado'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Token inválido'}), 401
        return f(*args, **kwargs)
    return decorated

# ─── CACHE DE SALDO ───────────────────────────────────────────
def invalidar_cache_saldo(user_id: str):
    r = get_redis()
    r.delete(f"saldo:{user_id}")
    r.delete(f"dashboard:{user_id}")

def calcular_saldo_db(user_id: str, db) -> dict:
    pipeline = [
        {'$match': {'usuario_id': user_id, 'ativo': True}},
        {'$group': {'_id': '$tipo', 'total': {'$sum': '$valor'}}}
    ]
    resultado = list(db.transacoes.aggregate(pipeline))
    receitas  = next((r['total'] for r in resultado if r['_id'] == 'receita'), 0)
    despesas  = next((r['total'] for r in resultado if r['_id'] == 'despesa'), 0)
    return {
        'receitas': float(receitas),
        'despesas': float(despesas),
        'saldo':    float(receitas - despesas)
    }

# ─── ROTAS ────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'service': 'transaction-service'})

@app.route('/transacoes', methods=['GET'])
@token_required
def listar_transacoes():
    user_id   = request.user['sub']
    page      = int(request.args.get('page', 1))
    limit     = int(request.args.get('limit', 20))
    tipo      = request.args.get('tipo')
    categoria = request.args.get('categoria')
    data_ini  = request.args.get('data_inicio')
    data_fim  = request.args.get('data_fim')

    filtro = {'usuario_id': user_id, 'ativo': True}
    if tipo:
        filtro['tipo'] = tipo
    if categoria:
        filtro['categoria'] = categoria
    if data_ini or data_fim:
        filtro['data'] = {}
        if data_ini:
            filtro['data']['$gte'] = data_ini
        if data_fim:
            filtro['data']['$lte'] = data_fim

    try:
        db    = get_mongo()
        total = db.transacoes.count_documents(filtro)
        transacoes = list(
            db.transacoes
            .find(filtro, {'_id': 1, 'tipo': 1, 'valor': 1, 'descricao': 1, 'categoria': 1, 'data': 1, 'data_criacao': 1})
            .sort('data', DESCENDING)
            .skip((page - 1) * limit)
            .limit(limit)
        )
        for t in transacoes:
            t['id'] = str(t.pop('_id'))
        return jsonify({
            'transacoes': transacoes,
            'total':      total,
            'page':       page,
            'pages':      (total + limit - 1) // limit
        })
    except Exception as e:
        logger.error(f"❌ Erro ao listar transações: {e}")
        return jsonify({'error': 'Erro interno'}), 500

@app.route('/transacoes', methods=['POST'])
@token_required
def criar_transacao():
    user_id   = request.user['sub']
    data      = request.get_json()
    tipo      = data.get('tipo')
    valor     = data.get('valor')
    descricao = data.get('descricao', '').strip()
    categoria = data.get('categoria', 'Outros')
    dt        = data.get('data', date.today().isoformat())

    if tipo not in ('receita', 'despesa'):
        return jsonify({'error': 'Tipo inválido. Use receita ou despesa'}), 400
    if not valor or float(valor) <= 0:
        return jsonify({'error': 'Valor deve ser positivo'}), 400
    if not descricao:
        return jsonify({'error': 'Descrição obrigatória'}), 400

    doc = {
        'usuario_id':   user_id,
        'tipo':         tipo,
        'valor':        float(valor),
        'descricao':    descricao,
        'categoria':    categoria,
        'data':         dt,
        'data_criacao': datetime.utcnow().isoformat(),
        'ativo':        True
    }

    try:
        db     = get_mongo()
        result = db.transacoes.insert_one(doc)
        doc_id = str(result.inserted_id)
        invalidar_cache_saldo(user_id)
        publicar_evento('transacao_criada', {
            'transacao_id': doc_id,
            'usuario_id':   user_id,
            'tipo':         tipo,
            'valor':        float(valor),
            'categoria':    categoria,
            'data':         dt
        })
        logger.info(f"✅ Transação criada: {tipo} R${valor} para user {user_id}")
        return jsonify({'message': 'Transação criada com sucesso', 'id': doc_id}), 201
    except Exception as e:
        logger.error(f"❌ Erro ao criar transação: {e}")
        return jsonify({'error': 'Erro interno'}), 500

@app.route('/transacoes/<transacao_id>', methods=['DELETE'])
@token_required
def excluir_transacao(transacao_id):
    from bson import ObjectId
    user_id = request.user['sub']
    try:
        db     = get_mongo()
        result = db.transacoes.update_one(
            {'_id': ObjectId(transacao_id), 'usuario_id': user_id},
            {'$set': {'ativo': False}}
        )
        if result.matched_count == 0:
            return jsonify({'error': 'Transação não encontrada'}), 404
        invalidar_cache_saldo(user_id)
        publicar_evento('transacao_excluida', {'transacao_id': transacao_id, 'usuario_id': user_id})
        return jsonify({'message': 'Transação excluída com sucesso'})
    except Exception as e:
        logger.error(f"❌ Erro ao excluir transação: {e}")
        return jsonify({'error': 'Erro interno'}), 500

@app.route('/transacoes/saldo', methods=['GET'])
@token_required
def saldo():
    user_id = request.user['sub']
    try:
        r      = get_redis()
        cached = r.get(f"saldo:{user_id}")
        if cached:
            return jsonify(json.loads(cached))
        db    = get_mongo()
        dados = calcular_saldo_db(user_id, db)
        r.setex(f"saldo:{user_id}", 300, json.dumps(dados))
        return jsonify(dados)
    except Exception as e:
        logger.error(f"❌ Erro ao calcular saldo: {e}")
        return jsonify({'error': 'Erro interno'}), 500

@app.route('/transacoes/resumo-mensal', methods=['GET'])
@token_required
def resumo_mensal():
    user_id = request.user['sub']
    try:
        db = get_mongo()
        pipeline = [
            {'$match': {'usuario_id': user_id, 'ativo': True}},
            {'$addFields': {'mes': {'$substr': ['$data', 0, 7]}}},
            {'$group': {'_id': {'mes': '$mes', 'tipo': '$tipo'}, 'total': {'$sum': '$valor'}}},
            {'$sort': {'_id.mes': 1}}
        ]
        return jsonify(list(db.transacoes.aggregate(pipeline)))
    except Exception as e:
        logger.error(f"❌ Erro no resumo mensal: {e}")
        return jsonify({'error': 'Erro interno'}), 500

@app.route('/transacoes/por-categoria', methods=['GET'])
@token_required
def por_categoria():
    user_id = request.user['sub']
    tipo    = request.args.get('tipo', 'despesa')
    try:
        db = get_mongo()
        pipeline = [
            {'$match': {'usuario_id': user_id, 'ativo': True, 'tipo': tipo}},
            {'$group': {'_id': '$categoria', 'total': {'$sum': '$valor'}}},
            {'$sort': {'total': -1}}
        ]
        return jsonify(list(db.transacoes.aggregate(pipeline)))
    except Exception as e:
        logger.error(f"❌ Erro por categoria: {e}")
        return jsonify({'error': 'Erro interno'}), 500

@app.route('/transacoes/categorias', methods=['GET'])
@token_required
def categorias():
    """Lista categorias disponíveis — protegido por token"""
    return jsonify({'despesa': CATEGORIAS_DESPESA, 'receita': CATEGORIAS_RECEITA})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=False)
