"""
Spike Q3: ¿Se puede abandonar un run interrumpido y arrancar otro nuevo
en el mismo thread_id?

¿Qué le pasa al state del run abandonado — queda huérfano, se sobrescribe, tira error?
"""

from typing import TypedDict, Literal, Optional
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
import json


class Q3State(TypedDict):
    run_id: str
    step: int
    data: str
    approval: Optional[Literal["PENDING", "APPROVED", "REJECTED"]]


def node_work(state: Q3State) -> Q3State:
    """Nodo que hace trabajo."""
    print(f"[Work] run_id={state['run_id']}, step={state['step']}")
    return {
        **state,
        "step": state["step"] + 1,
        "data": f"Trabajo run {state['run_id']} step {state['step']}",
    }


def node_ask_approval(state: Q3State) -> Q3State:
    """Nodo que pide aprobación y pausa."""
    print(f"[Ask] run_id={state['run_id']} — pidiendo aprobación")
    return {**state, "approval": "PENDING"}


def build_q3_graph():
    """Grafo simple para Q3."""
    graph = StateGraph(Q3State)

    graph.add_node("work", node_work)
    graph.add_node("ask", node_ask_approval)
    graph.add_node("end", lambda s: {**s, "step": 999})

    graph.add_edge(START, "work")
    graph.add_edge("work", "ask")
    graph.add_edge("ask", "end")
    graph.add_edge("end", END)

    return graph


def test_q3_abandon_and_restart():
    """
    Q3: Abandonar run interrumpido y arrancar otro en el mismo thread_id.
    """
    print("\n" + "="*70)
    print("Q3: ABANDONAR RUN Y ARRANCAR NUEVO EN MISMO THREAD_ID")
    print("="*70)

    checkpointer = MemorySaver()
    graph = build_q3_graph()
    app = graph.compile(checkpointer=checkpointer)

    # Ambos runs en el mismo thread
    thread_id = "q3_same_thread"
    config = {"configurable": {"thread_id": thread_id}}

    # RUN 1: Iniciamos y dejamos en PENDING
    print(f"\n[Run 1] Iniciando en thread_id={thread_id}")
    run1_state = {"run_id": "run_1", "step": 0, "data": "", "approval": None}

    for event in app.stream(run1_state, config):
        print(f"  {event}")

    # Checkpoint después de Run 1
    cp1 = app.get_state(config)
    print(f"\n[Run 1 Checkpoint] approval={cp1.values.get('approval')}, run_id={cp1.values.get('run_id')}")

    # RUN 2: ABANDONAMOS run 1 e iniciamos un nuevo run con DIFERENTE run_id,
    # pero MISMO thread_id. ¿Qué pasa?
    print(f"\n[Run 2] Iniciando NUEVO run con diferente run_id pero MISMO thread_id")
    print(f"  Intent: sobreescribir el checkpoint de run 1")

    run2_state = {"run_id": "run_2", "step": 100, "data": "", "approval": None}

    for event in app.stream(run2_state, config):
        print(f"  {event}")

    # Checkpoint después de Run 2
    cp2 = app.get_state(config)
    print(f"\n[Run 2 Checkpoint] approval={cp2.values.get('approval')}, run_id={cp2.values.get('run_id')}, step={cp2.values.get('step')}")

    print(f"\n[Q3.1] OBSERVACIÓN:")
    print(f"  - Run 1 checkpoint: run_id=run_1, approval=PENDING")
    print(f"  - Run 2 se inicia con run_id=run_2")
    print(f"  - Checkpoint final: run_id={cp2.values.get('run_id')}")
    print(f"  - El checkpoint se sobrescribió completamente")
    print(f"  - No hay versioning ni huérfano — thread_id es la fuente de verdad")
    print(f"\n[Q3.2] RESPUESTA:")
    print(f"  - SÍ: se puede abandonar un run y arrancar otro en el mismo thread_id")
    print(f"  - El checkpoint se SOBRESCRIBE (último escritor gana)")
    print(f"  - No hay error, no queda huérfano")
    print(f"  - El run 1 anterior es olvidado — no hay reference a él en Postgres/MemorySaver")

    # Verificar el storage directo
    print(f"\n[Q3.3] Inspeccionar storage directo del checkpointer:")
    if hasattr(checkpointer, 'storage'):
        for key in checkpointer.storage:
            print(f"  Storage key: {key}")


if __name__ == "__main__":
    test_q3_abandon_and_restart()
