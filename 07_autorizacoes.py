"""
07 - Autorizações (Carol / Unimed Nacional)
===========================================
Implementação fiel do fluxo de `autorizacoes.jpg`, agora **turno-a-turno**:
o estado da conversa (`etapa`) é persistido pelo checkpointer, e cada mensagem
do usuário avança a conversa um passo — como num chatbot de verdade.

Mapa do fluxo (etapas):

    inicio ──(API: existe autorização?)──┐
       │ SIM                             │ NÃO
       ▼                                 ▼
    menu_lista                       menu_sem
       │                                 │
       ├─1 falar  → falar_protocolo → TRANSFERE ATH (fim)
       ├─2 ver mais → menu_lista
       ├─3 nova   → confirma_loc ─(SIM/NÃO)→ nova_localidade
       ├─4 voltar → menu (fim)            │  Solicitar → nova_localidade
       └─5 encerrar → encerrado (fim)     │  Voltar/Encerrar → fim
                                          │
    nova_localidade ──1..6 (Cidades)──► nova_procedimento ─► nova_foto
                    └──7 (Outras Loc.)─► outras_localidades (link) → fim
                                          │
    nova_foto ─► nova_local ─► TRANSFERE ATH (fim)

O texto de cada tela segue o roteiro da imagem. Um helper opcional usa o LLM
(Gemini 2.5 Flash no Vertex) para deixar a mensagem mais natural; a *transição*
de estado é sempre determinística (parsing do número/opção escolhida).
"""

import operator
import re
from typing import TypedDict, Annotated, List

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langchain_core.messages import SystemMessage, HumanMessage

from common import model

memory = SqliteSaver.from_conn_string(":memory:")

NOME_CLIENTE = "Rafael"  # mock: viria da identificação do cliente (CPF/carteirinha)


# ---------------------------------------------------------------------------
# Estado
# ---------------------------------------------------------------------------
class AutorizacaoState(TypedDict, total=False):
    mensagem_usuario: str
    etapa: str                                   # passo atual (persistido)
    resposta: str                                # resposta da Carol neste turno
    localidade: str                              # slot: nova solicitação
    procedimento: str                            # slot: nova solicitação
    finalizado: bool                             # True quando transfere/encerra
    historico: Annotated[List[str], operator.add]


# ---------------------------------------------------------------------------
# "API"/CRM mockado de autorizações
# ---------------------------------------------------------------------------
_AUTORIZACOES_MOCK = [
    {
        "procedimento": "Ressonância Magnética de Joelho",
        "data": "02/06/2026",
        "prazo": "5 dias úteis",
        "prestador": "Hospital Unimed - Unidade Central",
        "pedido": "AUT-2026-000123",
        "status": "Em análise",
    },
    {
        "procedimento": "Fisioterapia (10 sessões)",
        "data": "28/05/2026",
        "prazo": "Concluído",
        "prestador": "Clínica Movimente / Unimed",
        "pedido": "AUT-2026-000098",
        "status": "Autorizada",
    },
]

LOCALIDADES = [
    "São Paulo CAPITAL e ABC",
    "Brasília/Luziânia",
    "São Luís",
    "Salvador",
    "Ilhéus/Itabuna/Feira de Santana/Santo Antônio de Jesus",
    "Manaus",
    "Outras Localidades",
]


def consultar_autorizacoes_api(mensagem: str) -> List[dict]:
    """Mock da consulta à API/CRM.

    Para permitir testar os DOIS ramos da imagem sem mexer no código: se a
    primeira mensagem indicar que o cliente não tem nada ("não tenho",
    "sem autorização", "vazio"), devolve lista vazia; caso contrário devolve as
    autorizações mockadas.
    """
    t = (mensagem or "").lower()
    if any(k in t for k in ("não tenho", "nao tenho", "sem autoriz", "vazio", "nenhuma")):
        return []
    return _AUTORIZACOES_MOCK


# ---------------------------------------------------------------------------
# Helper de redação via LLM (exercita o Gemini; transição é determinística)
# ---------------------------------------------------------------------------
PERSONA = (
    "Você é a Carol, Agente Virtual da Unimed Nacional, no fluxo de Autorizações. "
    "Seja cordial, objetiva e use o tom de atendimento por WhatsApp. "
    "NÃO se reapresente nem cumprimente novamente ('Olá, sou a Carol...') a cada "
    "mensagem — a conversa já está em andamento. Entregue apenas o conteúdo da "
    "mensagem, sem preâmbulos."
)


def _redigir(roteiro: str, usar_llm: bool = True) -> str:
    """Pede ao Gemini para apresentar o `roteiro` de forma natural, SEM inventar
    dados nem alterar números/itens. Em caso de falha, devolve o roteiro cru."""
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


def _escolha_menu(texto: str) -> str:
    """Extrai o número da opção (1..n) de uma mensagem livre do usuário."""
    m = re.search(r"[1-9]", texto or "")
    return m.group(0) if m else ""


# ---------------------------------------------------------------------------
# Nós (cada um trata uma etapa e define a PRÓXIMA etapa)
# ---------------------------------------------------------------------------
def verificacao_node(state: AutorizacaoState):
    """Etapa `inicio`: consulta a API e ramifica (tem / não tem)."""
    autorizacoes = consultar_autorizacoes_api(state.get("mensagem_usuario", ""))

    if autorizacoes:
        linhas = "\n".join(
            f"• Nome Procedimento: {a['procedimento']} / Data da Solicitação: {a['data']} / "
            f"Prazo de análise: {a['prazo']} / Prestador: {a['prestador']} / "
            f"Nº Pedido: {a['pedido']} / Status: {a['status']}"
            for a in autorizacoes
        )
        roteiro = (
            f"Só um momento enquanto verifico suas autorizações...\n\n"
            f"Estas são as suas solicitações, {NOME_CLIENTE}:\n{linhas}\n\n"
            "1. Desejo falar sobre as autorizações apresentadas\n"
            "2. Ver mais autorizações\n"
            "3. Solicitar nova autorização\n"
            "4. Voltar ao menu\n"
            "5. Encerrar"
        )
        return {"resposta": _redigir(roteiro), "etapa": "menu_lista",
                "historico": [f"carol: lista ({len(autorizacoes)} autorizações)"]}

    roteiro = (
        f"Não encontrei nenhuma autorização recente no seu cadastro, {NOME_CLIENTE}. "
        "Posso lhe ajudar em algo mais?\n\n"
        "1. Solicitar Autorização\n"
        "2. Voltar ao Menu\n"
        "3. Encerrar"
    )
    return {"resposta": _redigir(roteiro), "etapa": "menu_sem",
            "historico": ["carol: sem autorizações"]}


def menu_lista_node(state: AutorizacaoState):
    """Etapa `menu_lista`: usuário escolheu 1..5 do menu de autorizações."""
    op = _escolha_menu(state.get("mensagem_usuario", ""))

    if op == "1":  # falar sobre
        protocolos = "\n".join(
            f"{i}. {a['procedimento']} + {a['prestador']}"
            for i, a in enumerate(_AUTORIZACOES_MOCK, 1)
        )
        roteiro = (
            "Por favor, agora me informe o protocolo que deseja falar:\n"
            f"{protocolos}"
        )
        return {"resposta": _redigir(roteiro), "etapa": "falar_protocolo",
                "historico": ["carol: pede protocolo"]}

    if op == "2":  # ver mais
        roteiro = ("Por enquanto estas são todas as suas autorizações. "
                   "Deseja: 1. Falar sobre elas  3. Solicitar nova  4. Voltar ao menu  5. Encerrar?")
        return {"resposta": _redigir(roteiro), "etapa": "menu_lista",
                "historico": ["carol: ver mais (sem mais itens)"]}

    if op == "3":  # nova solicitação
        roteiro = (
            "Estou vendo que deseja solicitar uma NOVA autorização. "
            "Deseja manter seu atendimento nesta localidade? Responda SIM ou NÃO."
        )
        return {"resposta": _redigir(roteiro), "etapa": "confirma_loc",
                "historico": ["carol: confirma localidade"]}

    if op == "4":  # voltar ao menu
        return {"resposta": "Certo! Voltando ao menu principal. 👍",
                "etapa": "menu", "finalizado": True, "historico": ["carol: voltar ao menu"]}

    if op == "5":  # encerrar
        return {"resposta": "Atendimento encerrado. Obrigada por falar com a Carol! 💚",
                "etapa": "encerrado", "finalizado": True, "historico": ["carol: encerrar"]}

    return {"resposta": "Não entendi. Escolha uma opção de 1 a 5, por favor.",
            "etapa": "menu_lista", "historico": ["carol: opção inválida (menu_lista)"]}


def menu_sem_node(state: AutorizacaoState):
    """Etapa `menu_sem`: tela 'sem autorização' — Solicitar / Voltar / Encerrar."""
    op = _escolha_menu(state.get("mensagem_usuario", ""))
    t = (state.get("mensagem_usuario", "")).lower()

    if op == "1" or "solicit" in t:  # Solicitar Autorização → inicia nova
        roteiro = (
            "Vamos solicitar sua autorização! Deseja manter seu atendimento nesta "
            "localidade? Responda SIM ou NÃO."
        )
        return {"resposta": _redigir(roteiro), "etapa": "confirma_loc",
                "historico": ["carol: sem->solicitar"]}

    if op == "2" or "menu" in t:
        return {"resposta": "Certo! Voltando ao menu principal. 👍",
                "etapa": "menu", "finalizado": True, "historico": ["carol: sem->voltar"]}

    if op == "3" or "encerr" in t:
        return {"resposta": "Atendimento encerrado. Obrigada por falar com a Carol! 💚",
                "etapa": "encerrado", "finalizado": True, "historico": ["carol: sem->encerrar"]}

    return {"resposta": "Não entendi. Escolha: 1. Solicitar Autorização  2. Voltar ao Menu  3. Encerrar.",
            "etapa": "menu_sem", "historico": ["carol: opção inválida (menu_sem)"]}


def falar_protocolo_node(state: AutorizacaoState):
    """Etapa `falar_protocolo`: usuário informou o protocolo → Transfere ATH."""
    roteiro = ("Perfeito! Vamos transferí-lo para um de nossos atendentes para "
               "falar sobre essa autorização. É só aguardar! (Transfere ATH)")
    return {"resposta": _redigir(roteiro), "etapa": "transferido",
            "finalizado": True, "historico": ["carol: TRANSFERE ATH (falar)"]}


def confirma_loc_node(state: AutorizacaoState):
    """Etapa `confirma_loc`: SIM/NÃO — ambos levam ao menu de localidades."""
    lista = "\n".join(f"{i}. {loc}" for i, loc in enumerate(LOCALIDADES, 1))
    roteiro = ("Para qual localidade deseja? Clique em Menu e escolha uma opção, por favor:\n"
               f"{lista}")
    return {"resposta": _redigir(roteiro), "etapa": "nova_localidade",
            "historico": ["carol: pede localidade"]}


def nova_localidade_node(state: AutorizacaoState):
    """Etapa `nova_localidade`: 1..6 = Cidades do Menu; 7 = Outras Localidades."""
    op = _escolha_menu(state.get("mensagem_usuario", ""))

    if op == "7":  # Outras Localidades → orientação + contato
        roteiro = (
            "Para autorização na Unimed local, entre em contato e verifique o processo!\n"
            "Localize o contato aqui: https://www.unimed.coop.br/web/guest/rodape/unimed-mais-proxima\n\n"
            "Posso lhe ajudar com algo mais?\n1. Voltar ao Menu\n2. Encerrar"
        )
        return {"resposta": _redigir(roteiro), "etapa": "outras_localidades",
                "historico": ["carol: outras localidades"]}

    if op in {"1", "2", "3", "4", "5", "6"}:  # Cidades do Menu
        loc = LOCALIDADES[int(op) - 1]
        roteiro = ("Informe o procedimento da autorização:\n- Exames/Procedimentos\n- Terapias")
        return {"resposta": _redigir(roteiro), "etapa": "nova_procedimento", "localidade": loc,
                "historico": [f"carol: localidade={loc}"]}

    return {"resposta": "Por favor, escolha a localidade pelo número (1 a 7).",
            "etapa": "nova_localidade", "historico": ["carol: localidade inválida"]}


def nova_procedimento_node(state: AutorizacaoState):
    """Etapa `nova_procedimento`: capturou o procedimento → pede a foto do pedido."""
    roteiro = "Por favor, encaminhe uma foto do pedido médico."
    return {"resposta": _redigir(roteiro), "etapa": "nova_foto",
            "procedimento": state.get("mensagem_usuario", ""),
            "historico": ["carol: pede foto do pedido"]}


def nova_foto_node(state: AutorizacaoState):
    """Etapa `nova_foto`: recebeu a foto/anexo → pergunta o local de realização."""
    lista = "\n".join(f"{i}. {loc}" for i, loc in enumerate(LOCALIDADES, 1))
    roteiro = f"Recebido! Agora me informe o local onde será realizado:\n{lista}"
    return {"resposta": _redigir(roteiro), "etapa": "nova_local",
            "historico": ["carol: pede local de realização"]}


def nova_local_node(state: AutorizacaoState):
    """Etapa `nova_local`: fecha a nova solicitação → Transfere ATH."""
    roteiro = (
        f"{NOME_CLIENTE}, obrigado pelas informações, vou seguir para o seu atendimento. "
        "Vou te transferir para o atendimento aqui mesmo no WhatsApp. É só aguardar! 😉 "
        "(Transfere ATH)"
    )
    return {"resposta": _redigir(roteiro), "etapa": "transferido",
            "finalizado": True, "historico": ["carol: TRANSFERE ATH (nova solicitação)"]}


def outras_localidades_node(state: AutorizacaoState):
    """Etapa `outras_localidades`: Voltar ao Menu / Encerrar."""
    op = _escolha_menu(state.get("mensagem_usuario", ""))
    if op == "2" or "encerr" in (state.get("mensagem_usuario", "")).lower():
        return {"resposta": "Atendimento encerrado. Obrigada por falar com a Carol! 💚",
                "etapa": "encerrado", "finalizado": True, "historico": ["carol: outras->encerrar"]}
    return {"resposta": "Certo! Voltando ao menu principal. 👍",
            "etapa": "menu", "finalizado": True, "historico": ["carol: outras->voltar"]}


# ---------------------------------------------------------------------------
# Roteador: lê a `etapa` persistida e despacha para o nó certo
# ---------------------------------------------------------------------------
def roteador_node(state: AutorizacaoState):
    """Nó de entrada sem efeito — só serve de âncora para as arestas condicionais."""
    return {}


def decidir_etapa(state: AutorizacaoState) -> str:
    etapa = state.get("etapa") or "inicio"
    # Conversa já finalizada: reinicia do zero numa nova mensagem.
    if etapa in {"transferido", "encerrado", "menu"}:
        return "inicio"
    return etapa


ETAPAS = {
    "inicio": "verificacao",
    "menu_lista": "menu_lista",
    "menu_sem": "menu_sem",
    "falar_protocolo": "falar_protocolo",
    "confirma_loc": "confirma_loc",
    "nova_localidade": "nova_localidade",
    "nova_procedimento": "nova_procedimento",
    "nova_foto": "nova_foto",
    "nova_local": "nova_local",
    "outras_localidades": "outras_localidades",
}


builder = StateGraph(AutorizacaoState)
builder.add_node("roteador", roteador_node)
builder.add_node("verificacao", verificacao_node)
builder.add_node("menu_lista", menu_lista_node)
builder.add_node("menu_sem", menu_sem_node)
builder.add_node("falar_protocolo", falar_protocolo_node)
builder.add_node("confirma_loc", confirma_loc_node)
builder.add_node("nova_localidade", nova_localidade_node)
builder.add_node("nova_procedimento", nova_procedimento_node)
builder.add_node("nova_foto", nova_foto_node)
builder.add_node("nova_local", nova_local_node)
builder.add_node("outras_localidades", outras_localidades_node)

builder.set_entry_point("roteador")
builder.add_conditional_edges("roteador", decidir_etapa, ETAPAS)
for destino in set(ETAPAS.values()):
    builder.add_edge(destino, END)

graph = builder.compile(checkpointer=memory)


# ---------------------------------------------------------------------------
# API de conveniência (usada pelo Flask): UMA mensagem por chamada, com estado
# persistido por sessão via thread_id.
# ---------------------------------------------------------------------------
def responder(mensagem_usuario: str, sessao: str = "default") -> dict:
    cfg = {"configurable": {"thread_id": sessao}}
    return graph.invoke({"mensagem_usuario": mensagem_usuario}, cfg)


if __name__ == "__main__":
    # Demonstração turno-a-turno (caminho "nova solicitação" via menu).
    roteiro_teste = [
        "Olá, quero ver minhas autorizações",  # inicio -> menu_lista
        "3",                                   # nova solicitação -> confirma_loc
        "SIM",                                 # -> nova_localidade
        "1",                                   # São Paulo -> nova_procedimento
        "Exames/Procedimentos",                # -> nova_foto
        "[foto do pedido médico]",             # -> nova_local
        "1",                                   # local -> TRANSFERE ATH
    ]
    for i, msg in enumerate(roteiro_teste, 1):
        estado = responder(msg, sessao="teste-cli")
        print("=" * 79)
        print(f"👤 [{i}] {msg}")
        print(f"🤖 Carol [{estado.get('etapa')}]:\n{estado.get('resposta')}\n")
