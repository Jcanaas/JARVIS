## codebase-memory

**ALWAYS use codebase-memory-mcp FIRST for any code exploration.** This project has a live index. Do not default to grep/read/glob.

Priority order:
1. search_graph(query/name_pattern) - find functions, classes, routes by name or description
2. trace_path(function_name) - trace callers, callees, data flow
3. query_graph(cypher_query) - complex multi-hop patterns
4. get_code_snippet(qualified_name) - exact source after search_graph locates it

Only fall back to grep/read if the graph doesn't surface enough context.

Disabled graphify in favor of codebase-memory.

## Ajustes que "no se guardan"

Antes de tocar `actions/app_settings.py` por un reporte de "los ajustes se
resetean/no se guardan": lee [`NOTE_settings_persistence.md`](NOTE_settings_persistence.md)
entero primero. Ya nos pasó tres veces por causas distintas (caché de
escritura obsoleto, build de PyInstaller vieja, ACL sin permiso de
escritura) y las tres están documentadas ahí con el fix aplicado.
