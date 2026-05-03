"""
Teste manual da camada de retrieval (Fase 3, item 10 do planejamento).

Como rodar (a partir da raiz do projeto):

    # Cenario 1 - deterministico: todos os cases de Varejo (primario ou associado)
    python -m scripts.test_retrieval det --setor Varejo

    # Cenario 2 - semantico: pergunta livre, top 5
    python -m scripts.test_retrieval sem "previsao de demanda em sazonalidade"

    # Cenario 3 - hibrido: filtros + pergunta
    python -m scripts.test_retrieval hib --setor Varejo --query "previsao de demanda"

    # Sem argumentos: roda uma bateria pre-definida de queries realistas
    python -m scripts.test_retrieval

A ideia nao e validar qualidade (isso e a Fase 4), e sim provar que a
mecanica funciona: as tres funcoes rodam, devolvem CaseResultado, e o
formato bate com o esperado.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Permite rodar `python scripts/test_retrieval.py` direto (sem -m).
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.retrieval.cases import (
    buscar_deterministico,
    buscar_hibrido,
    buscar_semantico,
)
from src.schemas.case import CaseResultado


# -----------------------------------------------------------------------------
# Apresentacao
# -----------------------------------------------------------------------------
def imprimir_resultado(resultados: list[CaseResultado], limite: int = 5) -> None:
    """Imprime uma lista de CaseResultado de forma compacta."""
    if not resultados:
        print("  (nenhum resultado)")
        return
    for i, r in enumerate(resultados[:limite], start=1):
        score = (
            f"score={r.score_similaridade:.3f} "
            if r.score_similaridade is not None
            else ""
        )
        print(
            f"  {i:>2}. [{r.id:>2}] {r.cliente:<40} "
            f"{score}({r.motivo_match})"
        )
        print(f"      setor={r.setor_empresa} | area={r.area_pj} | servico={r.servico_pj}")
    if len(resultados) > limite:
        print(f"  ... e mais {len(resultados) - limite} resultado(s).")


def cabecalho(titulo: str) -> None:
    print()
    print("=" * 70)
    print(titulo)
    print("=" * 70)


# -----------------------------------------------------------------------------
# Bateria pre-definida - quando rodado sem subcomando
# -----------------------------------------------------------------------------
def rodar_bateria_padrao() -> None:
    """Executa um conjunto fixo de queries para smoke-test rapido."""

    cabecalho("[1/6] Deterministico: filtros vazios -> deve retornar todos os 24")
    res = buscar_deterministico(filtros={})
    print(f"Total: {len(res)} cases")
    imprimir_resultado(res, limite=3)

    cabecalho("[2/6] Deterministico: setor=Varejo (primario ou associado)")
    res = buscar_deterministico(filtros={"setor": "Varejo"})
    print(f"Total: {len(res)} cases")
    imprimir_resultado(res, limite=10)

    cabecalho("[3/6] Deterministico: area_pj=Ciencia de Dados")
    res = buscar_deterministico(filtros={"area_pj": "Ciência de Dados"})
    print(f"Total: {len(res)} cases")
    imprimir_resultado(res, limite=10)

    cabecalho("[4/6] Semantico: 'previsao de demanda com sazonalidade'")
    res = buscar_semantico("previsão de demanda com sazonalidade", top_k=5)
    imprimir_resultado(res)

    cabecalho("[5/6] Semantico: 'reducao de fraude em transacoes financeiras'")
    res = buscar_semantico("redução de fraude em transações financeiras", top_k=5)
    imprimir_resultado(res)

    cabecalho("[6/6] Hibrido: setor=Varejo + 'previsao de demanda'")
    res = buscar_hibrido(
        filtros={"setor": "Varejo"},
        query="previsão de demanda",
        top_k=5,
    )
    imprimir_resultado(res)

    print()
    print("Bateria concluida.")


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Teste manual da camada de retrieval do PJ Warmer."
    )
    sub = parser.add_subparsers(dest="modo")

    # det
    p_det = sub.add_parser("det", help="busca deterministica")
    p_det.add_argument("--setor", help="filtro de setor (primario ou associado)")
    p_det.add_argument("--area", dest="area_pj", help="filtro de area_pj")
    p_det.add_argument("--servico", dest="servico_pj", help="filtro de servico_pj")

    # sem
    p_sem = sub.add_parser("sem", help="busca semantica")
    p_sem.add_argument("query", help="pergunta em texto livre")
    p_sem.add_argument("--top-k", type=int, default=5)

    # hib
    p_hib = sub.add_parser("hib", help="busca hibrida")
    p_hib.add_argument("--setor")
    p_hib.add_argument("--area", dest="area_pj")
    p_hib.add_argument("--servico", dest="servico_pj")
    p_hib.add_argument("--query", required=True)
    p_hib.add_argument("--top-k", type=int, default=5)

    args = parser.parse_args()

    if args.modo is None:
        # Sem subcomando -> bateria padrao.
        rodar_bateria_padrao()
        return 0

    if args.modo == "det":
        filtros = {
            k: v
            for k, v in {
                "setor": args.setor,
                "area_pj": args.area_pj,
                "servico_pj": args.servico_pj,
            }.items()
            if v is not None
        }
        cabecalho(f"Deterministico - filtros: {filtros}")
        res = buscar_deterministico(filtros)
        print(f"Total: {len(res)} cases")
        imprimir_resultado(res, limite=20)

    elif args.modo == "sem":
        cabecalho(f"Semantico - query: {args.query!r} (top_k={args.top_k})")
        res = buscar_semantico(args.query, top_k=args.top_k)
        imprimir_resultado(res, limite=args.top_k)

    elif args.modo == "hib":
        filtros = {
            k: v
            for k, v in {
                "setor": args.setor,
                "area_pj": args.area_pj,
                "servico_pj": args.servico_pj,
            }.items()
            if v is not None
        }
        cabecalho(
            f"Hibrido - filtros: {filtros}, query: {args.query!r} "
            f"(top_k={args.top_k})"
        )
        res = buscar_hibrido(filtros, args.query, top_k=args.top_k)
        imprimir_resultado(res, limite=args.top_k)

    return 0


if __name__ == "__main__":
    sys.exit(main())
