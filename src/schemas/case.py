"""
Schema Pydantic do resultado de uma busca de case.

Esta classe é o "contrato de saída" da camada de retrieval — quem chama
buscar_deterministico/buscar_semantico/buscar_hibrido recebe uma lista
disso. Ter um schema explícito permite:

1. Tipagem: o agente do chatbot pode confiar nos campos.
2. Validação: o Pydantic recusa dados malformados antes de chegarem
   no LLM.
3. Documentação viva: olhar essa classe é a forma mais rápida de
   entender o que o retrieval devolve.

Definição vem direto da seção 3.6 do PLANEJAMENTO_BUSCA_CASES.md.
"""

from typing import Optional

from pydantic import BaseModel, Field


class CaseResultado(BaseModel):
    """Resultado individual de uma busca de case."""

    # Campos vindos diretamente da tabela `casepj`.
    id: int = Field(description="ID interno do case.")
    cliente: str = Field(description="Nome do cliente. Ex: 'Hypera Pharma'.")
    setor_empresa: str = Field(
        description="Setor primário da empresa cliente. Ex: 'Farmacêutica'."
    )
    setores_associados: list[str] = Field(
        default_factory=list,
        description="Outros setores onde o case se aplica. Pode ser vazio.",
    )
    area_pj: str = Field(description="Área da PJ que executou. Ex: 'Ciência de Dados'.")
    servico_pj: str = Field(description="Serviço entregue. Ex: 'Análise Preditiva'.")
    problema: str = Field(description="Descrição do problema.")
    solucao: str = Field(description="Descrição da solução entregue.")
    impacto_roi: str = Field(description="Resultados quantitativos/qualitativos.")

    # Campos calculados pela busca (não estão na tabela).
    score_similaridade: Optional[float] = Field(
        default=None,
        description=(
            "Similaridade de cosseno entre 0 e 1 (vetores L2-normalizados). "
            "None quando a busca foi puramente determinística."
        ),
    )
    motivo_match: str = Field(
        description=(
            "Explica por que este case apareceu no resultado. "
            "Ex: 'filtro: setor=Varejo' ou 'similaridade: 0.87' ou ambos."
        ),
    )
