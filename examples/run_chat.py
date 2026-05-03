"""
Interface de chat (CLI) do PJ Warmer — linguagem natural.

Como usar (a partir da raiz do projeto):
    python -m examples.run_chat

Exemplos de input:
    "briefing da Ambev, setor bebidas, contato João Silva, diretor comercial"
    "cases de saúde"
    "cases de ciência de dados sobre previsão de demanda"
    "quero cases do setor financeiro"

/quit ou /exit para encerrar.

Design:
  - Loop async porque o agente (e run_warmer) são async.
  - Histórico stateful por sessão: o agente lembra o contexto da conversa.
  - prompt_toolkit para autocomplete de /quit, histórico em memória e
    edição de linha decente.
  - Sem persistência entre sessões (decisão de produto: cada sessão é nova).
"""

from __future__ import annotations

import asyncio

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import InMemoryHistory
from pydantic_ai.messages import ModelMessage

from src.agents.warmer_agent import chat

# Só completamos os comandos de controle — o restante é linguagem natural.
_COMPLETER = WordCompleter(["/quit", "/exit"], ignore_case=True)


async def _loop_principal() -> None:
    """REPL assíncrono. Sai com /quit, /exit, EOF (Ctrl+D) ou Ctrl+C."""
    session = PromptSession(completer=_COMPLETER, history=InMemoryHistory())

    # Histórico da conversa atual. Cresce a cada turno e é passado de volta
    # ao agente para manter contexto (coleta de parâmetros, follow-ups, etc.).
    history: list[ModelMessage] = []

    print("🔥 PJ Warmer — chat")
    print("Fale em linguagem natural. /quit para sair.\n")

    while True:
        try:
            linha = await session.prompt_async("🔥 > ")
        except (EOFError, KeyboardInterrupt):
            print("\nAté mais.")
            return

        linha = linha.strip()
        if not linha:
            continue

        if linha.lower() in {"/quit", "/exit"}:
            print("Até mais.")
            return

        try:
            print()  # respiro visual antes da resposta
            resposta, history = await chat(linha, history)
            print(resposta)
            print()
        except Exception as exc:  # noqa: BLE001
            # Erros de rede, cota da API etc. não derrubam o REPL.
            print(f"Erro: {exc}\n")


def main() -> None:
    """Entry point síncrono — `python -m examples.run_chat`."""
    asyncio.run(_loop_principal())


if __name__ == "__main__":
    main()
