"""
Spike Q5: ¿Cómo se ve todo esto desde Streamlit y no desde un notebook?
¿Dónde vive el estado de "hay una aprobación pendiente" entre reruns?

Crear un stub mínimo de Streamlit que demuestre cómo persiste
el estado entre app reruns.
"""

import streamlit as st
from typing import TypedDict, Literal, Optional
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
import json


class Q5State(TypedDict):
    step: int
    data: str
    approval: Optional[Literal["PENDING", "APPROVED", "REJECTED"]]


def node_work(state: Q5State) -> Q5State:
    """Nodo que genera trabajo."""
    return {**state, "step": 1, "data": "Generé datos"}


def node_ask(state: Q5State) -> Q5State:
    """Nodo que pide aprobación."""
    return {**state, "approval": "PENDING"}


def node_continue(state: Q5State) -> Q5State:
    """Nodo que continúa si fue aprobado."""
    return {**state, "step": 2, "data": "Continué"}


def build_q5_graph():
    """Grafo simple."""
    graph = StateGraph(Q5State)

    graph.add_node("work", node_work)
    graph.add_node("ask", node_ask)
    graph.add_node("continue", node_continue)

    graph.add_edge(START, "work")
    graph.add_edge("work", "ask")
    graph.add_edge("ask", "continue")
    graph.add_edge("continue", END)

    return graph


def streamlit_app():
    """
    Q5: Aplicación Streamlit que demuestra:
    1. El grafo ejecuta
    2. Si hay PENDING, moestra botones de aprobación
    3. El estado persiste entre reruns via st.session_state
    """
    st.set_page_config(page_title="Q5: Streamlit + LangGraph")
    st.title("Spike Q5: LangGraph en Streamlit")

    # CLAVE: Streamlit reruns todo el script en cada interacción
    # Pero st.session_state persiste entre reruns

    # Inicializar el grafo y checkpointer UNA SOLA VEZ
    if "app" not in st.session_state:
        st.write("[Init] Creando grafo y checkpointer...")
        checkpointer = MemorySaver()
        graph = build_q5_graph()
        st.session_state.app = graph.compile(checkpointer=checkpointer)
        st.session_state.thread_id = "q5_streamlit_thread"
        st.session_state.config = {
            "configurable": {"thread_id": st.session_state.thread_id}
        }

    # Inicializar state si no existe
    if "current_state" not in st.session_state:
        st.write("[Init] Ejecutando grafo...")
        initial = {"step": 0, "data": "", "approval": None}

        # Ejecutar el grafo y guardar los eventos
        events = []
        for event in st.session_state.app.stream(initial, st.session_state.config):
            events.append(event)

        # Obtener el estado final del checkpointer
        final = st.session_state.app.get_state(st.session_state.config)
        st.session_state.current_state = final.values
        st.write(f"[Stream] Ejecutados {len(events)} eventos")

    # Mostrar el estado actual
    st.subheader("Estado actual")
    st.json(st.session_state.current_state)

    # Si está PENDING, mostrar botones
    if st.session_state.current_state.get("approval") == "PENDING":
        st.warning("Hay una aprobación pendiente!")

        col1, col2, col3 = st.columns(3)

        with col1:
            if st.button("✓ Aprobar"):
                st.write("[Action] Usuario aprobó")
                st.session_state.current_state["approval"] = "APPROVED"
                st.rerun()

        with col2:
            if st.button("✗ Rechazar"):
                st.write("[Action] Usuario rechazó")
                st.session_state.current_state["approval"] = "REJECTED"
                st.rerun()

        with col3:
            if st.button("? Más info"):
                st.write("[Action] Usuario pidió más info")
                st.info("No hay más información disponible en este stub.")

    else:
        if st.session_state.current_state.get("approval") == "APPROVED":
            st.success(f"Aprobado. Estado final: {st.session_state.current_state['data']}")
        elif st.session_state.current_state.get("approval") == "REJECTED":
            st.error(f"Rechazado. Resets el estado.")
            if st.button("Reintentar"):
                del st.session_state.current_state
                st.rerun()

    st.divider()
    st.subheader("[Q5] Observaciones sobre Streamlit + Checkpointer")
    st.markdown("""
    1. **Persistencia entre reruns:** `st.session_state` persiste el state durante la sesión.
    2. **Checkpointer local:** MemorySaver está en-memory, vive en `st.session_state`.
    3. **Postgres:** Con PostgresSaver, el checkpointer persiste en BD — el estado vive aquí, no en `st.session_state`.
    4. **Donde vive la aprobación PENDING:**
       - Con MemorySaver: en `st.session_state.app` (checkpointer en-memory)
       - Con Postgres: en la tabla `checkpoints` de la BD
    5. **El estado entre reruns es:**
       - Session state: `st.session_state.current_state` (lo que muestra la UI)
       - Checkpointer: `st.session_state.app.get_state(config).values` (fuente de verdad)
    """)


if __name__ == "__main__":
    streamlit_app()
