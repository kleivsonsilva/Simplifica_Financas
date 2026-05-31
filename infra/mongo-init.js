// ================================================================
// MONGO INIT — Simplifica Finanças
// Executado automaticamente na primeira inicialização do MongoDB
// Cria coleções com validação de schema e índices de performance
// ================================================================

db = db.getSiblingDB('simplifica_financas');

// ─── COLEÇÃO: transacoes ─────────────────────────────────────────
db.createCollection('transacoes', {
  validator: {
    $jsonSchema: {
      bsonType: 'object',
      required: ['usuario_id', 'tipo', 'valor', 'descricao', 'data'],
      properties: {
        usuario_id: { bsonType: 'string' },
        tipo:       { bsonType: 'string', enum: ['receita', 'despesa'] },
        valor:      { bsonType: 'double', minimum: 0.01 },
        descricao:  { bsonType: 'string', minLength: 1 },
        categoria:  { bsonType: 'string' },
        data:       { bsonType: 'string' },
        ativo:      { bsonType: 'bool' }
      }
    }
  }
});

db.transacoes.createIndex({ usuario_id: 1, data: -1 });
db.transacoes.createIndex({ usuario_id: 1, tipo: 1 });
db.transacoes.createIndex({ usuario_id: 1, ativo: 1 });
db.transacoes.createIndex({ usuario_id: 1, categoria: 1 });

// ─── COLEÇÃO: metas ──────────────────────────────────────────────
db.createCollection('metas', {
  validator: {
    $jsonSchema: {
      bsonType: 'object',
      required: ['usuario_id', 'titulo', 'valor_alvo'],
      properties: {
        usuario_id:  { bsonType: 'string' },
        titulo:      { bsonType: 'string', minLength: 1 },
        valor_alvo:  { bsonType: 'double', minimum: 0.01 },
        valor_atual: { bsonType: 'double' },
        status:      { bsonType: 'string', enum: ['ativa', 'concluida', 'cancelada'] }
      }
    }
  }
});

db.metas.createIndex({ usuario_id: 1, status: 1 });
db.metas.createIndex({ usuario_id: 1, data_criacao: -1 });

print('✅ MongoDB inicializado com sucesso — coleções e índices criados.');
