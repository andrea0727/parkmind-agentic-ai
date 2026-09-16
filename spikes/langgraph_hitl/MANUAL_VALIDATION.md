# Guía: Validar LangGraph + Postgres Manualmente

Tu Postgres está corriendo y el test funciona. Aquí te muestro cómo hacer queries directas a las tablas y validar todo.

## 1. Conectar a Postgres

```bash
docker exec -it langgraph_spike_pg psql -U postgres -d langgraph_spike
```

## 2. Ver las tablas que crea LangGraph

```sql
\dt
```

Verás 4 tablas:
- `checkpoints` — el estado guardado de cada paso
- `checkpoint_writes` — qué campos cambiaron en cada paso
- `checkpoint_blobs` — datos binarios codificados en msgpack
- `checkpoint_migrations` — metadatos de versión

## 3. Ver todos los checkpoints de un run

```sql
SELECT thread_id, checkpoint_id, step, ts 
FROM checkpoints 
WHERE thread_id = 'manual_test_thread'
ORDER BY ts;
```

**Resultado esperado:** 10 filas (uno por paso del grafo)

## 4. VER EL STATE COMPLETO GUARDADO

Este es lo más importante — el state COMPLETO está en la columna `checkpoint` (JSONB):

```sql
SELECT 
  checkpoint_id,
  checkpoint->'step' as step_value,
  checkpoint->'data' as data_value,
  checkpoint->'approval' as approval_value,
  checkpoint->'user_info' as user_info_value
FROM checkpoints
WHERE thread_id = 'manual_test_thread'
ORDER BY checkpoint_id DESC
LIMIT 1;
```

**Resultado:** Verás todo el state en JSON:
```
step_value    | 2
data_value    | "Completé node_complete"
approval_value| "PENDING"
user_info_value| {"user_id": "user_123", "name": "Alice"}
```

## 5. Ver qué fields se escribieron en cada checkpoint

```sql
SELECT 
  checkpoint_id,
  channel,
  type,
  length(blob) as blob_size
FROM checkpoint_writes
WHERE thread_id = 'manual_test_thread'
ORDER BY checkpoint_id DESC
LIMIT 10;
```

**Resultado:** Verás qué fields cambiaron:
```
checkpoint_id            | channel      | type     | blob_size
--------------------------|--------------|----------|----------
1f1b2080-5692-6d7a-8008...| step        | msgpack  | 1
1f1b2080-5692-6d7a-8008...| data        | msgpack  | 24
1f1b2080-5692-6d7a-8008...| approval    | msgpack  | 8
1f1b2080-5692-6d7a-8008...| user_info   | msgpack  | 29
```

**Esto CONFIRMA:** ✓ El state COMPLETO se persiste (step, data, approval, user_info).

## 6. Ver el tamaño total guardado

```sql
SELECT 
  thread_id,
  COUNT(DISTINCT checkpoint_id) as checkpoints_count,
  SUM(length(checkpoint)::bigint) as total_bytes
FROM checkpoints
WHERE thread_id = 'manual_test_thread'
GROUP BY thread_id;
```

## 7. Ver los blobs (datos binarios)

```sql
SELECT thread_id, channel, version, type 
FROM checkpoint_blobs
WHERE thread_id = 'manual_test_thread';
```

Los blobs están en formato msgpack (binario eficiente).

## 8. Validar C19: ¿Se persiste TODO o solo un subset?

**Pregunta:** Si pongo `AccessibilityRequirements` en el state, ¿se guardará en el checkpoint?

**Respuesta:** SÍ. Todo lo que esté en el TypedDict se persiste.

Ejemplo - añade esto a tu state:
```python
class ParkMindState(TypedDict):
    guest_profiles: list[GuestProfile]
    accessibility_ref: list[str]  # ✓ Esto SÍ se guarda
    # accessibility_requirements: list[AccessibilityRequirements]  # ✗ ¡NO incluir aquí!
    # En su lugar, cargar desde session store per-run
```

## 9. Query para confirmar la estructura completa

Corre esto para ver **exactamente** qué data está guardada en Postgres:

```sql
SELECT 
  checkpoint_id,
  jsonb_pretty(checkpoint) as pretty_checkpoint
FROM checkpoints
WHERE thread_id = 'manual_test_thread'
ORDER BY checkpoint_id DESC
LIMIT 1;
```

Verás el JSON completo y formateado.

## 10. Borrar todos los checkpoints de un run (para empezar limpio)

```sql
DELETE FROM checkpoints WHERE thread_id = 'manual_test_thread';
DELETE FROM checkpoint_writes WHERE thread_id = 'manual_test_thread';
DELETE FROM checkpoint_blobs WHERE thread_id = 'manual_test_thread';
```

## Python: Queries desde código

Ya tienes esto en `test_manual_postgres.py`:

```bash
poetry run python test_manual_postgres.py          # Ejecuta el test
poetry run python test_manual_postgres.py schema   # Ver schema de tablas
```

## Checklist de Validación

- [ ] Postgres está corriendo: `docker ps | grep langgraph_spike_pg`
- [ ] Conecta a BD: `psql -U postgres -d langgraph_spike`
- [ ] Ver checkpoints: `SELECT COUNT(*) FROM checkpoints;`
- [ ] Ver un state completo: `SELECT checkpoint FROM checkpoints LIMIT 1;`
- [ ] Confirmar fields: `SELECT DISTINCT channel FROM checkpoint_writes;`
- [ ] El contenido está en JSONB: `SELECT checkpoint->'approval' FROM checkpoints LIMIT 1;`

## Conclusión para C19

**✓ CONFIRMADO:** LangGraph persiste TODO el state completo en Postgres.

- Cada field del TypedDict se guarda
- No hay subset — todo está
- Los datos están en formato JSONB (fácil de consultar)
- Para session-only retention: **NO incluir `AccessibilityRequirements` en el state**, cargarlo desde session store

Esto valida que la arquitectura v2.2 C19 es viable.
