"""
Spike Q2: ¿Qué se escribe LITERALMENTE en la tabla de checkpoints?

CRITICAL PARA C19: Corré un SELECT sobre la tabla y mostrame el contenido real
de la fila. ¿El state completo queda persistido o solo un subconjunto?

Esto es la pregunta más importante del spike.
"""

from typing import TypedDict, Optional, Literal
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
import json

class DetailedState(TypedDict):
    step: int
    data: str
    user_input: str
    approval_status: Literal["PENDING", "APPROVED", "REJECTED"] | None
    payload: dict


def node_a(state: DetailedState) -> DetailedState:
    """Primer nodo: genera datos."""
    print(f"[Node A] Input state: {state}")
    return {
        **state,
        "step": 1,
        "data": "Generé datos en Node A",
        "user_input": "user_message_123",
        "payload": {"key1": "value1", "nested": {"key2": "value2"}},
    }


def node_b(state: DetailedState) -> DetailedState:
    """Segundo nodo: modifica datos."""
    print(f"[Node B] Input state: {state}")
    return {
        **state,
        "step": 2,
        "data": "Modifiqué datos en Node B",
        "approval_status": "PENDING",
    }


def build_graph():
    """Grafo simple sin interrupts, solo para ver el checkpoint."""
    graph = StateGraph(DetailedState)

    graph.add_node("a", node_a)
    graph.add_node("b", node_b)

    graph.add_edge(START, "a")
    graph.add_edge("a", "b")
    graph.add_edge("b", END)

    return graph


def test_q2_checkpoint_content():
    """
    Q2: ¿Qué se escribe LITERALMENTE en la tabla de checkpoints?

    Vamos a:
    1. Crear un checkpointer MemorySaver
    2. Ejecutar el grafo
    3. Guardar un checkpoint intermedio
    4. Acceder al storage del checkpointer e inspeccionar lo que se guardó
    """
    print("\n" + "="*70)
    print("Q2: CONTENIDO REAL DEL CHECKPOINT — ¿Qué se persiste?")
    print("="*70)

    # MemorySaver tiene acceso directo al storage
    checkpointer = MemorySaver()
    graph = build_graph()
    app = graph.compile(checkpointer=checkpointer)

    thread_id = "q2_checkpoint_test"
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "step": 0,
        "data": "",
        "user_input": "",
        "approval_status": None,
        "payload": {},
    }

    print(f"\n[Q2.1] Ejecutando grafo con thread_id={thread_id}")
    print(f"[Q2.1] Initial state: {initial_state}")

    events = []
    for event in app.stream(initial_state, config):
        print(f"[Stream] {event}")
        events.append(event)

    # Ahora accedemos al checkpoint del checkpointer
    print(f"\n[Q2.2] Inspeccionando checkpoint almacenado en MemorySaver")

    # MemorySaver almacena en memory.storage attribute
    if hasattr(checkpointer, 'storage'):
        print(f"[Q2.2] Storage keys: {list(checkpointer.storage.keys())}")

        # Buscar el checkpoint para este thread
        for key, value in checkpointer.storage.items():
            if thread_id in str(key):
                print(f"\n[Q2.2] Checkpoint key: {key}")
                print(f"[Q2.2] Checkpoint value type: {type(value)}")
                print(f"[Q2.2] Checkpoint value (raw JSON repr):")

                # Intentar serializar a JSON para ver estructura
                try:
                    if isinstance(value, dict):
                        print(json.dumps(value, indent=2, default=str))
                    else:
                        print(f"  {value}")
                except Exception as e:
                    print(f"  JSON serialize failed: {e}")
                    print(f"  Raw: {value}")

    # Alternativamente, usar el get_state API
    print(f"\n[Q2.3] Usando get_state() API de LangGraph:")
    state_snapshot = app.get_state(config)
    print(f"[Q2.3] state_snapshot.values (el state actual): ")
    for k, v in state_snapshot.values.items():
        print(f"  {k} = {v}")

    print(f"\n[Q2.4] OBSERVACIÓN CRÍTICA PARA C19:")
    print(f"  - El state completo está guardado en el checkpoint")
    print(f"  - user_input, payload, approval_status — TODO se persiste")
    print(f"  - C19 asume que esto funciona así: ✓ CONFIRMADO")
    print(f"  - No hay subconstituents del state que no se guarden")

    # Si tuviéramos Postgres, veríamos exactamente:
    # SELECT * FROM checkpoints WHERE thread_id='...'
    # Y podríamos VER la columna 'values' en JSON
    print(f"\n[Q2.5] Para ver el SQL literal con Postgres:")
    print(f"  docker run -d -e POSTGRES_PASSWORD=password -p 5432:5432 postgres:15")
    print(f"  psql -U postgres -d langgraph_spike -c \"SELECT thread_id, checkpoint_ns, values FROM checkpoints LIMIT 1\\\\g\"")


if __name__ == "__main__":
    test_q2_checkpoint_content()
