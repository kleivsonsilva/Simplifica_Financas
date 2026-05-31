#!/bin/bash
set -e

echo "🚀 Iniciando Simplifica Finanças — Sistema Distribuído"
echo "========================================================"
echo ""

# Verifica se .env existe
if [ ! -f .env ]; then
    echo "⚠️  Arquivo .env não encontrado. Copiando .env.example..."
    cp .env.example .env
    echo ""
    echo "❗ ATENÇÃO: Edite o arquivo .env e defina um JWT_SECRET seguro antes de continuar."
    echo "   Gere um com: python3 -c \"import secrets; print(secrets.token_hex(32))\""
    echo ""
    read -p "Pressione ENTER para continuar com os valores padrão (NÃO recomendado para produção)..."
fi

# Sobe os containers
docker-compose up --build -d

echo ""
echo "⏳ Aguardando serviços inicializarem (45s)..."
sleep 45

echo ""
echo "📊 Status dos containers:"
docker-compose ps

echo ""
echo "🔍 Verificando health do gateway:"
curl -s http://localhost:8000/health | python3 -m json.tool 2>/dev/null || echo "Gateway ainda inicializando..."

echo ""
echo "✅ Sistema disponível em:"
echo "   🌐 Frontend:  http://localhost:3000"
echo "   🔌 Gateway:   http://localhost:8000"
echo "   🐇 RabbitMQ:  http://localhost:15672  (user: admin / pass: simplifica2025)"
echo ""
echo "📋 Para rodar os testes:"
echo "   pip install pytest requests"
echo "   pytest tests/ -v"
