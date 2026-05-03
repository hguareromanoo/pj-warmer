"""
Interface de chat (CLI) do PJ Warmer com slash commands.

Como usar (a partir da raiz do projeto):
    1. Garanta que o `.env` está preenchido (copie de `.env.example`).
    2. Instale dependências: `pip install -r requirements.txt`
    3. Execute: `python -m examples.run_chat`

Comandos disponíveis (mais detalhe via `/help`):
    /briefing <empresa>   - Roda o Warmer Briefing para uma empresa
    /cases                - Busca cases por setor/área
    /help                 - Lista os comandos
    /quit | /exit         - Sai do chat

Estado atual: ESQUELETO. Os handlers de `/briefing` e `/cases` são stubs;
serão preenchidos nas sub-fases B.2 e B.3.

Princípios de design:
  - Loop async porque `run_warmer` é async.
  - Sem persistência (decisão de produto: cada comando é stateless).
  - `prompt-toolkit` para ganhar autocomplete, histórico (em memória) e
    edição em linha decente (PowerShell tem `input()` pobre).
"""

from __future__ import annotations

import asyncio
import re
from typing import Awaitable, Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.validation import Validator

from src.agents.warmer_orchestrator import run_warmer
from src.retrieval.cases import buscar_hibrido
from src.retrieval.formatters import format_as_markdown
from src.schemas.research import LeadInput


# ============================================================================
# Parser de comandos
# ============================================================================
# Regex para extrair (comando, argumentos) de uma linha do tipo `/cmd args...`.
# - `^/(\w+)`         — começa com `/` seguido de palavra (o comando).
# - `(?:\s+(.*))?$`   — opcionalmente, espaços + resto da linha (os args).
# Linhas que NÃO começam com `/` não casam aqui — são tratadas como pergunta
# livre (ainda não implementada nesta sessão; só avisamos o usuário).
_COMANDO_RE = re.compile(r"^/(\w+)(?:\s+(.*))?$")


def _parse_comando(linha: str) -> tuple[str, str] | None:
    """
    Tenta interpretar a linha como `/comando [args]`.

    Returns
    -------
    tuple[str, str] | None
        `(comando_lowercase, args_strip)` se a linha for um slash command;
        `None` se não for (linha vazia, pergunta livre, etc.).
    """
    match = _COMANDO_RE.match(linha.strip())
    if match is None:
        return None
    comando, args = match.group(1).lower(), (match.group(2) or "").strip()
    return comando, args


# ============================================================================
# Helpers de diálogo (sub-prompts dentro de um handler)
# ============================================================================
# Validator anti-vazio reaproveitado em todos os campos obrigatórios.
# Usar `Validator.from_callable` deixa o prompt_toolkit rejeitar Enter direto
# até receber texto não-vazio, sem precisar de loop manual no nosso lado.
_VALIDATOR_OBRIGATORIO = Validator.from_callable(
    lambda text: bool(text.strip()),
    error_message="Campo obrigatório, não pode estar em branco.",
    move_cursor_to_end=True,
)

# Conjuntos de respostas que tratamos como "sim" e "não" na confirmação.
# Tudo case-insensitive. Qualquer outra coisa cai no "abortar".
_RESPOSTAS_SIM = {"s", "sim", "y", "yes"}


async def _perguntar_obrigatorio(label: str) -> str:
    """
    Sub-prompt para um campo obrigatório.

    Usa um `PromptSession` local — sem completer nem histórico — para que
    o diálogo não polua a UX do REPL principal (não queremos que `/briefing`
    apareça como sugestão de autocomplete enquanto o Hunter digita um nome
    de empresa, por exemplo).
    """
    sub = PromptSession()
    texto = await sub.prompt_async(label, validator=_VALIDATOR_OBRIGATORIO)
    return texto.strip()


async def _perguntar_opcional(label: str) -> str:
    """
    Sub-prompt para um campo opcional. Enter vazio retorna `""`.

    Diferente do `_perguntar_obrigatorio`, NÃO usa validator: o vazio é
    legítimo e o handler que chama decide como interpretá-lo (em `/cases`,
    pelo menos um dos três campos precisa estar preenchido — essa regra
    fica no handler, não aqui).
    """
    sub = PromptSession()
    texto = await sub.prompt_async(label)
    return texto.strip()


async def _confirmar(label: str = "Confirma? [s/n]: ") -> bool:
    """Sub-prompt sim/não. Default conservador: qualquer coisa != sim aborta."""
    sub = PromptSession()
    resposta = (await sub.prompt_async(label)).strip().lower()
    return resposta in _RESPOSTAS_SIM


# ============================================================================
# Handlers
# ============================================================================
# Tipagem do dispatcher: cada handler recebe os args como string e é async
# (porque o handler real do `/briefing` precisa awaitar `run_warmer`).
Handler = Callable[[str], Awaitable[None]]


async def _handler_briefing(args: str) -> None:
    """
    Coleta os 4 campos do `LeadInput`, confirma, e roda `run_warmer`.

    `args` traz o que veio depois de `/briefing` na linha original. Se o
    Hunter já escreveu o nome da empresa ali, aproveitamos; senão, perguntamos.
    """
    # Capturamos KeyboardInterrupt/EOF do diálogo para abortar APENAS o
    # comando (e não o REPL inteiro). Os helpers `_perguntar_obrigatorio` e
    # `_confirmar` propagam essas exceções para cá.
    try:
        # Etapa 1 — empresa: usa o que veio em `args` se não vazio,
        # senão pergunta interativamente.
        org_name = args.strip() or await _perguntar_obrigatorio("Empresa: ")

        # Etapa 2 — campos restantes.
        org_setor = await _perguntar_obrigatorio("Setor: ")
        person_name = await _perguntar_obrigatorio("Contato: ")
        person_position = await _perguntar_obrigatorio("Cargo: ")

        # Etapa 3 — confirmação. Mostrar os 4 campos coletados antes de
        # disparar `run_warmer` evita queimar 30s-2min de busca + tokens
        # de Gemini por causa de typo num campo.
        print("\nDados coletados:")
        print(f"  Empresa: {org_name}")
        print(f"  Setor:   {org_setor}")
        print(f"  Contato: {person_name}")
        print(f"  Cargo:   {person_position}")

        if not await _confirmar():
            print("Briefing cancelado.\n")
            return
    except (KeyboardInterrupt, EOFError):
        # Ctrl+C / Ctrl+D dentro do diálogo: aborta o comando sem sair do REPL.
        print("\nBriefing cancelado.\n")
        return

    lead = LeadInput(
        org_name=org_name,
        org_setor=org_setor,
        person_name=person_name,
        person_position=person_position,
    )

    print(f"\n🔍 Pesquisando lead: {lead.org_name}...")
    print("(isso pode levar de 30s a 2min, dependendo da quantidade de buscas)\n")

    # Etapa 4 — execução. `run_warmer` é a parte cara (web + LLM + DB).
    # Erros aqui são capturados pelo try/except do `_loop_principal`.
    result = await run_warmer(lead)
    briefing = result.briefing
    cases = result.cases

    # Etapa 5 — apresentação. Mesmo formato do `run_manual.py`: markdown
    # do briefing + bloco cru de cases. Os cases ainda NÃO entram no markdown
    # (decisão 6 do PLANEJAMENTO_INTEGRACAO_SETOR.md — fica para quando o
    # `format_as_markdown` for estendido).
    print("\n" + "=" * 70)
    print(format_as_markdown(briefing, lead))
    print("=" * 70 + "\n")

    print("📂 Cases relacionados\n")

    print(f"   Setores consultados ({len(cases.setores_consultados)}):")
    for setor in cases.setores_consultados:
        print(f"     - {setor}")

    print(f"\n   por_setor ({len(cases.por_setor)}):")
    for c in cases.por_setor:
        print(
            f"     - [{c.id}] {c.cliente} | {c.setor_empresa} "
            f"| {c.area_pj} / {c.servico_pj}"
        )

    print(f"\n   por_relacao ({len(cases.por_relacao)}):")
    for c in cases.por_relacao:
        print(f"     - [{c.id}] {c.cliente} | {c.setor_empresa}")

    print()


async def _handler_cases(args: str) -> None:
    """
    Busca cases por setor / área / descrição livre. Os três são opcionais,
    mas pelo menos um precisa vir preenchido — caso contrário a busca
    retornaria a base inteira sem critério.

    `args` é ignorado: a UX é via diálogo (consistente com `/briefing` e mais
    à prova de erro do Hunter — uma string com 3 campos separados por
    delimitador é frágil).
    """
    try:
        setor = await _perguntar_opcional("Setor (opcional, Enter para pular): ")
        area = await _perguntar_opcional("Área (opcional, Enter para pular): ")
        descricao = await _perguntar_opcional(
            "Descrição (opcional, ex: 'previsão de demanda'): "
        )
    except (KeyboardInterrupt, EOFError):
        print("\nBusca cancelada.\n")
        return

    # Validação combinada: se NADA foi preenchido, abortar com aviso.
    # Sem isso, `buscar_hibrido({}, "", ...)` devolveria a base inteira.
    if not (setor or area or descricao):
        print(
            "Pelo menos um dos campos (setor, área ou descrição) "
            "deve ser preenchido.\n"
        )
        return

    # Monta os filtros só com as chaves preenchidas. `buscar_hibrido` valida
    # as chaves contra `FILTROS_SUPORTADOS` — passar chaves desconhecidas
    # quebra; ausência é tratada como "sem filtro nesse campo".
    filtros: dict = {}
    if setor:
        filtros["setor"] = setor
    if area:
        filtros["area_pj"] = area

    # `buscar_hibrido` cai automaticamente em `buscar_deterministico` quando
    # a query é vazia (ramo 1 da função em `cases.py`). Por isso podemos
    # chamar SEMPRE `buscar_hibrido` aqui, sem branching.
    resultados = buscar_hibrido(filtros, descricao, top_k=5)

    if not resultados:
        print("\nNenhum case encontrado.\n")
        return

    print(f"\nEncontrados {len(resultados)} case(s):")
    for c in resultados:
        # `score_similaridade` é None na busca puramente determinística
        # (descrição vazia) e float no modo híbrido — só mostramos quando
        # existe, para não poluir a saída com `(score=None)`.
        score_str = (
            f" (score={c.score_similaridade:.2f})"
            if c.score_similaridade is not None
            else ""
        )
        print(
            f"  - [{c.id}] {c.cliente} | {c.setor_empresa} "
            f"| {c.area_pj} / {c.servico_pj}{score_str}"
        )
    print()


async def _handler_help(_: str) -> None:
    """Imprime a lista de comandos disponíveis."""
    print(
        "\nComandos disponíveis:\n"
        "  /briefing <empresa>   - Roda o Warmer Briefing para uma empresa\n"
        "  /cases                - Busca cases por setor/área\n"
        "  /help                 - Mostra esta mensagem\n"
        "  /quit, /exit          - Sai do chat\n"
    )


# `/quit` é tratado fora do dispatcher (precisa quebrar o loop). Não tem
# handler async dedicado; ver `_loop_principal`.


# ============================================================================
# Setup do PromptSession e dispatch
# ============================================================================
COMANDOS = ["/briefing", "/cases", "/help", "/quit", "/exit"]


def _build_session() -> PromptSession:
    """
    Configura o PromptSession.

    - WordCompleter case-insensitive: o Hunter pode digitar `/B<Tab>` e
      autocompletar para `/briefing`.
    - InMemoryHistory: setas pra cima/baixo navegam comandos da SESSÃO atual.
      Sem persistência em disco (decisão de produto consciente; trivial
      trocar para FileHistory no futuro se virar útil).
    """
    completer = WordCompleter(COMANDOS, ignore_case=True)
    history = InMemoryHistory()
    return PromptSession(completer=completer, history=history)


# Mapa comando → handler. `/quit` e `/exit` ficam fora porque encerram o loop;
# tratamos eles diretamente no `_loop_principal`.
DISPATCHER: dict[str, Handler] = {
    "briefing": _handler_briefing,
    "cases": _handler_cases,
    "help": _handler_help,
}


# ============================================================================
# Loop principal
# ============================================================================
async def _loop_principal() -> None:
    """REPL assíncrono. Sai com /quit, /exit, EOF (Ctrl+D) ou Ctrl+C."""
    session = _build_session()

    print("🔥 PJ Warmer — chat CLI")
    print("Digite /help para ver os comandos. /quit para sair.\n")

    while True:
        try:
            linha = await session.prompt_async("🔥 > ")
        except (EOFError, KeyboardInterrupt):
            # Ctrl+D / Ctrl+C: sai limpo, sem stack trace.
            print("\nAté mais.")
            return

        linha = linha.strip()
        if not linha:
            # Enter vazio: ignora silenciosamente (UX comum em REPLs).
            continue

        parsed = _parse_comando(linha)
        if parsed is None:
            # Linha sem `/`: futura "pergunta livre". Por ora, só avisa.
            print("Use /help para ver os comandos disponíveis.")
            continue

        comando, args = parsed

        # `/quit` e `/exit` são terminais — encerram o loop sem passar
        # pelo dispatcher.
        if comando in {"quit", "exit"}:
            print("Até mais.")
            return

        handler = DISPATCHER.get(comando)
        if handler is None:
            print(f"Comando desconhecido: /{comando}. Tente /help.")
            continue

        # Try/except envolvendo o handler para que um erro num comando
        # não derrube o REPL inteiro. O handler real do /briefing pode
        # falhar por rede, cota da API, etc.; o Hunter prefere ver o
        # erro e continuar do que perder a sessão.
        try:
            await handler(args)
        except Exception as exc:  # noqa: BLE001
            print(f"Erro ao executar /{comando}: {exc}")


def main() -> None:
    """Entry point síncrono — `python -m examples.run_chat`."""
    asyncio.run(_loop_principal())


if __name__ == "__main__":
    main()
