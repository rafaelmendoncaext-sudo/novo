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
import re
import uuid

import requests
from flask import Flask, request, jsonify, make_response, abort

# O módulo do agente começa com dígito → carregado via importlib.
autorizacoes = importlib.import_module("07_autorizacoes")
responder = autorizacoes.responder

app = Flask(__name__)

# Whisper carregado uma vez ao iniciar (evita overhead por request).
# WHISPER_MODEL: "tiny" (39 MB, rápido) ou "base" (140 MB, mais preciso).
# Use "tiny" no Render free tier (512 MB RAM); "base" localmente ou plano pago.
_WHISPER_MODEL_NAME = os.environ.get("WHISPER_MODEL", "base")
try:
    import whisper as _whisper
    _WHISPER_MODEL = _whisper.load_model(_WHISPER_MODEL_NAME)
    app.logger.info("Whisper '%s' carregado com sucesso.", _WHISPER_MODEL_NAME)
except Exception as _we:
    _WHISPER_MODEL = None
    app.logger.warning("Whisper indisponível: %s", _we)

# Limite seguro por mensagem de WhatsApp (corte conservador).
LIMITE_WHATSAPP = 1500

# --- Twilio (opcional / legado) ---------------------------------------------
# Se definido, valida a assinatura do Twilio. Importado de forma preguiçosa para
# que o app funcione mesmo sem o pacote twilio instalado.
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")

# --- Meta WhatsApp Cloud API ------------------------------------------------
META_VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN", "carol-unimed")
META_ACCESS_TOKEN = os.environ.get("META_ACCESS_TOKEN")        # token da Graph API
META_PHONE_NUMBER_ID = os.environ.get("META_PHONE_NUMBER_ID")  # ID do nº de teste
META_API_VERSION = os.environ.get("META_API_VERSION", "v21.0")

# --- Z-API (https://z-api.io) ----------------------------------------------
# Conecta no WhatsApp via QR Code. Pegue ID e Token no painel da instância e o
# Client-Token em "Segurança" (Account Security Token).
ZAPI_INSTANCE_ID = os.environ.get("ZAPI_INSTANCE_ID")
ZAPI_TOKEN = os.environ.get("ZAPI_TOKEN")
ZAPI_CLIENT_TOKEN = os.environ.get("ZAPI_CLIENT_TOKEN")  # header Client-Token


def _enviar_whatsapp_zapi(para: str, texto: str) -> None:
    """Envia uma mensagem de texto via Z-API (endpoint /send-text)."""
    if not (ZAPI_INSTANCE_ID and ZAPI_TOKEN):
        app.logger.error("ZAPI_INSTANCE_ID/ZAPI_TOKEN não configurados.")
        return
    url = f"https://api.z-api.io/instances/{ZAPI_INSTANCE_ID}/token/{ZAPI_TOKEN}/send-text"
    headers = {"Content-Type": "application/json"}
    if ZAPI_CLIENT_TOKEN:
        headers["Client-Token"] = ZAPI_CLIENT_TOKEN
    for parte in _quebrar(texto, 3500):
        try:
            r = requests.post(url, json={"phone": para, "message": parte},
                              headers=headers, timeout=30)
            if r.status_code >= 400:
                app.logger.error("Falha ao enviar p/ Z-API (%s): %s", r.status_code, r.text)
        except Exception:
            app.logger.exception("Erro ao chamar a Z-API")


def _enviar_whatsapp_meta(para: str, texto: str) -> None:
    """Envia uma mensagem de texto via WhatsApp Cloud API (Graph API da Meta)."""
    if not (META_ACCESS_TOKEN and META_PHONE_NUMBER_ID):
        app.logger.error("META_ACCESS_TOKEN/META_PHONE_NUMBER_ID não configurados.")
        return
    url = f"https://graph.facebook.com/{META_API_VERSION}/{META_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {META_ACCESS_TOKEN}", "Content-Type": "application/json"}
    # WhatsApp aceita até ~4096 chars por mensagem de texto.
    for parte in _quebrar(texto, 3500):
        payload = {
            "messaging_product": "whatsapp",
            "to": para,
            "type": "text",
            "text": {"body": parte},
        }
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=30)
            if r.status_code >= 400:
                app.logger.error("Falha ao enviar p/ Meta (%s): %s", r.status_code, r.text)
        except Exception:
            app.logger.exception("Erro ao chamar a Graph API da Meta")

PAGINA = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Carol — Autorizações (Unimed Nacional)</title>
<style>
  :root { --verde:#009639; --verde-esc:#007a2f; --bg:#e5ddd5; --msg-bot:#fff; --msg-user:#dcf8c6; --border:#d1d7db; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif; background:var(--bg); }
  .wrap { max-width:680px; margin:0 auto; height:100vh; display:flex; flex-direction:column; box-shadow:0 1px 4px rgba(0,0,0,.15); }

  /* Header */
  header { background:var(--verde); color:#fff; padding:10px 16px; display:flex; align-items:center; gap:12px; }
  .avatar { width:42px; height:42px; background:rgba(255,255,255,.2); border-radius:50%; display:flex; align-items:center; justify-content:center; font-size:22px; }
  .hinfo .name { font-weight:600; font-size:15px; }
  .hinfo .sub  { font-size:12px; opacity:.8; }

  /* Chat */
  #chat { flex:1; overflow-y:auto; padding:12px 14px; display:flex; flex-direction:column; gap:2px; }
  .mw { display:flex; flex-direction:column; }
  .mw.user { align-items:flex-end; }
  .mw.bot  { align-items:flex-start; }
  .msg { max-width:78%; padding:8px 12px; border-radius:8px; font-size:14px; line-height:1.45; white-space:pre-wrap; word-break:break-word; box-shadow:0 1px 1px rgba(0,0,0,.08); }
  .mw.bot  .msg { background:var(--msg-bot); border-top-left-radius:0; }
  .mw.user .msg { background:var(--msg-user); border-top-right-radius:0; }
  .msg img { max-width:220px; border-radius:6px; display:block; margin-bottom:4px; cursor:pointer; }
  .meta { font-size:11px; color:#8696a0; margin:1px 4px 5px; }

  /* Atalhos */
  .shortcuts { background:#f0f2f5; border-top:1px solid var(--border); padding:7px 12px; display:flex; flex-wrap:wrap; gap:6px; align-items:center; }
  .shortcuts span { font-size:12px; color:#667781; }
  .sc { font-size:12px; padding:4px 11px; border:1px solid var(--verde); border-radius:16px; background:#fff; color:var(--verde); cursor:pointer; }
  .sc:hover { background:#e7f5ec; }

  /* Input area */
  .input-area { background:#f0f2f5; border-top:1px solid var(--border); }

  /* Preview imagem */
  #imgPreview { display:none; align-items:center; gap:8px; padding:8px 14px 0; }
  #imgThumb   { width:54px; height:54px; object-fit:cover; border-radius:6px; border:1px solid var(--border); }
  #imgNome    { flex:1; font-size:13px; color:#3b4a54; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  #cancelImg  { background:none; border:none; cursor:pointer; color:#8696a0; font-size:19px; padding:4px; }
  #cancelImg:hover { color:#e53935; }

  /* Status gravação */
  #recStatus { display:none; font-size:12px; color:#e53935; padding:0 14px 5px; align-items:center; gap:6px; }
  #recStatus.on { display:flex; }
  .dot { width:8px; height:8px; border-radius:50%; background:#e53935; animation:pulsar 1s infinite; }
  @keyframes pulsar { 0%,100%{opacity:1} 50%{opacity:.3} }

  /* Linha de input */
  .irow { display:flex; align-items:center; gap:4px; padding:8px 12px; }
  .ibtn { background:none; border:none; cursor:pointer; color:#8696a0; width:38px; height:38px; border-radius:50%; display:flex; align-items:center; justify-content:center; transition:background .15s,color .15s; flex-shrink:0; }
  .ibtn:hover { background:rgba(0,0,0,.06); color:#3b4a54; }
  .ibtn.rec   { color:#e53935; animation:pulsar 1s infinite; }
  #txt { flex:1; padding:10px 14px; border:none; border-radius:20px; font-size:14px; background:#fff; outline:none; box-shadow:0 1px 2px rgba(0,0,0,.07); }
  #sendBtn { width:38px; height:38px; border-radius:50%; border:none; background:var(--verde); color:#fff; display:flex; align-items:center; justify-content:center; cursor:pointer; flex-shrink:0; }
  #sendBtn:hover { background:var(--verde-esc); }
</style>
</head>
<body>
<div class="wrap">

  <header>
    <div class="avatar">💚</div>
    <div class="hinfo">
      <div class="name">Carol — Unimed Nacional</div>
      <div class="sub">Autorizações · Gemini 2.5 Flash (Vertex AI)</div>
    </div>
  </header>

  <div id="chat"></div>

  <div class="shortcuts">
    <span>Atalhos:</span>
    <button class="sc" onclick="enviarTexto('Olá, quero ver minhas autorizações')">Ver autorizações</button>
    <button class="sc" onclick="enviarTexto('Quero solicitar uma nova autorização')">Nova solicitação</button>
    <button class="sc" onclick="reiniciar()">🔄 Reiniciar</button>
  </div>

  <div class="input-area">
    <div id="imgPreview">
      <img id="imgThumb" src="" alt="preview">
      <span id="imgNome"></span>
      <button id="cancelImg" onclick="cancelarImg()" title="Remover">✕</button>
    </div>
    <div id="recStatus"><div class="dot"></div> Ouvindo…</div>
    <div class="irow">
      <!-- Upload de imagem -->
      <input type="file" id="imgInput" accept="image/*" style="display:none" onchange="selecionarImg()">
      <button class="ibtn" onclick="document.getElementById('imgInput').click()" title="Enviar imagem">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor">
          <path d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/>
        </svg>
      </button>
      <!-- Texto -->
      <input id="txt" type="text" placeholder="Digite uma mensagem" autocomplete="off" autofocus
             onkeydown="if(event.key==='Enter')mandar()">
      <!-- Microfone -->
      <button class="ibtn" id="micBtn" onclick="toggleMic()" title="Gravar áudio">
        <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor">
          <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3z"/>
          <path d="M17 11c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-3.08c3.39-.49 6-3.39 6-6.92h-2z"/>
        </svg>
      </button>
      <!-- Enviar -->
      <button id="sendBtn" onclick="mandar()" title="Enviar">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
          <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"/>
        </svg>
      </button>
    </div>
  </div>

</div>
<script>
let imgSelecionada = null;
let gravando = false;

const chat      = document.getElementById('chat');
const txtEl     = document.getElementById('txt');
const micBtn    = document.getElementById('micBtn');
const recStatus = document.getElementById('recStatus');

// ── Adicionar mensagem ──────────────────────────────────────────────────────
function add(conteudo, quem, etapa, tipo) {
  const wrap = document.createElement('div');
  wrap.className = 'mw ' + (quem === 'user' ? 'user' : 'bot');

  const msg = document.createElement('div');
  msg.className = 'msg';

  if (tipo === 'img') {
    const im = document.createElement('img');
    im.src = conteudo;
    im.onclick = () => window.open(conteudo);
    msg.appendChild(im);
  } else {
    msg.textContent = conteudo;
  }

  wrap.appendChild(msg);
  if (etapa) {
    const m = document.createElement('div');
    m.className = 'meta';
    m.textContent = 'etapa: ' + etapa;
    wrap.appendChild(m);
  }
  chat.appendChild(wrap);
  chat.scrollTop = chat.scrollHeight;
}

function addLoader() {
  const w = document.createElement('div'); w.className='mw bot'; w.id='loader';
  const m = document.createElement('div'); m.className='msg'; m.textContent='...';
  w.appendChild(m); chat.appendChild(w); chat.scrollTop=chat.scrollHeight;
}
function rmLoader() { const e=document.getElementById('loader'); if(e) e.remove(); }

// ── Envio de texto ──────────────────────────────────────────────────────────
async function enviarTexto(txt) {
  if (!txt.trim()) return;
  add(txt, 'user');
  addLoader();
  try {
    const r = await fetch('/chat', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({mensagem:txt}) });
    const j = await r.json();
    rmLoader(); add(j.resposta, 'bot', j.etapa);
  } catch { rmLoader(); add('Erro de conexão. Tente novamente.','bot'); }
}

// ── Envio com imagem ────────────────────────────────────────────────────────
async function enviarComImg(txt, img) {
  add(img.dataUrl, 'user', null, 'img');
  if (txt) add(txt, 'user');
  addLoader();
  try {
    const r = await fetch('/chat-imagem', { method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ mensagem: txt || '', imagem: img.dataUrl }) });
    const j = await r.json();
    rmLoader(); add(j.resposta, 'bot', j.etapa);
  } catch { rmLoader(); add('Erro de conexão. Tente novamente.','bot'); }
}

// ── Mandar (decide rota) ────────────────────────────────────────────────────
function mandar() {
  const txt = txtEl.value.trim();
  txtEl.value = '';
  if (imgSelecionada) { enviarComImg(txt, imgSelecionada); cancelarImg(); }
  else if (txt)       { enviarTexto(txt); }
}

// ── Imagem ──────────────────────────────────────────────────────────────────
function selecionarImg() {
  const f = document.getElementById('imgInput').files[0];
  if (!f) return;
  const reader = new FileReader();
  reader.onload = e => {
    imgSelecionada = { dataUrl: e.target.result, nome: f.name };
    document.getElementById('imgThumb').src = e.target.result;
    document.getElementById('imgNome').textContent = f.name;
    document.getElementById('imgPreview').style.display = 'flex';
    txtEl.focus();
  };
  reader.readAsDataURL(f);
  document.getElementById('imgInput').value = '';
}

function cancelarImg() {
  imgSelecionada = null;
  document.getElementById('imgPreview').style.display = 'none';
  document.getElementById('imgThumb').src = '';
}

// ── Microfone (MediaRecorder → /transcrever via Gemini) ─────────────────────
let mediaRecorder = null;
let audioChunks  = [];

async function toggleMic() {
  if (gravando) {
    // Para a gravação; o processamento continua no onstop
    mediaRecorder.stop();
    return;
  }

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    add('Sem permissão para o microfone. Verifique as configurações do navegador.', 'bot');
    return;
  }

  // Escolhe o melhor codec suportado
  const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? 'audio/webm;codecs=opus'
    : MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm'
    : 'audio/ogg;codecs=opus';

  mediaRecorder = new MediaRecorder(stream, { mimeType });
  audioChunks   = [];

  mediaRecorder.ondataavailable = e => { if (e.data.size > 0) audioChunks.push(e.data); };

  mediaRecorder.onstart = () => {
    gravando = true;
    micBtn.classList.add('rec');
    recStatus.classList.add('on');
    txtEl.placeholder = 'Gravando… toque novamente para parar';
  };

  mediaRecorder.onstop = async () => {
    gravando = false;
    micBtn.classList.remove('rec');
    recStatus.classList.remove('on');
    txtEl.placeholder = 'Transcrevendo…';
    stream.getTracks().forEach(t => t.stop());

    const blob   = new Blob(audioChunks, { type: mimeType });
    const reader = new FileReader();
    reader.readAsDataURL(blob);
    reader.onloadend = async () => {
      const audioDataUrl = reader.result;
      try {
        const r = await fetch('/transcrever', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ audio: audioDataUrl, mime_type: mimeType }),
        });
        const j = await r.json();
        txtEl.placeholder = 'Digite uma mensagem';
        if (j.transcricao) {
          txtEl.value = j.transcricao;
          mandar();
        } else {
          add('Não consegui transcrever o áudio. Tente novamente.', 'bot');
        }
      } catch {
        txtEl.placeholder = 'Digite uma mensagem';
        add('Erro ao transcrever. Verifique a conexão.', 'bot');
      }
    };
  };

  mediaRecorder.start();
}

// ── Reiniciar ───────────────────────────────────────────────────────────────
async function reiniciar() {
  await fetch('/reset', { method:'POST' });
  chat.innerHTML = '';
  cancelarImg();
  add('Sessão reiniciada. Diga "oi" para começar. 💚','bot');
}

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
        app.logger.exception("Falha no fluxo de autorizações: %s", exc)
        resposta = "Ocorreu um erro interno. Por favor, tente novamente em alguns instantes. 🙏"
        etapa = "erro"
    out = jsonify({"resposta": resposta, "etapa": etapa})
    resp = make_response(out)
    resp.set_cookie("sessao", sessao, samesite="Lax")
    return resp


def _analisar_imagem_ocr(imagem_b64: str) -> dict:
    """Usa Gemini para validar se a imagem é um pedido médico e extrair dados.

    Retorna dict com:
        eh_pedido (bool)
        procedimento (str)
        cid (str)
        observacao (str)
        mensagem_fluxo (str)  — texto pronto para injetar no fluxo
    """
    fallback = {
        "eh_pedido": True,
        "procedimento": "",
        "cid": "",
        "observacao": "",
        "mensagem_fluxo": "[imagem: pedido médico enviado]",
    }
    if not imagem_b64:
        return fallback
    try:
        import json as _json
        from langchain_core.messages import HumanMessage as HM
        from common import model as llm

        prompt = (
            "Analise esta imagem e responda SOMENTE com um JSON válido, sem markdown:\n"
            '{"eh_pedido_medico": true|false, '
            '"procedimento": "<nome do procedimento ou exame, ou vazio>", '
            '"cid": "<código CID se visível, ou vazio>", '
            '"observacao": "<resumo em 1 frase>"}\n\n'
            "Critérios para eh_pedido_medico=true: imagem contém pedido/solicitação "
            "médica, receita, guia de exame, laudo ou documento com código CID."
        )
        result = llm.invoke([HM(content=[
            {"type": "image_url", "image_url": {"url": imagem_b64}},
            {"type": "text", "text": prompt},
        ])])
        raw = (result.content or "").strip()
        # Remove blocos de markdown se o modelo os incluir
        raw = re.sub(r"^```[a-z]*\n?|\n?```$", "", raw, flags=re.MULTILINE).strip()
        dados = _json.loads(raw)
        eh_pedido = bool(dados.get("eh_pedido_medico"))
        procedimento = dados.get("procedimento", "")
        cid = dados.get("cid", "")
        obs = dados.get("observacao", "")

        if eh_pedido:
            partes = ["[imagem: pedido médico"]
            if procedimento:
                partes.append(f"procedimento: {procedimento}")
            if cid:
                partes.append(f"CID: {cid}")
            partes.append("]")
            msg_fluxo = " | ".join(partes)
        else:
            msg_fluxo = ""

        return {
            "eh_pedido": eh_pedido,
            "procedimento": procedimento,
            "cid": cid,
            "observacao": obs,
            "mensagem_fluxo": msg_fluxo,
        }
    except Exception:
        app.logger.exception("Falha no OCR da imagem")
        return fallback


@app.route("/chat-imagem", methods=["POST"])
def chat_imagem():
    """Recebe imagem (base64 data-URL) + texto opcional.
    Valida via OCR se é um pedido médico antes de passar ao fluxo.
    """
    dados  = request.get_json(silent=True) or {}
    sessao = _sessao()
    imagem_b64  = dados.get("imagem", "")
    texto_extra = (dados.get("mensagem") or "").strip()

    ocr = _analisar_imagem_ocr(imagem_b64)

    if not ocr["eh_pedido"]:
        msg = (
            "Esta imagem não parece ser um pedido médico. "
            f"{ocr['observacao']}\n\n"
            "Por favor, envie a foto do pedido/solicitação médica para continuar."
        )
        out  = jsonify({"resposta": msg, "etapa": "nova_foto", "valido": False})
        resp = make_response(out)
        resp.set_cookie("sessao", sessao, samesite="Lax")
        return resp

    mensagem_fluxo = ocr["mensagem_fluxo"]
    if texto_extra:
        mensagem_fluxo += f" {texto_extra}"

    try:
        estado  = responder(mensagem_fluxo, sessao=sessao)
        resposta = estado.get("resposta") or "Desculpe, não consegui processar agora."
        etapa    = estado.get("etapa", "")
    except Exception as exc:
        app.logger.exception("Falha ao processar imagem")
        resposta = f"Instabilidade técnica ({type(exc).__name__}). Tente novamente. 🙏"
        etapa    = "erro"

    out  = jsonify({"resposta": resposta, "etapa": etapa, "valido": True,
                    "procedimento": ocr["procedimento"], "cid": ocr["cid"]})
    resp = make_response(out)
    resp.set_cookie("sessao", sessao, samesite="Lax")
    return resp


@app.route("/transcrever", methods=["POST"])
def transcrever():
    """Recebe áudio (base64 data-URL) e retorna a transcrição via Whisper local.

    Usa openai-whisper (CPU, modelo tiny/base) — sem chamadas externas, sem
    dependências de auth do Vertex AI que causavam 401 para áudio inline_data.
    """
    dados     = request.get_json(silent=True) or {}
    audio_url = dados.get("audio", "")   # "data:audio/webm;base64,..."

    if not audio_url:
        return jsonify({"transcricao": None, "erro": "sem áudio"})

    if not _WHISPER_MODEL:
        return jsonify({"transcricao": None, "erro": "Whisper não disponível no servidor"})

    try:
        import base64 as _b64
        import tempfile

        # Extrai extensão do MIME type para nomear o arquivo temporário
        header, _, b64data = audio_url.partition(";base64,")
        base_mime = header.split(";")[0].lstrip("data:")   # "audio/webm"
        ext = base_mime.split("/")[-1].split(";")[0]       # "webm", "wav", "ogg"
        if ext not in {"wav", "mp3", "ogg", "webm", "mp4", "m4a", "flac"}:
            ext = "wav"
        audio_bytes = _b64.b64decode(b64data)

        # Whisper lê de arquivo; usa tempfile para não poluir o disco
        with tempfile.NamedTemporaryFile(suffix=f".{ext}", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        try:
            result = _WHISPER_MODEL.transcribe(tmp_path, language="pt", fp16=False)
            transcricao = (result.get("text") or "").strip()
        finally:
            os.unlink(tmp_path)

        if not transcricao:
            return jsonify({"transcricao": None, "erro": "áudio sem fala detectada"})

        return jsonify({"transcricao": transcricao})

    except Exception as exc:
        app.logger.exception("Falha ao transcrever áudio")
        return jsonify({"transcricao": None, "erro": str(exc)})


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
    from twilio.request_validator import RequestValidator
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
    from twilio.twiml.messaging_response import MessagingResponse

    if not _validar_twilio():
        abort(403)

    mensagem = (request.form.get("Body") or "").strip()
    remetente = request.form.get("From", "desconhecido")  # ex.: 'whatsapp:+55...'
    app.logger.info("WhatsApp(Twilio) de %s: %s", remetente, mensagem)

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


# ---------------------------------------------------------------------------
# WhatsApp via Meta (WhatsApp Cloud API) — sem Twilio
# ---------------------------------------------------------------------------
@app.route("/webhook", methods=["GET"])
def webhook_verify():
    """Verificação do webhook (a Meta faz um GET ao configurar a URL)."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")
    if mode == "subscribe" and token == META_VERIFY_TOKEN:
        app.logger.info("Webhook da Meta verificado com sucesso.")
        return challenge or "", 200
    app.logger.warning("Falha na verificação do webhook da Meta (token incorreto).")
    return "Forbidden", 403


@app.route("/webhook", methods=["POST"])
def webhook_meta():
    """Recebe mensagens da Meta e responde via Graph API.

    A Meta NÃO usa a resposta HTTP para enviar a mensagem (diferente do Twilio):
    respondemos 200 rápido e mandamos a resposta da Carol chamando a Graph API.
    """
    dados = request.get_json(silent=True) or {}
    try:
        for entry in dados.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                mensagens = value.get("messages")
                if not mensagens:
                    continue  # ex.: eventos de status (entregue/lido) — ignora
                for m in mensagens:
                    numero = m.get("from")  # ex.: '5511988887777'
                    if m.get("type") == "text":
                        texto = (m.get("text", {}).get("body") or "").strip()
                    else:
                        # imagem/áudio/etc.: tratamos como um anexo qualquer no fluxo
                        texto = "[anexo recebido]"
                    app.logger.info("WhatsApp(Meta) de %s: %s", numero, texto)
                    try:
                        estado = responder(texto, sessao=numero)
                        carol = estado.get("resposta") or "Desculpe, não consegui processar agora."
                        app.logger.info("Carol [%s]", estado.get("etapa"))
                    except Exception:
                        app.logger.exception("Falha ao gerar resposta")
                        carol = ("Estou com uma instabilidade técnica no momento. "
                                 "Tente novamente em instantes, por favor. 🙏")
                    _enviar_whatsapp_meta(numero, carol)
    except Exception:
        app.logger.exception("Erro ao processar webhook da Meta")
    # Sempre 200: senão a Meta reenvia o evento repetidamente.
    return "OK", 200


# ---------------------------------------------------------------------------
# WhatsApp via Z-API (QR Code) — recomendado para teste no Brasil
# ---------------------------------------------------------------------------
@app.route("/zapi", methods=["POST"])
def webhook_zapi():
    """Webhook 'Ao receber' da Z-API.

    A Z-API faz POST com a mensagem; respondemos 200 e mandamos a resposta da
    Carol chamando a REST da Z-API (/send-text). Mensagens enviadas pelo próprio
    número conectado (`fromMe`) e grupos são ignoradas para evitar loop/ruído.
    """
    dados = request.get_json(silent=True) or {}

    if dados.get("fromMe") or dados.get("isGroup"):
        return "OK", 200

    numero = dados.get("phone")
    texto = ((dados.get("text") or {}).get("message") or "").strip()
    if not numero:
        return "OK", 200
    if not texto:
        texto = "[anexo recebido]"  # imagem/áudio/etc.

    app.logger.info("WhatsApp(Z-API) de %s: %s", numero, texto)
    try:
        estado = responder(texto, sessao=numero)
        carol = estado.get("resposta") or "Desculpe, não consegui processar agora."
        app.logger.info("Carol [%s]", estado.get("etapa"))
    except Exception:
        app.logger.exception("Falha ao gerar resposta")
        carol = ("Estou com uma instabilidade técnica no momento. "
                 "Tente novamente em instantes, por favor. 🙏")

    _enviar_whatsapp_zapi(numero, carol)
    return "OK", 200


if __name__ == "__main__":
    porta = int(os.environ.get("PORT", "5005"))
    app.run(host="0.0.0.0", port=porta, debug=False)
