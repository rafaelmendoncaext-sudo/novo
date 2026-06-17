# Fluxo — autorizacoes.jpg

```
Início
 └── ◇ Será API ou buscaremos do CRM?
      └── ◇ Existe alguma autorização disponível?
           │
           ├── NÃO ──► "Não encontrei nenhuma autorização recente
           │             no seu cadastro, [NomeCliente].
           │             Posso lhe ajudar em algo mais?"
           │             ├── [Solicitar Autorização] ──────────────────────────────────┐
           │             ├── [Voltar ao Menu] ──► 🟡 Voltar ao Menu                    │
           │             └── [Encerrar] ──────► 🟡 Encerrar                            │
           │                                                                            │
           └── SIM ──► 📋 PUSH: "Estas são suas solicitações                           │
                        'Autorizadas' e/ou 'Negadas': [NomeCliente]"                   │
                        └── "Só um momento enquanto verifico                           │
                              suas autorizações..."                                    │
                              └── "Estas são as suas solicitações, [NomeCliente]:"    │
                                   └── 🔵 Consulta de API                             │
                                        ├── 🟡 nota: Autorização tha história         │
                                        │          de WhatsApp!                        │
                                        └── Detalhes da autorização:                  │
                                             • Nome Procedimento: xx                  │
                                             • Data da Solicitação: xxx               │
                                             • Prazo de análise: xx                   │
                                             • Prestador: xx                          │
                                             • Nº Pedido: xx                          │
                                             • Status: xx                             │
                                             └── Menu principal                       │
                                                  ├── 1. Falar sobre autorizações     │
                                                  │    └── [nota: demais opções       │
                                                  │         seguem disponíveis]       │
                                                  │         └── "Por favor, informe   │
                                                  │              o protocolo:"        │
                                                  │              ├── 1. Nome proc.    │
                                                  │              │   + Prestador/     │
                                                  │              │   Ou Unimed        │
                                                  │              ├── 2. Nome proc.    │
                                                  │              │   + Prestador/     │
                                                  │              │   Ou Unimed        │
                                                  │              └── 3. Nome proc.    │
                                                  │                  + Prestador/     │
                                                  │                  Ou Unimed        │
                                                  │                  └── "Vamos       │
                                                  │                       transferi-  │
                                                  │                       lo para um  │
                                                  │                       atendente." │
                                                  │                       └── 🟡 ATH  │
                                                  │                                   │
                                                  ├── 2. Ver mais autorizações        │
                                                  │    └── "Por enquanto estas são    │
                                                  │         todas as suas autorizações│
                                                  │         Deseja: 1 / 3 / 4 / 5"   │
                                                  │         └── (retorna ao menu)     │
                                                  │                                   │
                                                  ├── 3. Novas Solicitações ──────────┘
                                                  ├── 4. Voltar ao menu ──► 🟡 Voltar ao Menu
                                                  └── 5. Encerrar ───────► 🟡 Encerrar
                                                                    │
                                                                    ▼
                                             ◇ "Estou vendo que está falando de XXXX,
                                               deseja manter seu atendimento
                                               para essa localidade?"
                                               ├── NÃO ──► "Para qual localidade deseja?
                                               │            Clique em Menu e escolha
                                               │            uma opção, por favor"
                                               │            ├── [Menu] ────────────────┐
                                               │            ├── 🟡 Voltar ao Menu      │
                                               │            └── 🟡 Encerrar            │
                                               │                                       │
                                               └── SIM ────────────────────────────────┘
                                                                    │
                                                                    ▼
                                             Lista de 7 cidades
                                               ├── 1. São Paulo CAPITAL e ABC
                                               ├── 2. Brasília/Luziânia
                                               ├── 3. São Luís
                                               ├── 4. Salvador
                                               ├── 5. Ilhéus/Itabuna/Feira de Santana/
                                               │      Santo Antônio de Jesus
                                               ├── 6. Manaus
                                               └── 7. Outras Localidades
                                                    │
                                                    ├── Opções 1–6 (Cidades do Menu)
                                                    │    └── ◇ Relacionado ao TOA —
                                                    │         já existe essa definição?
                                                    │         └── "Informe o procedimento:"
                                                    │              ├── Exames/Procedimentos
                                                    │              └── Terapias
                                                    │              └── [Resposta do Usuário]
                                                    │                   └── "Encaminhe uma
                                                    │                        foto do pedido
                                                    │                        médico."
                                                    │                        └── [Resposta do Usuário]
                                                    │                             └── "Informe o local
                                                    │                                  onde será realizado:"
                                                    │                                  └── Lista de cidades
                                                    │                                       (mesmas 7)
                                                    │                                       └── [Resposta do Usuário]
                                                    │                                            └── "[NomeCliente], obrigado
                                                    │                                                 pelas informações, vou
                                                    │                                                 te transferir no WhatsApp.
                                                    │                                                 É só aguardar! 😉"
                                                    │                                                 └── 🟡 TRANSFERE ATH
                                                    │                                                      └── 📋 Régua de posição
                                                    │                                                           na fila e tempo de espera
                                                    │                                                           └── 📝 Disponível para
                                                    │                                                                Central answer?
                                                    │                                                                Encaminha auto-
                                                    │                                                                maticamente para
                                                    │                                                                fila de autorizações
                                                    │
                                                    └── Opção 7 (Outras Localidades)
                                                         └── "Para autorização na Unimed
                                                              local, entre em contato e
                                                              verifique o processo!"
                                                              🔗 unimed.coop.br/web/guest/
                                                                 rodape/unimed-mais-proxima
                                                              └── "Posso lhe ajudar com
                                                                   algo mais?
                                                                   Escolha a opção desejada:"
                                                                   └── [Resposta do Usuário]
                                                                        ├── 🟡 Voltar ao Menu
                                                                        └── 🟡 Encerrar
```

---

## Legenda

| Símbolo | Significado |
|---|---|
| `◇` | Decisão (losango) |
| `🔵` | Nó de sistema / botão azul |
| `🟡 ATH` | TRANSFERE ATH (terminal amarelo) |
| `🟡 Voltar / Encerrar` | Terminais amarelos |
| `📋` | Callout / Régua de fila |
| `📝` | Anotação / Nota explicativa |
| `🔗` | Link externo |
| `[Resposta do Usuário]` | Input do usuário (sem lógica) |
| `[nota: ...]` | Caixa cinza de contexto visível na tela |
