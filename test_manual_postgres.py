"""
Test Manual con PostgreSQL Real

Script que:
1. Crea un grafo mínimo
2. Ejecuta contra PostgresSaver (tu contenedor)
3. Muestra el contenido LITERAL de las tablas
4. Valida las 5 preguntas del spike

Ejecuta con:
  python test_manual_postgres.py
"""

from typing import TypedDict, Literal, Optional
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
import psycopg
import json


class ManualTestState(TypedDict):
    step: int
    data: str
    approval: Optional[Literal["PENDING", "APPROVED", "REJECTED"]]
    user_info: dict


def node_work(state: ManualTestState) -> ManualTestState:
    """Genera datos."""
    return {
        **state,
        "step": 1,
        "data": "Generé datos en node_work",
        "user_info": {"user_id": "user_123", "name": "Alice"},
    }


def node_ask(state: ManualTestState) -> ManualTestState:
    """Pide aprobación."""
    return {**state, "approval": "PENDING"}


def node_complete(state: ManualTestState) -> ManualTestState:
    """Completa."""
    return {**state, "step": 2, "data": "Completé node_complete"}


def build_graph():
    """Grafo simple de 3 nodos."""
    graph = StateGraph(ManualTestState)

    graph.add_node("work", node_work)
    graph.add_node("ask", node_ask)
    graph.add_node("complete", node_complete)

    graph.add_edge(START, "work")
    graph.add_edge("work", "ask")
    graph.add_edge("ask", "complete")
    graph.add_edge("complete", END)

    return graph


def test_with_postgres():
    """Prueba REAL con PostgreSQL."""

    # Credenciales del contenedor
    db_uri = "postgresql://postgres:password@localhost:5432/langgraph_spike"

    print("\n" + "=" * 70)
    print("TEST MANUAL CON POSTGRESQL")
    print("=" * 70)

    # 1. Crear checkpointer
    print("\n[1] Creando PostgresSaver...")
    try:
        with PostgresSaver.from_conn_string(db_uri) as checkpointer:
            print("    ✓ Checkpointer creado exitosamente")

            # 2. Compilar grafo
            print("\n[2] Compilando grafo...")
            graph = build_graph()
            app = graph.compile(checkpointer=checkpointer)
            print("    ✓ Grafo compilado")

            # 3. Ejecutar
            thread_id = "manual_test_thread"
            config = {"configurable": {"thread_id": thread_id}}

            initial_state = {
                "step": 0,
                "data": "",
                "approval": None,
                "user_info": {},
            }

            print(f"\n[3] Ejecutando grafo (thread_id={thread_id})...")
            for event in app.stream(initial_state, config):
                for node, state in event.items():
                    print(
                        f"    {node}: step={state.get('step')}, "
                        f"approval={state.get('approval')}, "
                        f"user_info={state.get('user_info')}"
                    )

            # 4. Obtener estado final
            print("\n[4] Estado final del checkpointer:")
            final = app.get_state(config)
            for k, v in final.values.items():
                print(f"    {k} = {v}")

            # 5. Inspeccionar Postgres DIRECTAMENTE
            print("\n[5] Inspeccionando tablas Postgres...")

            # Conectar a Postgres
            with psycopg.connect(db_uri) as conn:
                with conn.cursor() as cur:
                    # Tabla checkpoints
                    print("\n    SELECT * FROM checkpoints WHERE thread_id = %s")
                    cur.execute(
                        "SELECT thread_id, checkpoint_ns, checkpoint_id, checkpoint, metadata FROM checkpoints WHERE thread_id = %s",
                        (thread_id,),
                    )
                    rows = cur.fetchall()

                    if rows:
                        print(f"    ✓ Rows encontradas: {len(rows)}")
                        for i, (tid, ns, cp_id, checkpoint, metadata) in enumerate(rows):
                            print(f"\n    Row {i + 1}:")
                            print(f"      thread_id = {tid}")
                            print(f"      checkpoint_ns = '{ns}'")
                            print(f"      checkpoint_id = {cp_id}")
                            print(f"      checkpoint (JSONB, tamaño) = {len(str(checkpoint)) if checkpoint else 0} bytes")
                            if checkpoint:
                                try:
                                    print(f"      checkpoint (contenido JSON primeros 200 chars):")
                                    cp_str = json.dumps(checkpoint, indent=8) if isinstance(checkpoint, dict) else str(checkpoint)
                                    print(f"        {cp_str[:200]}...")
                                except:
                                    print(f"        {str(checkpoint)[:200]}...")
                            print(f"      metadata = {metadata}")
                    else:
                        print("    ✗ No rows found")

                    # Tabla checkpoint_writes
                    print("\n\n    SELECT * FROM checkpoint_writes WHERE thread_id = %s")
                    cur.execute(
                        "SELECT thread_id, checkpoint_id, channel, type, length(blob) as blob_size FROM checkpoint_writes WHERE thread_id = %s",
                        (thread_id,),
                    )
                    rows = cur.fetchall()
                    if rows:
                        print(f"    ✓ Rows encontradas: {len(rows)}")
                        for i, row in enumerate(rows):
                            print(f"      Row {i + 1}: {row}")
                    else:
                        print("    ✗ No rows found")

                    # Tabla checkpoint_blobs
                    print("\n\n    SELECT * FROM checkpoint_blobs")
                    cur.execute("SELECT thread_id, channel, version, type FROM checkpoint_blobs WHERE thread_id = %s", (thread_id,))
                    rows = cur.fetchall()
                    if rows:
                        print(f"    ✓ Rows encontradas: {len(rows)}")
                        for i, row in enumerate(rows):
                            print(f"      Row {i + 1}: {row}")
                    else:
                        print("    ✗ No rows found")

    except Exception as e:
        print(f"\n✗ Error: {e}")
        print("\nVerifica que:")
        print("  1. Postgres está corriendo: docker ps | grep langgraph_spike_pg")
        print("  2. Tienes conexión: psql -U postgres -d langgraph_spike")
        print("  3. Deps instaladas: pip install langgraph-checkpoint-postgres")
        raise


def inspect_schema():
    """Inspecciona el schema completo."""
    db_uri = "postgresql://postgres:password@localhost:5432/langgraph_spike"

    print("\n" + "=" * 70)
    print("SCHEMA DE LAS TABLAS LANGGRAPH")
    print("=" * 70)

    with psycopg.connect(db_uri) as conn:
        with conn.cursor() as cur:
            # Columnas de checkpoints
            print("\nTABLA: checkpoints")
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'checkpoints'
                ORDER BY ordinal_position
                """
            )
            for col, dtype, nullable in cur.fetchall():
                print(f"  {col:25} {dtype:15} nullable={nullable}")

            # Columnas de checkpoint_writes
            print("\nTABLA: checkpoint_writes")
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'checkpoint_writes'
                ORDER BY ordinal_position
                """
            )
            for col, dtype, nullable in cur.fetchall():
                print(f"  {col:25} {dtype:15} nullable={nullable}")

            # Columnas de checkpoint_blobs
            print("\nTABLA: checkpoint_blobs")
            cur.execute(
                """
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = 'checkpoint_blobs'
                ORDER BY ordinal_position
                """
            )
            for col, dtype, nullable in cur.fetchall():
                print(f"  {col:25} {dtype:15} nullable={nullable}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "schema":
        inspect_schema()
    else:
        test_with_postgres()
        print("\n" + "=" * 70)
        print("TEST COMPLETADO")
        print("=" * 70)
        print("\nPara ver el schema de las tablas:")
        print("  python test_manual_postgres.py schema")
