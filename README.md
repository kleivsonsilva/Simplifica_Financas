# 💰 Simplifica Finanças — Sistema Distribuído

> **Projeto A3 — Sistemas Distribuídos e Mobile**
> Transformação de aplicação monolítica Flask em arquitetura de microsserviços com comunicação distribuída.

---

## 🏗️ Arquitetura do Sistema

```
┌────────────────────────────────────────────────────────────┐
│                      CLIENTE (Browser)                      │
└──────────────────────────┬─────────────────────────────────┘
                           │ HTTP
┌──────────────────────────▼─────────────────────────────────┐
│              FRONTEND SERVICE  :3000                        │
│              Flask + Jinja2 (Modo Simples / Avançado)       │
└──────────────────────────┬─────────────────────────────────┘
                           │ HTTP/REST
┌──────────────────────────▼─────────────────────────────────┐
│               API GATEWAY  :8000                            │
│        Rate Limiting · JWT Verify · Proxy · Logs           │
│                   Redis (Rate Limit)                        │
└──┬──────────┬────────────┬────────────┬────────────────────┘
   │          │            │            │
   ▼          ▼            ▼            ▼
┌──────┐ ┌────────────┐ ┌────────┐ ┌────────┐ ┌──────────────┐
│ AUTH │ │TRANSACTION │ │  GOAL  │ │REPORT  │ │NOTIFICATION  │
│:5001 │ │  :5002     │ │ :5003  │ │ :5004  │ │   :5005      │
│      │ │            │ │        │ │        │ │              │
│Postgr│ │  MongoDB   │ │MongoDB │ │MongoDB │ │   RabbitMQ   │
│Redis │ │Redis·Rabbit│ │ Rabbit │ │ Redis  │ │    Redis     │
└──────┘ └────────────┘ └────────┘ └────────┘ └──────────────┘
```

### Fluxo de Comunicação Assíncrona (RabbitMQ)
```
Transaction Service ──► [fila: transacao_criada] ──► Notification Service
Transaction Service ──► [fila: transacao_excluida] ─► Notification Service
Goal Service        ──◄ [fila: transacao_criada]   (consome eventos)
```

---

## 📦 Microsserviços

| Serviço | Porta | Banco | Função |
|---------|-------|-------|--------|
| **Auth Service** | 5001 | PostgreSQL + Redis | Registro, Login, JWT, Sessões |
| **Transaction Service** | 5002 | MongoDB + Redis | CRUD Transações, Saldo, Categorias |
| **Goal Service** | 5003 | MongoDB | Metas Financeiras, Progresso, Gamificação |
| **Report Service** | 5004 | MongoDB + Redis | Relatórios, Exportação Excel/PDF |
| **Notification Service** | 5005 | RabbitMQ + Redis | Alertas assíncronos via mensageria |
| **API Gateway** | 8000 | Redis | Roteamento, Rate Limiting, Autenticação |
| **Frontend** | 3000 | — | Interface Web (Simples/Avançado) |

---

## 🔧 Tecnologias Utilizadas

| Camada | Tecnologia | Justificativa |
|--------|-----------|---------------|
| **Linguagem** | Python 3.11 | Ecossistema rico, Flask leve |
| **Framework API** | Flask + Flask-CORS | Simples, eficiente para microsserviços |
| **Auth DB** | PostgreSQL 16 | ACID, confiável para dados de usuário |
| **NoSQL / Transações** | MongoDB 7.0 | Documentos flexíveis, sharding nativo |
| **Cache / Sessões** | Redis 7.2 | In-memory, TTL, blacklist de tokens |
| **Mensageria** | RabbitMQ 3.13 | Comunicação assíncrona entre serviços |
| **Autenticação** | JWT (PyJWT) | Stateless, escalável |
| **Containerização** | Docker + Compose | Isolamento, portabilidade, orquestração |
| **Senhas** | Werkzeug Scrypt | Criptografia segura |
| **Exportação** | openpyxl + fpdf | Excel e PDF profissionais |

---

## 🚀 Como Executar

### Pré-requisitos
- Docker Desktop instalado
- Docker Compose v2+
- 4GB RAM disponível

### 1. Clonar e configurar
```bash
git clone https://github.com/seu-usuario/simplifica-distribuido
cd simplifica-distribuido
cp .env.example .env
```

### 2. Subir todos os serviços
```bash
docker-compose up --build -d
```

### 3. Verificar status
```bash
docker-compose ps
curl http://localhost:8000/health
```

### 4. Acessar o sistema
| Interface | URL |
|-----------|-----|
| 🌐 Frontend Web | http://localhost:3000 |
| 🔌 API Gateway | http://localhost:8000 |
| 📊 RabbitMQ Admin | http://localhost:15672 (admin/simplifica2025) |

### 5. Executar testes
```bash
pip install -r tests/requirements_test.txt
pytest tests/test_services.py -v --tb=short
```

### 6. Parar o sistema
```bash
docker-compose down
# Para remover volumes (banco de dados):
docker-compose down -v
```

---

## 🔐 Segurança

- **JWT HS256** com expiração de 24h
- **Blacklist de tokens** no Redis (logout seguro)
- **Rate Limiting** por IP no API Gateway (60 req/min padrão)
- **Senhas criptografadas** com Scrypt (salt aleatório)
- **Isolamento de rede** via Docker Network bridge
- **Variáveis sensíveis** via `.env` (nunca commitadas)

---

## 📊 Conceitos de Sistemas Distribuídos Aplicados

| Conceito | Implementação |
|----------|--------------|
| **Microsserviços** | 5 serviços independentes + gateway + frontend |
| **Comunicação REST** | API Gateway roteia HTTP/JSON entre serviços |
| **Mensageria Assíncrona** | RabbitMQ com filas duráveis |
| **Banco Distribuído** | MongoDB (documentos) + PostgreSQL (relacional) |
| **Cache Distribuído** | Redis com TTL por serviço (DB 0–4) |
| **Service Discovery** | Docker DNS interno (nomes de containers) |
| **Tolerância a Falhas** | Fallback gracioso quando serviço cai |
| **Escalabilidade** | Qualquer serviço pode ter múltiplas réplicas |
| **Persistência** | Volumes Docker para MongoDB, PostgreSQL, Redis |
| **Monitoramento** | `/health` em cada serviço + Gateway aggregado |

---

## 🧪 Testes Automatizados

```
tests/test_services.py
├── TestHealth          → health check do gateway
├── TestAuth            → registro, login, JWT, perfil
├── TestTransactions    → CRUD, saldo, filtros, validações
├── TestGoals           → metas, depósito, progresso
├── TestReports         → resumo, exportações
└── TestNotifications   → alertas e notificações
```

---

## 👥 Equipe
Projeto A3 — Disciplina: Sistemas Distribuídos e Mobile  
Universidade: FPB — 2026.1

---

## 💡 Impacto Social

O **Simplifica Finanças** atende dois públicos com necessidades distintas:

- **Modo Simples**: Idosos, aposentados e iniciantes em tecnologia — interface acessível com fontes grandes, botões coloridos e sem jargões técnicos
- **Modo Avançado**: Empreendedores e profissionais — gráficos, relatórios detalhados, exportação Excel/PDF, análise por categoria

A arquitetura distribuída garante que o sistema possa escalar conforme a demanda, mantendo disponibilidade alta mesmo com picos de acesso.
