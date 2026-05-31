"""
TESTES DE INTEGRAÇÃO DISTRIBUÍDA
Testa comunicação entre microsserviços via API Gateway
Execute: pytest tests/test_distributed.py -v
"""
import pytest, requests, time

BASE  = 'http://localhost:8000'
TOKEN = None

def auth_header():
    return {'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'}

class TestHealth:
    def test_gateway_ok(self):
        r = requests.get(f'{BASE}/health', timeout=5)
        assert r.status_code == 200
        assert 'services' in r.json()

class TestAuth:
    def test_registro(self):
        global TOKEN
        ts = int(time.time())
        r = requests.post(f'{BASE}/api/auth/registro', json={
            'nome': f'Teste {ts}', 'email': f'teste_{ts}@test.com',
            'senha': 'senha123', 'modo_interface': 'simples'
        }, timeout=10)
        assert r.status_code == 201
        TOKEN = r.json()['token']

    def test_login_valido(self):
        ts = int(time.time())
        email = f'login_{ts}@test.com'
        requests.post(f'{BASE}/api/auth/registro', json={'nome':'T','email':email,'senha':'abc123'}, timeout=10)
        r = requests.post(f'{BASE}/api/auth/login', json={'email':email,'senha':'abc123'}, timeout=10)
        assert r.status_code == 200
        assert 'token' in r.json()

    def test_login_invalido(self):
        r = requests.post(f'{BASE}/api/auth/login', json={'email':'nao@existe.com','senha':'errada'}, timeout=10)
        assert r.status_code == 401

    def test_sem_token_bloqueado(self):
        r = requests.get(f'{BASE}/api/transacoes', timeout=5)
        assert r.status_code == 401

class TestTransacoes:
    def test_criar_receita(self):
        if not TOKEN: pytest.skip()
        r = requests.post(f'{BASE}/api/transacoes', json={
            'tipo':'receita','valor':1500,'descricao':'Salário','categoria':'Salário','data':'2026-03-01'
        }, headers=auth_header(), timeout=10)
        assert r.status_code == 201

    def test_criar_despesa(self):
        if not TOKEN: pytest.skip()
        r = requests.post(f'{BASE}/api/transacoes', json={
            'tipo':'despesa','valor':300,'descricao':'Mercado','categoria':'Alimentação','data':'2026-03-02'
        }, headers=auth_header(), timeout=10)
        assert r.status_code == 201

    def test_listar(self):
        if not TOKEN: pytest.skip()
        r = requests.get(f'{BASE}/api/transacoes', headers=auth_header(), timeout=10)
        assert r.status_code == 200
        assert 'transacoes' in r.json()

    def test_saldo(self):
        if not TOKEN: pytest.skip()
        r = requests.get(f'{BASE}/api/transacoes/saldo', headers=auth_header(), timeout=10)
        assert r.status_code == 200
        d = r.json()
        assert 'saldo' in d and 'receitas' in d and 'despesas' in d

    def test_valor_negativo_rejeitado(self):
        if not TOKEN: pytest.skip()
        r = requests.post(f'{BASE}/api/transacoes', json={
            'tipo':'despesa','valor':-100,'descricao':'Invalido'
        }, headers=auth_header(), timeout=10)
        assert r.status_code == 400

class TestMetas:
    def test_criar_meta(self):
        if not TOKEN: pytest.skip()
        r = requests.post(f'{BASE}/api/metas', json={
            'titulo':'Viagem','valor_alvo':5000,'data_inicio':'2026-03-01'
        }, headers=auth_header(), timeout=10)
        assert r.status_code == 201

    def test_listar_metas(self):
        if not TOKEN: pytest.skip()
        r = requests.get(f'{BASE}/api/metas', headers=auth_header(), timeout=10)
        assert r.status_code == 200

class TestRelatorios:
    def test_resumo(self):
        if not TOKEN: pytest.skip()
        r = requests.get(f'{BASE}/api/relatorios/resumo', headers=auth_header(), timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert 'receitas' in d and 'despesas' in d

class TestNotificacoes:
    def test_listar(self):
        if not TOKEN: pytest.skip()
        r = requests.get(f'{BASE}/api/notificacoes', headers=auth_header(), timeout=10)
        assert r.status_code == 200
        assert 'notificacoes' in r.json()

class TestRateLimit:
    def test_rate_limit_ativo(self):
        codes = []
        for _ in range(25):
            r = requests.post(f'{BASE}/api/auth/login',json={'email':'spam@t.com','senha':'x'},timeout=5)
            codes.append(r.status_code)
        assert 429 in codes
