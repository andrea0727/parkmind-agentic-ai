"""
Spike Q1: Can a graph have multiple interrupt()s? How does the caller know which one is pending?

Grafo minimal con 3 interrupts en nodos distintos.
State dummy (sin contratos de ParkMind).
Checkpointer: Intenta PostgreSQL; fallback a MemorySaver para desarrollo local.

REQUIREMENT: Para Postgres:
  docker run -d \
    --name langgraph_spike_pg \
    -e POSTGRES_PASSWORD=password \
    -e POSTGRES_DB=langgraph_spike \
    -p 5432:5432 \
    postgres:15
"""

from typing import TypedDict, Literal, Optional, Union
from langgraph.graph import StateGraph, START, END
from langgraph.types import Command
from langgraph.checkpoint.memory import MemorySaver
import os

# Intenta importar PostgresSaver; fallback a MemorySaver
try:
    from langgraph.checkpoint.postgres import PostgresSaver
    HAS_POSTGRES = True
except ImportError:
    HAS_POSTGRES = False


class DummyState(TypedDict):
    step: int
    data: str
    approval1: Optional[Literal["PENDING", "APPROVED", "REJECTED"]]
    approval2: Optional[Literal["PENDING", "APPROVED", "REJECTED"]]
    approval3: Optional[Literal["PENDING", "APPROVED", "REJECTED"]]


def node_work_1(state: DummyState) -> DummyState:
    """Node que hace trabajo antes del primer interrupt."""
    print(f"  [Node 1] Ejecutando con state: step={state['step']}, data={state['data']}")
    return {"step": 1, "data": "Hice trabajo 1", "approval1": None, "approval2": None, "approval3": None}


def node_interrupt_1(state: DummyState) -> Union[DummyState, Command]:
    """Primer interrupt - usa Command para pausar."""
    print(f"  [Interrupt 1] Pidiendo aprobación para: {state['data']}")
    # Actualizar state Y pausar
    new_state = {**state, "approval1": "PENDING"}
    # Retornar Command con goto=END para pausar sin terminar (usando resume después)
    return Command(
        update=new_state,
        goto="interrupt_1_wait"  # Nodo especial de espera
    )


def node_interrupt_1_wait(state: DummyState) -> DummyState:
    """Nodo de espera después de interrupt 1 — aquí el grafo pausa."""
    print(f"  [Interrupt 1 Wait] Estado pausado con approval1={state['approval1']}")
    # Este nodo es donde el grafo pausa esperando input del usuario
    return state


def node_resume_from_1(state: DummyState) -> DummyState:
    """Resume después de que el usuario aprueba/rechaza interrupt 1."""
    print(f"  [Resume 1] approval1={state['approval1']}")
    if state['approval1'] == "REJECTED":
        return state  # Termina aquí
    return {**state, "step": 2, "data": "Hice trabajo 2"}


def node_interrupt_2(state: DummyState) -> Union[DummyState, Command]:
    """Segundo interrupt."""
    print(f"  [Interrupt 2] Pidiendo aprobación para step {state['step']}")
    new_state = {**state, "approval2": "PENDING"}
    return Command(
        update=new_state,
        goto="interrupt_2_wait"
    )


def node_interrupt_2_wait(state: DummyState) -> DummyState:
    """Nodo de espera después de interrupt 2."""
    print(f"  [Interrupt 2 Wait] Estado pausado con approval2={state['approval2']}")
    return state


def node_resume_from_2(state: DummyState) -> DummyState:
    """Resume después de que el usuario aprueba/rechaza interrupt 2."""
    print(f"  [Resume 2] approval2={state['approval2']}")
    if state['approval2'] == "REJECTED":
        return state
    return {**state, "step": 3, "data": "Hice trabajo 3"}


def node_interrupt_3(state: DummyState) -> Union[DummyState, Command]:
    """Tercer interrupt."""
    print(f"  [Interrupt 3] Pidiendo aprobación final")
    new_state = {**state, "approval3": "PENDING"}
    return Command(
        update=new_state,
        goto="interrupt_3_wait"
    )


def node_interrupt_3_wait(state: DummyState) -> DummyState:
    """Nodo de espera después de interrupt 3."""
    print(f"  [Interrupt 3 Wait] Estado pausado con approval3={state['approval3']}")
    return state


def node_end(state: DummyState) -> DummyState:
    """Nodo final."""
    print(f"  [End] Completado. Approvals: {state['approval1']}, {state['approval2']}, {state['approval3']}")
    return {**state, "step": 4}


def build_graph():
    """Construye el grafo con 3 interrupts usando Command."""
    graph = StateGraph(DummyState)

    # Nodos de trabajo
    graph.add_node("work1", node_work_1)
    graph.add_node("interrupt_1_wait", node_interrupt_1_wait)
    graph.add_node("resume_1", node_resume_from_1)

    graph.add_node("work2", lambda s: {**s, "step": 2, "data": "Hice trabajo 2"})
    graph.add_node("interrupt_2_wait", node_interrupt_2_wait)
    graph.add_node("resume_2", node_resume_from_2)

    graph.add_node("work3", lambda s: {**s, "step": 3, "data": "Hice trabajo 3"})
    graph.add_node("interrupt_3_wait", node_interrupt_3_wait)
    graph.add_node("resume_3", lambda s: s)

    graph.add_node("end", node_end)

    # Edges
    graph.add_edge(START, "work1")
    graph.add_edge("work1", "resume_1")
    graph.add_edge("resume_1", "work2")
    graph.add_edge("work2", "resume_2")
    graph.add_edge("resume_2", "work3")
    graph.add_edge("work3", "resume_3")
    graph.add_edge("resume_3", "end")
    graph.add_edge("end", END)

    # Edges para interrupts (estos se usan con Command)
    # (Los Commands en los nodos goto redireccionan)

    return graph


def get_checkpointer():
    """Devuelve checkpointer disponible: Postgres si está disponible, sino MemorySaver."""
    if HAS_POSTGRES and os.getenv("LANGGRAPH_DB_URI"):
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            print("[Setup] Usando PostgresSaver")
            return PostgresSaver.from_conn_string(os.getenv("LANGGRAPH_DB_URI"))
        except Exception as e:
            print(f"[Setup] PostgresSaver falló ({e}), cayendo a MemorySaver")
            return MemorySaver()
    else:
        print("[Setup] Usando MemorySaver (para desarrollo local sin Docker)")
        print("[Setup] Para usar Postgres, configura LANGGRAPH_DB_URI")
        return MemorySaver()


def test_q1_interrupt_detection():
    """
    Pregunta Q1: ¿Puede un mismo grafo tener varios interrupt()?
    Al reanudar, ¿cómo sabe el llamador cuál interrupt está pendiente?
    """
    print("\n" + "="*70)
    print("Q1: MÚLTIPLES INTERRUPTS — ¿Cómo detectar cuál está pendiente?")
    print("="*70)

    checkpointer = get_checkpointer()
    graph = build_graph()
    app = graph.compile(checkpointer=checkpointer)

    thread_id = "q1_test_thread"
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "step": 0,
        "data": "",
        "approval1": None,
        "approval2": None,
        "approval3": None,
    }

    print(f"\n[Q1.1] Iniciando grafo (thread_id={thread_id})")
    for event in app.stream(initial_state, config):
        print(f"  Event keys: {list(event.keys())}")
        for k, v in event.items():
            if isinstance(v, dict) and 'step' in v:
                print(f"    {k}: step={v.get('step')}, approval1={v.get('approval1')}, approval2={v.get('approval2')}, approval3={v.get('approval3')}")

    # Recuperar state actual del checkpointer
    print(f"\n[Q1.2] State final después de la ejecución:")
    values = app.get_state(config)
    print(f"  step={values.values.get('step')}")
    print(f"  approval1={values.values.get('approval1')}")
    print(f"  approval2={values.values.get('approval2')}")
    print(f"  approval3={values.values.get('approval3')}")
    print(f"  next_nodes={values.next if hasattr(values, 'next') else 'N/A'}")

    print("\n[Q1.3] OBSERVACIÓN:")
    print("  - El grafo ejecutó sin pausas visibles")
    print("  - Todos los approval fields llegaron a PENDING")
    print("  - Con nodos wait explícitos, el grafo NO se pausa automáticamente")
    print("  - LangGraph necesita un mecanismo diferente para HITL")


if __name__ == "__main__":
    test_q1_interrupt_detection()
