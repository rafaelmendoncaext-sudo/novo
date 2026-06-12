"""
app.py — Interface web (Flask) para testar o fluxo de Autorizações da Carol
===========================================================================
Sobe um chat simples no navegador para conversar com o grafo de
`07_autorizacoes.py` (Gemini 2.5 Flash no Vertex AI). Cada navegador recebe um
`sessao` (cookie), de modo que o estado da conversa (etapa) é isolado por usuário.

Como rodar:
    .venv/bin/python app.py
    # abra http://localhost:5000
"""

import importlib
import os
import uuid

from flask import Flask, request, jsonify, make_response, abort
from twilio.twiml.messaging_response import MessagingResponse
from twilio.request_validator import RequestValidator

# O módulo do agente começa com dígito → carregado via importlib.
autorizacoes = importlib.import_module("07_autorizacoes")
responder = autorizacoes.responder

app = Flask(__name__)

# Limite seguro por mensagem de WhatsApp (Twilio quebra acima de ~1600 chars).
LIMITE_WHATSAPP = 1500
# Se definido, valida a assinatura do Twilio (recomendado em produção).
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")

PAGINA = """<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Carol — Autorizações (Unimed Nacional)</title>
<style>
  :root { --verde:#009639; }
  * { box-sizing: border-box; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
  body { margin:0; background:#f0f2f5; }
  .wrap { max-width:680px; margin:0 auto; height:100vh; display:flex; flex-direction:column; }
  header { background:var(--verde); color:#fff; padding:14px 18px; font-weight:600; }
  header small { display:block; font-weight:400; opacity:.85; }
  #chat { flex:1; overflow-y:auto; padding:16px; }
  .msg { max-width:80%; padding:10px 14px; border-radius:14px; margin:6px 0; white-space:pre-wrap; line-height:1.35; }
  .bot { background:#fff; border:1px solid #e3e6ea; }
  .user { background:var(--verde); color:#fff; margin-left:auto; }
  .meta { font-size:11px; opacity:.6; margin:2px 4px; }
  form { display:flex; gap:8px; padding:12px; background:#fff; border-top:1px solid #e3e6ea; }
  input { flex:1; padding:12px; border:1px solid #ccd0d5; border-radius:10px; font-size:15px; }
  button { padding:0 18px; border:0; border-radius:10px; background:var(--verde); color:#fff; font-weight:600; cursor:pointer; }
  .tools { padding:8px 12px; background:#fafafa; border-top:1px solid #eee; font-size:12px; }
  .tools button { background:#e7f5ec; color:var(--verde); padding:5px 10px; border-radius:8px; margin-right:6px; }
</style></head>
<body><div class="wrap">
  <header>Carol — Autorizações <small>Unimed Nacional · Gemini 2.5 Flash (Vertex AI)</small></header>
  <div id="chat"></div>
  <div class="tools">
    Atalhos:
    <button onclick="enviar('Olá, quero ver minhas autorizações')">Tenho autorizações</button>
    <button onclick="enviar('Não tenho autorização, quero solicitar')">Sem autorização</button>
    <button onclick="reiniciar()">Reiniciar sessão</button>
  </div>
  <form onsubmit="return mandar()">
    <input id="txt" placeholder="Digite sua mensagem..." autocomplete="off" autofocus>
    <button type="submit">Enviar</button>
  </form>
</div>
<script>
const chat = document.getElementById('chat');
function add(texto, quem, etapa){
  const m = document.createElement('div');
  m.className = 'msg ' + (quem==='user'?'user':'bot');
  m.textContent = texto;
  chat.appendChild(m);
  if (etapa){ const e=document.createElement('div'); e.className='meta'; e.textContent='etapa: '+etapa; chat.appendChild(e); }
  chat.scrollTop = chat.scrollHeight;
}
async function enviar(texto){
  add(texto,'user');
  const r = await fetch('/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mensagem:texto})});
  const j = await r.json();
  add(j.resposta,'bot', j.etapa);
}
function mandar(){ const t=document.getElementById('txt'); if(t.value.trim()){ enviar(t.value.trim()); t.value=''; } return false; }
async function reiniciar(){ await fetch('/reset',{method:'POST'}); chat.innerHTML=''; add('Sessão reiniciada. Diga "oi" para começar.','bot'); }
add('Olá! Sou a Carol 💚 Posso ajudar com suas autorizações. Diga o que precisa ou use um atalho abaixo.','bot');
</script>
</body></html>"""


def _sessao():
    return request.cookies.get("sessao") or uuid.uuid4().hex


@app.route("/")
def index():
    resp = make_response(PAGINA)
    if not request.cookies.get("sessao"):
        resp.set_cookie("sessao", uuid.uuid4().hex, samesite="Lax")
    return resp


@app.route("/chat", methods=["POST"])
def chat():
    dados = request.get_json(silent=True) or {}
    mensagem = (dados.get("mensagem") or "").strip()
    sessao = _sessao()
    if not mensagem:
        return jsonify({"resposta": "Não recebi nenhuma mensagem. Pode escrever?", "etapa": ""})
    try:
        estado = responder(mensagem, sessao=sessao)
        resposta = estado.get("resposta") or "Desculpe, não consegui processar agora."
        etapa = estado.get("etapa", "")
    except Exception as exc:
        app.logger.exception("Falha no fluxo de autorizações")
        resposta = f"Instabilidade técnica ({type(exc).__name__}). Verifique a credencial/Vertex e tente novamente. 🙏"
        etapa = "erro"
    out = jsonify({"resposta": resposta, "etapa": etapa})
    resp = make_response(out)
    resp.set_cookie("sessao", sessao, samesite="Lax")
    return resp


@app.route("/reset", methods=["POST"])
def reset():
    """Gera uma nova sessão (novo thread_id) para começar a conversa do zero."""
    out = jsonify({"ok": True})
    resp = make_response(out)
    resp.set_cookie("sessao", uuid.uuid4().hex, samesite="Lax")
    return resp


# ---------------------------------------------------------------------------
# WhatsApp via Twilio
# ---------------------------------------------------------------------------
def _quebrar(texto: str, limite: int = LIMITE_WHATSAPP):
    """Quebra um texto longo em pedaços que cabem numa mensagem de WhatsApp."""
    texto = texto or ""
    partes, atual = [], ""
    for linha in texto.splitlines(keepends=True):
        if len(atual) + len(linha) > limite and atual:
            partes.append(atual)
            atual = ""
        atual += linha
    if atual:
        partes.append(atual)
    # Fallback duro caso uma única linha estoure o limite.
    return [p[i:i + limite] for p in partes for i in range(0, len(p), limite)] or [""]


def _validar_twilio() -> bool:
    """Valida X-Twilio-Signature se TWILIO_AUTH_TOKEN estiver definido."""
    if not TWILIO_AUTH_TOKEN:
        return True  # validação desativada (modo sandbox/teste)
    validator = RequestValidator(TWILIO_AUTH_TOKEN)
    # Atrás do proxy do Render a URL externa é https; reconstrói a partir dos headers.
    proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host", request.host)
    url = f"{proto}://{host}{request.full_path.rstrip('?')}"
    assinatura = request.headers.get("X-Twilio-Signature", "")
    return validator.validate(url, request.form, assinatura)


@app.route("/whatsapp", methods=["POST"])
def whatsapp():
    """Webhook chamado pelo Twilio a cada mensagem de WhatsApp recebida."""
    if not _validar_twilio():
        abort(403)

    mensagem = (request.form.get("Body") or "").strip()
    remetente = request.form.get("From", "desconhecido")  # ex.: 'whatsapp:+55...'
    app.logger.info("WhatsApp de %s: %s", remetente, mensagem)

    resp = MessagingResponse()
    if not mensagem:
        resp.message("Recebi sua mensagem, mas veio sem texto. Pode escrever sua dúvida? 😊")
        return str(resp)

    try:
        # A sessão é o número do remetente → isola o estado por usuário.
        estado = responder(mensagem, sessao=remetente)
        carol = estado.get("resposta") or "Desculpe, não consegui processar agora."
        app.logger.info("Carol [%s]", estado.get("etapa"))
    except Exception:
        app.logger.exception("Falha ao gerar resposta")
        carol = ("Estou com uma instabilidade técnica no momento. "
                 "Tente novamente em instantes, por favor. 🙏")

    for parte in _quebrar(carol):
        resp.message(parte)
    return str(resp)


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", "5005"))
    app.run(host="0.0.0.0", port=porta, debug=False)
