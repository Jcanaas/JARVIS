Launcher (Windows) — Jarvis + WhatsApp Bridge (actualizado)

He actualizado el lanzador para evitar que los procesos se inicien múltiples veces y para redirigir la salida a logs.

Cambios principales

- Se crea la carpeta `logs/` en la raíz con los ficheros:
   - `logs/node.log`, `logs/node.err`
   - `logs/jarvis.log`, `logs/jarvis.err`
- Antes de iniciar `node` o `python`, el script comprueba procesos en ejecución para no lanzar duplicados.
- Si el proceso ya está en ejecución, el script no intentará reiniciarlo.

Diagnóstico si algo parpadea (se abre y se cierra)

- Revisa `logs/jarvis.err` y `logs/node.err` para ver excepciones/errores.
- Si `jarvis.log` contiene un trace o el proceso se cierra inmediatamente, pega aquí las primeras líneas y lo reviso.

Cómo ejecutar manualmente y ver logs

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
& .\launch_jarvis_and_whatsapp.ps1
# luego revisar:
Get-Content .\logs\jarvis.err -Tail 50 -Wait
Get-Content .\logs\node.err -Tail 50 -Wait
```

Siguientes pasos

- Si el problema persiste (proceso se inicia y muere), copia aquí el contenido de `logs/jarvis.err` y `logs/jarvis.log` y lo diagnostico.
- Opcional: puedo convertir esto en un servicio de Windows que reinicie pero registre errores para evitar loops infinitos.
