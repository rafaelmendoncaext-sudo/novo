"""
07 - Autorizações (Carol / Unimed Nacional)
===========================================
Fluxo turno-a-turno com identificação de beneficiário persistida em sessão.

Mapa de etapas:

    pedir_beneficiario ──► aguardar_beneficiario ──(válido)──► inicio
                                                 └─(inválido)─► aguardar_beneficiario

    inicio ──(DB: existe autorização?)──┐
       │ SIM                            │ NÃO
       ▼                                ▼
    menu_lista                      menu_sem
       │                                │
       ├─1 falar  → falar_protocolo → transferido (fim)
       ├─2 ver mais → menu_lista
       ├─3 nova   → confirma_loc → nova_localidade
       ├─4 voltar → menu (fim — mantém NR_BENEFICIARIO)
       └─5 encerrar → encerrado (fim — limpa NR_BENEFICIARIO)

    nova_localidade ──1..6──► nova_procedimento ─► nova_foto ─► nova_local ─► transferido
                   └──7────► outras_localidades ─► fim
"""

import operator
import re
from typing import TypedDict, Annotated, List

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langchain_core.messages import SystemMessage, HumanMessage

from common import model
import db as pedidos_db

memory = SqliteSaver.from_conn_string(":memory:")

LOCALIDADES = [
    "São Paulo CAPITAL e ABC",
    "Brasília/Luziânia",
    "São Luís",
    "Salvador",
    "Ilhéus/Itabuna/Feira de Santana/Santo Antônio de Jesus",
    "Manaus",
    "Outras Localidades",
]


# ---------------------------------------------------------------------------
# Estado
# ---------------------------------------------------------------------------
class AutorizacaoState(TypedDict, total=False):
    mensagem_usuario: str
    etapa: str
    resposta: str
    nr_beneficiario: str                          # identificado na entrada, persistido
    localidade: str
    procedimento: str
    finalizado: bool
    historico: Annotated[List[str], operator.add]


# ---------------------------------------------------------------------------
# Helper LLM
# ---------------------------------------------------------------------------
PERSONA = (
    "Você é a Carol, Agente Virtual da Unimed Nacional, no fluxo de Autorizações. "
    "Seja cordial, objetiva e use o tom de atendimento por WhatsApp. "
    "NÃO se reapresente nem cumprimente novamente a cada mensagem — a conversa já "
    "está em andamento. Entregue apenas o conteúdo da mensagem, sem preâmbulos."
)


def _redigir(roteiro: str, usar_llm: bool = True) -> str:
    if not usar_llm:
        return roteiro
    try:
        msg = model.invoke([
            SystemMessage(content=PERSONA + (
                " Reescreva a MENSAGEM abaixo para o cliente mantendo EXATAMENTE "
                "os mesmos dados, opções e numeração. Não invente informações.")),
            HumanMessage(content=roteiro),
        ])
        return (msg.content or "").strip() or roteiro
    except Exception:
        return roteiro


# ---------------------------------------------------------------------------
# Detecção de desvio de intenção
# ---------------------------------------------------------------------------
_DESVIO_KW: dict[str, str] = {
    "encerr":          "encerrado",
    "finaliz":         "encerrado",
    "tchau":           "encerrado",
    "voltar ao menu":  "menu",
    "menu principal":  "menu",
    "início":          "menu",
    "inicio":          "menu",
    "falar sobre":     "menu_lista",
    "ver minha":       "menu_lista",
    "nova solicitaç":  "confirma_loc",
    "solicitar autor": "confirma_loc",
}

# Etapas intermediárias onde um desvio faz sentido verificar
_ETAPAS_INTERMEDIARIAS = {
    "nova_procedimento", "nova_foto", "nova_local", "falar_protocolo",
}

def _verificar_desvio(texto: str, etapa: str) -> str | None:
    """Verifica se o usuário mudou de intenção no meio do fluxo.

    Retorna a etapa destino do desvio, ou None se a mensagem é uma
    resposta normal ao contexto atual.
    Só atua em etapas intermediárias para não interferir nos menus.
    """
    if etapa not in _ETAPAS_INTERMEDIARIAS:
        return None

    t = texto.lower()

    # 1) Verificação rápida por palavras-chave
    for kw, destino in _DESVIO_KW.items():
        if kw in t:
            return destino

    # 2) LLM como árbitro quando as keywords não pegam
    prompt = (
        f'O usuário está na etapa "{etapa}" e disse: "{texto}"\n\n'
        "Ele está respondendo normalmente ao que foi pedido, "
        "ou está tentando ir para outra parte do atendimento?\n\n"
        "Se estiver tentando navegar, responda com UMA dessas chaves:\n"
        "  menu           → voltar ao menu principal\n"
        "  encerrado      → encerrar o atendimento\n"
        "  confirma_loc   → solicitar nova autorização\n"
        "  menu_lista     → ver/falar sobre uma autorização existente\n\n"
        "Se for uma resposta normal ao contexto, responda exatamente: continuar"
    )
    try:
        resp = model.invoke([HumanMessage(content=prompt)])
        resultado = (resp.content or "").strip().lower().split()[0]
        if resultado in {"menu", "encerrado", "confirma_loc", "menu_lista"}:
            return resultado
    except Exception:
        pass
    return None


def _estado_desvio(destino: str, state: AutorizacaoState) -> dict:
    """Constrói o dict de retorno adequado para cada destino de desvio."""
    msgs = {
        "menu":        "Claro! Voltando ao menu principal. 👍",
        "encerrado":   "Atendimento encerrado. Obrigada por falar com a Carol! 💚",
        "confirma_loc": (
            "Sem problema! Vamos iniciar uma nova solicitação.\n"
            "Para qual localidade deseja?\n"
            + "\n".join(f"{i}. {l}" for i, l in enumerate(LOCALIDADES, 1))
        ),
        "menu_lista": None,  # verificacao_node vai gerar a resposta
    }
    base = {
        "etapa":     destino,
        "finalizado": destino in {"menu", "encerrado"},
        "historico": [f"desvio → {destino}"],
    }
    if destino == "encerrado":
        base["nr_beneficiario"] = ""
    texto = msgs.get(destino)
    base["resposta"] = _redigir(texto) if texto else ""
    return base


# ---------------------------------------------------------------------------
# Helpers de menu
# ---------------------------------------------------------------------------
def _keyword_score(texto: str, opcoes: dict[str, str]) -> str:
    """Keyword scoring: conta palavras da descrição que aparecem no texto do usuário.

    Usado como fast-path antes do LLM e como fallback quando o LLM falha.
    Palavras com < 4 letras são ignoradas para evitar falsos positivos.
    """
    tl = texto.lower()
    best_score, best_key = 0, ""
    for key, desc in opcoes.items():
        palavras = [p for p in re.sub(r"[^\w\s]", "", desc.lower()).split() if len(p) >= 4]
        score = sum(1 for p in palavras if p in tl)
        if score > best_score:
            best_score, best_key = score, key
    return best_key if best_score > 0 else ""


def _escolha_menu(texto: str, opcoes: dict[str, str] | None = None) -> str:
    """Interpreta a resposta do usuário e devolve o número da opção correspondente.

    Fluxo:
    1. Dígito isolado → resposta direta
    2. Keyword scoring → fast-path sem LLM para frases óbvias
    3. LLM classifica intenção (melhor para texto ambíguo)
    4. Fallback: keyword scoring novamente (LLM falhou) ou busca de dígito
    """
    texto = (texto or "").strip()

    # 1. Dígito isolado → resposta direta
    m = re.search(r"^[1-9]$", texto)
    if m:
        return m.group(0)

    # Sem contexto de opções → extrai primeiro dígito do texto
    if not opcoes:
        m = re.search(r"[1-9]", texto)
        return m.group(0) if m else ""

    # 2. Keyword scoring como fast-path (não depende de auth/LLM)
    kw = _keyword_score(texto, opcoes)
    if kw:
        return kw

    # 3. Texto livre + opções → LLM classifica intenção (nuance extra)
    lista = "\n".join(f"{k}. {v}" for k, v in opcoes.items())
    prompt = (
        f"O usuário disse: \"{texto}\"\n\n"
        f"Opções disponíveis:\n{lista}\n\n"
        "Responda APENAS com o número da opção que melhor corresponde ao que o "
        "usuário quer. Se nenhuma opção fizer sentido, responda 0."
    )
    try:
        resp = model.invoke([HumanMessage(content=prompt)])
        digito = re.search(r"[0-9]", (resp.content or "").strip())
        return digito.group(0) if digito else ""
    except Exception:
        # 4. Fallback: re-tenta keyword scoring ou busca dígito avulso no texto
        fallback = _keyword_score(texto, opcoes)
        if fallback:
            return fallback
        m = re.search(r"[1-9]", texto)
        return m.group(0) if m else ""


# ---------------------------------------------------------------------------
# Nós
# ---------------------------------------------------------------------------

def pedir_beneficiario_node(state: AutorizacaoState):
    """Primeira etapa: solicita o número do beneficiário."""
    roteiro = (
        "Olá! Para acessar suas autorizações, por favor informe o número do seu "
        "beneficiário (carteirinha)."
    )
    return {
        "resposta": _redigir(roteiro),
        "etapa": "aguardar_beneficiario",
        "historico": ["carol: pede nr_beneficiario"],
    }


def aguardar_beneficiario_node(state: AutorizacaoState):
    """Recebe e valida o NR_BENEFICIARIO digitado pelo usuário."""
    nr = re.sub(r"\s+", "", state.get("mensagem_usuario", ""))

    if pedidos_db.beneficiario_existe(nr):
        return {
            "nr_beneficiario": nr,
            "etapa": "inicio",
            "resposta": "",          # sobrescrito por verificacao_node no mesmo turno
            "historico": [f"identificado: {nr}"],
        }

    roteiro = (
        f"Não encontrei o número *{nr}* na nossa base. "
        "Por favor, verifique e informe novamente o número do beneficiário."
    )
    return {
        "resposta": _redigir(roteiro),
        "etapa": "aguardar_beneficiario",
        "historico": [f"beneficiario não encontrado: {nr}"],
    }


def verificacao_node(state: AutorizacaoState):
    """Consulta o banco com o NR_BENEFICIARIO da sessão."""
    nr = state.get("nr_beneficiario", "")
    autorizacoes = pedidos_db.get_autorizacoes(nr)

    if autorizacoes:
        blocos = []
        for i, a in enumerate(autorizacoes, 1):
            blocos.append(
                f"*{i}. Pedido {a['NUM_PEDIDO']}*\n"
                f"   Procedimento: {a['NOME_ITEM']}\n"
                f"   Status do item: {a['DS_SITUACAOITEM']}\n"
                f"   Status do pedido: {a['NOM_SITUACAO']}\n"
                f"   Prestador: {a['NOME_PRESTADOR']}"
            )
        linhas = "\n\n".join(blocos)
        roteiro = (
            f"Só um momento enquanto verifico suas autorizações...\n\n"
            f"Estas são as suas solicitações:\n\n{linhas}\n\n"
            "1. Desejo falar sobre as autorizações apresentadas\n"
            "2. Ver mais autorizações\n"
            "3. Solicitar nova autorização\n"
            "4. Voltar ao menu\n"
            "5. Encerrar"
        )
        return {
            "resposta": _redigir(roteiro),
            "etapa": "menu_lista",
            "historico": [f"carol: lista ({len(autorizacoes)} pedidos)"],
        }

    roteiro = (
        "Não encontrei nenhuma autorização recente no seu cadastro. "
        "Posso lhe ajudar em algo mais?\n\n"
        "1. Solicitar Autorização\n"
        "2. Voltar ao Menu\n"
        "3. Encerrar"
    )
    return {
        "resposta": _redigir(roteiro),
        "etapa": "menu_sem",
        "historico": ["carol: sem autorizações"],
    }


def menu_lista_node(state: AutorizacaoState):
    op = _escolha_menu(state.get("mensagem_usuario", ""), {
        "1": "falar sobre a autorização apresentada",
        "2": "ver mais autorizações",
        "3": "solicitar nova autorização",
        "4": "voltar ao menu",
        "5": "encerrar",
    })
    nr = state.get("nr_beneficiario", "")

    if op == "1":
        autorizacoes = pedidos_db.get_autorizacoes(nr)
        if len(autorizacoes) == 1:
            a = autorizacoes[0]
            roteiro = (
                f"Entendido! Vou encaminhar sua solicitação referente a:\n\n"
                f"*Pedido {a['NUM_PEDIDO']}*\n"
                f"Procedimento: {a['NOME_ITEM']}\n"
                f"Prestador: {a['NOME_PRESTADOR']}\n\n"
                "Aguarde, vou transferi-lo para um de nossos atendentes. É só aguardar!"
            )
            return {"resposta": _redigir(roteiro), "etapa": "transferido",
                    "finalizado": True, "historico": ["carol: TRANSFERE ATH (protocolo único)"]}

        protocolos = "\n".join(
            f"{i}. {a['NOME_ITEM']} — {a['NOME_PRESTADOR']} (Pedido {a['NUM_PEDIDO']})"
            for i, a in enumerate(autorizacoes, 1)
        )
        roteiro = f"Por favor, informe o protocolo que deseja falar:\n{protocolos}"
        return {"resposta": _redigir(roteiro), "etapa": "falar_protocolo",
                "historico": ["carol: pede protocolo"]}

    if op == "2":
        roteiro = ("Por enquanto estas são todas as suas autorizações. "
                   "Deseja: 1. Falar sobre elas  3. Solicitar nova  4. Voltar  5. Encerrar?")
        return {"resposta": _redigir(roteiro), "etapa": "menu_lista",
                "historico": ["carol: ver mais (sem mais itens)"]}

    if op == "3":
        roteiro = (
            "Vamos solicitar uma nova autorização! "
            "Para qual localidade deseja? Clique em Menu e escolha uma opção:\n"
            + "\n".join(f"{i}. {loc}" for i, loc in enumerate(LOCALIDADES, 1))
        )
        return {"resposta": _redigir(roteiro), "etapa": "confirma_loc",
                "historico": ["carol: confirma localidade"]}

    if op == "4":
        return {"resposta": "Certo! Voltando ao menu principal. 👍",
                "etapa": "menu", "finalizado": True,
                "historico": ["carol: voltar ao menu"]}

    if op == "5":
        return {"resposta": "Atendimento encerrado. Obrigada por falar com a Carol! 💚",
                "etapa": "encerrado", "finalizado": True,
                "nr_beneficiario": "",          # limpa para próxima sessão
                "historico": ["carol: encerrar"]}

    return {"resposta": "Não entendi. Escolha uma opção de 1 a 5, por favor.",
            "etapa": "menu_lista", "historico": ["carol: opção inválida (menu_lista)"]}


def menu_sem_node(state: AutorizacaoState):
    op = _escolha_menu(state.get("mensagem_usuario", ""), {
        "1": "solicitar autorização",
        "2": "voltar ao menu",
        "3": "encerrar",
    })
    t  = (state.get("mensagem_usuario", "")).lower()

    if op == "1" or "solicit" in t:
        roteiro = (
            "Vamos solicitar sua autorização! "
            "Para qual localidade deseja?\n"
            + "\n".join(f"{i}. {loc}" for i, loc in enumerate(LOCALIDADES, 1))
        )
        return {"resposta": _redigir(roteiro), "etapa": "confirma_loc",
                "historico": ["carol: sem->solicitar"]}

    if op == "2" or "menu" in t:
        return {"resposta": "Certo! Voltando ao menu principal. 👍",
                "etapa": "menu", "finalizado": True,
                "historico": ["carol: sem->voltar"]}

    if op == "3" or "encerr" in t:
        return {"resposta": "Atendimento encerrado. Obrigada por falar com a Carol! 💚",
                "etapa": "encerrado", "finalizado": True,
                "nr_beneficiario": "",          # limpa para próxima sessão
                "historico": ["carol: sem->encerrar"]}

    return {"resposta": "Não entendi. Escolha: 1. Solicitar  2. Voltar ao Menu  3. Encerrar.",
            "etapa": "menu_sem", "historico": ["carol: opção inválida (menu_sem)"]}


def falar_protocolo_node(state: AutorizacaoState):
    txt = state.get("mensagem_usuario", "")
    desvio = _verificar_desvio(txt, "falar_protocolo")
    if desvio:
        return _estado_desvio(desvio, state)

    roteiro = ("Perfeito! Vamos transferí-lo para um de nossos atendentes para "
               "falar sobre essa autorização. É só aguardar!")
    return {"resposta": _redigir(roteiro), "etapa": "transferido",
            "finalizado": True, "historico": ["carol: TRANSFERE ATH (falar)"]}


def confirma_loc_node(state: AutorizacaoState):
    """Recebe a localidade escolhida pelo usuário."""
    op = _escolha_menu(state.get("mensagem_usuario", ""), {
        "1": "São Paulo CAPITAL e ABC",
        "2": "Brasília e Luziânia",
        "3": "São Luís",
        "4": "Salvador",
        "5": "Ilhéus Itabuna Feira de Santana Santo Antônio de Jesus",
        "6": "Manaus",
        "7": "Outras Localidades",
    })

    if op == "7":
        roteiro = (
            "Para autorização na Unimed local, entre em contato e verifique o processo!\n"
            "Localize o contato aqui: https://www.unimed.coop.br/web/guest/rodape/unimed-mais-proxima\n\n"
            "Posso lhe ajudar com algo mais?\n1. Voltar ao Menu\n2. Encerrar"
        )
        return {"resposta": _redigir(roteiro), "etapa": "outras_localidades",
                "historico": ["carol: outras localidades"]}

    if op in {"1", "2", "3", "4", "5", "6"}:
        loc = LOCALIDADES[int(op) - 1]
        roteiro = "Informe o procedimento da autorização:\n- Exames/Procedimentos\n- Terapias"
        return {"resposta": _redigir(roteiro), "etapa": "nova_procedimento",
                "localidade": loc, "historico": [f"carol: localidade={loc}"]}

    return {"resposta": "Por favor, escolha a localidade pelo número (1 a 7).",
            "etapa": "confirma_loc", "historico": ["carol: localidade inválida"]}


def nova_procedimento_node(state: AutorizacaoState):
    txt = state.get("mensagem_usuario", "")
    desvio = _verificar_desvio(txt, "nova_procedimento")
    if desvio:
        return _estado_desvio(desvio, state)

    roteiro = "Por favor, encaminhe uma foto do pedido médico."
    return {"resposta": _redigir(roteiro), "etapa": "nova_foto",
            "procedimento": txt,
            "historico": ["carol: pede foto do pedido"]}


def nova_foto_node(state: AutorizacaoState):
    txt = state.get("mensagem_usuario", "")
    desvio = _verificar_desvio(txt, "nova_foto")
    if desvio:
        return _estado_desvio(desvio, state)

    lista = "\n".join(f"{i}. {loc}" for i, loc in enumerate(LOCALIDADES, 1))
    roteiro = f"Recebido! Agora me informe o local onde será realizado:\n{lista}"
    return {"resposta": _redigir(roteiro), "etapa": "nova_local",
            "historico": ["carol: pede local de realização"]}


def nova_local_node(state: AutorizacaoState):
    txt = state.get("mensagem_usuario", "")
    desvio = _verificar_desvio(txt, "nova_local")
    if desvio:
        return _estado_desvio(desvio, state)

    roteiro = (
        "Obrigado pelas informações! Vou te transferir para o atendimento aqui mesmo "
        "no WhatsApp. O tempo de resposta pode demorar um pouco mais que o esperado. "
        "É só aguardar! 😉"
    )
    return {"resposta": _redigir(roteiro), "etapa": "transferido",
            "finalizado": True, "historico": ["carol: TRANSFERE ATH (nova solicitação)"]}


def outras_localidades_node(state: AutorizacaoState):
    op = _escolha_menu(state.get("mensagem_usuario", ""), {
        "1": "voltar ao menu",
        "2": "encerrar",
    })
    if op == "2" or "encerr" in (state.get("mensagem_usuario", "")).lower():
        return {"resposta": "Atendimento encerrado. Obrigada por falar com a Carol! 💚",
                "etapa": "encerrado", "finalizado": True,
                "nr_beneficiario": "",
                "historico": ["carol: outras->encerrar"]}
    return {"resposta": "Certo! Voltando ao menu principal. 👍",
            "etapa": "menu", "finalizado": True,
            "historico": ["carol: outras->voltar"]}


# ---------------------------------------------------------------------------
# Roteador
# ---------------------------------------------------------------------------
def roteador_node(state: AutorizacaoState):
    return {}


def decidir_etapa(state: AutorizacaoState) -> str:
    etapa = state.get("etapa") or "inicio"
    nr    = state.get("nr_beneficiario", "")

    # Sem beneficiário identificado → sempre pede primeiro
    if not nr:
        if etapa == "aguardar_beneficiario":
            return "aguardar_beneficiario"
        return "pedir_beneficiario"

    # Sessão finalizada (com beneficiário ainda válido) → reinicia consulta
    if etapa in {"transferido", "encerrado", "menu"}:
        return "inicio"

    return etapa


ETAPAS = {
    "pedir_beneficiario":    "pedir_beneficiario",
    "aguardar_beneficiario": "aguardar_beneficiario",
    "inicio":                "verificacao",
    "menu_lista":            "menu_lista",
    "menu_sem":              "menu_sem",
    "falar_protocolo":       "falar_protocolo",
    "confirma_loc":          "confirma_loc",
    "nova_procedimento":     "nova_procedimento",
    "nova_foto":             "nova_foto",
    "nova_local":            "nova_local",
    "outras_localidades":    "outras_localidades",
}

builder = StateGraph(AutorizacaoState)
builder.add_node("roteador",              roteador_node)
builder.add_node("pedir_beneficiario",    pedir_beneficiario_node)
builder.add_node("aguardar_beneficiario", aguardar_beneficiario_node)
builder.add_node("verificacao",           verificacao_node)
builder.add_node("menu_lista",            menu_lista_node)
builder.add_node("menu_sem",              menu_sem_node)
builder.add_node("falar_protocolo",       falar_protocolo_node)
builder.add_node("confirma_loc",          confirma_loc_node)
builder.add_node("nova_procedimento",     nova_procedimento_node)
builder.add_node("nova_foto",             nova_foto_node)
builder.add_node("nova_local",            nova_local_node)
builder.add_node("outras_localidades",    outras_localidades_node)

def rota_pos_aguardar(state: AutorizacaoState) -> str:
    """Após identificar o beneficiário, vai direto para verificação no mesmo turno."""
    if state.get("etapa") == "inicio":
        return "verificacao"
    return END


builder.set_entry_point("roteador")
builder.add_conditional_edges("roteador", decidir_etapa, ETAPAS)
builder.add_conditional_edges(
    "aguardar_beneficiario",
    rota_pos_aguardar,
    {"verificacao": "verificacao", END: END},
)
for destino in set(ETAPAS.values()) - {"aguardar_beneficiario"}:
    builder.add_edge(destino, END)

graph = builder.compile(checkpointer=memory)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------
def responder(mensagem_usuario: str, sessao: str = "default") -> dict:
    cfg = {"configurable": {"thread_id": sessao}}
    return graph.invoke({"mensagem_usuario": mensagem_usuario}, cfg)


if __name__ == "__main__":
    import sys
    NR = sys.argv[1] if len(sys.argv) > 1 else "08650018043896005"

    roteiro = [
        "oi",                       # → pede beneficiário
        NR,                         # → identifica e consulta
        "1",                        # → falar sobre
        "1",                        # → protocolo escolhido → ATH
    ]
    for i, msg in enumerate(roteiro, 1):
        estado = responder(msg, sessao="teste-cli")
        print("=" * 79)
        print(f"👤 [{i}] {msg}")
        print(f"🤖 Carol [{estado.get('etapa')}]:\n{estado.get('resposta')}\n")
