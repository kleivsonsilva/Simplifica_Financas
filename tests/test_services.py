"""
TESTES AUTOMATIZADOS - Sistema Distribuído Simplifica Finanças
Testa todos os microsserviços via API Gateway
Execute: pytest tests/test_services.py -v
"""

import pytest
import requests
import time

BASE_URL = "http://localhost:8000"

# ─── FIXTURES ─────────────────────────────────────────────────
@pytest.fixture(scope='session')
def token():
    email = f"teste_{int(time.time())}@simplifica.com"
    r     = requests.post(f"{BASE_URL}/api/auth/registro", json={
        "nome":           "Usuário Teste",
        "email":          email,
        "senha":          "senha123",
        "modo_interface": "avancado"
    })
    assert r.status_code == 201, f"Falha no registro: {r.text}"
    return r.json()['token']

@pytest.fixture(scope='session')
def headers(token):
    return {"Authorization": f"Bearer {token}"}

# ─── TESTES: HEALTH ───────────────────────────────────────────
class TestHealth:
    def test_gateway_health(self):
        r    = requests.get(f"{BASE_URL}/health")
        assert r.status_code == 200
        data = r.json()
        assert 'gateway'  in data
        assert 'services' in data

# ─── TESTES: AUTH SERVICE ─────────────────────────────────────
class TestAuth:
    def test_registro_sucesso(self):
        r = requests.post(f"{BASE_URL}/api/auth/registro", json={
            "nome":  "Novo Teste",
            "email": f"novo_{int(time.time())}@test.com",
            "senha": "senha123"
        })
        assert r.status_code == 201
        assert 'token' in r.json()

    def test_registro_email_duplicado(self):
        email = f"dup_{int(time.time())}@test.com"
        requests.post(f"{BASE_URL}/api/auth/registro", json={"nome": "D1", "email": email, "senha": "senha123"})
        r2 = requests.post(f"{BASE_URL}/api/auth/registro", json={"nome": "D2", "email": email, "senha": "senha123"})
        assert r2.status_code == 409

    def test_login_invalido(self):
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": "naoexiste@test.com", "senha": "errada"})
        assert r.status_code == 401

    def test_perfil(self, headers):
        r    = requests.get(f"{BASE_URL}/api/auth/perfil", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert 'email' in data
        assert 'nome'  in data

    def test_sem_token_retorna_401(self):
        r = requests.get(f"{BASE_URL}/api/auth/perfil")
        assert r.status_code == 401

# ─── TESTES: TRANSACTION SERVICE ──────────────────────────────
class TestTransactions:
    def test_criar_despesa(self, headers):
        r = requests.post(f"{BASE_URL}/api/transacoes", json={
            "tipo": "despesa", "valor": 150.00, "descricao": "Supermercado teste",
            "categoria": "Alimentação", "data": "2026-03-01"
        }, headers=headers)
        assert r.status_code == 201
        assert 'id' in r.json()

    def test_criar_receita(self, headers):
        r = requests.post(f"{BASE_URL}/api/transacoes", json={
            "tipo": "receita", "valor": 3000.00, "descricao": "Salário teste",
            "categoria": "Salário", "data": "2026-03-01"
        }, headers=headers)
        assert r.status_code == 201

    def test_listar_transacoes(self, headers):
        r    = requests.get(f"{BASE_URL}/api/transacoes", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert 'transacoes' in data
        assert 'total'      in data

    def test_saldo(self, headers):
        r    = requests.get(f"{BASE_URL}/api/transacoes/saldo", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert 'saldo'    in data
        assert 'receitas' in data
        assert 'despesas' in data

    def test_filtro_tipo(self, headers):
        r = requests.get(f"{BASE_URL}/api/transacoes?tipo=despesa", headers=headers)
        assert r.status_code == 200

    def test_por_categoria(self, headers):
        r = requests.get(f"{BASE_URL}/api/transacoes/por-categoria", headers=headers)
        assert r.status_code == 200

    def test_categorias_requer_token(self):
        """Endpoint /categorias deve exigir autenticação"""
        r = requests.get(f"{BASE_URL}/api/transacoes/categorias")
        assert r.status_code == 401

    def test_tipo_invalido(self, headers):
        r = requests.post(f"{BASE_URL}/api/transacoes", json={
            "tipo": "invalido", "valor": 100, "descricao": "Teste", "data": "2026-03-01"
        }, headers=headers)
        assert r.status_code == 400

    def test_valor_negativo(self, headers):
        r = requests.post(f"{BASE_URL}/api/transacoes", json={
            "tipo": "despesa", "valor": -100, "descricao": "Teste", "data": "2026-03-01"
        }, headers=headers)
        assert r.status_code == 400

# ─── TESTES: GOAL SERVICE ─────────────────────────────────────
class TestGoals:
    def test_criar_meta(self, headers):
        r = requests.post(f"{BASE_URL}/api/metas", json={
            "titulo": "Meta Teste", "valor_alvo": 1000.00,
            "data_inicio": "2026-03-01", "data_limite": "2026-12-31", "categoria": "Viagem"
        }, headers=headers)
        assert r.status_code == 201
        return r.json()['id']

    def test_listar_metas(self, headers):
        r = requests.get(f"{BASE_URL}/api/metas", headers=headers)
        assert r.status_code == 200

    def test_valor_alvo_invalido(self, headers):
        r = requests.post(f"{BASE_URL}/api/metas", json={
            "titulo": "Meta Inválida", "valor_alvo": -500, "data_inicio": "2026-03-01"
        }, headers=headers)
        assert r.status_code == 400

# ─── TESTES: REPORT SERVICE ───────────────────────────────────
class TestReports:
    def test_resumo(self, headers):
        r    = requests.get(f"{BASE_URL}/api/relatorios/resumo", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert 'receitas' in data
        assert 'despesas' in data
        assert 'saldo'    in data

# ─── TESTES: NOTIFICATION SERVICE ────────────────────────────
class TestNotifications:
    def test_listar_notificacoes(self, headers):
        r = requests.get(f"{BASE_URL}/api/notificacoes", headers=headers)
        assert r.status_code == 200
        assert 'notificacoes' in r.json()

# ─── TESTES: BLACKLIST (logout invalida token) ─────────────────
class TestBlacklist:
    def test_token_revogado_apos_logout(self):
        """Após logout, o token não deve mais funcionar em nenhum serviço"""
        ts = int(time.time())
        r  = requests.post(f"{BASE_URL}/api/auth/registro", json={
            "nome": "Temp", "email": f"temp_{ts}@test.com", "senha": "senha123"
        })
        assert r.status_code == 201
        tok     = r.json()['token']
        h       = {"Authorization": f"Bearer {tok}"}

        # Verifica acesso antes do logout
        r_antes = requests.get(f"{BASE_URL}/api/transacoes", headers=h)
        assert r_antes.status_code == 200

        # Faz logout
        requests.post(f"{BASE_URL}/api/auth/logout", headers=h)

        # Verifica que token foi revogado
        r_depois = requests.get(f"{BASE_URL}/api/transacoes", headers=h)
        assert r_depois.status_code == 401

if __name__ == '__main__':
    pytest.main([__file__, '-v', '--tb=short'])
