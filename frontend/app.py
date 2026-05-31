"""
FRONTEND SERVICE - Interface Web do Simplifica Finanças
Responsabilidade: Servir templates HTML, fazer proxy de chamadas para o API Gateway
Porta: 3000
"""

from flask import Flask, render_template, request, redirect, url_for, session, jsonify, Response, flash
from flask_cors import CORS
import requests
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [FRONTEND] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv('JWT_SECRET', os.urandom(32))
CORS(app)

GATEWAY_URL = os.getenv('API_GATEWAY_URL', 'http://api-gateway:8000')
PORT        = int(os.getenv('FRONTEND_PORT', 3000))
TIMEOUT     = (5, 30)

# ─── HELPERS ──────────────────────────────────────────────────
def api(method: str, path: str, **kwargs):
    """Faz chamada ao API Gateway com o token do usuário logado"""
    token   = session.get('token', '')
    headers = kwargs.pop('headers', {})
    if token:
        headers['Authorization'] = f'Bearer {token}'
    headers['Content-Type'] = 'application/json'
    try:
        return requests.request(
            method,
            f"{GATEWAY_URL}{path}",
            headers=headers,
            timeout=TIMEOUT,
            **kwargs
        )
    except requests.exceptions.ConnectionError:
        logger.error(f"❌ Gateway indisponível: {path}")
        return None
    except requests.exceptions.Timeout:
        logger.error(f"⏱ Timeout: {path}")
        return None

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('token'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def get_modo():
    return session.get('modo_interface', 'simples')

# ─── ROTAS PÚBLICAS ───────────────────────────────────────────

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'service': 'frontend'})

@app.route('/')
def index():
    if session.get('token'):
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if session.get('token'):
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        senha = request.form.get('senha', '')
        r     = api('POST', '/api/auth/login', json={'email': email, 'senha': senha})

        if r and r.status_code == 200:
            data = r.json()
            session['token']          = data['token']
            session['usuario']        = data['usuario']
            session['modo_interface'] = data['usuario'].get('modo_interface', 'simples')
            session['user_id']        = data['usuario']['id']
            session['user_nome']      = data['usuario']['nome']
            session['user_modo']      = data['usuario'].get('modo_interface', 'simples')
            logger.info(f"✅ Login frontend: {email}")
            # ✅ FIX: flash de login bem-sucedido
            flash(f"Bem-vindo de volta, {data['usuario']['nome']}! 👋", 'success')
            return redirect(url_for('dashboard'))
        else:
            erro_msg = 'Email ou senha inválidos'
            if r:
                try:
                    erro_msg = r.json().get('error', erro_msg)
                except Exception:
                    pass
            flash(erro_msg, 'danger')

    return render_template('login.html')

@app.route('/registro', methods=['GET', 'POST'])
def registro():
    if session.get('token'):
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        # ✅ FIX: campo no template é 'modo', não 'modo_interface'
        payload = {
            'nome':           request.form.get('nome', '').strip(),
            'email':          request.form.get('email', '').strip(),
            'senha':          request.form.get('senha', ''),
            'modo_interface': request.form.get('modo', 'simples')  # ← corrigido
        }
        r = api('POST', '/api/auth/registro', json=payload)

        if r and r.status_code == 201:
            data = r.json()
            session['token']          = data['token']
            session['usuario']        = data['usuario']
            session['modo_interface'] = data['usuario'].get('modo_interface', 'simples')
            session['user_id']        = data['usuario']['id']
            session['user_nome']      = data['usuario']['nome']
            session['user_modo']      = data['usuario'].get('modo_interface', 'simples')
            # ✅ FIX: flash de conta criada com sucesso
            flash(f"Conta criada com sucesso! Bem-vindo(a), {data['usuario']['nome']}! 🎉", 'success')
            return redirect(url_for('dashboard'))
        else:
            erro_msg = 'Erro no registro'
            if r:
                try:
                    erro_msg = r.json().get('error', erro_msg)
                except Exception:
                    pass
            else:
                erro_msg = 'Erro ao conectar ao servidor'
            flash(erro_msg, 'danger')

    return render_template('registro.html')

@app.route('/logout')
def logout():
    token = session.get('token')
    if token:
        api('POST', '/api/auth/logout')
    session.clear()
    flash('Você saiu da sua conta.', 'info')
    return redirect(url_for('login'))

# ─── ROTAS PROTEGIDAS ─────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    modo    = get_modo()
    saldo_r = api('GET', '/api/transacoes/saldo')
    notif_r = api('GET', '/api/notificacoes')
    transac = api('GET', '/api/transacoes?limit=5')
    metas_r = api('GET', '/api/metas?status=ativa')

    saldo        = saldo_r.json()   if saldo_r  and saldo_r.ok  else {'receitas': 0, 'despesas': 0, 'saldo': 0}
    notificacoes = notif_r.json()   if notif_r  and notif_r.ok  else {'notificacoes': []}
    transacoes   = transac.json()   if transac  and transac.ok  else {'transacoes': []}
    metas        = metas_r.json()   if metas_r  and metas_r.ok  else []

    tpl = 'dashboard_simples.html' if modo == 'simples' else 'dashboard_avancado.html'
    return render_template(
        tpl,
        usuario      = session.get('usuario', {}),
        saldo        = saldo,
        notificacoes = notificacoes.get('notificacoes', []),
        transacoes   = transacoes.get('transacoes', []),
        metas        = metas if isinstance(metas, list) else [],
        modo         = modo
    )

@app.route('/transacoes', methods=['GET', 'POST'])
@login_required
def adicionar_transacao():
    modo = get_modo()

    if request.method == 'POST':
        payload = {
            'tipo':      request.form.get('tipo'),
            'valor':     request.form.get('valor'),
            'descricao': request.form.get('descricao', '').strip(),
            'categoria': request.form.get('categoria', 'Outros'),
            'data':      request.form.get('data')
        }
        r = api('POST', '/api/transacoes', json=payload)
        if r and r.status_code == 201:
            flash('Transação adicionada com sucesso! ✅', 'success')
            return redirect(url_for('dashboard'))
        erro_msg = (r.json().get('error') if r else 'Erro ao conectar') or 'Erro ao salvar transação'
        flash(erro_msg, 'danger')

    cat_r      = api('GET', '/api/transacoes/categorias')
    categorias = cat_r.json() if cat_r and cat_r.ok else {'despesa': [], 'receita': []}
    tpl        = 'adicionar_transacao_simples.html' if modo == 'simples' else 'adicionar_transacao_avancado.html'
    return render_template(tpl, categorias=categorias, modo=modo, usuario=session.get('usuario', {}))

# ─── ROTAS DE METAS ───────────────────────────────────────────

@app.route('/metas', methods=['GET', 'POST'])
@login_required
def metas():
    modo = get_modo()

    # ✅ FIX: aceita POST para criar meta
    if request.method == 'POST':
        payload = {
            'titulo':      request.form.get('titulo', '').strip(),
            'valor_alvo':  request.form.get('valor_alvo'),
            'data_inicio': request.form.get('data_inicio'),
            'data_limite': request.form.get('data_limite') or None,
            'categoria':   request.form.get('categoria', 'Outros'),
            'cor':         request.form.get('cor', '#6366F1'),
            'descricao':   request.form.get('descricao', '')
        }
        r = api('POST', '/api/metas', json=payload)
        if r and r.status_code == 201:
            flash('Meta criada com sucesso! 🎯', 'success')
        else:
            erro_msg = (r.json().get('error') if r else 'Erro ao conectar') or 'Erro ao criar meta'
            flash(erro_msg, 'danger')
        return redirect(url_for('metas'))

    metas_r = api('GET', '/api/metas?status=todas')
    lista   = metas_r.json() if metas_r and metas_r.ok else []
    tpl     = 'metas_simples.html' if modo == 'simples' else 'metas_avancado.html'
    return render_template(tpl, metas=lista if isinstance(lista, list) else [], modo=modo, usuario=session.get('usuario', {}))

@app.route('/metas/<meta_id>/deposito', methods=['POST'])
@login_required
def deposito_meta(meta_id):
    """✅ FIX: rota Flask para depósito em meta (antes ia direto para /api/ sem autenticação de sessão)"""
    valor = request.form.get('valor')
    r = api('POST', f'/api/metas/{meta_id}/deposito', json={'valor': float(valor)})
    if r and r.ok:
        flash('Depósito realizado com sucesso! 💰', 'success')
    else:
        erro_msg = (r.json().get('error') if r else 'Erro ao conectar') or 'Erro no depósito'
        flash(erro_msg, 'danger')
    return redirect(url_for('metas'))

@app.route('/metas/<meta_id>/cancelar', methods=['POST'])
@login_required
def cancelar_meta(meta_id):
    """✅ FIX: rota Flask para cancelar meta"""
    r = api('POST', f'/api/metas/{meta_id}/cancelar')
    if r and r.ok:
        flash('Meta cancelada.', 'warning')
    else:
        flash('Erro ao cancelar meta.', 'danger')
    return redirect(url_for('metas'))

@app.route('/metas/<meta_id>/excluir', methods=['POST'])
@login_required
def excluir_meta(meta_id):
    """✅ FIX: rota Flask para excluir meta"""
    r = api('DELETE', f'/api/metas/{meta_id}')
    if r and r.ok:
        flash('Meta excluída com sucesso.', 'success')
    else:
        flash('Erro ao excluir meta.', 'danger')
    return redirect(url_for('metas'))

@app.route('/metas/<meta_id>/editar', methods=['POST'])
@login_required
def editar_meta(meta_id):
    """✅ FIX: rota Flask para editar meta"""
    payload = {k: v for k, v in {
        'titulo':      request.form.get('titulo', '').strip(),
        'valor_alvo':  request.form.get('valor_alvo'),
        'data_limite': request.form.get('data_limite') or None,
        'cor':         request.form.get('cor'),
    }.items() if v is not None}
    r = api('PUT', f'/api/metas/{meta_id}', json=payload)
    if r and r.ok:
        flash('Meta atualizada com sucesso! ✅', 'success')
    else:
        flash('Erro ao atualizar meta.', 'danger')
    return redirect(url_for('metas'))

# ─── RELATÓRIOS ───────────────────────────────────────────────

@app.route('/relatorios')
@login_required
def relatorios():
    mes      = request.args.get('mes', '')
    path     = f'/api/relatorios/resumo{"?mes=" + mes if mes else ""}'
    resumo_r = api('GET', path)
    resumo   = resumo_r.json() if resumo_r and resumo_r.ok else {}
    return render_template('relatorios.html', resumo=resumo, mes=mes, modo=get_modo(), usuario=session.get('usuario', {}))

@app.route('/relatorios/excel')
@login_required
def exportar_excel():
    mes = request.args.get('mes', '')
    r   = api('GET', f'/api/relatorios/exportar/excel{"?mes=" + mes if mes else ""}')
    if r and r.ok:
        return Response(
            r.content,
            status=200,
            headers={
                'Content-Type':        r.headers.get('Content-Type', 'application/octet-stream'),
                'Content-Disposition': r.headers.get('Content-Disposition', 'attachment; filename=relatorio.xlsx')
            }
        )
    return redirect(url_for('relatorios'))

@app.route('/relatorios/pdf')
@login_required
def exportar_pdf():
    mes = request.args.get('mes', '')
    r   = api('GET', f'/api/relatorios/exportar/pdf{"?mes=" + mes if mes else ""}')
    if r and r.ok:
        return Response(
            r.content,
            status=200,
            headers={
                'Content-Type':        r.headers.get('Content-Type', 'application/pdf'),
                'Content-Disposition': r.headers.get('Content-Disposition', 'attachment; filename=relatorio.pdf')
            }
        )
    return redirect(url_for('relatorios'))

# ─── CONFIGURAÇÕES ────────────────────────────────────────────

@app.route('/configuracoes', methods=['GET', 'POST'])
@login_required
def configuracoes():
    if request.method == 'POST':
        payload = {
            'nome':           request.form.get('nome', '').strip(),
            'modo_interface': request.form.get('modo_interface', 'simples')
        }
        r = api('PUT', '/api/auth/perfil', json=payload)
        if r and r.ok:
            session['modo_interface']            = payload['modo_interface']
            session['usuario']['nome']           = payload['nome'] or session['usuario'].get('nome')
            session['usuario']['modo_interface'] = payload['modo_interface']
            session['user_nome']                 = payload['nome'] or session.get('user_nome', '')
            session['user_modo']                 = payload['modo_interface']
            flash('Configurações salvas com sucesso! ✅', 'success')
        else:
            flash('Erro ao salvar configurações.', 'danger')
        return redirect(url_for('configuracoes'))

    perfil_r = api('GET', '/api/auth/perfil')
    perfil   = perfil_r.json() if perfil_r and perfil_r.ok else session.get('usuario', {})
    return render_template('configuracoes.html', perfil=perfil, modo=get_modo(), usuario=session.get('usuario', {}))

# ─── TRANSAÇÕES - EXCLUIR ─────────────────────────────────────

@app.route('/transacoes/<id>/excluir', methods=['POST'])
@login_required
def excluir_transacao(id):
    r = api('DELETE', f'/api/transacoes/{id}')
    if r and r.ok:
        flash('Transação excluída com sucesso.', 'success')
    else:
        flash('Erro ao excluir transação.', 'danger')
    return redirect(url_for('dashboard'))

# ─── INICIALIZAÇÃO ────────────────────────────────────────────
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=PORT, debug=False)
